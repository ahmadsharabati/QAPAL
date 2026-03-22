"""
Repair Pipeline for QAPAL

Connects all repair stages into one flow:
  failure → parse → graph lookup → rank candidates → generate patch → validate

One call returns a complete repair result.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from engine.graph import SiteStateGraph, GraphNode
from engine.repair.failure_parser import FailureParser, ParsedFailure
from engine.repair.locator_matcher import LocatorMatcher, LocatorMatch
from engine.repair.patch_generator import PatchGenerator, PatchResult
from engine.repair.validator import PatchValidator, PatchValidationResult, PatchStatus


# ============================================================================
# Repair Result
# ============================================================================

@dataclass
class RepairResult:
    """
    Complete result of a repair attempt.
    """
    # Overall status
    status: str                     # 'validated', 'draft', 'failed', 'no_candidates'
    
    # Patched code
    patched_code: Optional[str] = None
    diff: Optional[str] = None
    
    # Pipeline stages
    failure_info: Optional[ParsedFailure] = None
    candidate_locators: List[LocatorMatch] = field(default_factory=list)
    patch_result: Optional[PatchResult] = None
    validation_result: Optional[PatchValidationResult] = None
    
    # Metadata
    repair_strategy: str = ""
    confidence: float = 0.0
    duration_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # Trace (for debugging)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage/API response."""
        return {
            'status': self.status,
            'patched_code': self.patched_code,
            'diff': self.diff,
            'repair_strategy': self.repair_strategy,
            'confidence': self.confidence,
            'duration_ms': self.duration_ms,
            'timestamp': self.timestamp,
            'failure_type': self.failure_info.failure_type.value if self.failure_info else None,
            'locator_text': self.failure_info.locator_text if self.failure_info else None,
            'candidate_count': len(self.candidate_locators),
            'validation_status': self.validation_result.status.value if self.validation_result else None,
            'validation_passes': self.validation_result.pass_count if self.validation_result else 0,
            'trace_steps': len(self.trace),
        }


# ============================================================================
# Repair Pipeline
# ============================================================================

class RepairPipeline:
    """
    Full repair pipeline: parse → match → patch → validate.
    """
    
    def __init__(self, graph: SiteStateGraph,
                 validate_patches: bool = True,
                 dry_run: bool = False):
        """
        Args:
            graph: Site state graph for locator lookup
            validate_patches: Whether to run Playwright validation
            dry_run: If True, skip actual Playwright execution
        """
        self.graph = graph
        self.validate_patches = validate_patches
        self.dry_run = dry_run
        
        # Initialize components
        self.parser = FailureParser()
        self.matcher = LocatorMatcher(graph)
        self.generator = PatchGenerator()
        self.validator = PatchValidator()
    
    def repair(self, test_code: str, error_message: str,
              stack_trace: str = "",
              target_url: Optional[str] = None,
              test_dir: Optional[str] = None) -> RepairResult:
        """
        Run the full repair pipeline.
        
        Args:
            test_code: Original failing test code
            error_message: Playwright error message
            stack_trace: Optional stack trace
            target_url: URL the test targets
            test_dir: Directory with test dependencies
            
        Returns:
            RepairResult with status, patched code, diff, and trace
        """
        import time
        start = time.monotonic()
        
        result = RepairResult(status='failed')
        
        # Step 1: Parse failure
        result.trace.append({'step': 'parse_failure', 'status': 'start'})
        
        failure = self.parser.parse(error_message, stack_trace, test_code)
        result.failure_info = failure
        
        result.trace.append({
            'step': 'parse_failure',
            'status': 'done',
            'failure_type': failure.failure_type.value,
            'locator': failure.locator_text,
            'confidence': failure.confidence,
        })
        
        # Step 2: Find graph node
        result.trace.append({'step': 'graph_lookup', 'status': 'start'})
        
        # Use URL from failure or target_url
        lookup_url = failure.page_url or target_url
        node = None
        if lookup_url:
            node_ids = self.graph.url_to_nodes.get(lookup_url, set())
            if node_ids:
                nid = next(iter(node_ids))
                node = self.graph.nodes.get(nid)
        
        result.trace.append({
            'step': 'graph_lookup',
            'status': 'done',
            'node_found': node is not None,
            'node_id': node.node_id if node else None,
            'elements_available': len(node.interactive_elements) if node else 0,
        })
        
        # Step 3: Find candidate locators
        result.trace.append({'step': 'find_candidates', 'status': 'start'})
        
        candidates = self.matcher.find_candidates(failure, node)
        candidates = self.matcher.rank_candidates(candidates, failure)
        result.candidate_locators = candidates
        
        result.trace.append({
            'step': 'find_candidates',
            'status': 'done',
            'count': len(candidates),
            'top_candidate': candidates[0].playwright_expression if candidates else None,
            'top_score': candidates[0].match_score if candidates else 0,
        })
        
        if not candidates:
            result.status = 'no_candidates'
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        
        # Step 4: Generate patch
        result.trace.append({'step': 'generate_patch', 'status': 'start'})
        
        patch = self.generator.generate(test_code, failure, candidates)
        result.patch_result = patch
        
        result.trace.append({
            'step': 'generate_patch',
            'status': 'done',
            'success': patch.success,
            'strategy': patch.strategy,
            'lines_changed': patch.lines_changed,
        })
        
        if not patch.success:
            result.status = 'failed'
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        
        result.patched_code = patch.patched_code
        result.diff = patch.diff
        result.repair_strategy = patch.strategy
        result.confidence = patch.confidence
        
        # Step 5: Validate patch
        if self.validate_patches:
            result.trace.append({'step': 'validate_patch', 'status': 'start'})
            
            if self.dry_run:
                validation = self.validator.validate_dry_run(patch.patched_code)
            else:
                validation = self.validator.validate(
                    patch.patched_code,
                    target_url=target_url,
                    test_dir=test_dir,
                )
            
            result.validation_result = validation
            
            result.trace.append({
                'step': 'validate_patch',
                'status': 'done',
                'validation_status': validation.status.value,
                'passes': validation.pass_count,
                'fails': validation.fail_count,
            })
            
            # Set final status based on validation
            if validation.status == PatchStatus.VALIDATED:
                result.status = 'validated'
            elif validation.status == PatchStatus.FLAKY:
                result.status = 'draft'  # Flaky = not trusted
            elif validation.status == PatchStatus.FAILED:
                result.status = 'failed'
            else:
                result.status = 'draft'
        else:
            result.status = 'draft'  # Patch generated but not validated
        
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result
