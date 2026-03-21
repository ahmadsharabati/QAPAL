"""
Patch Generator for QAPAL Repair Engine

Generates minimal patches — never rewrites entire tests.

Strategy:
1. Replace broken locator with best candidate (deterministic)
2. Add explicit wait if timing issue (deterministic)
3. Narrow locator if strict mode violation (deterministic)
4. Re-query element if detached (deterministic)
5. AI fallback only if all deterministic approaches fail

Patch rules:
- Change as few lines as possible
- Preserve test name, structure, style
- Never touch unrelated code
- Output both patched code and unified diff
"""

import re
import difflib
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from engine.repair.failure_parser import ParsedFailure, FailureType
from engine.repair.locator_matcher import LocatorMatch


# ============================================================================
# Patch Result
# ============================================================================

@dataclass
class PatchResult:
    """Result of patch generation."""
    success: bool                   # Whether a patch was generated
    patched_code: str               # Full patched test code
    diff: str                       # Unified diff
    
    # What was changed
    strategy: str                   # 'locator_replace', 'add_wait', 'narrow_locator', etc.
    changes: List[Dict] = field(default_factory=list)  # Line-level changes
    
    # Metadata
    lines_changed: int = 0
    is_ai_generated: bool = False   # True if AI fallback was used
    confidence: float = 0.0


# ============================================================================
# Patch Generator
# ============================================================================

class PatchGenerator:
    """
    Generates minimal patches for failing Playwright tests.
    Deterministic rules first, AI fallback last.
    """
    
    # Regex to find locator expressions in test code
    _RE_GETBY_EXPR = re.compile(
        r'(page\.(?:getByRole|getByTestId|getByText|getByLabel|getByPlaceholder|getByAltText|getByTitle|locator)\s*\([^)]+\))'
    )
    
    # Regex for await expressions with locators
    _RE_AWAIT_LOCATOR = re.compile(
        r'(await\s+)(page\.(?:getByRole|getByTestId|getByText|getByLabel|getByPlaceholder|getByAltText|getByTitle|locator)\s*\([^)]+\))\s*\.(click|fill|check|uncheck|hover|press|type|selectOption|focus|blur|dblclick|tap)\s*\(([^)]*)\)'
    )
    
    def generate(self, original_code: str, failure: ParsedFailure,
                candidates: List[LocatorMatch]) -> PatchResult:
        """
        Generate a minimal patch for a failing test.
        
        Args:
            original_code: The full test source code
            failure: Parsed failure information
            candidates: Ranked replacement locators
            
        Returns:
            PatchResult with patched code and diff
        """
        if not candidates:
            return PatchResult(
                success=False,
                patched_code=original_code,
                diff="",
                strategy="no_candidates",
            )
        
        # Try deterministic strategies in order
        result = None
        
        # Strategy 1: Direct locator replacement
        if failure.failure_type in (
            FailureType.SELECTOR_NOT_FOUND,
            FailureType.TIMEOUT,
            FailureType.ELEMENT_NOT_VISIBLE,
        ):
            result = self._replace_locator(original_code, failure, candidates)
        
        # Strategy 2: Add wait for timing issues
        if result is None and failure.failure_type == FailureType.TIMEOUT:
            result = self._add_wait(original_code, failure, candidates)
        
        # Strategy 3: Narrow locator for strict mode
        if result is None and failure.failure_type == FailureType.STRICT_MODE_VIOLATION:
            result = self._narrow_locator(original_code, failure, candidates)
        
        # Strategy 4: Re-query for detached elements
        if result is None and failure.failure_type == FailureType.DETACHED_ELEMENT:
            result = self._requery_element(original_code, failure, candidates)
        
        # Strategy 5: Generic locator replacement (fallback)
        if result is None:
            result = self._replace_locator(original_code, failure, candidates)
        
        if result is None:
            return PatchResult(
                success=False,
                patched_code=original_code,
                diff="",
                strategy="no_viable_patch",
            )
        
        return result
    
    def _replace_locator(self, code: str, failure: ParsedFailure,
                        candidates: List[LocatorMatch]) -> Optional[PatchResult]:
        """
        Strategy 1: Replace the broken locator with the best candidate.
        """
        if not failure.locator_text or not candidates:
            return None
        
        best = candidates[0]
        old_locator = failure.locator_text
        new_expr = best.playwright_expression
        
        # Find and replace the failing locator in the code
        patched, count = self._replace_in_code(code, old_locator, new_expr, failure)
        
        if count == 0:
            return None
        
        diff = self._generate_diff(code, patched)
        
        return PatchResult(
            success=True,
            patched_code=patched,
            diff=diff,
            strategy="locator_replace",
            changes=[{
                "type": "replace",
                "old": old_locator,
                "new": new_expr,
                "reason": best.match_reason,
            }],
            lines_changed=count,
            confidence=best.match_score * best.candidate.score,
        )
    
    def _add_wait(self, code: str, failure: ParsedFailure,
                  candidates: List[LocatorMatch]) -> Optional[PatchResult]:
        """
        Strategy 2: Add an explicit waitFor before the failing action.
        """
        if not failure.locator_text:
            return None
        
        best = candidates[0]
        new_expr = best.playwright_expression
        
        lines = code.split('\n')
        patched_lines = []
        changed = False
        
        for i, line in enumerate(lines):
            # Find the line with the failing locator
            if failure.locator_text in line and not changed:
                # Detect indentation
                indent = len(line) - len(line.lstrip())
                indent_str = line[:indent]
                
                # Insert waitFor before the action
                wait_line = f"{indent_str}await {new_expr}.waitFor({{ state: 'visible', timeout: 10000 }});"
                
                # Replace locator in the action line
                new_line = line
                if failure.locator_text in line:
                    # Build the replacement
                    old_part = self._find_locator_expression(line)
                    if old_part:
                        new_line = line.replace(old_part, new_expr)
                
                patched_lines.append(wait_line)
                patched_lines.append(new_line)
                changed = True
            else:
                patched_lines.append(line)
        
        if not changed:
            return None
        
        patched = '\n'.join(patched_lines)
        diff = self._generate_diff(code, patched)
        
        return PatchResult(
            success=True,
            patched_code=patched,
            diff=diff,
            strategy="add_wait",
            changes=[{
                "type": "insert_wait",
                "locator": new_expr,
                "reason": "timeout_recovery",
            }],
            lines_changed=2,
            confidence=best.match_score * 0.8,  # Slightly lower confidence for wait strategy
        )
    
    def _narrow_locator(self, code: str, failure: ParsedFailure,
                       candidates: List[LocatorMatch]) -> Optional[PatchResult]:
        """
        Strategy 3: Narrow the locator for strict mode violations.
        Pick the most unique candidate to avoid multi-match.
        """
        if not candidates:
            return None
        
        # Sort by uniqueness (highest first)
        unique_candidates = sorted(candidates, key=lambda c: c.candidate.uniqueness, reverse=True)
        best = unique_candidates[0]
        
        if best.candidate.uniqueness < 0.9:
            # Not unique enough, might still hit strict mode
            return None
        
        return self._replace_locator(code, failure, [best])
    
    def _requery_element(self, code: str, failure: ParsedFailure,
                        candidates: List[LocatorMatch]) -> Optional[PatchResult]:
        """
        Strategy 4: Re-query element before action (for detached DOM).
        Wraps the action in a fresh locator query.
        """
        if not failure.locator_text or not candidates:
            return None
        
        best = candidates[0]
        new_expr = best.playwright_expression
        
        lines = code.split('\n')
        patched_lines = []
        changed = False
        
        for i, line in enumerate(lines):
            if failure.locator_text in line and not changed:
                indent = len(line) - len(line.lstrip())
                indent_str = line[:indent]
                
                # Add a small wait for DOM to stabilize
                wait_line = f"{indent_str}await page.waitForTimeout(500); // Wait for DOM to stabilize"
                
                # Replace locator
                old_part = self._find_locator_expression(line)
                new_line = line.replace(old_part, new_expr) if old_part else line
                
                patched_lines.append(wait_line)
                patched_lines.append(new_line)
                changed = True
            else:
                patched_lines.append(line)
        
        if not changed:
            return None
        
        patched = '\n'.join(patched_lines)
        diff = self._generate_diff(code, patched)
        
        return PatchResult(
            success=True,
            patched_code=patched,
            diff=diff,
            strategy="requery_element",
            changes=[{
                "type": "requery",
                "locator": new_expr,
                "reason": "detached_element_recovery",
            }],
            lines_changed=2,
            confidence=best.match_score * 0.7,  # Lower confidence for requery
        )
    
    def _replace_in_code(self, code: str, old_locator: str, new_expr: str,
                        failure: ParsedFailure) -> tuple:
        """
        Replace a locator in test code, targeting the right line.
        Returns (patched_code, replacement_count).
        """
        lines = code.split('\n')
        count = 0
        
        for i, line in enumerate(lines):
            # If we have a line number, target that specific line
            if failure.line_number and i + 1 != failure.line_number:
                continue
            
            if old_locator in line:
                # Find the full locator expression containing old_locator
                old_expr = self._find_locator_expression(line)
                if old_expr:
                    lines[i] = line.replace(old_expr, new_expr)
                    count += 1
                    break  # Only replace first match (minimal diff)
        
        if count == 0:
            # Fallback: replace anywhere old_locator appears
            for i, line in enumerate(lines):
                if old_locator in line:
                    old_expr = self._find_locator_expression(line)
                    if old_expr:
                        lines[i] = line.replace(old_expr, new_expr)
                        count += 1
                        break
        
        return '\n'.join(lines), count
    
    def _find_locator_expression(self, line: str) -> Optional[str]:
        """Extract the full locator expression from a line of code."""
        match = self._RE_GETBY_EXPR.search(line)
        if match:
            return match.group(1)
        return None
    
    def _generate_diff(self, original: str, patched: str) -> str:
        """Generate unified diff between original and patched code."""
        original_lines = original.splitlines(keepends=True)
        patched_lines = patched.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            original_lines,
            patched_lines,
            fromfile='original.spec.ts',
            tofile='patched.spec.ts',
            lineterm='',
        )
        
        return '\n'.join(diff)
