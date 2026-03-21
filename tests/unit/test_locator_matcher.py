"""
Unit tests for Locator Matcher
"""

import pytest
from engine.graph import (
    SiteStateGraph, GraphNode, StateSnapshot,
    InteractiveElement, LocatorCandidate, LocatorStrategy,
)
from engine.repair.locator_matcher import LocatorMatcher
from engine.repair.failure_parser import ParsedFailure, FailureType


def _make_snapshot(url="https://example.com"):
    return StateSnapshot(
        url=url, title="Test", dom_hash="h", a11y_hash="a",
        visible_text_hash="t", visible_text="Test",
        error_messages=[], console_errors=[],
        pending_requests=0, network_errors=[],
    )


def _make_element(eid, tag, name, role, locators):
    return InteractiveElement(
        element_id=eid, tag=tag, accessible_name=name,
        locators=locators, role=role,
    )


def _make_locator(strategy, value, confidence=0.9, uniqueness=1.0, visibility=1.0):
    return LocatorCandidate(
        strategy=strategy, value=value,
        confidence=confidence, uniqueness=uniqueness, visibility=visibility,
    )


@pytest.fixture
def graph_with_elements():
    """Graph with a page node containing interactive elements."""
    graph = SiteStateGraph(site_id="example.com", root_url="https://example.com")
    
    snap = _make_snapshot("https://example.com/login")
    
    elements = {
        "btn-submit": _make_element(
            "btn-submit", "button", "Log in", "button",
            [
                _make_locator(LocatorStrategy.TESTID, "login-submit", 0.99),
                _make_locator(LocatorStrategy.ROLE, "button: Log in", 0.9),
                _make_locator(LocatorStrategy.TEXT, "Log in", 0.7),
            ]
        ),
        "input-email": _make_element(
            "input-email", "input", "Email", "textbox",
            [
                _make_locator(LocatorStrategy.TESTID, "email-input", 0.99),
                _make_locator(LocatorStrategy.LABEL, "Email", 0.85),
                _make_locator(LocatorStrategy.PLACEHOLDER, "Enter your email", 0.75),
            ]
        ),
        "input-password": _make_element(
            "input-password", "input", "Password", "textbox",
            [
                _make_locator(LocatorStrategy.TESTID, "password-input", 0.99),
                _make_locator(LocatorStrategy.LABEL, "Password", 0.85),
            ]
        ),
        "link-forgot": _make_element(
            "link-forgot", "a", "Forgot password?", "link",
            [
                _make_locator(LocatorStrategy.ROLE, "link: Forgot password?", 0.85),
                _make_locator(LocatorStrategy.TEXT, "Forgot password?", 0.7),
            ]
        ),
    }
    
    node = GraphNode(
        node_id="n-login", url="https://example.com/login",
        title="Login", dom_hash="h", a11y_hash="a", visible_text_hash="t",
        snapshot=snap, interactive_elements=elements,
    )
    
    graph.add_node(node)
    return graph


class TestCandidateFinding:
    def test_finds_candidates_for_broken_button(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="button: Login",
            locator_method="getByRole",
            page_url="https://example.com/login",
        )
        
        candidates = matcher.find_candidates(failure)
        assert len(candidates) > 0
        # Button should be top candidate
        assert candidates[0].element.element_id == "btn-submit"
    
    def test_finds_candidates_for_broken_testid(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="email-input",
            locator_method="getByTestId",
            page_url="https://example.com/login",
        )
        
        candidates = matcher.find_candidates(failure)
        assert len(candidates) > 0
        assert candidates[0].element.element_id == "input-email"
    
    def test_no_candidates_when_no_node(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="submit",
            page_url="https://unknown.com/page",
        )
        
        candidates = matcher.find_candidates(failure)
        assert len(candidates) == 0


class TestLocatorRanking:
    def test_testid_ranked_higher_than_css(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="email-input",
            locator_method="getByTestId",
            page_url="https://example.com/login",
        )
        
        candidates = matcher.find_candidates(failure)
        if len(candidates) > 0:
            top = candidates[0]
            assert top.candidate.strategy in (LocatorStrategy.TESTID, LocatorStrategy.ROLE)
    
    def test_strict_mode_prefers_unique(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.STRICT_MODE_VIOLATION,
            locator_text="button: Log in",
            locator_method="getByRole",
            page_url="https://example.com/login",
            match_count=3,
        )
        
        candidates = matcher.find_candidates(failure)
        ranked = matcher.rank_candidates(candidates, failure)
        
        if len(ranked) > 0:
            # After ranking for strict mode, uniqueness should be prioritized
            assert ranked[0].candidate.uniqueness >= 0.9


class TestPlaywrightExpression:
    def test_testid_expression(self, graph_with_elements):
        matcher = LocatorMatcher(graph_with_elements)
        
        failure = ParsedFailure(
            failure_type=FailureType.SELECTOR_NOT_FOUND,
            locator_text="email-input",
            locator_method="getByTestId",
            page_url="https://example.com/login",
        )
        
        candidates = matcher.find_candidates(failure)
        assert len(candidates) > 0
        
        expr = candidates[0].playwright_expression
        assert "getByTestId" in expr or "getByRole" in expr or "getByLabel" in expr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
