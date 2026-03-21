"""
Locator Matcher for QAPAL Repair Engine

Uses the site state graph to find replacement locators for broken selectors.
This is step 2 of the repair pipeline.

Matching strategy:
1. Look up the graph node for the failing page
2. Find interactive elements that match the failing locator's intent
3. Rank candidates by stability, uniqueness, and semantic similarity
4. Return ranked list of replacement locators

No AI calls. Pure graph lookup + deterministic ranking.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum

from engine.graph import (
    SiteStateGraph,
    GraphNode,
    InteractiveElement,
    LocatorCandidate,
    LocatorStrategy,
)
from engine.repair.failure_parser import ParsedFailure, FailureType


# ============================================================================
# Match Result
# ============================================================================

@dataclass
class LocatorMatch:
    """A candidate replacement locator with match reasoning."""
    candidate: LocatorCandidate
    element: InteractiveElement
    
    # Why this is a match
    match_reason: str                # 'same_role', 'same_name', 'same_testid', etc.
    match_score: float               # 0.0–1.0 (composite match quality)
    
    # Playwright expression
    playwright_expression: str       # e.g., "page.getByRole('button', { name: 'Submit' })"


# ============================================================================
# Locator Matcher
# ============================================================================

class LocatorMatcher:
    """
    Finds candidate replacement locators from the graph.
    """
    
    def __init__(self, graph: SiteStateGraph):
        self.graph = graph
    
    def find_candidates(self, failure: ParsedFailure,
                       node: Optional[GraphNode] = None,
                       max_candidates: int = 5) -> List[LocatorMatch]:
        """
        Find candidate replacement locators for a broken selector.
        
        Args:
            failure: Parsed failure information
            node: Graph node for the failing page (auto-resolved if None)
            max_candidates: Maximum candidates to return
            
        Returns:
            Ranked list of LocatorMatch objects
        """
        # Resolve node from URL if not provided
        if node is None and failure.page_url:
            node = self._resolve_node(failure.page_url)
        
        if node is None:
            return []
        
        # Get all interactive elements on this page
        elements = list(node.interactive_elements.values())
        if not elements:
            return []
        
        # Score each element against the failure
        candidates: List[LocatorMatch] = []
        
        for element in elements:
            match = self._score_element(element, failure)
            if match is not None:
                candidates.append(match)
        
        # Sort by match score (highest first)
        candidates.sort(key=lambda m: m.match_score, reverse=True)
        
        return candidates[:max_candidates]
    
    def rank_candidates(self, candidates: List[LocatorMatch],
                       failure: ParsedFailure) -> List[LocatorMatch]:
        """
        Re-rank candidates based on failure-specific context.
        
        For strict mode violations: prefer more specific locators.
        For timeouts: prefer visible, stable locators.
        For detached elements: prefer role-based locators.
        """
        if failure.failure_type == FailureType.STRICT_MODE_VIOLATION:
            # Prefer higher uniqueness to avoid multi-match
            candidates.sort(
                key=lambda m: (m.candidate.uniqueness, m.match_score),
                reverse=True
            )
        
        elif failure.failure_type == FailureType.TIMEOUT:
            # Prefer visible, stable elements
            candidates.sort(
                key=lambda m: (m.candidate.visibility, m.candidate.confidence, m.match_score),
                reverse=True
            )
        
        elif failure.failure_type == FailureType.DETACHED_ELEMENT:
            # Prefer role-based (more stable than CSS)
            def role_priority(m):
                role_strategies = {
                    LocatorStrategy.ROLE, LocatorStrategy.ROLE_CONTAINER,
                    LocatorStrategy.TESTID, LocatorStrategy.ARIA_LABEL,
                }
                is_role = 1.0 if m.candidate.strategy in role_strategies else 0.0
                return (is_role, m.match_score)
            
            candidates.sort(key=role_priority, reverse=True)
        
        return candidates
    
    def _resolve_node(self, url: str) -> Optional[GraphNode]:
        """Find graph node for a URL."""
        node_ids = self.graph.url_to_nodes.get(url, set())
        if not node_ids:
            # Try prefix matching
            for graph_url, ids in self.graph.url_to_nodes.items():
                if url.startswith(graph_url) or graph_url.startswith(url):
                    node_ids = ids
                    break
        
        if not node_ids:
            return None
        
        # Return most recently visited node
        nodes = [self.graph.nodes[nid] for nid in node_ids if nid in self.graph.nodes]
        if not nodes:
            return None
        
        return max(nodes, key=lambda n: n.visit_count)
    
    def _score_element(self, element: InteractiveElement,
                      failure: ParsedFailure) -> Optional[LocatorMatch]:
        """
        Score how well an element matches the failing locator's intent.
        Returns None if no reasonable match.
        """
        if not element.locators:
            return None
        
        # Skip hidden/disabled elements (they won't help)
        if not element.is_visible or not element.is_enabled:
            return None
        
        best_locator = element.locators[0]  # Already ranked by score
        match_reason = "fallback"
        match_score = 0.0
        
        failing_locator = failure.locator_text or ""
        failing_method = failure.locator_method or ""
        
        # Match by role similarity
        if failing_method.lower().startswith("getbyrole"):
            role_match = self._match_by_role(element, failing_locator)
            if role_match > match_score:
                match_score = role_match
                match_reason = "same_role"
        
        # Match by testid
        if failing_method.lower().startswith("getbytestid"):
            testid_match = self._match_by_testid(element, failing_locator)
            if testid_match > match_score:
                match_score = testid_match
                match_reason = "same_testid"
        
        # Match by text content
        if failing_method.lower().startswith("getbytext"):
            text_match = self._match_by_text(element, failing_locator)
            if text_match > match_score:
                match_score = text_match
                match_reason = "same_text"
        
        # Match by label
        if failing_method.lower().startswith("getbylabel"):
            label_match = self._match_by_label(element, failing_locator)
            if label_match > match_score:
                match_score = label_match
                match_reason = "same_label"
        
        # CSS selector fallback: match by tag + role
        if failing_method == "locator" and match_score < 0.3:
            css_match = self._match_by_css_intent(element, failing_locator)
            if css_match > match_score:
                match_score = css_match
                match_reason = "css_intent_match"
        
        # Minimum threshold
        if match_score < 0.2:
            return None
        
        # Build Playwright expression
        pw_expr = self._build_playwright_expression(best_locator, element)
        
        return LocatorMatch(
            candidate=best_locator,
            element=element,
            match_reason=match_reason,
            match_score=match_score,
            playwright_expression=pw_expr,
        )
    
    def _match_by_role(self, element: InteractiveElement, failing_text: str) -> float:
        """Score match based on ARIA role similarity."""
        score = 0.0
        failing_lower = failing_text.lower()
        
        # Exact role match
        if element.role and element.role.lower() in failing_lower:
            score += 0.5
        
        # Name match
        if element.accessible_name:
            name_lower = element.accessible_name.lower()
            if name_lower in failing_lower or failing_lower in name_lower:
                score += 0.4
            # Partial word match
            elif any(word in failing_lower for word in name_lower.split()):
                score += 0.2
        
        return min(score, 1.0)
    
    def _match_by_testid(self, element: InteractiveElement, failing_text: str) -> float:
        """Score match based on testid similarity."""
        for loc in element.locators:
            if loc.strategy == LocatorStrategy.TESTID:
                if loc.value.lower() == failing_text.lower():
                    return 1.0
                if failing_text.lower() in loc.value.lower():
                    return 0.7
                # Fuzzy: same prefix
                if loc.value.split('-')[0] == failing_text.split('-')[0]:
                    return 0.5
        return 0.0
    
    def _match_by_text(self, element: InteractiveElement, failing_text: str) -> float:
        """Score match based on visible text."""
        if not element.accessible_name:
            return 0.0
        
        name = element.accessible_name.lower()
        target = failing_text.lower()
        
        if name == target:
            return 1.0
        if target in name or name in target:
            return 0.7
        
        # Word overlap
        name_words = set(name.split())
        target_words = set(target.split())
        overlap = name_words & target_words
        if overlap:
            return 0.3 + 0.3 * (len(overlap) / max(len(name_words), len(target_words)))
        
        return 0.0
    
    def _match_by_label(self, element: InteractiveElement, failing_text: str) -> float:
        """Score match based on form label."""
        for loc in element.locators:
            if loc.strategy == LocatorStrategy.LABEL:
                if loc.value.lower() == failing_text.lower():
                    return 1.0
                if failing_text.lower() in loc.value.lower():
                    return 0.7
        return 0.0
    
    def _match_by_css_intent(self, element: InteractiveElement,
                             css_selector: str) -> float:
        """
        Infer intent from CSS selector and match element.
        E.g., 'button.submit' → look for button with submit-like name.
        """
        css_lower = css_selector.lower()
        score = 0.0
        
        # Tag match
        if element.tag.lower() in css_lower:
            score += 0.3
        
        # Class hints
        intent_keywords = ['submit', 'login', 'save', 'cancel', 'delete', 
                          'search', 'close', 'next', 'prev', 'back', 'continue',
                          'add', 'remove', 'edit', 'update', 'create']
        
        for keyword in intent_keywords:
            if keyword in css_lower:
                name = (element.accessible_name or "").lower()
                if keyword in name:
                    score += 0.5
                    break
        
        return min(score, 1.0)
    
    def _build_playwright_expression(self, locator: LocatorCandidate,
                                     element: InteractiveElement) -> str:
        """Build a Playwright locator expression from a candidate."""
        strategy = locator.strategy
        value = locator.value
        
        if strategy == LocatorStrategy.TESTID:
            return f"page.getByTestId('{value}')"
        
        elif strategy == LocatorStrategy.ROLE:
            # Parse "role: name" format
            if ':' in value:
                role, name = value.split(':', 1)
                return f"page.getByRole('{role.strip()}', {{ name: '{name.strip()}' }})"
            return f"page.getByRole('{value}')"
        
        elif strategy == LocatorStrategy.ROLE_CONTAINER:
            if ':' in value:
                role, name = value.split(':', 1)
                expr = f"page.getByRole('{role.strip()}', {{ name: '{name.strip()}' }})"
                if element.container_role:
                    return f"page.getByRole('{element.container_role}').{expr.replace('page.', '')}"
                return expr
            return f"page.getByRole('{value}')"
        
        elif strategy == LocatorStrategy.ARIA_LABEL:
            return f"page.getByLabel('{value}')"
        
        elif strategy == LocatorStrategy.PLACEHOLDER:
            return f"page.getByPlaceholder('{value}')"
        
        elif strategy == LocatorStrategy.TEXT:
            return f"page.getByText('{value}')"
        
        elif strategy == LocatorStrategy.LABEL:
            return f"page.getByLabel('{value}')"
        
        elif strategy == LocatorStrategy.CSS:
            return f"page.locator('{value}')"
        
        else:
            return f"page.locator('{value}')"
