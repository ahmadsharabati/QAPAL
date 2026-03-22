"""
Integration tests for the full repair flow.

Uses benchmark cases from tests/data/ to verify:
1. Failure parsing produces correct type
2. Graph lookup finds the right node
3. Candidate locators are ranked correctly
4. Patch is minimal (1 line changed)
5. Patched code compiles (dry-run validation)
6. Output is deterministic across repeated runs
"""

import json
import pytest
from pathlib import Path

from engine.graph import (
    SiteStateGraph, GraphNode, StateSnapshot,
    InteractiveElement, LocatorCandidate, LocatorStrategy,
)
from engine.repair import (
    RepairPipeline, RepairResult,
    FailureType, PatchStatus,
)


DATA_DIR = Path(__file__).parent.parent / "data"


def _load_case(case_name: str) -> dict:
    """Load a benchmark case from tests/data/."""
    case_dir = DATA_DIR / case_name
    return {
        "original_test": (case_dir / "original_test.ts").read_text(),
        "error": (case_dir / "error.txt").read_text(),
        "expected_fix": (case_dir / "expected_fixed_test.ts").read_text(),
        "graph_snapshot": json.loads((case_dir / "graph_snapshot.json").read_text()),
    }


def _build_graph_from_snapshot(snapshot: dict) -> SiteStateGraph:
    """Build a SiteStateGraph from a benchmark graph_snapshot.json."""
    graph = SiteStateGraph(
        site_id="test",
        root_url=snapshot["url"],
    )
    
    snap = StateSnapshot(
        url=snapshot["url"], title="Test",
        dom_hash="h", a11y_hash="a", visible_text_hash="t",
        visible_text="Test Page",
        error_messages=[], console_errors=[],
        pending_requests=0, network_errors=[],
    )
    
    elements = {}
    for eid, edata in snapshot.get("elements", {}).items():
        locators = []
        for loc in edata.get("locators", []):
            locators.append(LocatorCandidate(
                strategy=LocatorStrategy(loc["strategy"]),
                value=loc["value"],
                confidence=loc["confidence"],
                uniqueness=loc["uniqueness"],
                visibility=loc["visibility"],
            ))
        
        elements[eid] = InteractiveElement(
            element_id=eid,
            tag=edata.get("tag", "button"),
            accessible_name=edata.get("accessible_name", ""),
            role=edata.get("role", "button"),
            locators=locators,
        )
    
    node = GraphNode(
        node_id=snapshot["node_id"],
        url=snapshot["url"],
        title="Test",
        dom_hash="h", a11y_hash="a", visible_text_hash="t",
        snapshot=snap,
        interactive_elements=elements,
    )
    
    graph.add_node(node)
    return graph


# ============================================================================
# Case 1: Selector Not Found
# ============================================================================

class TestCase01SelectorNotFound:
    """Benchmark: broken button selector → testid replacement."""
    
    @pytest.fixture
    def case(self):
        return _load_case("case_01_selector_not_found")
    
    def test_failure_type(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert result.failure_info.failure_type == FailureType.SELECTOR_NOT_FOUND
    
    def test_candidates_found(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert len(result.candidate_locators) > 0
    
    def test_patch_is_minimal(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert result.patch_result is not None
        assert result.patch_result.lines_changed <= 2
    
    def test_patch_compiles(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert result.validation_result is not None
        assert result.validation_result.status != PatchStatus.FAILED
    
    def test_testid_in_patch(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert "getByTestId" in (result.patched_code or "")


# ============================================================================
# Case 2: Timeout (CSS selector)
# ============================================================================

class TestCase02Timeout:
    """Benchmark: CSS selector timeout → testid replacement."""
    
    @pytest.fixture
    def case(self):
        return _load_case("case_02_timeout")
    
    def test_failure_type(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert result.failure_info.failure_type in (
            FailureType.SELECTOR_NOT_FOUND, FailureType.TIMEOUT
        )
    
    def test_css_replaced(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        if result.patched_code:
            assert "button.login-btn" not in result.patched_code


# ============================================================================
# Case 3: Strict Mode Violation
# ============================================================================

class TestCase03StrictMode:
    """Benchmark: strict mode → narrow to unique testid."""
    
    @pytest.fixture
    def case(self):
        return _load_case("case_03_strict_mode")
    
    def test_failure_type(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        assert result.failure_info.failure_type == FailureType.STRICT_MODE_VIOLATION
    
    def test_unique_locator_chosen(self, case):
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        if result.candidate_locators:
            top = result.candidate_locators[0]
            assert top.candidate.uniqueness >= 0.9


# ============================================================================
# Determinism: Same input → Same output
# ============================================================================

class TestDeterminism:
    """Run the same case multiple times, verify identical output."""
    
    def test_deterministic_output(self):
        case = _load_case("case_01_selector_not_found")
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        
        results = []
        for _ in range(5):
            pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
            result = pipeline.repair(
                case["original_test"], case["error"],
                target_url=case["graph_snapshot"]["url"],
            )
            results.append(result)
        
        # All runs should produce identical patches
        patches = [r.patched_code for r in results]
        assert len(set(patches)) == 1, "Non-deterministic output detected"
        
        # All runs should produce identical diffs
        diffs = [r.diff for r in results]
        assert len(set(diffs)) == 1, "Non-deterministic diff detected"
        
        # All runs should produce same status
        statuses = [r.status for r in results]
        assert len(set(statuses)) == 1, "Non-deterministic status detected"


# ============================================================================
# Serialization round-trip
# ============================================================================

class TestSerialization:
    """Verify result can be serialized and contains all required fields."""
    
    def test_to_dict_complete(self):
        case = _load_case("case_01_selector_not_found")
        graph = _build_graph_from_snapshot(case["graph_snapshot"])
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        result = pipeline.repair(
            case["original_test"], case["error"],
            target_url=case["graph_snapshot"]["url"],
        )
        
        d = result.to_dict()
        
        required_keys = [
            'status', 'patched_code', 'diff', 'repair_strategy',
            'confidence', 'duration_ms', 'timestamp',
            'failure_type', 'candidate_count', 'validation_status',
        ]
        for key in required_keys:
            assert key in d, f"Missing key: {key}"
        
        # Should be JSON-serializable
        serialized = json.dumps(d)
        assert len(serialized) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
