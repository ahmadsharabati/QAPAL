"""
Unit tests for Graph Schema

Tests:
- Node creation and deduplication
- Edge creation and validation
- Graph operations (add_node, add_edge, find_node_by_state)
- Locator ranking
- State snapshots
"""

import pytest
from engine import (
    GraphNode,
    GraphEdge,
    SiteStateGraph,
    StateSnapshot,
    InteractiveElement,
    LocatorCandidate,
    LocatorStrategy,
    ActionType,
    ValidationResult,
    DeduplicationStrategy,
    create_snapshot_hash,
    create_a11y_hash,
    create_text_hash,
)
import uuid


class TestStateSnapshot:
    """Test state snapshot creation and hashing."""
    
    def test_snapshot_creation(self):
        snap = StateSnapshot(
            url="https://example.com",
            title="Example",
            dom_hash=create_snapshot_hash("<div>hello</div>"),
            a11y_hash=create_a11y_hash("main heading"),
            visible_text_hash=create_text_hash("Hello World"),
            visible_text="Hello World",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        assert snap.url == "https://example.com"
        assert snap.title == "Example"
        assert snap.dom_hash is not None
        assert len(snap.dom_hash) == 32  # MD5 hex
        assert snap.visible_text == "Hello World"
    
    def test_snapshot_hash_stability(self):
        """Same content produces same hash."""
        hash1 = create_snapshot_hash("<div>test</div>")
        hash2 = create_snapshot_hash("<div>test</div>")
        assert hash1 == hash2
    
    def test_snapshot_hash_sensitivity(self):
        """Different content produces different hash."""
        hash1 = create_snapshot_hash("<div>test1</div>")
        hash2 = create_snapshot_hash("<div>test2</div>")
        assert hash1 != hash2
    
    def test_text_hash_normalized(self):
        """Text hashes normalize whitespace."""
        hash1 = create_text_hash("hello  world")
        hash2 = create_text_hash("hello world")
        assert hash1 == hash2


class TestLocatorCandidate:
    """Test locator ranking."""
    
    def test_testid_highest_score(self):
        testid = LocatorCandidate(
            strategy=LocatorStrategy.TESTID,
            value="btn-submit",
            confidence=0.99,
            uniqueness=1.0,
            visibility=1.0,
        )
        
        css = LocatorCandidate(
            strategy=LocatorStrategy.CSS,
            value="div.container > button:nth-child(2)",
            confidence=0.5,
            uniqueness=0.7,
            visibility=1.0,
        )
        
        assert testid.score > css.score
    
    def test_role_higher_than_text(self):
        role = LocatorCandidate(
            strategy=LocatorStrategy.ROLE,
            value="button: Submit",
            confidence=0.9,
            uniqueness=1.0,
            visibility=1.0,
        )
        
        text = LocatorCandidate(
            strategy=LocatorStrategy.TEXT,
            value="Submit",
            confidence=0.8,
            uniqueness=0.6,
            visibility=1.0,
        )
        
        assert role.score > text.score


class TestGraphNode:
    """Test graph node creation."""
    
    def test_node_creation(self):
        snap = StateSnapshot(
            url="https://example.com",
            title="Home",
            dom_hash="abc123",
            a11y_hash="def456",
            visible_text_hash="ghi789",
            visible_text="Home Page",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        node = GraphNode(
            node_id="node-1",
            url="https://example.com",
            title="Home",
            dom_hash="abc123",
            a11y_hash="def456",
            visible_text_hash="ghi789",
            snapshot=snap,
        )
        
        assert node.node_id == "node-1"
        assert node.url == "https://example.com"
        assert node.visit_count == 0
    
    def test_node_hash_signature(self):
        snap = StateSnapshot(
            url="https://example.com",
            title="Test",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Test",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        node = GraphNode(
            node_id="n1",
            url="https://example.com",
            title="Test",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            snapshot=snap,
        )
        
        # Hash signature should be stable
        sig1 = node.hash_signature
        sig2 = node.hash_signature
        assert sig1 == sig2


class TestGraphEdge:
    """Test graph edge (action) creation."""
    
    def test_edge_creation(self):
        edge = GraphEdge(
            edge_id="edge-1",
            from_node="node-1",
            to_node="node-2",
            action_type=ActionType.CLICK,
            target_element_id="btn-submit",
        )
        
        assert edge.edge_id == "edge-1"
        assert edge.from_node == "node-1"
        assert edge.to_node == "node-2"
        assert edge.action_type == ActionType.CLICK
        assert edge.validation == ValidationResult.UNKNOWN
    
    def test_edge_validation_tracking(self):
        edge = GraphEdge(
            edge_id="e1",
            from_node="n1",
            to_node="n2",
            action_type=ActionType.TYPE,
            action_value="test@example.com",
        )
        
        # Initially unknown
        assert edge.validation == ValidationResult.UNKNOWN
        
        # Update validation
        edge.validation = ValidationResult.PASS
        assert edge.validation == ValidationResult.PASS


class TestSiteStateGraph:
    """Test graph operations."""
    
    def test_add_node(self):
        graph = SiteStateGraph(
            site_id="example.com",
            root_url="https://example.com",
        )
        
        snap = StateSnapshot(
            url="https://example.com",
            title="Home",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            visible_text="Home",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        node = GraphNode(
            node_id="n1",
            url="https://example.com",
            title="Home",
            dom_hash="h1",
            a11y_hash="a1",
            visible_text_hash="t1",
            snapshot=snap,
        )
        
        node_id = graph.add_node(node)
        assert node_id == "n1"
        assert "n1" in graph.nodes
        assert "https://example.com" in graph.url_to_nodes
    
    def test_add_edge(self):
        graph = SiteStateGraph(
            site_id="example.com",
            root_url="https://example.com",
        )
        
        edge = GraphEdge(
            edge_id="e1",
            from_node="n1",
            to_node="n2",
            action_type=ActionType.CLICK,
        )
        
        edge_id = graph.add_edge(edge)
        assert edge_id == "e1"
        assert "e1" in graph.edges
    
    def test_get_outgoing_edges(self):
        graph = SiteStateGraph(
            site_id="example.com",
            root_url="https://example.com",
        )
        
        e1 = GraphEdge("e1", "n1", "n2", ActionType.CLICK)
        e2 = GraphEdge("e2", "n1", "n3", ActionType.SUBMIT)
        e3 = GraphEdge("e3", "n2", "n4", ActionType.NAVIGATE)
        
        graph.add_edge(e1)
        graph.add_edge(e2)
        graph.add_edge(e3)
        
        outgoing = graph.get_outgoing_edges("n1")
        assert len(outgoing) == 2
        assert all(e.from_node == "n1" for e in outgoing)
    
    def test_find_node_by_state(self):
        graph = SiteStateGraph(
            site_id="example.com",
            root_url="https://example.com",
        )
        
        snap = StateSnapshot(
            url="https://example.com",
            title="Test",
            dom_hash="dom1",
            a11y_hash="a11y1",
            visible_text_hash="text1",
            visible_text="Test",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        node = GraphNode(
            node_id="n1",
            url="https://example.com",
            title="Test",
            dom_hash="dom1",
            a11y_hash="a11y1",
            visible_text_hash="text1",
            snapshot=snap,
        )
        
        graph.add_node(node)
        
        # Create identical state
        snap2 = StateSnapshot(
            url="https://example.com",
            title="Test",
            dom_hash="dom1",
            a11y_hash="a11y1",
            visible_text_hash="text1",
            visible_text="Test",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        found = graph.find_node_by_state(snap2)
        assert found is not None
        assert found.node_id == "n1"


class TestDeduplication:
    """Test state deduplication rules."""
    
    def test_should_merge_identical_states(self):
        state1 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        state2 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        should_merge = DeduplicationStrategy.should_merge(state1, state2, "page")
        assert should_merge is True
    
    def test_should_not_merge_different_states(self):
        state1 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Content 1",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        state2 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="xyz",
            a11y_hash="uvw",
            visible_text_hash="rst",
            visible_text="Content 2",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        should_merge = DeduplicationStrategy.should_merge(state1, state2, "page")
        assert should_merge is False
    
    def test_never_merge_error_states(self):
        state1 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        state2 = StateSnapshot(
            url="https://example.com",
            title="Page",
            dom_hash="abc",
            a11y_hash="def",
            visible_text_hash="ghi",
            visible_text="Content",
            error_messages=[],
            console_errors=[],
            pending_requests=0,
            network_errors=[],
        )
        
        # Error states should never merge
        should_merge = DeduplicationStrategy.should_merge(state1, state2, "error")
        assert should_merge is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
