"""
QAPAL Engine — Graph-based QA system core

Main exports:
- Graph schema (nodes, edges, snapshots)
- Validation engine (truth system)
- Deduplication rules
"""

from engine.graph import (
    ActionType,
    LocatorStrategy,
    ValidationResult,
    StateSnapshot,
    LocatorCandidate,
    InteractiveElement,
    GraphNode,
    GraphEdge,
    SiteStateGraph,
    DeduplicationStrategy,
    create_snapshot_hash,
    create_a11y_hash,
    create_text_hash,
)

from engine.validation import (
    ValidationRuleType,
    ValidationIssue,
    ValidationContext,
    ValidationEngine,
    ValidationReport,
)

__all__ = [
    # Graph types
    'ActionType',
    'LocatorStrategy',
    'ValidationResult',
    'StateSnapshot',
    'LocatorCandidate',
    'InteractiveElement',
    'GraphNode',
    'GraphEdge',
    'SiteStateGraph',
    'DeduplicationStrategy',
    
    # Validation types
    'ValidationRuleType',
    'ValidationIssue',
    'ValidationContext',
    'ValidationEngine',
    'ValidationReport',
    
    # Helpers
    'create_snapshot_hash',
    'create_a11y_hash',
    'create_text_hash',
]
