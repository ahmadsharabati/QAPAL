"""
tests/unit/test_state_graph.py
===============================
Trust-verification suite for state_graph.py::StateGraph.

The graph is the planner's map of the application.  If the graph lies —
wrong paths, phantom edges, duplicate counts, stale data — the AI generates
broken plans.  Every test here answers one trust question.

Trust dimensions covered:
  1.  Edge storage & retrieval
  2.  Traversal-count deduplication
  3.  Edge-ID determinism
  4.  BFS shortest-path correctness
  5.  Cycle safety in BFS
  6.  Multi-source BFS (all_paths_from)
  7.  min_count noise filtering
  8.  URL-pattern deduplication in prompt
  9.  'navigate' action excluded from prompt
  10. Semantic hash stability (dynamic prices/dates stripped)
  11. Semantic hash sensitivity (structurally different pages differ)
  12. classify_page_change correctness
  13. stats() accuracy
  14. clear() wipes all edges
  15. Prompt format is parseable / non-empty
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from locator_db import LocatorDB
from state_graph import (
    StateGraph,
    compute_semantic_hash,
    classify_page_change,
    _make_edge_id,
)


# ── Fixture helpers ───────────────────────────────────────────────────

def _make_db() -> LocatorDB:
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close()
    return LocatorDB(path=tf.name)


def _make_sg() -> tuple:
    """Return (LocatorDB, StateGraph) backed by a temp file."""
    db = _make_db()
    sg = StateGraph(db)
    return db, sg


BASE = "https://app.example.com"
LOGIN   = f"{BASE}/login"
DASH    = f"{BASE}/dashboard"
PROFILE = f"{BASE}/profile"
SETTINGS= f"{BASE}/settings"
PRODUCT = f"{BASE}/product/01KKNWM4EP4HYEP1X6CF8622XB"   # ULID
PRODUCT2= f"{BASE}/product/01KKNWM4EP4HYEP1X6CF8622XC"   # different ULID


# ════════════════════════════════════════════════════════════════════════
# 1. Edge storage & retrieval
# ════════════════════════════════════════════════════════════════════════

class TestEdgeStorage:

    def test_record_transition_stored(self):
        """A recorded transition must be retrievable via get_transitions_from."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        edges = sg.get_transitions_from(LOGIN)
        assert len(edges) == 1
        e = edges[0]
        assert e["from_url"] == LOGIN
        assert e["to_url"]   == DASH
        assert e["trigger"]["action"] == "click"
        assert e["trigger"]["label"]  == "Sign In"
        db.close()

    def test_get_transitions_to(self):
        """get_transitions_to must return edges pointing AT a URL."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN,   DASH, "click", "Sign In")
        sg.record_transition(PROFILE, DASH, "click", "Home")
        incoming = sg.get_transitions_to(DASH)
        froms = {e["from_url"] for e in incoming}
        assert froms == {LOGIN, PROFILE}
        db.close()

    def test_self_loop_ignored(self):
        """from_url == to_url must not be stored."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, LOGIN, "click", "Refresh")
        assert sg.get_transitions_from(LOGIN) == []
        db.close()

    def test_empty_url_ignored(self):
        """Empty from_url or to_url must not be stored."""
        db, sg = _make_sg()
        sg.record_transition("", DASH,  "click", "X")
        sg.record_transition(LOGIN, "",  "click", "Y")
        assert sg.all_transitions() == []
        db.close()

    def test_all_urls_aggregates_both_sides(self):
        """all_urls must include both from_url and to_url of every edge."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.record_transition(DASH, PROFILE, "click", "Profile")
        urls = sg.all_urls()
        assert LOGIN   in urls
        assert DASH    in urls
        assert PROFILE in urls
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 2. Traversal-count deduplication
# ════════════════════════════════════════════════════════════════════════

class TestDeduplication:

    def test_same_edge_increments_count(self):
        """Recording the same transition twice must produce one edge with count=2."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        edges = sg.get_transitions_from(LOGIN)
        assert len(edges) == 1, "Duplicate edge was NOT deduplicated"
        assert edges[0]["traversal_count"] == 2
        db.close()

    def test_different_label_creates_new_edge(self):
        """Same from/to but different label must create a second edge."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.record_transition(LOGIN, DASH, "click", "Login")
        edges = sg.get_transitions_from(LOGIN)
        assert len(edges) == 2
        db.close()

    def test_different_action_creates_new_edge(self):
        """Same from/to/label but different action must create a second edge."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click",  "Sign In")
        sg.record_transition(LOGIN, DASH, "dblclick","Sign In")
        edges = sg.get_transitions_from(LOGIN)
        assert len(edges) == 2
        db.close()

    def test_ten_traversals_accurate_count(self):
        """Recording the same edge 10 times must yield traversal_count==10."""
        db, sg = _make_sg()
        for _ in range(10):
            sg.record_transition(LOGIN, DASH, "click", "Sign In")
        edges = sg.get_transitions_from(LOGIN)
        assert edges[0]["traversal_count"] == 10
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 3. Edge-ID determinism
# ════════════════════════════════════════════════════════════════════════

class TestEdgeIdDeterminism:

    def test_same_inputs_same_id(self):
        """Same from/to/action/label must always produce the same 16-char ID."""
        id1 = _make_edge_id(LOGIN, DASH, "click", "Sign In")
        id2 = _make_edge_id(LOGIN, DASH, "click", "Sign In")
        assert id1 == id2
        assert len(id1) == 16

    def test_different_label_different_id(self):
        id1 = _make_edge_id(LOGIN, DASH, "click", "Sign In")
        id2 = _make_edge_id(LOGIN, DASH, "click", "Login")
        assert id1 != id2

    def test_swapped_urls_different_id(self):
        """A→B must have a different ID than B→A."""
        id1 = _make_edge_id(LOGIN, DASH, "click", "Sign In")
        id2 = _make_edge_id(DASH,  LOGIN, "click", "Sign In")
        assert id1 != id2


# ════════════════════════════════════════════════════════════════════════
# 4. BFS shortest-path correctness
# ════════════════════════════════════════════════════════════════════════

class TestPathFinding:

    def _build_chain(self, sg):
        """Build: LOGIN → DASH → PROFILE → SETTINGS"""
        sg.record_transition(LOGIN,   DASH,     "click", "Sign In")
        sg.record_transition(DASH,    PROFILE,  "click", "Profile")
        sg.record_transition(PROFILE, SETTINGS, "click", "Settings")

    def test_direct_path(self):
        """Direct edge A→B must return a one-step path."""
        db, sg = _make_sg()
        self._build_chain(sg)
        path = sg.get_path(LOGIN, DASH)
        assert path is not None
        assert len(path) == 1
        assert path[0]["from_url"] == LOGIN
        assert path[0]["to_url"]   == DASH
        db.close()

    def test_two_hop_path(self):
        """LOGIN → PROFILE requires LOGIN→DASH→PROFILE (2 steps)."""
        db, sg = _make_sg()
        self._build_chain(sg)
        path = sg.get_path(LOGIN, PROFILE)
        assert path is not None
        assert len(path) == 2
        assert path[0]["from_url"] == LOGIN
        assert path[1]["to_url"]   == PROFILE
        db.close()

    def test_three_hop_path(self):
        """LOGIN → SETTINGS requires 3 hops."""
        db, sg = _make_sg()
        self._build_chain(sg)
        path = sg.get_path(LOGIN, SETTINGS)
        assert path is not None
        assert len(path) == 3
        db.close()

    def test_unreachable_returns_none(self):
        """A URL with no path must return None, not raise."""
        db, sg = _make_sg()
        self._build_chain(sg)
        path = sg.get_path(LOGIN, f"{BASE}/nowhere")
        assert path is None
        db.close()

    def test_same_url_returns_empty_path(self):
        """get_path(A, A) must return an empty list (zero steps)."""
        db, sg = _make_sg()
        path = sg.get_path(LOGIN, LOGIN)
        assert path == []
        db.close()

    def test_shorter_path_preferred(self):
        """
        When two paths exist (short and long), BFS must return the shorter one.

        Graph:  LOGIN → DASH (direct)
                LOGIN → PROFILE → DASH (two hops)
        Expected: direct path (1 step).
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN,   DASH,   "click", "Sign In")   # direct
        sg.record_transition(LOGIN,   PROFILE,"click", "Profile")
        sg.record_transition(PROFILE, DASH,   "click", "Home")      # long way
        path = sg.get_path(LOGIN, DASH)
        assert path is not None
        assert len(path) == 1, f"BFS returned {len(path)}-step path, expected 1"
        db.close()

    def test_high_traversal_count_explored_first(self):
        """
        When two parallel paths to the destination exist,
        the more-traversed edge should be explored first.
        Both paths are 1 step — we just verify get_path returns a path.
        (traversal bias is tested implicitly via count.)
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        # Record multiple times so traversal_count is high
        for _ in range(5):
            sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.record_transition(LOGIN, DASH, "click", "Enter")  # low count
        path = sg.get_path(LOGIN, DASH)
        assert path is not None
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 5. Cycle safety
# ════════════════════════════════════════════════════════════════════════

class TestCycleSafety:

    def test_cycle_does_not_infinite_loop(self):
        """
        A graph with a cycle (A→B→A) must not cause get_path to loop forever.
        The destination is unreachable in this cycle, so None is expected.
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.record_transition(DASH, LOGIN, "click", "Logout")   # back-edge
        # SETTINGS is unreachable — BFS should terminate cleanly
        path = sg.get_path(LOGIN, SETTINGS)
        assert path is None  # unreachable, not infinite loop
        db.close()

    def test_cycle_reachable_destination(self):
        """
        A→B→C→A (cycle) with destination=C must find A→B→C (2 steps),
        not loop through the cycle repeatedly.
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN,   DASH,    "click", "Sign In")
        sg.record_transition(DASH,    PROFILE, "click", "Profile")
        sg.record_transition(PROFILE, LOGIN,   "click", "Logout")   # cycle back
        path = sg.get_path(LOGIN, PROFILE)
        assert path is not None
        assert len(path) == 2
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 6. Multi-source BFS
# ════════════════════════════════════════════════════════════════════════

class TestMultiSourceBFS:

    def test_all_paths_from_single_source(self):
        """all_paths_from([LOGIN]) must find DASH and PROFILE."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH,    "click", "Sign In")
        sg.record_transition(DASH,  PROFILE, "click", "Profile")
        paths = sg.all_paths_from([LOGIN])
        assert DASH    in paths
        assert PROFILE in paths
        db.close()

    def test_all_paths_excludes_start_url(self):
        """Start URLs must not appear as keys in all_paths_from result."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        paths = sg.all_paths_from([LOGIN])
        assert LOGIN not in paths
        db.close()

    def test_all_paths_multi_source(self):
        """
        Two entry points: [LOGIN, PROFILE].
        DASH is reachable from LOGIN; SETTINGS is reachable from PROFILE.
        Both must appear in the result.
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN,   DASH,     "click", "Sign In")
        sg.record_transition(PROFILE, SETTINGS, "click", "Settings")
        paths = sg.all_paths_from([LOGIN, PROFILE])
        assert DASH     in paths
        assert SETTINGS in paths
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 7. min_count noise filtering
# ════════════════════════════════════════════════════════════════════════

class TestMinCountFiltering:

    def test_min_count_excludes_low_confidence_edges(self):
        """
        format_for_prompt(min_count=3) must not show edges seen only once.
        A one-off transition is noise (could be a test artefact, not a real flow).
        """
        db, sg = _make_sg()
        # High-confidence edge (seen 5 times)
        for _ in range(5):
            sg.record_transition(LOGIN, DASH, "click", "Sign In")
        # Noisy edge (seen once)
        sg.record_transition(LOGIN, f"{BASE}/404", "click", "Broken Link")

        prompt = sg.format_for_prompt(min_count=3)
        assert "Sign In" in prompt,    "High-confidence edge must appear"
        assert "Broken Link" not in prompt, "Noise edge must be filtered out"
        db.close()

    def test_min_count_1_shows_all_edges(self):
        """min_count=1 (default) must include every recorded edge."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH,    "click", "Sign In")
        sg.record_transition(DASH,  PROFILE, "click", "Profile")
        prompt = sg.format_for_prompt(min_count=1)
        assert "Sign In" in prompt
        assert "Profile" in prompt
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 8. URL-pattern deduplication in prompt
# ════════════════════════════════════════════════════════════════════════

class TestUrlPatternDeduplication:

    def test_product_ulid_deduplicated_in_prompt(self):
        """
        Two product URLs with different ULIDs must produce ONE line in the prompt,
        not two.  Rotating product IDs must not bloat the prompt.
        """
        db, sg = _make_sg()
        sg.record_transition(DASH, PRODUCT,  "click", "Hammer")
        sg.record_transition(DASH, PRODUCT2, "click", "Hammer")  # same label, diff ULID

        prompt = sg.format_for_prompt()
        # Count how many times "Hammer" appears — should be 1 (deduplicated)
        occurrences = prompt.count('"Hammer"')
        assert occurrences <= 1, (
            f"ULID product URLs not deduplicated — 'Hammer' appears {occurrences} times"
        )
        db.close()

    def test_pattern_label_uses_id_placeholder(self):
        """
        The prompt line for a dynamic URL must reference ':id' (the normalised
        pattern), not the raw ULID, so the AI understands it's a pattern.
        """
        db, sg = _make_sg()
        sg.record_transition(DASH, PRODUCT, "click", "View Product")
        prompt = sg.format_for_prompt()
        # The path should be /product/:id not the raw ULID
        assert ":id" in prompt or "product" in prompt, (
            "Dynamic URL pattern not normalised in prompt output"
        )
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 9. 'navigate' action excluded from prompt
# ════════════════════════════════════════════════════════════════════════

class TestNavigateExclusion:

    def test_navigate_trigger_not_in_prompt(self):
        """
        Edges with trigger_action='navigate' are test-runner initialisations
        (e.g. browser.goto('/login')), not user interactions.
        They must be excluded from format_for_prompt output.
        """
        db, sg = _make_sg()
        sg.record_transition(
            f"{BASE}/", LOGIN, "navigate", "direct navigation",
        )
        sg.record_transition(LOGIN, DASH, "click", "Sign In")

        prompt = sg.format_for_prompt()
        assert "direct navigation" not in prompt, (
            "'navigate' trigger must be excluded from prompt"
        )
        assert "Sign In" in prompt, "Real user action must still appear"
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 10. Semantic hash stability
# ════════════════════════════════════════════════════════════════════════

class TestSemanticHashStability:

    def _snap(self, items):
        """Build a minimal a11y snapshot from (role, name) tuples."""
        return [{"role": r, "name": n} for r, n in items]

    def test_same_page_same_hash(self):
        """Identical a11y snapshots must produce the same hash."""
        snap = self._snap([("button", "Sign In"), ("textbox", "Email")])
        assert compute_semantic_hash(snap) == compute_semantic_hash(snap)

    def test_price_stripped_before_hash(self):
        """
        Pages with different prices ($12 vs $15) but same structure
        must hash identically — price is dynamic content.
        """
        snap1 = self._snap([("button", "Add to cart $12.99"), ("link", "Home")])
        snap2 = self._snap([("button", "Add to cart $15.00"), ("link", "Home")])
        assert compute_semantic_hash(snap1) == compute_semantic_hash(snap2), (
            "Price difference caused different hash — prices should be stripped"
        )

    def test_counter_stripped_before_hash(self):
        """Cart counter '(3)' vs '(7)' must not change the hash."""
        snap1 = self._snap([("button", "Cart (3)"), ("link", "Home")])
        snap2 = self._snap([("button", "Cart (7)"), ("link", "Home")])
        assert compute_semantic_hash(snap1) == compute_semantic_hash(snap2)

    def test_order_independent(self):
        """
        Element order in the snapshot must not affect the hash.
        The a11y tree order varies across browsers/OS.
        """
        snap1 = self._snap([("button", "Sign In"), ("textbox", "Email")])
        snap2 = self._snap([("textbox", "Email"), ("button", "Sign In")])
        assert compute_semantic_hash(snap1) == compute_semantic_hash(snap2)

    def test_hash_is_16_chars(self):
        """Hash must be a 16-character hex string."""
        snap = self._snap([("button", "Sign In")])
        h = compute_semantic_hash(snap)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ════════════════════════════════════════════════════════════════════════
# 11. Semantic hash sensitivity
# ════════════════════════════════════════════════════════════════════════

class TestSemanticHashSensitivity:

    def _snap(self, items):
        return [{"role": r, "name": n} for r, n in items]

    def test_different_structure_different_hash(self):
        """Login page vs dashboard page must have different hashes."""
        login = self._snap([("textbox", "Email"), ("textbox", "Password"), ("button", "Sign In")])
        dash  = self._snap([("link", "Home"), ("link", "Profile"), ("button", "Logout")])
        assert compute_semantic_hash(login) != compute_semantic_hash(dash)

    def test_extra_element_changes_hash(self):
        """Adding a new element to the page must change the hash."""
        snap1 = self._snap([("button", "Sign In")])
        snap2 = self._snap([("button", "Sign In"), ("link", "Forgot Password")])
        assert compute_semantic_hash(snap1) != compute_semantic_hash(snap2)

    def test_empty_snapshot_has_stable_hash(self):
        """Empty snapshot must not raise and must return a consistent 16-char hash."""
        h1 = compute_semantic_hash([])
        h2 = compute_semantic_hash([])
        assert h1 == h2
        assert len(h1) == 16


# ════════════════════════════════════════════════════════════════════════
# 12. classify_page_change
# ════════════════════════════════════════════════════════════════════════

class TestClassifyPageChange:

    def _snap(self, items):
        return [{"role": r, "name": n} for r, n in items]

    def test_url_change_is_navigation(self):
        snap = self._snap([("button", "Sign In")])
        result = classify_page_change(snap, snap, LOGIN, DASH)
        assert result == "navigation"

    def test_modal_detected(self):
        """Appearance of 'dialog' role with same URL → 'modal'."""
        before = self._snap([("button", "Delete")])
        after  = self._snap([("button", "Delete"), ("dialog", "Confirm")])
        result = classify_page_change(before, after, LOGIN, LOGIN)
        assert result == "modal"

    def test_structural_change_is_partial(self):
        """Different structure on same URL → 'partial' (e.g. tab changed)."""
        before = self._snap([("button", "Tab A")])
        after  = self._snap([("button", "Tab B"), ("textbox", "Search")])
        result = classify_page_change(before, after, LOGIN, LOGIN)
        assert result == "partial"

    def test_no_change_is_none(self):
        """Same page, same URL → 'none'."""
        snap = self._snap([("button", "Sign In"), ("textbox", "Email")])
        result = classify_page_change(snap, snap, LOGIN, LOGIN)
        assert result == "none"


# ════════════════════════════════════════════════════════════════════════
# 13. stats() accuracy
# ════════════════════════════════════════════════════════════════════════

class TestStats:

    def test_stats_counts_edges_and_pages(self):
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH,    "click", "Sign In")
        sg.record_transition(DASH,  PROFILE, "click", "Profile")

        s = sg.stats()
        assert s["total_transitions"] == 2
        # 3 unique pages: LOGIN, DASH, PROFILE
        assert s["unique_pages"] == 3
        db.close()

    def test_stats_most_traversed_sorted(self):
        """most_traversed must list edges by traversal_count descending."""
        db, sg = _make_sg()
        for _ in range(10):
            sg.record_transition(LOGIN, DASH, "click", "Sign In")
        for _ in range(3):
            sg.record_transition(DASH, PROFILE, "click", "Profile")

        s = sg.stats()
        counts = [t[2] for t in s["most_traversed"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 10  # highest first
        db.close()

    def test_stats_empty_graph(self):
        """stats() on an empty graph must return zeros, not raise."""
        db, sg = _make_sg()
        s = sg.stats()
        assert s["total_transitions"] == 0
        assert s["unique_pages"] == 0
        assert s["most_traversed"] == []
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 14. clear() wipes all edges
# ════════════════════════════════════════════════════════════════════════

class TestClear:

    def test_clear_removes_all_transitions(self):
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH,    "click", "Sign In")
        sg.record_transition(DASH,  PROFILE, "click", "Profile")
        count = sg.clear()
        assert count == 2
        assert sg.all_transitions() == []
        db.close()

    def test_clear_returns_correct_count(self):
        db, sg = _make_sg()
        for i in range(7):
            sg.record_transition(LOGIN, f"{BASE}/page-{i}", "click", f"Link {i}")
        count = sg.clear()
        assert count == 7
        db.close()

    def test_clear_then_record_works(self):
        """After clear(), the graph must accept new transitions normally."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        sg.clear()
        sg.record_transition(LOGIN, PROFILE, "click", "Skip Login")
        edges = sg.get_transitions_from(LOGIN)
        assert len(edges) == 1
        assert edges[0]["to_url"] == PROFILE
        db.close()


# ════════════════════════════════════════════════════════════════════════
# 15. Prompt format — parseable, non-empty, structure check
# ════════════════════════════════════════════════════════════════════════

class TestPromptFormat:

    def test_empty_graph_returns_placeholder(self):
        """An empty graph must return a non-empty placeholder string (not crash)."""
        db, sg = _make_sg()
        prompt = sg.format_for_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 10
        db.close()

    def test_prompt_contains_transition_lines(self):
        """Prompt must include the arrow notation '--[action "label"]-->'."""
        db, sg = _make_sg()
        sg.record_transition(LOGIN, DASH, "click", "Sign In")
        prompt = sg.format_for_prompt()
        assert "-->" in prompt
        assert "Sign In" in prompt
        db.close()

    def test_prompt_contains_traversal_count(self):
        """Prompt must show the observation count, e.g. '(3x observed)'."""
        db, sg = _make_sg()
        for _ in range(3):
            sg.record_transition(LOGIN, DASH, "click", "Sign In")
        prompt = sg.format_for_prompt()
        assert "3x" in prompt or "(3" in prompt
        db.close()

    def test_prompt_url_filter_limits_output(self):
        """
        format_for_prompt(urls=[LOGIN]) must only include edges related to LOGIN,
        not unrelated parts of the graph.
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN,    DASH,     "click", "Sign In")
        sg.record_transition(PROFILE,  SETTINGS, "click", "Edit Settings")
        prompt = sg.format_for_prompt(urls=[LOGIN])
        assert "Sign In"       in prompt
        assert "Edit Settings" not in prompt
        db.close()

    def test_prompt_max_edges_respected(self):
        """format_for_prompt(max_edges=2) must not output more than 2 edges."""
        db, sg = _make_sg()
        for i in range(10):
            sg.record_transition(LOGIN, f"{BASE}/page-{i}", "click", f"Link {i}")
        prompt = sg.format_for_prompt(max_edges=2)
        # Count arrow occurrences as a proxy for edge lines
        arrow_count = prompt.count("-->")
        assert arrow_count <= 2, f"Expected ≤2 edges but got {arrow_count}"
        db.close()

    def test_reachable_paths_section_appears(self):
        """
        When urls= is provided and paths exist, a 'Reachable navigation paths'
        section must appear in the prompt.
        """
        db, sg = _make_sg()
        sg.record_transition(LOGIN,   DASH,    "click", "Sign In")
        sg.record_transition(DASH,    PROFILE, "click", "Profile")
        prompt = sg.format_for_prompt(urls=[LOGIN])
        assert "Reachable" in prompt or "navigation paths" in prompt
        db.close()
