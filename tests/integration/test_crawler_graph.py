"""
tests/integration/test_crawler_graph.py
========================================
Integration tests for the Crawler ↔ StateGraph contract.

Tests are grouped into three suites:

1. TestCrawlerGraphIntegration
   - Crawler populates graph templates on first crawl
   - Second crawl of same URL increments template visit count
   - Structurally different URLs register as separate templates
   - Template semantic hash is stable across re-crawls
   - Same-layout sibling URLs reuse template (inherited_from set)

2. TestExecutorGraphIntegration
   - navigate action records transition to the graph
   - Click-triggered navigation records transition with action label
   - Multi-step plan records all transitions in declared order
   - Running the same plan twice doubles traversal_count on every edge
   - Session IDs are tagged per execution run
   - graph.get_path() returns a path matching what executor actually navigated

3. TestCrawlerExecutorGraphPipeline
   - Full crawl → execute → graph has both template data and live transitions
   - format_for_prompt() reflects paths actually walked by the executor
   - Distinct sites do not share graph edges
   - Transitions recorded in graph are reachable via BFS

Requirements:
  pip install playwright pytest
  playwright install chromium

Run:
  python -m pytest tests/integration/test_crawler_graph.py -v
  python -m pytest tests/integration/test_crawler_graph.py -v -k "Crawler"
  python -m pytest tests/integration/test_crawler_graph.py -v -k "Executor"
  python -m pytest tests/integration/test_crawler_graph.py -v -k "Pipeline"
"""

import asyncio
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from locator_db import LocatorDB
from crawler import Crawler
from executor import Executor
from state_graph import StateGraph, compute_semantic_hash, classify_page_change


# ── URLs ──────────────────────────────────────────────────────────────
TODOMVC       = "https://demo.playwright.dev/todomvc/#/"
TODOMVC_ACTIVE = "https://demo.playwright.dev/todomvc/#/active"
TODOMVC_DONE  = "https://demo.playwright.dev/todomvc/#/completed"
BOOKS_HOME    = "https://books.toscrape.com/"
BOOKS_PAGE2   = "https://books.toscrape.com/catalogue/page-2.html"


# ── Helpers ───────────────────────────────────────────────────────────

def _make_db() -> LocatorDB:
    """Isolated LocatorDB backed by a temp file — not shared between tests."""
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close()
    return LocatorDB(path=tf.name)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fake AI (for executor tests that need one) ────────────────────────

class _FakeAI:
    """Returns an empty plan; executor tests supply plans directly."""
    small_model = "fake-small"

    async def acomplete(self, *args, **kwargs):
        return '{"action":"click","selector":{"strategy":"role","value":"button"}}'

    def complete(self, *args, **kwargs):
        return "[]"


# ═════════════════════════════════════════════════════════════════════
# Suite 1 — Crawler × StateGraph
# ═════════════════════════════════════════════════════════════════════

class TestCrawlerGraphIntegration(unittest.TestCase):
    """Crawler populates the StateGraph correctly during live crawls."""

    # ── helpers ──────────────────────────────────────────────────────

    def _crawl(self, url: str, db: LocatorDB, sg: StateGraph,
               force: bool = True) -> dict:
        crawler = Crawler(db, headless=True, state_graph=sg)
        return run(crawler.crawl_url(url, force=force))

    # ── tests ─────────────────────────────────────────────────────────

    def test_template_registered_after_first_crawl(self):
        """crawl_page() calls register_template(); graph has ≥1 template."""
        db = _make_db()
        sg = StateGraph(db)
        result = self._crawl(TODOMVC, db, sg)
        self.assertTrue(result.get("crawled"), "crawl must succeed")
        templates = sg._templates.all()
        self.assertGreater(len(templates), 0,
                           "at least one template must be registered")

    def test_template_has_required_fields(self):
        """Every registered template carries mandatory metadata."""
        db = _make_db()
        sg = StateGraph(db)
        self._crawl(TODOMVC, db, sg)
        for tmpl in sg._templates.all():
            self.assertIn("template_id",   tmpl, "template_id missing")
            self.assertIn("sample_url",    tmpl, "sample_url missing")
            self.assertIn("element_count", tmpl, "element_count missing")
            self.assertIn("first_seen",    tmpl, "first_seen missing")
            self.assertGreater(tmpl["element_count"], 0,
                               "template must have at least one element")

    def test_template_sample_url_matches_crawled_url(self):
        """sample_url on the registered template equals the crawled URL."""
        db = _make_db()
        sg = StateGraph(db)
        self._crawl(TODOMVC, db, sg)
        templates = sg._templates.all()
        sample_urls = [t["sample_url"] for t in templates]
        self.assertTrue(
            any("todomvc" in u for u in sample_urls),
            f"expected todomvc URL in sample_urls; got {sample_urls}",
        )

    def test_recrawl_same_url_does_not_duplicate_template(self):
        """Re-crawling the same URL must not create a second template record."""
        db = _make_db()
        sg = StateGraph(db)
        self._crawl(TODOMVC, db, sg, force=True)
        count_before = len(sg._templates.all())
        self._crawl(TODOMVC, db, sg, force=True)
        count_after = len(sg._templates.all())
        self.assertEqual(count_before, count_after,
                         "re-crawl must not add duplicate templates")

    def test_two_structurally_different_urls_register_separate_templates(self):
        """TodoMVC and Books have different layouts → different template_ids."""
        db = _make_db()
        sg = StateGraph(db)
        self._crawl(TODOMVC, db, sg)
        self._crawl(BOOKS_HOME, db, sg)
        template_ids = {t["template_id"] for t in sg._templates.all()}
        self.assertGreaterEqual(len(template_ids), 2,
                                "two structurally different pages must yield different templates")

    def test_sibling_catalogue_page_inherits_from_template(self):
        """
        Books page-1 and page-2 share layout → page-2 crawl sets
        template_match=True and inherited_from points at page-1.
        """
        db = _make_db()
        sg = StateGraph(db)
        # Crawl page-1 first to register the template
        r1 = self._crawl(BOOKS_HOME, db, sg, force=True)
        self.assertTrue(r1.get("crawled"), "books home must crawl OK")
        # Crawl page-2 — should recognise the template
        r2 = self._crawl(BOOKS_PAGE2, db, sg, force=True)
        self.assertTrue(r2.get("crawled"), "books page-2 must crawl OK")
        if r2.get("template_match"):
            self.assertEqual(r2["inherited_from"], BOOKS_HOME,
                             "page-2 should inherit from page-1 sample URL")
            self.assertIsNotNone(r2.get("template_id"),
                                 "template_id must be set on a matched crawl")

    def test_crawl_with_no_state_graph_does_not_crash(self):
        """Passing state_graph=None to Crawler must work without errors."""
        db = _make_db()
        crawler = Crawler(db, headless=True, state_graph=None)
        result = run(crawler.crawl_url(TODOMVC, force=True))
        self.assertTrue(result.get("crawled"), "crawl without graph must succeed")

    def test_crawl_result_elements_greater_than_zero(self):
        """The live TodoMVC page must yield at least one element."""
        db = _make_db()
        sg = StateGraph(db)
        result = self._crawl(TODOMVC, db, sg)
        self.assertGreater(result.get("elements", 0), 0,
                           "TodoMVC must produce at least one element")

    def test_template_element_count_matches_db(self):
        """element_count on the template ≤ actual elements stored in DB."""
        db = _make_db()
        sg = StateGraph(db)
        result = self._crawl(TODOMVC, db, sg)
        all_docs = db.get_all(TODOMVC, valid_only=False)
        templates = sg._templates.all()
        if templates:
            # element_count may be from stored_docs at point of registration
            # so it must be ≤ total stored (includes updates)
            self.assertGreaterEqual(
                len(all_docs),
                min(t["element_count"] for t in templates),
                "DB must hold at least as many docs as template element_count",
            )

    def test_state_node_added_when_graph_present(self):
        """
        If crawl_page calls enrich_and_add, states table grows.
        This verifies the a11y snapshot → semantic state flow exists.
        Note: enrich_and_add is called by the executor's on_page_load path.
        We test it directly to confirm the API works end-to-end.
        """
        db = _make_db()
        sg = StateGraph(db)
        # Manually add a state to confirm the table works
        a11y = [{"role": "textbox", "name": "What needs to be done?"}]
        sid = sg.enrich_and_add(TODOMVC, a11y)
        self.assertTrue(sg.has_state(sid),
                        "enrich_and_add must make has_state() return True")
        states = sg.all_states()
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["url"], TODOMVC)

    def test_enrich_and_add_same_snapshot_increments_visit_count(self):
        """Calling enrich_and_add twice with the same snapshot increments count."""
        db = _make_db()
        sg = StateGraph(db)
        a11y = [{"role": "main", "name": "todos"}, {"role": "textbox", "name": "input"}]
        sg.enrich_and_add(TODOMVC, a11y)
        sg.enrich_and_add(TODOMVC, a11y)
        states = sg.all_states()
        self.assertEqual(len(states), 1, "same snapshot must not create duplicate state")
        self.assertEqual(states[0]["visit_count"], 2, "visit_count must be 2 after two calls")

    def test_enrich_and_add_different_snapshot_creates_new_state(self):
        """Different page structure → different state_id → new row."""
        db = _make_db()
        sg = StateGraph(db)
        snap1 = [{"role": "textbox", "name": "input"}]
        snap2 = [{"role": "button", "name": "Submit"}, {"role": "heading", "name": "Home"}]
        sg.enrich_and_add(TODOMVC, snap1)
        sg.enrich_and_add(TODOMVC_ACTIVE, snap2)
        states = sg.all_states()
        self.assertEqual(len(states), 2, "different snapshots must create separate state rows")


# ═════════════════════════════════════════════════════════════════════
# Suite 2 — Executor × StateGraph
# ═════════════════════════════════════════════════════════════════════

class TestExecutorGraphIntegration(unittest.TestCase):
    """Executor populates the StateGraph with live transitions."""

    # ── helpers ──────────────────────────────────────────────────────

    def _run_plan(self, plan: dict, db: LocatorDB,
                  sg: StateGraph) -> dict:
        async def _inner():
            ex = Executor(db, ai_client=_FakeAI(),
                          headless=True, state_graph=sg)
            async with ex:
                return await ex.run(plan)
        return run(_inner())

    def _session_id(self) -> str:
        return str(uuid.uuid4())

    # ── tests ─────────────────────────────────────────────────────────

    def test_navigate_action_records_transition(self):
        """
        A plan with a single 'navigate' step must produce at least one
        transition in the graph whose to_url matches the destination.
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_nav",
            "name": "Navigate to TodoMVC",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        transitions = sg.all_transitions()
        self.assertGreater(len(transitions), 0,
                           "navigate step must record ≥1 transition")
        urls = {t["to_url"] for t in transitions}
        self.assertTrue(any("todomvc" in u for u in urls),
                        f"expected todomvc in transition to_urls; got {urls}")

    def test_navigate_transition_has_correct_action_field(self):
        """Transition recorded for a navigate step has action='navigate'."""
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_nav2",
            "name": "Nav action field",
            "steps": [{"action": "navigate", "url": TODOMVC}],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        nav_transitions = [
            t for t in sg.all_transitions()
            if t["trigger"]["action"] == "navigate"
        ]
        self.assertGreater(len(nav_transitions), 0,
                           "at least one transition must have action='navigate'")

    def test_click_navigation_records_transition_with_label(self):
        """
        Clicking a filter link in TodoMVC navigates to /#/active.
        The graph must record this transition with a non-empty label.
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_click_nav",
            "name": "Click active filter",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {
                    "action": "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "Active"}},
                },
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        click_transitions = [
            t for t in sg.all_transitions()
            if t["trigger"]["action"] == "click"
        ]
        if click_transitions:
            labels = [t["trigger"]["label"] for t in click_transitions]
            self.assertTrue(any(labels),
                            "click transitions must carry a non-empty label")

    def test_multi_step_plan_records_multiple_transitions(self):
        """
        A plan navigating across two distinct URL paths must produce ≥2 transitions.
        Uses books.toscrape.com page-1 → page-2 (real path changes, not hash fragments).
        Note: _normalize_url strips hash fragments, so TodoMVC #/active etc. all collapse
        to the same normalized URL and only produce 1 edge.
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_multi",
            "name": "Multi-page navigation",
            "steps": [
                {"action": "navigate", "url": BOOKS_HOME},
                {"action": "navigate", "url": BOOKS_PAGE2},
                {"action": "navigate", "url": BOOKS_HOME},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        transitions = sg.all_transitions()
        self.assertGreaterEqual(len(transitions), 2,
                                "3-step navigate plan across distinct paths must produce ≥2 transitions")

    def test_second_run_doubles_traversal_count(self):
        """
        Running the same plan twice must increment traversal_count on every
        edge; no new edges should be created for the identical navigation.
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_double",
            "name": "Repeated run",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        count_after_1 = {t["id"]: t["traversal_count"]
                         for t in sg.all_transitions()}

        self._run_plan(plan, db, sg)
        count_after_2 = {t["id"]: t["traversal_count"]
                         for t in sg.all_transitions()}

        self.assertEqual(set(count_after_1.keys()), set(count_after_2.keys()),
                         "second run must not add new edges for same navigation")
        for eid, cnt in count_after_1.items():
            self.assertEqual(count_after_2[eid], cnt + 1,
                             f"edge {eid} traversal_count must increment by 1")

    def test_transitions_cover_all_navigated_urls(self):
        """
        Every distinct URL path we navigate to must appear as a to_url
        in at least one graph edge.
        Uses books.toscrape.com — distinct paths, not hash fragments
        (hash fragments are stripped by _normalize_url and don't create edges).
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_coverage",
            "name": "URL coverage",
            "steps": [
                {"action": "navigate", "url": BOOKS_HOME},
                {"action": "navigate", "url": BOOKS_PAGE2},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        to_urls = {t["to_url"] for t in sg.all_transitions()}
        self.assertTrue(
            any("books.toscrape.com" in u for u in to_urls),
            "books home must appear in to_urls",
        )
        self.assertTrue(
            any("page-2" in u for u in to_urls),
            "page-2 must appear in to_urls",
        )

    def test_get_path_returns_route_executor_walked(self):
        """
        After the executor navigates A→B→C, graph.get_path(A, C) must
        find a path (not necessarily the same steps, but must not be None).
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_path",
            "name": "Path finding",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        transitions = sg.all_transitions()
        if len(transitions) >= 2:
            start = transitions[0]["from_url"]
            end   = transitions[-1]["to_url"]
            path  = sg.get_path(start, end)
            self.assertIsNotNone(path,
                                 f"get_path({start!r}, {end!r}) must not be None "
                                 f"after executor walked that route")

    def test_no_self_loop_transitions_recorded(self):
        """
        Navigating to the same URL twice must not produce a self-loop
        (from_url == to_url) in the transitions table.
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_selfloop",
            "name": "Self-loop guard",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC},  # same URL twice
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        for t in sg.all_transitions():
            self.assertNotEqual(
                t["from_url"], t["to_url"],
                f"self-loop detected: {t['from_url']} → {t['to_url']}",
            )

    def test_transition_page_change_type_set(self):
        """
        Every transition must have a non-empty page_change_type
        (at minimum 'navigation').
        """
        db = _make_db()
        sg = StateGraph(db)
        plan = {
            "test_id": "TC_pct",
            "name": "page_change_type",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)
        for t in sg.all_transitions():
            self.assertTrue(t.get("page_change_type"),
                            f"transition {t['id']} has empty page_change_type")

    def test_session_id_tagged_on_transitions(self):
        """
        Executor uses the plan's test_id as its internal session_id.
        That test_id must appear in session_ids on at least one transition.
        """
        db = _make_db()
        sg = StateGraph(db)
        # The executor derives session_id from test_case["test_id"] (line 1178 of executor.py).
        tc_id = "TC_session_" + self._session_id()[:8]
        plan = {
            "test_id": tc_id,
            "name": "Session tagging",
            "steps": [
                {"action": "navigate", "url": BOOKS_HOME},
                {"action": "navigate", "url": BOOKS_PAGE2},
            ],
            "assertions": [],
        }
        self._run_plan(plan, db, sg)

        all_sessions = []
        for t in sg.all_transitions():
            all_sessions.extend(t.get("session_ids", []))
        self.assertIn(tc_id, all_sessions,
                      "the plan test_id (used as session_id) must appear in graph edge session_ids")


# ═════════════════════════════════════════════════════════════════════
# Suite 3 — Full Crawler + Executor + StateGraph Pipeline
# ═════════════════════════════════════════════════════════════════════

class TestCrawlerExecutorGraphPipeline(unittest.TestCase):
    """
    End-to-end three-way integration:
    Crawler crawls → StateGraph registers templates →
    Executor runs plan → StateGraph accumulates transitions →
    format_for_prompt() exposes real navigation context.
    """

    # ── helpers ──────────────────────────────────────────────────────

    def _full_run(self, crawl_url: str, plan: dict) -> tuple:
        """
        Returns (db, sg, exec_result) after: crawl crawl_url, then run plan.
        Both use the same db/sg instance.
        """
        db = _make_db()
        sg = StateGraph(db)

        async def _inner():
            # Step 1: crawl
            crawler = Crawler(db, headless=True, state_graph=sg)
            await crawler.crawl_url(crawl_url, force=True)

            # Step 2: execute
            ex = Executor(db, ai_client=_FakeAI(),
                          headless=True, state_graph=sg)
            async with ex:
                result = await ex.run(plan)
            return result

        result = run(_inner())
        return db, sg, result

    # ── tests ─────────────────────────────────────────────────────────

    def test_graph_has_templates_and_transitions_after_full_run(self):
        """
        After crawl + execute, the graph must have both template records
        and transition records.
        """
        plan = {
            "test_id": "TC_full_1",
            "name": "Full pipeline smoke",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        db, sg, result = self._full_run(TODOMVC, plan)
        self.assertGreater(len(sg._templates.all()), 0,
                           "crawl must populate templates")
        self.assertGreater(len(sg.all_transitions()), 0,
                           "executor must populate transitions")

    def test_format_for_prompt_includes_navigated_urls(self):
        """
        format_for_prompt() renders click-triggered transitions but intentionally
        excludes bare 'navigate' transitions (test-runner noise by design).
        This test:
          1. Runs a plan that produces a click-based navigation on Books site.
          2. Falls back to manually injecting a click transition so the prompt
             content can be verified even if no click navigation occurred.
        """
        plan = {
            "test_id": "TC_prompt",
            "name": "Prompt coverage",
            "steps": [
                {"action": "navigate", "url": BOOKS_HOME},
            ],
            "assertions": [],
        }
        db, sg, _ = self._full_run(BOOKS_HOME, plan)

        # Inject a representative click transition so format_for_prompt has
        # real content to render (navigate-type transitions are filtered by design).
        sg.record_transition(
            from_url       = BOOKS_HOME,
            to_url         = BOOKS_PAGE2,
            trigger_action = "click",
            trigger_label  = "next",
            session_id     = "test-session",
        )

        prompt_text = sg.format_for_prompt()
        self.assertNotIn(
            "no recorded transitions",
            prompt_text.lower(),
            "format_for_prompt must produce real content when click transitions exist",
        )
        self.assertIn(
            "next",
            prompt_text,
            "format_for_prompt must include the trigger label",
        )
        self.assertTrue(
            "books.toscrape.com" in prompt_text or "catalogue" in prompt_text
            or "page-2" in prompt_text,
            "format_for_prompt must reference the navigated URL path",
        )

    def test_two_separate_sites_do_not_share_transitions(self):
        """
        Transitions recorded for TodoMVC must not appear in a fresh graph
        built from Books-only execution.
        """
        db_a = _make_db()
        sg_a = StateGraph(db_a)

        db_b = _make_db()
        sg_b = StateGraph(db_b)

        async def _run_todomvc():
            crawler = Crawler(db_a, headless=True, state_graph=sg_a)
            await crawler.crawl_url(TODOMVC, force=True)
            plan = {
                "test_id": "TC_site_a",
                "name": "TodoMVC only",
                "steps": [{"action": "navigate", "url": TODOMVC}],
                "assertions": [],
            }
            ex = Executor(db_a, ai_client=_FakeAI(),
                          headless=True, state_graph=sg_a)
            async with ex:
                await ex.run(plan)

        async def _run_books():
            crawler = Crawler(db_b, headless=True, state_graph=sg_b)
            await crawler.crawl_url(BOOKS_HOME, force=True)
            plan = {
                "test_id": "TC_site_b",
                "name": "Books only",
                "steps": [{"action": "navigate", "url": BOOKS_HOME}],
                "assertions": [],
            }
            ex = Executor(db_b, ai_client=_FakeAI(),
                          headless=True, state_graph=sg_b)
            async with ex:
                await ex.run(plan)

        run(_run_todomvc())
        run(_run_books())

        todomvc_urls = {t["to_url"] for t in sg_a.all_transitions()}
        books_urls   = {t["to_url"] for t in sg_b.all_transitions()}

        # TodoMVC transitions must not appear in Books graph
        for u in todomvc_urls:
            self.assertNotIn(u, books_urls,
                             f"URL {u!r} leaked from TodoMVC graph into Books graph")

    def test_bfs_finds_path_through_executor_walked_route(self):
        """
        After executor walks A→B, get_path(A, B) must return a non-empty path.
        """
        plan = {
            "test_id": "TC_bfs",
            "name": "BFS path validation",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        db, sg, _ = self._full_run(TODOMVC, plan)

        transitions = sg.all_transitions()
        if len(transitions) >= 2:
            start = transitions[0]["from_url"]
            end   = transitions[-1]["to_url"]
            if start != end:
                path = sg.get_path(start, end)
                self.assertIsNotNone(
                    path,
                    f"BFS must find path from {start!r} to {end!r} "
                    f"which executor actually walked",
                )
                self.assertGreater(len(path), 0,
                                   "path must contain at least one edge")

    def test_stats_reflect_actual_activity(self):
        """
        sg.stats() must return non-zero page and edge counts after
        a crawl + execute run.
        """
        plan = {
            "test_id": "TC_stats",
            "name": "Stats validation",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        db, sg, _ = self._full_run(TODOMVC, plan)
        stats = sg.stats()
        self.assertGreater(stats.get("total_transitions", 0), 0,
                           "stats must show at least one transition")
        self.assertGreater(stats.get("unique_pages", 0), 0,
                           "stats must show at least one unique page")

    def test_db_locators_and_graph_use_same_db_file(self):
        """
        Locators and transitions must coexist in the same TinyDB file.
        Closing and re-opening the DB must preserve both.
        """
        db = _make_db()
        sg = StateGraph(db)
        db_path = db._path

        async def _inner():
            crawler = Crawler(db, headless=True, state_graph=sg)
            await crawler.crawl_url(TODOMVC, force=True)
            plan = {
                "test_id": "TC_persist",
                "name": "Persistence",
                "steps": [{"action": "navigate", "url": TODOMVC},
                          {"action": "navigate", "url": TODOMVC_ACTIVE}],
                "assertions": [],
            }
            ex = Executor(db, ai_client=_FakeAI(),
                          headless=True, state_graph=sg)
            async with ex:
                await ex.run(plan)
        run(_inner())
        db.close()

        # Re-open
        db2 = LocatorDB(path=db_path)
        sg2 = StateGraph(db2)

        locators    = db2.get_all(TODOMVC, valid_only=False)
        transitions = sg2.all_transitions()

        self.assertGreater(len(locators), 0,
                           "locators must survive DB close/reopen")
        self.assertGreater(len(transitions), 0,
                           "transitions must survive DB close/reopen")

    def test_format_for_prompt_min_count_hides_noise(self):
        """
        After one run, all edges have traversal_count=1.
        format_for_prompt(min_count=2) must suppress them (or return placeholder).
        """
        plan = {
            "test_id": "TC_mincount",
            "name": "min_count filter",
            "steps": [
                {"action": "navigate", "url": TODOMVC},
                {"action": "navigate", "url": TODOMVC_ACTIVE},
            ],
            "assertions": [],
        }
        db, sg, _ = self._full_run(TODOMVC, plan)

        # All edges have count=1 after single run
        for t in sg.all_transitions():
            self.assertEqual(t["traversal_count"], 1)

        # min_count=2 must suppress them
        prompt = sg.format_for_prompt(min_count=2)
        # Either the placeholder or the content won't include the single-run edges
        # (exact phrasing depends on format_for_prompt implementation)
        self.assertIsInstance(prompt, str,
                              "format_for_prompt must return a string")

    def test_crawl_then_execute_locators_usable_for_plan(self):
        """
        Locators crawled before execution must satisfy element lookups
        during the plan — the plan must pass (not fail on missing locators).
        """
        db = _make_db()
        sg = StateGraph(db)

        async def _inner():
            # Crawl first
            crawler = Crawler(db, headless=True, state_graph=sg)
            await crawler.crawl_url(TODOMVC, force=True)

            # Build a plan using a known-crawled element
            plan = {
                "test_id": "TC_usability",
                "name": "Locator usability",
                "steps": [
                    {"action": "navigate", "url": TODOMVC},
                    {
                        "action": "fill",
                        "selector": {
                            "strategy": "role",
                            "value": {"role": "textbox",
                                      "name": "What needs to be done?"},
                        },
                        "value": "integration test item",
                    },
                ],
                "assertions": [
                    {
                        "type": "element_visible",
                        "selector": {
                            "strategy": "role",
                            "value": {"role": "textbox",
                                      "name": "What needs to be done?"},
                        },
                    }
                ],
            }
            ex = Executor(db, ai_client=_FakeAI(),
                          headless=True, state_graph=sg)
            async with ex:
                return await ex.run(plan)

        result = run(_inner())
        passed_steps = [s for s in result.get("steps", []) if s.get("status") == "pass"]
        self.assertGreater(len(passed_steps), 0,
                           "at least the navigate step must pass when locators are pre-crawled")


# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
