"""
ranker.py — Selector stability scoring and ranking engine.

Scores individual selectors on a 0.0–1.0 scale based on:
  - Strategy type (testid > role > text > css)
  - Uniqueness (count == 1 is ideal)
  - Visibility (visible + in viewport)
  - Interactability (enabled + not obscured)
  - Historical reliability (hit/miss ratio from DB)

Pure module — no browser or Playwright dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Strategy weights — higher = more stable selector type
# ---------------------------------------------------------------------------

STRATEGY_SCORES: dict[str, float] = {
    "testid":         1.00,
    "id":             0.90,
    "role+container": 0.85,
    "role":           0.80,
    "role_container":  0.85,   # alias used in QAPAL plans
    "aria-label":     0.75,
    "aria_label":     0.75,   # underscore alias
    "label":          0.70,
    "placeholder":    0.65,
    "testid_prefix":  0.60,
    "text":           0.50,
    "alt_text":       0.45,
    "css":            0.30,
    "xpath":          0.20,
}

# Dimension weights for combined score
WEIGHTS = {
    "strategy":    0.35,
    "uniqueness":  0.30,
    "visibility":  0.15,
    "interaction": 0.10,
    "history":     0.10,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SelectorCandidate:
    """A single selector option with its score."""
    strategy: str
    value: Any                  # str or dict (e.g. {"role": "button", "name": "Submit"})
    unique: Optional[bool]      # True if count == 1, None if not probed
    score: float = 0.0
    expression: str = ""        # Playwright code string, e.g. "page.get_by_test_id('email')"

    def __repr__(self) -> str:
        return f"SelectorCandidate({self.strategy}={self.value!r}, score={self.score:.2f})"


class SelectorGrade(str, Enum):
    """Human-readable grade for selector confidence."""
    A = "A"   # > 0.80 — rock solid
    B = "B"   # > 0.60 — reliable
    C = "C"   # > 0.40 — acceptable
    D = "D"   # > 0.20 — fragile
    F = "F"   # <= 0.20 — broken or unusable


# ---------------------------------------------------------------------------
# Individual dimension scorers
# ---------------------------------------------------------------------------

def score_strategy(strategy: str) -> float:
    """Score based on selector strategy type. Unknown strategies get 0.1."""
    return STRATEGY_SCORES.get(strategy, 0.10)


def score_uniqueness(count: int) -> float:
    """
    Score based on how many elements match the selector.
    - count == 1: perfect (1.0)
    - count == 0: not found (0.0)
    - count > 1: degrades gracefully
    """
    if count == 1:
        return 1.0
    if count == 0:
        return 0.0
    # Multiple matches — still usable (first-of) but less reliable
    return max(0.1, 1.0 / count)


def score_visibility(visible: bool, in_viewport: bool) -> float:
    """Score based on element visibility state."""
    if visible and in_viewport:
        return 1.0
    if visible:
        return 0.7   # visible but off-screen (needs scroll)
    return 0.0        # hidden element


def score_interaction(enabled: bool, attached: bool = True) -> float:
    """Score based on element interactability."""
    if enabled and attached:
        return 1.0
    if attached:
        return 0.5    # attached but disabled
    return 0.0         # detached from DOM


def score_history(hit_count: int, miss_count: int) -> float:
    """
    Score based on historical reliability from locator DB.
    A selector that's been found 50 times and missed 0 is very reliable.
    """
    total = hit_count + miss_count
    if total == 0:
        return 0.5     # no history — neutral
    return hit_count / total


# ---------------------------------------------------------------------------
# Combined scorer
# ---------------------------------------------------------------------------

def score_selector(
    strategy: str,
    count: int = 1,
    visible: bool = True,
    in_viewport: bool = True,
    enabled: bool = True,
    attached: bool = True,
    hit_count: int = 0,
    miss_count: int = 0,
) -> float:
    """
    Compute a combined confidence score (0.0–1.0) for a selector.

    Uses weighted combination of:
      - Strategy type (35%)
      - Uniqueness (30%)
      - Visibility (15%)
      - Interactability (10%)
      - Historical reliability (10%)
    """
    s = (
        WEIGHTS["strategy"]    * score_strategy(strategy)
        + WEIGHTS["uniqueness"]  * score_uniqueness(count)
        + WEIGHTS["visibility"]  * score_visibility(visible, in_viewport)
        + WEIGHTS["interaction"] * score_interaction(enabled, attached)
        + WEIGHTS["history"]     * score_history(hit_count, miss_count)
    )
    return round(min(1.0, max(0.0, s)), 4)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_candidates(candidates: List[SelectorCandidate]) -> List[SelectorCandidate]:
    """Sort candidates by score descending. Returns a new list."""
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def grade(score: float) -> SelectorGrade:
    """Convert a numeric score to a letter grade."""
    if score > 0.80:
        return SelectorGrade.A
    if score > 0.60:
        return SelectorGrade.B
    if score > 0.40:
        return SelectorGrade.C
    if score > 0.20:
        return SelectorGrade.D
    return SelectorGrade.F


def format_grade(score: float) -> str:
    """Human-readable grade string, e.g. '[A — 0.95]'."""
    g = grade(score)
    return f"[{g.value} \u2014 {score:.2f}]"
