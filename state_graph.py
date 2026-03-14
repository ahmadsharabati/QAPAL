"""
state_graph.py — QAPal State Graph Engine
==========================================
Records page transitions observed during test execution, building a directed
graph of how pages connect.

Every time a UI action causes a URL change (click, fill+submit, navigate, etc.)
the executor calls StateGraph.record_transition() to persist that edge.
Over multiple test runs the graph grows into a map of the application's flow:

    /login → /dashboard → /users → /users/create

The planner and generator query this graph via format_for_prompt() to inject
a compact navigation context block into their AI prompts, giving the model
real route information rather than forcing it to guess multi-step paths.

Storage: 'transitions' table in the existing LocatorDB TinyDB instance —
no separate file, no new connection, all writes serialised by LocatorDB's lock.

Thread-safe: all writes go through the LocatorDB RLock.
"""

import hashlib
import json
import re
from collections import deque, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from locator_db import _url_to_pattern


# ── Helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_label(url: str) -> str:
    """Return just the path portion of a URL for compact display."""
    try:
        p = urlparse(url)
        return p.path or "/"
    except Exception:
        return url


def _make_edge_id(from_url: str, to_url: str, action: str, label: str) -> str:
    """Deterministic 16-char hex ID for a transition edge."""
    key = f"{from_url}|{to_url}|{action}|{label}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Semantic hashing ──────────────────────────────────────────────────

_DYNAMIC_NAME_RE = re.compile(
    r"\$[\d,]+\.?\d*"       # prices: $1.99
    r"|\(\d+\)"             # counters: (3)
    r"|\b\d{1,2}:\d{2}\b"  # timestamps: 12:30
    r"|\b\d{4}-\d{2}-\d{2}\b"  # ISO dates
)

_SKIP_ROLES = frozenset({"none", "presentation", "generic", "group", "region", "separator"})


def _normalize_name_for_hash(name: str) -> str:
    """Strip dynamic values from element names before hashing."""
    return _DYNAMIC_NAME_RE.sub("", name).strip().lower()


def compute_semantic_hash(a11y_snapshot: list) -> str:
    """
    Compute a stable 16-char hash from an a11y node list.

    Sorts (role, normalized_name) pairs so hash is order-independent.
    Excludes structural/presentational roles and strips dynamic values
    (prices, counts, timestamps) so the same page hashes identically
    regardless of transient dynamic content.
    """
    pairs = sorted(
        (_normalize_name_for_hash(node.get("role", "")),
         _normalize_name_for_hash(node.get("name", "")))
        for node in a11y_snapshot
        if node.get("role", "") not in _SKIP_ROLES
    )
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()[:16]


def classify_page_change(
    before_snap: list,
    after_snap:  list,
    before_url:  str,
    after_url:   str,
) -> str:
    """
    Classify what happened after a UI interaction.

    Returns one of:
      'navigation' — URL changed
      'modal'      — a dialog/alertdialog ARIA role appeared (URL unchanged)
      'partial'    — semantic structure changed, no modal, URL unchanged
      'none'       — nothing observable changed
    """
    if after_url != before_url:
        return "navigation"

    before_roles = {n.get("role", "") for n in before_snap}
    after_roles  = {n.get("role", "") for n in after_snap}
    new_roles    = after_roles - before_roles
    if "dialog" in new_roles or "alertdialog" in new_roles:
        return "modal"

    if compute_semantic_hash(before_snap) != compute_semantic_hash(after_snap):
        return "partial"

    return "none"


# ════════════════════════════════════════════════════════════════════════
# StateGraph
# ════════════════════════════════════════════════════════════════════════

class StateGraph:
    """
    Directed graph of page transitions observed during test execution.

    Edges represent:  from_url --[action "label"]--> to_url

    Same logical transition (same two pages + same action + same element)
    is deduplicated and its traversal_count incremented.  Different buttons
    that lead to different destinations produce separate edges.

    Usage:
        db = LocatorDB()
        sg = StateGraph(db)

        # record a transition (called by executor)
        sg.record_transition("/login", "/dashboard", "click", "Sign In")

        # query (called by planner/generator)
        print(sg.format_for_prompt(urls=["https://app.com/login"]))

        # path-find
        path = sg.get_path("https://app.com/login", "https://app.com/users/create")
    """

    _MAX_SESSIONS = 10  # cap session_id list per edge

    def __init__(self, db):
        """
        db: LocatorDB instance.  We use db._db, db._lock, db._Q directly
        to stay within the same TinyDB connection and lock.
        """
        self._db     = db
        self._table  = db._db.table("transitions")
        self._states = db._db.table("page_states")  # per-page semantic state nodes
        self._Q      = db._Q

    # ── State nodes ────────────────────────────────────────────────────

    def enrich_and_add(
        self,
        url:              str,
        a11y_snapshot:    list,
        screenshot_path:  str = "",
        semantic_context: str = "",
    ) -> str:
        """
        Upsert a page state node derived from an a11y snapshot.
        Returns the state_id (semantic hash).

        Call this from the crawler after each page crawl so the states table
        accumulates a map of known semantic states across runs.
        """
        state_id    = compute_semantic_hash(a11y_snapshot)
        url_pattern = _url_to_pattern(url)

        with self._db._lock:
            existing = self._states.get(self._Q.state_id == state_id)
            if existing:
                self._states.update(
                    {"visit_count": existing.get("visit_count", 1) + 1,
                     "last_seen": _now()},
                    self._Q.state_id == state_id,
                )
            else:
                self._states.insert({
                    "state_id":         state_id,
                    "url":              url,
                    "url_pattern":      url_pattern,
                    "screenshot_path":  screenshot_path,
                    "semantic_context": semantic_context,
                    "first_seen":       _now(),
                    "last_seen":        _now(),
                    "visit_count":      1,
                })
        return state_id

    def has_state(self, state_id: str) -> bool:
        """Return True if this semantic state has been visited before."""
        with self._db._lock:
            return bool(self._states.get(self._Q.state_id == state_id))

    def all_states(self) -> list:
        """Return all known page state nodes."""
        with self._db._lock:
            return self._states.all()

    # ── Write ─────────────────────────────────────────────────────────

    def record_transition(
        self,
        from_url:         str,
        to_url:           str,
        trigger_action:   str,
        trigger_label:    str,
        trigger_selector: Optional[dict] = None,
        session_id:       str = "",
    ) -> None:
        """
        Upsert a transition edge.

        If the same edge already exists (same from/to/action/label), its
        traversal_count is incremented and last_seen updated.
        Otherwise a new record is inserted.
        """
        if not from_url or not to_url or from_url == to_url:
            return

        eid = _make_edge_id(from_url, to_url, trigger_action, trigger_label)

        with self._db._lock:
            existing = self._table.get(self._Q.id == eid)
            if existing:
                sessions = existing.get("session_ids", [])
                if session_id and session_id not in sessions:
                    sessions = (sessions + [session_id])[-self._MAX_SESSIONS:]
                self._table.update(
                    {
                        "traversal_count": existing["traversal_count"] + 1,
                        "last_seen":       _now(),
                        "session_ids":     sessions,
                    },
                    self._Q.id == eid,
                )
            else:
                self._table.insert({
                    "id":              eid,
                    "from_url":        from_url,
                    "to_url":          to_url,
                    "from_pattern":    _url_to_pattern(from_url),
                    "to_pattern":      _url_to_pattern(to_url),
                    "trigger": {
                        "action":   trigger_action,
                        "label":    trigger_label,
                        "selector": trigger_selector,
                    },
                    "traversal_count": 1,
                    "first_seen":      _now(),
                    "last_seen":       _now(),
                    "session_ids":     [session_id] if session_id else [],
                })

    # ── Read ──────────────────────────────────────────────────────────

    def all_transitions(self) -> List[dict]:
        """Return all recorded transition edges."""
        with self._db._lock:
            return self._table.all()

    def get_transitions_from(self, url: str) -> List[dict]:
        """All outgoing edges from a URL."""
        with self._db._lock:
            return self._table.search(self._Q.from_url == url)

    def get_transitions_to(self, url: str) -> List[dict]:
        """All incoming edges leading to a URL."""
        with self._db._lock:
            return self._table.search(self._Q.to_url == url)

    def all_urls(self) -> List[str]:
        """Sorted list of all unique URLs that appear in the graph."""
        urls: Set[str] = set()
        for t in self.all_transitions():
            urls.add(t["from_url"])
            urls.add(t["to_url"])
        return sorted(urls)

    # ── Path-finding (BFS) ────────────────────────────────────────────

    def get_path(
        self,
        from_url:  str,
        to_url:    str,
        max_depth: int = 8,
    ) -> Optional[List[dict]]:
        """
        BFS shortest path from from_url to to_url.

        Returns an ordered list of transition dicts forming the path,
        or None if the destination is unreachable within max_depth hops.
        Higher-traversal-count edges are explored first (reliability bias).
        """
        if from_url == to_url:
            return []

        transitions = self.all_transitions()
        graph: Dict[str, List[dict]] = defaultdict(list)
        for t in transitions:
            graph[t["from_url"]].append(t)

        queue   = deque([(from_url, [])])
        visited: Set[str] = {from_url}

        while queue:
            current, path = queue.popleft()
            # Prefer more-traversed edges (more reliable in practice)
            edges = sorted(
                graph.get(current, []),
                key=lambda e: e["traversal_count"],
                reverse=True,
            )
            for edge in edges:
                nxt = edge["to_url"]
                if nxt == to_url:
                    return path + [edge]
                if len(path) >= max_depth:
                    continue
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [edge]))

        return None  # unreachable

    def all_paths_from(
        self,
        start_urls: List[str],
        max_depth:  int = 6,
    ) -> Dict[str, List[dict]]:
        """
        Multi-source BFS from all start_urls.

        Returns {to_url: [transition_edge, ...]} for every reachable page.
        Start URLs themselves are excluded from the result.
        """
        transitions = self.all_transitions()
        graph: Dict[str, List[dict]] = defaultdict(list)
        for t in transitions:
            graph[t["from_url"]].append(t)

        visited: Dict[str, List[dict]] = {url: [] for url in start_urls}
        queue: deque = deque()
        for url in start_urls:
            queue.append((url, []))

        while queue:
            current, path = queue.popleft()
            edges = sorted(
                graph.get(current, []),
                key=lambda e: e["traversal_count"],
                reverse=True,
            )
            for edge in edges:
                nxt = edge["to_url"]
                if nxt not in visited:
                    visited[nxt] = path + [edge]
                    if len(path) >= max_depth:
                        continue
                    queue.append((nxt, path + [edge]))

        return {url: path for url, path in visited.items() if url not in start_urls}

    # ── Prompt formatting ─────────────────────────────────────────────

    def format_for_prompt(
        self,
        urls:      Optional[List[str]] = None,
        max_edges: int = 40,
        min_count: int = 1,
    ) -> str:
        """
        Render the graph as a compact text block for AI prompt injection.

        If urls is provided, only edges where from_url or to_url is in the
        set are shown (keeps the block small for single-page tests).
        If the graph is empty, returns a placeholder string.
        """
        transitions = self.all_transitions()
        if not transitions:
            return (
                "(no navigation graph data yet — transitions are recorded "
                "automatically as tests run)"
            )

        url_set = set(urls) if urls else None

        def _is_relevant_url(url: str) -> bool:
            """Match exact URL or any sub-path under a base URL in url_set."""
            if url_set is None:
                return True
            for u in url_set:
                base = u.rstrip("/")
                if url == base or url.startswith(base + "/"):
                    return True
            return False

        # Filter to relevant edges, removing noise below min_count threshold.
        # Exclude "navigate" trigger actions — these are test-runner initializations
        # (e.g. "blank → /auth/login") that add no useful user-flow information.
        relevant = [
            t for t in transitions
            if (_is_relevant_url(t["from_url"]) or _is_relevant_url(t["to_url"]))
            and t["traversal_count"] >= min_count
            and t.get("trigger", {}).get("action", "") != "navigate"
        ]

        if not relevant:
            return "(no recorded transitions for these URLs yet)"

        # Deduplicate by URL pattern: when multiple edges share the same
        # from_pattern/to_pattern/action/label (e.g. rotating product ULIDs),
        # keep only the highest-count representative to avoid prompt bloat.
        pattern_best: dict = {}
        for t in relevant:
            from_pat = t.get("from_pattern") or _url_to_pattern(t["from_url"])
            to_pat   = t.get("to_pattern")   or _url_to_pattern(t["to_url"])
            tr       = t.get("trigger", {})
            pkey     = (from_pat, to_pat, tr.get("action", ""), tr.get("label", ""))
            if pkey not in pattern_best or t["traversal_count"] > pattern_best[pkey]["traversal_count"]:
                pattern_best[pkey] = t

        deduped = sorted(pattern_best.values(), key=lambda t: t["traversal_count"], reverse=True)
        deduped = deduped[:max_edges]

        lines: List[str] = ["Known page transitions (from observed test runs):"]
        for t in deduped:
            # Use pattern labels when the URL contains a dynamic segment
            from_pat = t.get("from_pattern") or t["from_url"]
            to_pat   = t.get("to_pattern")   or t["to_url"]
            frm   = _path_label(from_pat)
            to    = _path_label(to_pat)
            tr    = t["trigger"]
            act   = tr.get("action", "?")
            lbl   = tr.get("label", "")
            count = t["traversal_count"]
            lines.append(f'  {frm} --[{act} "{lbl}"]--> {to}  ({count}x observed)')

        # Compute reachable paths from the provided entry points.
        # Cap at 12 paths (shortest first) and skip /admin/* to keep output compact.
        if urls:
            paths = self.all_paths_from(urls)
            if paths:
                lines.append("")
                lines.append("Reachable navigation paths from entry points:")
                shown = 0
                for dest, path in sorted(paths.items(), key=lambda kv: len(kv[1])):
                    if not path or shown >= 12:
                        continue
                    if "/admin" in dest:
                        continue
                    dest_label = _path_label(dest)
                    crumbs = " → ".join(
                        _path_label(e["from_url"]) for e in path
                    ) + f" → {dest_label}"
                    steps = " then ".join(
                        f'{e["trigger"]["action"]} "{e["trigger"]["label"]}"'
                        for e in path
                    )
                    lines.append(f"  {crumbs}")
                    lines.append(f"    ({steps})")
                    shown += 1

        return "\n".join(lines)

    # ── Stats & maintenance ───────────────────────────────────────────

    def stats(self) -> dict:
        """
        Return summary statistics for the graph.

        Returns:
            {
                "total_transitions": int,
                "unique_pages":      int,
                "most_traversed":    [(from_path, to_path, count), ...],  # top 5
            }
        """
        transitions = self.all_transitions()
        pages       = self.all_urls()

        top = sorted(transitions, key=lambda t: t["traversal_count"], reverse=True)[:5]
        most = [
            (
                _path_label(t["from_url"]),
                _path_label(t["to_url"]),
                t["traversal_count"],
            )
            for t in top
        ]

        return {
            "total_transitions": len(transitions),
            "unique_pages":      len(pages),
            "most_traversed":    most,
        }

    def clear(self) -> int:
        """Delete all transition records. Returns the count deleted."""
        with self._db._lock:
            count = len(self._table.all())
            self._table.truncate()
            return count
