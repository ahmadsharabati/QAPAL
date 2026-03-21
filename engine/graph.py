"""
Graph Schema for QAPAL

The site state graph is the product's memory. It represents:
- Nodes: discrete, stable states of a web application
- Edges: user actions that transition between states
- Snapshots: DOM, screenshot, and accessibility summaries at each state
- Locators: stable selectors for interactive elements

Core principles:
1. Determinism: states are identified by structural hashes, not timestamps
2. Deduplication: nearly identical states merge into one node
3. Stability: locators are ranked by confidence and changeability
4. Validation: edges track whether actions succeeded
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Set, Tuple
from datetime import datetime
from enum import Enum
import hashlib
import json


# ============================================================================
# Enums
# ============================================================================

class ActionType(Enum):
    """Supported user actions."""
    CLICK = "click"
    TYPE = "type"
    SUBMIT = "submit"
    NAVIGATE = "navigate"
    WAIT = "wait"
    HOVER = "hover"
    SCROLL = "scroll"
    CHECK = "check"
    UNCHECK = "uncheck"


class LocatorStrategy(Enum):
    """Selector strategies, ranked by stability."""
    TESTID = "testid"              # data-testid (most stable)
    ROLE = "role"                  # aria-role + accessible name
    ROLE_CONTAINER = "role_container"  # role + name + parent
    ARIA_LABEL = "aria_label"
    PLACEHOLDER = "placeholder"
    TESTID_PREFIX = "testid_prefix"
    TEXT = "text"
    LABEL = "label"
    CSS = "css"                    # CSS selector (least stable)


class ValidationResult(Enum):
    """Outcome of action validation."""
    PASS = "pass"                  # Action succeeded as expected
    FAIL = "fail"                  # Action failed (broken)
    NO_OP = "no_op"               # Action did nothing (harmless but useless)
    PARTIAL = "partial"            # Action partially succeeded
    UNKNOWN = "unknown"            # Inconclusive


# ============================================================================
# Snapshot Data Structures
# ============================================================================

@dataclass
class StateSnapshot:
    """
    Compact representation of a page state.
    Used for state deduplication and validation.
    """
    url: str
    title: str
    
    # Structural hashes (used for deduplication)
    dom_hash: str                   # Hash of normalized DOM
    a11y_hash: str                  # Hash of accessibility tree
    visible_text_hash: str          # Hash of visible text content
    
    # UI state signals
    visible_text: str               # Concatenated visible text (up to 5KB)
    error_messages: List[str]       # Detected error text
    console_errors: List[str]       # JS console errors
    
    # Network state
    pending_requests: int           # Active network requests
    network_errors: List[str]       # Failed network calls
    
    # Screenshot reference
    screenshot_key: Optional[str] = None  # R2 or local path
    
    # Metadata
    captured_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    page_load_time_ms: Optional[int] = None


@dataclass
class LocatorCandidate:
    """
    A ranked selector for finding an interactive element.
    """
    strategy: LocatorStrategy
    value: str                      # The actual selector
    confidence: float               # 0.0–1.0 (higher = more stable)
    uniqueness: float               # 0.0–1.0 (1.0 = only match on page)
    visibility: float               # 0.0–1.0 (1.0 = always visible)
    
    # Score breakdown for debugging
    score: float = field(init=False)
    
    def __post_init__(self):
        """Calculate composite score."""
        # Weighted average: strategy weight + uniqueness + visibility + confidence
        strategy_weight = {
            LocatorStrategy.TESTID: 0.95,
            LocatorStrategy.ROLE: 0.85,
            LocatorStrategy.ROLE_CONTAINER: 0.80,
            LocatorStrategy.ARIA_LABEL: 0.75,
            LocatorStrategy.PLACEHOLDER: 0.70,
            LocatorStrategy.TESTID_PREFIX: 0.65,
            LocatorStrategy.TEXT: 0.55,
            LocatorStrategy.LABEL: 0.60,
            LocatorStrategy.CSS: 0.30,
        }
        
        w = strategy_weight.get(self.strategy, 0.5)
        self.score = (
            w * 0.35 +
            self.confidence * 0.30 +
            self.uniqueness * 0.20 +
            self.visibility * 0.15
        )


@dataclass
class InteractiveElement:
    """
    An interactive element discovered on a page.
    Identified by a ranked locator chain.
    """
    element_id: str                 # Stable ID within page
    tag: str                        # button, a, input, etc.
    accessible_name: str            # aria-label, text, etc.
    locators: List[LocatorCandidate]  # Ranked selectors
    
    # Element state
    is_visible: bool = True
    is_enabled: bool = True
    is_clickable: bool = True
    
    # Role and context
    role: str = ""                  # button, link, textbox, etc.
    container_role: Optional[str] = None  # Parent role (e.g., modal)
    
    # For text-based elements
    placeholder: Optional[str] = None
    value: Optional[str] = None


# ============================================================================
# Graph Node
# ============================================================================

@dataclass
class GraphNode:
    """
    A stable state of the web application.
    Identified by structural hashes, not URL alone.
    """
    node_id: str                    # Unique node ID (UUID or hash-based)
    url: str
    title: str
    
    # State identification (for deduplication)
    dom_hash: str
    a11y_hash: str
    visible_text_hash: str
    
    # Snapshot data
    snapshot: StateSnapshot
    
    # Elements on this page
    interactive_elements: Dict[str, InteractiveElement] = field(default_factory=dict)
    
    # State classification
    state_type: str = "page"        # 'page', 'modal', 'error', 'loading', 'form'
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    visit_count: int = 0            # How many times we've seen this state
    is_entry_point: bool = False    # Starting page
    is_dead_end: bool = False       # No outgoing edges
    
    @property
    def hash_signature(self) -> str:
        """Composite hash for state identification."""
        return hashlib.md5(
            f"{self.dom_hash}:{self.a11y_hash}:{self.visible_text_hash}".encode()
        ).hexdigest()


# ============================================================================
# Graph Edge (Action)
# ============================================================================

@dataclass
class GraphEdge:
    """
    A user action that transitions from one state to another.
    """
    edge_id: str                    # Unique edge ID
    from_node: str                  # Node ID
    to_node: str                    # Node ID
    
    # Action details
    action_type: ActionType
    target_element_id: Optional[str] = None  # Element that was interacted with
    target_locator: Optional[LocatorCandidate] = None
    action_value: Optional[str] = None  # For type, submit, navigate
    
    # Snapshots for validation
    before_snapshot: Optional[StateSnapshot] = None
    after_snapshot: Optional[StateSnapshot] = None
    
    # Validation result
    validation: ValidationResult = ValidationResult.UNKNOWN
    validation_details: Dict[str, Any] = field(default_factory=dict)
    
    # Execution metrics
    duration_ms: int = 0
    attempts: int = 0
    failures: List[str] = field(default_factory=list)
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_executed_at: Optional[str] = None
    confidence: float = 0.5         # How confident we are this edge is correct


# ============================================================================
# Site State Graph
# ============================================================================

@dataclass
class SiteStateGraph:
    """
    The complete graph representation of a site.
    """
    site_id: str                    # Site identifier (URL base)
    root_url: str
    
    # Nodes and edges
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: Dict[str, GraphEdge] = field(default_factory=dict)
    
    # Indices for fast lookup
    url_to_nodes: Dict[str, Set[str]] = field(default_factory=dict)  # URL → node IDs
    hash_to_node: Dict[str, str] = field(default_factory=dict)  # Hash signature → node ID
    
    # Entry point
    entry_node_id: Optional[str] = None
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def add_node(self, node: GraphNode) -> str:
        """Add a node to the graph, handling deduplication."""
        node_id = node.node_id
        self.nodes[node_id] = node
        
        # Index by URL
        if node.url not in self.url_to_nodes:
            self.url_to_nodes[node.url] = set()
        self.url_to_nodes[node.url].add(node_id)
        
        # Index by hash signature
        self.hash_to_node[node.hash_signature] = node_id
        
        return node_id
    
    def add_edge(self, edge: GraphEdge) -> str:
        """Add an edge (action) to the graph."""
        edge_id = edge.edge_id
        self.edges[edge_id] = edge
        return edge_id
    
    def find_node_by_state(self, state: StateSnapshot) -> Optional[GraphNode]:
        """
        Find an existing node that matches this state.
        Uses hash matching for deduplication.
        """
        hash_sig = hashlib.md5(
            f"{state.dom_hash}:{state.a11y_hash}:{state.visible_text_hash}".encode()
        ).hexdigest()
        
        node_id = self.hash_to_node.get(hash_sig)
        if node_id:
            return self.nodes[node_id]
        return None
    
    def get_outgoing_edges(self, node_id: str) -> List[GraphEdge]:
        """Get all edges from a node."""
        return [e for e in self.edges.values() if e.from_node == node_id]
    
    def get_incoming_edges(self, node_id: str) -> List[GraphEdge]:
        """Get all edges to a node."""
        return [e for e in self.edges.values() if e.to_node == node_id]


# ============================================================================
# Deduplication Rules
# ============================================================================

class DeduplicationStrategy:
    """
    Rules for merging nearly identical states into a single node.
    """
    
    # Two states are the same if:
    DOM_HASH_WEIGHT = 0.5           # Structural content matches
    A11Y_HASH_WEIGHT = 0.3          # Accessibility tree matches
    TEXT_HASH_WEIGHT = 0.2          # Visible text matches
    
    # Similarity threshold (0.0–1.0)
    MERGE_THRESHOLD = 0.85
    
    # Never merge these state types (always create new node)
    IMMERGEABLE_TYPES = {'error', 'modal', 'form_error'}
    
    # Split states when these change meaningfully
    SPLIT_SIGNALS = {
        'auth_status',              # Logged in vs out
        'modal_open',               # Modal appears
        'error_present',            # Error message shown
        'loading_state',            # Spinner/skeleton visible
    }
    
    @staticmethod
    def should_merge(state1: StateSnapshot, state2: StateSnapshot, node_type: str) -> bool:
        """Determine if two states should be merged into one node."""
        if node_type in DeduplicationStrategy.IMMERGEABLE_TYPES:
            return False
        
        # Calculate similarity
        similarity = (
            DeduplicationStrategy.DOM_HASH_WEIGHT * (1.0 if state1.dom_hash == state2.dom_hash else 0.0) +
            DeduplicationStrategy.A11Y_HASH_WEIGHT * (1.0 if state1.a11y_hash == state2.a11y_hash else 0.0) +
            DeduplicationStrategy.TEXT_HASH_WEIGHT * (1.0 if state1.visible_text_hash == state2.visible_text_hash else 0.0)
        )
        
        return similarity >= DeduplicationStrategy.MERGE_THRESHOLD


# ============================================================================
# Example usage and helpers
# ============================================================================

def create_snapshot_hash(dom_content: str) -> str:
    """Create a stable hash of DOM content."""
    normalized = dom_content.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()


def create_a11y_hash(a11y_tree: str) -> str:
    """Create a stable hash of accessibility tree."""
    normalized = a11y_tree.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()


def create_text_hash(visible_text: str) -> str:
    """Create a stable hash of visible text."""
    # Normalize: lowercase, remove extra whitespace
    normalized = ' '.join(visible_text.split()).lower()
    return hashlib.md5(normalized.encode()).hexdigest()


if __name__ == "__main__":
    # Quick test
    snap = StateSnapshot(
        url="https://example.com",
        title="Example",
        dom_hash=create_snapshot_hash("<div>hello</div>"),
        a11y_hash=create_a11y_hash("main [heading]"),
        visible_text_hash=create_text_hash("Hello World"),
        visible_text="Hello World",
        error_messages=[],
        console_errors=[],
        pending_requests=0,
        network_errors=[],
    )
    
    print(f"Snapshot hash signature: {snap}")
    print(f"DOM hash: {snap.dom_hash}")
    print(f"A11y hash: {snap.a11y_hash}")
    print(f"Text hash: {snap.visible_text_hash}")
