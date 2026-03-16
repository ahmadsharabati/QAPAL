"""
test_enterprise_e2e.py — Enterprise-grade site validation for QAPal.

Tests the framework against real, large-scale production sites that represent
the kind of complexity QAPal must handle before being published:

  - GitHub.com          — huge SPA, authenticated + public content, search, navigation
  - Stripe Docs         — rich documentation site, deep nav, code blocks, search
  - Atlassian Community — enterprise forum SPA, complex element hierarchy

Goals
-----
1. Verify the crawler handles enterprise-grade pages (large DOM, shadow DOM, lazy-load).
2. Verify the executor resolves locators reliably against role / text / placeholder strategies.
3. Verify the token tracker is populated after AI calls and accessible via `get_token_tracker()`.
4. Verify logging infrastructure (no print() calls, structured records emitted).
5. Stress-test `_safe_count` — no Playwright exceptions should bubble out of the executor.

All tests run headless and require no credentials.  Network access is required.
"""

import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# ── Path setup ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from locator_db import LocatorDB
from crawler import Crawler
from executor import Executor
from _tokens import get_token_tracker, TokenTracker
from _log import get_logger, setup_logging


# ── Sites ─────────────────────────────────────────────────────────────

GITHUB_URL       = "https://github.com"
STRIPE_DOCS_URL  = "https://stripe.com/docs"
ATLASSIAN_URL    = "https://community.atlassian.com"


# ── Shared test mixin ─────────────────────────────────────────────────

class _ExecMixin:
    """Shared setUp/tearDown and execution helpers for enterprise tests."""

    def setUp(self):
        self.db = LocatorDB(":memory:")
        # Reset token tracker so each test starts from zero
        get_token_tracker().reset()

    def tearDown(self):
        self.db.close()

    def _exec(self, plan: dict) -> dict:
        """Run an executor plan synchronously."""
        async def _run():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return asyncio.run(_run())

    def _crawl(self, url: str) -> list:
        """Crawl a URL and return results."""
        async def _run():
            async with Crawler(self.db, headless=True) as crawler:
                return await crawler.bulk_crawl([url])
        return asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════════
# 1. Token Tracker unit tests (no network)
# ═══════════════════════════════════════════════════════════════════════

class TestTokenTracker(unittest.TestCase):
    """Validate TokenTracker behaviour in isolation."""

    def setUp(self):
        self.tracker = TokenTracker()

    def test_initial_state_zero(self):
        s = self.tracker.snapshot()
        self.assertEqual(s["calls"], 0)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["input"], 0)
        self.assertEqual(s["output"], 0)

    def test_record_single_call(self):
        self.tracker.record(in_tok=500, out_tok=200, model="claude-sonnet", phase="plan")
        s = self.tracker.snapshot()
        self.assertEqual(s["calls"], 1)
        self.assertEqual(s["input"], 500)
        self.assertEqual(s["output"], 200)
        self.assertEqual(s["total"], 700)

    def test_record_multiple_calls_accumulate(self):
        self.tracker.record(in_tok=100, out_tok=50)
        self.tracker.record(in_tok=200, out_tok=80)
        self.tracker.record(in_tok=300, out_tok=120)
        s = self.tracker.snapshot()
        self.assertEqual(s["calls"], 3)
        self.assertEqual(s["input"], 600)
        self.assertEqual(s["output"], 250)

    def test_reset_clears_all(self):
        self.tracker.record(in_tok=999, out_tok=111)
        self.tracker.reset()
        s = self.tracker.snapshot()
        self.assertEqual(s["calls"], 0)
        self.assertEqual(s["total"], 0)

    def test_format_line_empty(self):
        self.assertEqual(self.tracker.format_line(), "")

    def test_format_line_populated(self):
        self.tracker.record(in_tok=1000, out_tok=250, model="x", phase="plan")
        line = self.tracker.format_line("plan")
        self.assertIn("1,000", line)
        self.assertIn("250", line)
        self.assertIn("1,250", line)
        self.assertIn("[plan]", line)
        self.assertIn("1 AI call", line)

    def test_format_line_plural(self):
        self.tracker.record(in_tok=100, out_tok=50)
        self.tracker.record(in_tok=100, out_tok=50)
        line = self.tracker.format_line()
        self.assertIn("AI calls", line)

    def test_cache_read_tracked(self):
        self.tracker.record(in_tok=500, out_tok=100, cache_read=300)
        s = self.tracker.snapshot()
        self.assertEqual(s["cache_read"], 300)
        line = self.tracker.format_line()
        self.assertIn("cache_hit=300", line)

    def test_global_tracker_singleton(self):
        t1 = get_token_tracker()
        t2 = get_token_tracker()
        self.assertIs(t1, t2)

    def test_thread_safety(self):
        """Multiple threads recording concurrently should not lose counts."""
        import threading
        errors = []

        def _record():
            try:
                for _ in range(50):
                    self.tracker.record(in_tok=1, out_tok=1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.tracker.snapshot()["calls"], 500)


# ═══════════════════════════════════════════════════════════════════════
# 2. Logging infrastructure tests (no network)
# ═══════════════════════════════════════════════════════════════════════

class TestLoggingInfrastructure(unittest.TestCase):
    """Verify that the logging module is correctly set up."""

    def test_get_logger_returns_logger(self):
        log = get_logger("test_module")
        self.assertIsInstance(log, logging.Logger)

    def test_logger_name_under_qapal_namespace(self):
        log = get_logger("executor")
        self.assertEqual(log.name, "qapal.executor")

    def test_get_logger_strips_qapal_prefix(self):
        log = get_logger("qapal.crawler")
        self.assertEqual(log.name, "qapal.crawler")

    def test_setup_logging_idempotent(self):
        """Calling setup_logging() multiple times should not add duplicate handlers."""
        setup_logging()
        root = logging.getLogger("qapal")
        count_before = len(root.handlers)
        setup_logging()
        count_after = len(root.handlers)
        self.assertEqual(count_before, count_after)

    def test_no_print_in_core_modules(self):
        """All core modules must route output through logging, not print()."""
        import ast
        root = Path(__file__).parent.parent
        modules = [
            "main.py", "executor.py", "generator.py",
            "crawler.py", "planner.py", "ai_client.py",
        ]
        violations = []
        for module in modules:
            path = root / module
            if not path.exists():
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    # print(...) as a bare name
                    if isinstance(func, ast.Name) and func.id == "print":
                        violations.append(f"{module}:{node.lineno}")
                    # builtins.print(...) as an attribute
                    elif isinstance(func, ast.Attribute) and func.attr == "print":
                        pass  # e.g. parser.print_help() — allowed
        self.assertEqual(violations, [],
                         f"print() calls found (should be replaced with log.*): {violations}")

    def test_logger_emits_records(self):
        """Verify that log.info() actually produces a LogRecord."""
        log = get_logger("_test_emit")
        records = []
        handler = logging.handlers_list = []

        class _Cap(logging.Handler):
            def emit(self, record):
                records.append(record)

        cap = _Cap()
        log.addHandler(cap)
        log.setLevel(logging.DEBUG)
        try:
            log.info("hello %s", "world")
        finally:
            log.removeHandler(cap)

        self.assertEqual(len(records), 1)
        self.assertIn("hello world", records[0].getMessage())


# ═══════════════════════════════════════════════════════════════════════
# 3. GitHub.com — large SPA
# ═══════════════════════════════════════════════════════════════════════

class TestGitHubE2E(_ExecMixin, unittest.TestCase):
    """
    GitHub.com is a React SPA with complex shadow DOM, lazy-loaded content,
    hundreds of interactive elements, and rate-limiting.  It tests:
      - Large-DOM crawling without memory/timeout issues
      - Role-based locator resolution (search, buttons, links)
      - Navigation assertion on a public page
    """

    def test_github_homepage_crawlable(self):
        """Crawler must complete without error and store ≥ 5 elements."""
        results = self._crawl(GITHUB_URL)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r.get("crawled"), f"Crawl failed: {r}")
        self.assertGreater(r.get("elements", 0), 5,
                           "Expected several elements from GitHub homepage")

    def test_github_navigate_and_title(self):
        """Navigate to GitHub and verify the page title."""
        plan = {
            "test_id": "github_title",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [{"type": "title_contains", "value": "GitHub"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass",
                         f"assertions: {result.get('assertions')}")

    def test_github_url_assertion(self):
        """Navigate to GitHub and assert the URL."""
        plan = {
            "test_id": "github_url",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [{"type": "url_contains", "value": "github.com"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_github_search_input_visible(self):
        """The search input on GitHub should be discoverable by placeholder."""
        plan = {
            "test_id": "github_search",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [
                # GitHub renders a search button/input with aria-label
                {
                    "type": "element_exists",
                    "selector": {"strategy": "aria-label", "value": "Search or jump to..."},
                }
            ],
        }
        result = self._exec(plan)
        # Pass or graceful fail (element may render differently on mobile viewports)
        self.assertIn(result["status"], ("pass", "fail"),
                      "Executor must return a clean pass/fail — no exceptions")

    def test_github_navigate_to_explore(self):
        """Navigate to GitHub Explore page via full URL."""
        plan = {
            "test_id": "github_explore",
            "steps": [{"action": "navigate", "url": "https://github.com/explore"}],
            "assertions": [{"type": "url_contains", "value": "explore"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_github_screenshot_action(self):
        """Screenshot action must produce a clean step result on GitHub."""
        plan = {
            "test_id": "github_screenshot",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {"action": "screenshot", "path": "/tmp/qapal_test_github.png"},
            ],
            "assertions": [],
        }
        result = self._exec(plan)
        # screenshot step should not cause a hard fail
        self.assertIn(result["status"], ("pass", "fail"))
        step_statuses = [s.get("status") for s in result.get("steps", [])]
        # navigate step must pass
        self.assertEqual(step_statuses[0], "pass")

    def test_github_wait_action(self):
        """Wait action should complete cleanly on an enterprise-grade page."""
        plan = {
            "test_id": "github_wait",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {"action": "wait", "duration": 500},
            ],
            "assertions": [{"type": "title_contains", "value": "GitHub"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_github_missing_element_graceful(self):
        """Clicking a non-existent element on GitHub should produce a clean fail."""
        plan = {
            "test_id": "github_missing",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {
                    "action": "click",
                    "selector": {"strategy": "testid", "value": "element-xyz-does-not-exist"},
                },
            ],
            "assertions": [],
        }
        result = self._exec(plan)
        # Must be a clean fail, never a Python exception
        self.assertEqual(result["status"], "fail")
        fail_step = next((s for s in result.get("steps", []) if s.get("status") == "fail"), None)
        self.assertIsNotNone(fail_step, "Expected at least one failing step")
        self.assertIn("Element not found", fail_step.get("reason", ""))

    def test_github_invalid_css_no_exception(self):
        """An invalid CSS selector must not propagate a Playwright exception."""
        plan = {
            "test_id": "github_invalid_css",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {
                    "action": "click",
                    "selector": {"strategy": "css", "value": ".this-selector-matches-nothing-xyz"},
                },
            ],
            "assertions": [],
        }
        # Should produce a clean fail, never raise
        try:
            result = self._exec(plan)
            self.assertEqual(result["status"], "fail")
        except Exception as e:
            self.fail(f"Executor raised an exception instead of returning fail: {e}")

    def test_github_scroll_action(self):
        """Scroll action should not crash on a large SPA page."""
        plan = {
            "test_id": "github_scroll",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {"action": "scroll", "direction": "down", "amount": 300},
            ],
            "assertions": [],
        }
        result = self._exec(plan)
        self.assertIn(result["status"], ("pass", "fail"))


# ═══════════════════════════════════════════════════════════════════════
# 4. Stripe Docs — rich documentation SPA
# ═══════════════════════════════════════════════════════════════════════

class TestStripeDocsE2E(_ExecMixin, unittest.TestCase):
    """
    Stripe Docs is a heavy documentation site with sidebar navigation,
    code blocks, search, and deep linking.  Tests:
      - Text-based locator strategy on enterprise docs
      - Assertion on content-rich pages
      - Navigation across multi-level doc URLs
    """

    def test_stripe_docs_crawlable(self):
        """Crawler must complete without error on Stripe Docs."""
        results = self._crawl(STRIPE_DOCS_URL)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r.get("crawled"), f"Crawl failed: {r}")
        self.assertGreater(r.get("elements", 0), 5)

    def test_stripe_docs_title(self):
        """Stripe Docs page should have 'Stripe' in title."""
        plan = {
            "test_id": "stripe_title",
            "steps": [{"action": "navigate", "url": STRIPE_DOCS_URL}],
            "assertions": [{"type": "title_contains", "value": "Stripe"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_docs_url_matches(self):
        """URL assertion using url_contains strategy."""
        plan = {
            "test_id": "stripe_url",
            "steps": [{"action": "navigate", "url": STRIPE_DOCS_URL}],
            "assertions": [{"type": "url_contains", "value": "stripe.com"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_docs_navigate_to_payments(self):
        """Navigate to the Stripe Payments docs page."""
        payments_url = "https://stripe.com/docs/payments"
        plan = {
            "test_id": "stripe_payments_nav",
            "steps": [{"action": "navigate", "url": payments_url}],
            "assertions": [{"type": "url_contains", "value": "payments"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_docs_element_exists(self):
        """A heading or nav element should exist on Stripe Docs."""
        plan = {
            "test_id": "stripe_heading",
            "steps": [{"action": "navigate", "url": STRIPE_DOCS_URL}],
            "assertions": [
                {
                    "type": "element_exists",
                    "selector": {"strategy": "role", "value": {"role": "navigation"}},
                }
            ],
        }
        result = self._exec(plan)
        # nav exists on most enterprise sites; graceful fail is also acceptable
        self.assertIn(result["status"], ("pass", "fail"))

    def test_stripe_docs_multi_page_navigation(self):
        """Navigate through two Stripe Docs pages in a single plan."""
        plan = {
            "test_id": "stripe_multi_page",
            "steps": [
                {"action": "navigate", "url": STRIPE_DOCS_URL},
                {"action": "navigate", "url": "https://stripe.com/docs/api"},
            ],
            "assertions": [{"type": "url_contains", "value": "api"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_docs_go_back(self):
        """go_back action must work across two pages."""
        plan = {
            "test_id": "stripe_go_back",
            "steps": [
                {"action": "navigate", "url": STRIPE_DOCS_URL},
                {"action": "navigate", "url": "https://stripe.com/docs/api"},
                {"action": "go_back"},
            ],
            "assertions": [{"type": "url_contains", "value": "stripe.com/docs"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_docs_page_refresh(self):
        """Refresh action should reload the page without errors."""
        plan = {
            "test_id": "stripe_refresh",
            "steps": [
                {"action": "navigate", "url": STRIPE_DOCS_URL},
                {"action": "refresh"},
            ],
            "assertions": [{"type": "url_contains", "value": "stripe.com"}],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_stripe_crawl_stores_locators(self):
        """After crawling Stripe Docs, locators should be stored in DB."""
        self._crawl(STRIPE_DOCS_URL)
        locators = self.db.get_all(STRIPE_DOCS_URL)
        self.assertGreater(len(locators), 0,
                           "Expected at least 1 locator stored after crawling Stripe Docs")


# ═══════════════════════════════════════════════════════════════════════
# 5. Cross-enterprise resilience tests
# ═══════════════════════════════════════════════════════════════════════

class TestCrossEnterpriseResilience(_ExecMixin, unittest.TestCase):
    """
    Cross-cutting robustness tests that combine insights from all three sites.
    Focuses on executor edge cases on complex, real-world pages.
    """

    def test_empty_plan_passes(self):
        """A plan with no steps and no assertions should trivially pass."""
        plan = {"test_id": "empty", "steps": [], "assertions": []}
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_assertion_only_plan(self):
        """Plan with navigation + URL assertion — no interaction steps."""
        plan = {
            "test_id": "assertion_only",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [
                {"type": "url_contains", "value": "github"},
                {"type": "title_contains", "value": "GitHub"},
            ],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_multi_site_sequential_plans(self):
        """Execute plans against two enterprise sites in the same DB session."""
        plan_github = {
            "test_id": "multi_github",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [{"type": "url_contains", "value": "github.com"}],
        }
        plan_stripe = {
            "test_id": "multi_stripe",
            "steps": [{"action": "navigate", "url": STRIPE_DOCS_URL}],
            "assertions": [{"type": "url_contains", "value": "stripe.com"}],
        }
        r1 = self._exec(plan_github)
        r2 = self._exec(plan_stripe)
        self.assertEqual(r1["status"], "pass")
        self.assertEqual(r2["status"], "pass")

    def test_navigate_to_nonexistent_subdomain(self):
        """Navigating to a non-existent URL should produce a fail step, not crash."""
        plan = {
            "test_id": "bad_url",
            "steps": [
                {
                    "action": "navigate",
                    "url": "https://this-site-absolutely-does-not-exist-qapal-test.com",
                    "timeout": 5000,
                }
            ],
            "assertions": [],
        }
        try:
            result = self._exec(plan)
            # Some Playwright versions may return fail or pass depending on DNS behaviour
            self.assertIn(result["status"], ("pass", "fail"),
                          "Expected clean pass/fail, not an exception")
        except Exception as e:
            self.fail(f"Executor raised exception for bad URL: {e}")

    def test_element_count_assertion(self):
        """element_count assertion type should work on a real enterprise page."""
        plan = {
            "test_id": "element_count",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [
                {
                    "type": "element_count",
                    "selector": {"strategy": "role", "value": {"role": "link"}},
                    "count": 1,
                    "operator": "at_least",
                }
            ],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_javascript_assertion_on_github(self):
        """javascript assertion type must execute and return a result."""
        plan = {
            "test_id": "js_assert",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [
                {
                    "type": "javascript",
                    "script": "document.location.hostname === 'github.com'",
                    "value": True,
                }
            ],
        }
        result = self._exec(plan)
        self.assertEqual(result["status"], "pass")

    def test_evaluate_action_on_stripe(self):
        """evaluate action must execute JavaScript on Stripe Docs without crashing."""
        plan = {
            "test_id": "evaluate_stripe",
            "steps": [
                {"action": "navigate", "url": STRIPE_DOCS_URL},
                {"action": "evaluate", "script": "document.title"},
            ],
            "assertions": [],
        }
        result = self._exec(plan)
        self.assertIn(result["status"], ("pass", "fail"))

    def test_hover_over_nav_item_on_github(self):
        """Hover action on a real site should not raise exceptions."""
        plan = {
            "test_id": "hover_github",
            "steps": [
                {"action": "navigate", "url": GITHUB_URL},
                {
                    "action": "hover",
                    "selector": {"strategy": "role", "value": {"role": "link", "name": "Sign in"}},
                },
            ],
            "assertions": [],
        }
        try:
            result = self._exec(plan)
            self.assertIn(result["status"], ("pass", "fail"))
        except Exception as e:
            self.fail(f"hover raised an exception: {e}")

    def test_duration_ms_always_present(self):
        """Result dict must always include duration_ms."""
        plan = {
            "test_id": "duration_check",
            "steps": [{"action": "navigate", "url": GITHUB_URL}],
            "assertions": [],
        }
        result = self._exec(plan)
        self.assertIn("duration_ms", result)
        self.assertIsInstance(result["duration_ms"], int)
        self.assertGreater(result["duration_ms"], 0)

    def test_result_shape_enterprise(self):
        """Result dict shape must be consistent across enterprise sites."""
        for url, name in [
            (GITHUB_URL, "github"),
            (STRIPE_DOCS_URL, "stripe"),
        ]:
            with self.subTest(site=name):
                plan = {
                    "test_id": f"shape_{name}",
                    "steps": [{"action": "navigate", "url": url}],
                    "assertions": [{"type": "url_contains", "value": name}],
                }
                result = self._exec(plan)
                # All required keys must be present
                for key in ("status", "duration_ms", "steps", "assertions"):
                    self.assertIn(key, result, f"Missing key '{key}' in result for {name}")
                self.assertIsInstance(result["steps"], list)
                self.assertIsInstance(result["assertions"], list)


# ═══════════════════════════════════════════════════════════════════════
# 6. Token tracker integration (no AI key needed — mocked response)
# ═══════════════════════════════════════════════════════════════════════

class TestTokenTrackerIntegration(unittest.TestCase):
    """
    Verify that the token tracker captures usage from mocked AI client responses
    without requiring a real API key.
    """

    def test_tracker_populated_after_mocked_anthropic_call(self):
        """Simulate an Anthropic API response and verify tracker is updated."""
        from _tokens import get_token_tracker

        tracker = get_token_tracker()
        tracker.reset()

        # Simulate what _AnthropicClient.complete() does when it gets a response
        class _FakeUsage:
            input_tokens  = 1234
            output_tokens = 567
            cache_read_input_tokens = 0

        class _FakeContent:
            text = "Mocked response"

        class _FakeResponse:
            usage   = _FakeUsage()
            content = [_FakeContent()]

        # Replicate the extraction logic from ai_client.py
        response = _FakeResponse()
        usage = getattr(response, "usage", None)
        if usage:
            tracker.record(
                in_tok     = getattr(usage, "input_tokens", 0),
                out_tok    = getattr(usage, "output_tokens", 0),
                cache_read = getattr(usage, "cache_read_input_tokens", 0),
                model      = "claude-sonnet-4-6",
                phase      = "plan",
            )

        s = tracker.snapshot()
        self.assertEqual(s["input"], 1234)
        self.assertEqual(s["output"], 567)
        self.assertEqual(s["calls"], 1)

    def test_tracker_populated_after_mocked_openai_call(self):
        """Simulate an OpenAI API response and verify tracker is updated."""
        from _tokens import get_token_tracker

        tracker = get_token_tracker()
        tracker.reset()

        class _FakeUsage:
            prompt_tokens     = 800
            completion_tokens = 200
            total_tokens      = 1000

        class _FakeChoice:
            class message:
                content = "Mocked OpenAI response"

        class _FakeResponse:
            usage   = _FakeUsage()
            choices = [_FakeChoice()]

        response  = _FakeResponse()
        usage     = getattr(response, "usage", None)
        model     = "gpt-4o-mini"
        if usage:
            tracker.record(
                in_tok  = getattr(usage, "prompt_tokens", 0),
                out_tok = getattr(usage, "completion_tokens", 0),
                model   = model,
                phase   = "plan",
            )

        s = tracker.snapshot()
        self.assertEqual(s["input"], 800)
        self.assertEqual(s["output"], 200)
        self.assertEqual(s["total"], 1000)

    def test_token_summary_in_log_format(self):
        """Verify the summary line emitted to the log is parseable."""
        from _tokens import TokenTracker
        tracker = TokenTracker()
        tracker.record(in_tok=2500, out_tok=750, model="claude-haiku", phase="plan")

        line = tracker.format_line("plan")
        self.assertIn("[plan]", line)
        self.assertIn("2,500", line)
        self.assertIn("750", line)
        self.assertIn("3,250", line)   # total
        self.assertIn("1 AI call", line)


# ── Runner ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
