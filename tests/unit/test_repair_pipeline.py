"""
Unit tests for Repair Pipeline (full flow, dry-run mode)
"""

import pytest
from engine.graph import (
    SiteStateGraph, GraphNode, StateSnapshot,
    InteractiveElement, LocatorCandidate, LocatorStrategy,
)
from engine.repair.repair_pipeline import RepairPipeline


FAILING_TEST = """import { test, expect } from '@playwright/test';

test('user can log in', async ({ page }) => {
  await page.goto('https://example.com/login');
  await page.getByRole('textbox', { name: 'Email' }).fill('user@example.com');
  await page.getByRole('textbox', { name: 'Password' }).fill('password123');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(page).toHaveURL('https://example.com/dashboard');
});
"""

TIMEOUT_ERROR = "locator.click: Timeout 30000ms exceeded.\n  waiting for getByRole('button', { name: 'Login' })"

STRICT_ERROR = "strict mode violation: getByRole('button') resolved to 3 elements"


def _build_test_graph():
    """Build a graph with a login page node."""
    graph = SiteStateGraph(site_id="example.com", root_url="https://example.com")
    
    snap = StateSnapshot(
        url="https://example.com/login", title="Login",
        dom_hash="h", a11y_hash="a", visible_text_hash="t",
        visible_text="Login Page", error_messages=[], console_errors=[],
        pending_requests=0, network_errors=[],
    )
    
    elements = {
        "btn-login": InteractiveElement(
            element_id="btn-login", tag="button",
            accessible_name="Log in", role="button",
            locators=[
                LocatorCandidate(LocatorStrategy.TESTID, "login-submit", 0.99, 1.0, 1.0),
                LocatorCandidate(LocatorStrategy.ROLE, "button: Log in", 0.9, 1.0, 1.0),
            ],
        ),
        "input-email": InteractiveElement(
            element_id="input-email", tag="input",
            accessible_name="Email address", role="textbox",
            locators=[
                LocatorCandidate(LocatorStrategy.TESTID, "email-input", 0.99, 1.0, 1.0),
                LocatorCandidate(LocatorStrategy.LABEL, "Email address", 0.85, 1.0, 1.0),
            ],
        ),
    }
    
    node = GraphNode(
        node_id="n-login", url="https://example.com/login",
        title="Login", dom_hash="h", a11y_hash="a", visible_text_hash="t",
        snapshot=snap, interactive_elements=elements,
    )
    
    graph.add_node(node)
    return graph


class TestFullPipeline:
    def test_repair_selector_not_found(self):
        graph = _build_test_graph()
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        
        result = pipeline.repair(
            test_code=FAILING_TEST,
            error_message=TIMEOUT_ERROR,
            target_url="https://example.com/login",
        )
        
        # Should produce a result
        assert result.status in ('validated', 'draft', 'failed')
        assert result.failure_info is not None
        assert result.failure_info.failure_type.value in ('selector_not_found', 'timeout')
        
        # Should find candidates
        assert len(result.candidate_locators) > 0
        
        # Should generate a patch
        if result.patch_result:
            assert result.patch_result.success
    
    def test_repair_with_no_graph_match(self):
        graph = SiteStateGraph(site_id="unknown.com", root_url="https://unknown.com")
        pipeline = RepairPipeline(graph, validate_patches=False, dry_run=True)
        
        result = pipeline.repair(
            test_code=FAILING_TEST,
            error_message=TIMEOUT_ERROR,
            target_url="https://unknown.com/login",
        )
        
        # Should gracefully handle no candidates
        assert result.status == 'no_candidates'
        assert len(result.candidate_locators) == 0
    
    def test_trace_recorded(self):
        graph = _build_test_graph()
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        
        result = pipeline.repair(
            test_code=FAILING_TEST,
            error_message=TIMEOUT_ERROR,
            target_url="https://example.com/login",
        )
        
        # Trace should record all steps
        assert len(result.trace) >= 4  # parse, lookup, candidates, patch
        
        steps = [t['step'] for t in result.trace]
        assert 'parse_failure' in steps
        assert 'graph_lookup' in steps
        assert 'find_candidates' in steps
        assert 'generate_patch' in steps
    
    def test_to_dict_serialization(self):
        graph = _build_test_graph()
        pipeline = RepairPipeline(graph, validate_patches=True, dry_run=True)
        
        result = pipeline.repair(
            test_code=FAILING_TEST,
            error_message=TIMEOUT_ERROR,
            target_url="https://example.com/login",
        )
        
        d = result.to_dict()
        assert 'status' in d
        assert 'failure_type' in d
        assert 'candidate_count' in d
        assert 'trace_steps' in d


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
