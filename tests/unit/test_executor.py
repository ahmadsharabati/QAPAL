"""
tests/unit/test_executor.py
============================
Unit tests for executor.py — pure helper functions, result builders,
failure taxonomy, and locator-resolution logic.

All Playwright/browser/network calls are mocked.
No live browser required.

Coverage:
  TestResultBuilders       — _step_pass(), _step_fail(), _assert_pass(), _assert_fail()
  TestFailureCategory      — constant values and class hierarchy
  TestTriggerLabel         — _trigger_label() selector → label extraction
  TestSignalFailure        — _is_signal_failure() noise-domain filtering
  TestDetectUnknownState   — _detect_unknown_state() DB-backed URL check
  TestBuildLocatorBasic    — _build_locator() strategy dispatch (mocked ctx)
  TestBuildLocatorEdgeCases— None inputs, unknown strategy, exception fallback
  TestSafeCount            — _safe_count() wraps Playwright .count() safely
  TestResolveLocatorPrimary— resolve_locator() happy-path (primary found)
  TestResolveLocatorFallback— resolve_locator() falls through to fallback
  TestResolveLocatorAIPath  — resolve_locator() AI rediscovery branch
  TestResolveLocatorNone    — resolve_locator() all strategies fail → (None, reason)
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from executor import (
    _step_pass,
    _step_fail,
    _assert_pass,
    _assert_fail,
    FailureCategory,
    _trigger_label,
    _is_signal_failure,
    _detect_unknown_state,
)
from probe import _build_locator, _safe_count, resolve_locator


def _run(coro):
    return asyncio.run(coro)


# ── Fixtures ──────────────────────────────────────────────────────────

_CLICK_STEP = {
    "action": "click",
    "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
}

_FILL_STEP = {
    "action": "fill",
    "selector": {"strategy": "testid", "value": "email"},
    "value": "user@test.com",
}

_NAV_STEP = {
    "action": "navigate",
    "url": "https://app.com/login",
}

_ROLE_SEL  = {"strategy": "role",  "value": {"role": "button", "name": "Login"}}
_TESTID_SEL = {"strategy": "testid", "value": "submit-btn"}
_LABEL_SEL  = {"strategy": "label", "value": "Email Address"}


# ═══════════════════════════════════════════════════════════════════════
# Suite 1 — Result builders
# ═══════════════════════════════════════════════════════════════════════

class TestResultBuilders(unittest.TestCase):
    """_step_pass(), _step_fail(), _assert_pass(), _assert_fail()."""

    # _step_pass ─────────────────────────────────────────────────────

    def test_step_pass_status_is_pass(self):
        r = _step_pass(_CLICK_STEP)
        self.assertEqual(r["status"], "pass")

    def test_step_pass_action_copied(self):
        r = _step_pass(_CLICK_STEP)
        self.assertEqual(r["action"], "click")

    def test_step_pass_selector_copied(self):
        r = _step_pass(_CLICK_STEP)
        self.assertEqual(r["selector"], _CLICK_STEP["selector"])

    def test_step_pass_detail_stored(self):
        r = _step_pass(_CLICK_STEP, detail="clicked button")
        self.assertEqual(r["detail"], "clicked button")

    def test_step_pass_strategy_default_page(self):
        r = _step_pass(_CLICK_STEP)
        self.assertEqual(r["strategy"], "page")

    def test_step_pass_strategy_override(self):
        r = _step_pass(_CLICK_STEP, strategy="role")
        self.assertEqual(r["strategy"], "role")

    def test_step_pass_nav_step_action(self):
        r = _step_pass(_NAV_STEP)
        self.assertEqual(r["action"], "navigate")

    # _step_fail ─────────────────────────────────────────────────────

    def test_step_fail_status_is_fail(self):
        r = _step_fail(_CLICK_STEP, reason="not found")
        self.assertEqual(r["status"], "fail")

    def test_step_fail_action_copied(self):
        r = _step_fail(_CLICK_STEP, reason="err")
        self.assertEqual(r["action"], "click")

    def test_step_fail_reason_stored(self):
        r = _step_fail(_CLICK_STEP, reason="Element disappeared")
        self.assertEqual(r["reason"], "Element disappeared")

    def test_step_fail_default_category_unknown(self):
        r = _step_fail(_CLICK_STEP, reason="x")
        self.assertEqual(r["category"], "UNKNOWN")

    def test_step_fail_category_override(self):
        r = _step_fail(_CLICK_STEP, reason="x",
                       category=FailureCategory.SELECTOR_NOT_FOUND)
        self.assertEqual(r["category"], "SELECTOR_NOT_FOUND")

    def test_step_fail_screenshot_stored(self):
        r = _step_fail(_CLICK_STEP, reason="x", screenshot="path/to/ss.png")
        self.assertEqual(r["screenshot"], "path/to/ss.png")

    def test_step_fail_screenshot_none_by_default(self):
        r = _step_fail(_CLICK_STEP, reason="x")
        self.assertIsNone(r["screenshot"])

    def test_step_fail_selector_preserved(self):
        r = _step_fail(_CLICK_STEP, reason="x")
        self.assertIsNotNone(r["selector"])

    # _assert_pass ───────────────────────────────────────────────────

    def test_assert_pass_status(self):
        r = _assert_pass({"type": "url_contains", "value": "/dashboard"})
        self.assertEqual(r["status"], "pass")

    def test_assert_pass_type_copied(self):
        r = _assert_pass({"type": "element_visible", "selector": {}})
        self.assertEqual(r["type"], "element_visible")

    def test_assert_pass_actual_stored(self):
        r = _assert_pass({"type": "url_contains", "value": "/x"}, actual="https://app.com/x")
        self.assertEqual(r["actual"], "https://app.com/x")

    def test_assert_pass_no_actual_key_when_none(self):
        r = _assert_pass({"type": "element_visible"})
        self.assertNotIn("actual", r)

    # _assert_fail ───────────────────────────────────────────────────

    def test_assert_fail_status(self):
        r = _assert_fail({"type": "url_equals", "value": "x"}, reason="mismatch")
        self.assertEqual(r["status"], "fail")

    def test_assert_fail_reason_stored(self):
        r = _assert_fail({"type": "url_equals", "value": "x"}, reason="wrong url")
        self.assertEqual(r["reason"], "wrong url")

    def test_assert_fail_default_category(self):
        r = _assert_fail({"type": "url_equals", "value": "x"}, reason="x")
        self.assertEqual(r["category"], "ASSERTION_FAILED")

    def test_assert_fail_category_override(self):
        r = _assert_fail({"type": "url_equals", "value": "x"}, reason="x",
                         category="AUTH_REJECTED")
        self.assertEqual(r["category"], "AUTH_REJECTED")

    def test_assert_fail_actual_stored(self):
        r = _assert_fail({"type": "url_contains", "value": "/x"},
                         reason="not found", actual="https://app.com/other")
        self.assertEqual(r["actual"], "https://app.com/other")


# ═══════════════════════════════════════════════════════════════════════
# Suite 2 — FailureCategory constants
# ═══════════════════════════════════════════════════════════════════════

class TestFailureCategory(unittest.TestCase):
    """Verify every category constant exists and has the right string value."""

    def test_auth_rejected(self):
        self.assertEqual(FailureCategory.AUTH_REJECTED, "AUTH_REJECTED")

    def test_selector_not_found(self):
        self.assertEqual(FailureCategory.SELECTOR_NOT_FOUND, "SELECTOR_NOT_FOUND")

    def test_semantic_mismatch(self):
        self.assertEqual(FailureCategory.SEMANTIC_MISMATCH, "SEMANTIC_MISMATCH")

    def test_nav_timeout(self):
        self.assertEqual(FailureCategory.NAV_TIMEOUT, "NAV_TIMEOUT")

    def test_assertion_failed(self):
        self.assertEqual(FailureCategory.ASSERTION_FAILED, "ASSERTION_FAILED")

    def test_flow_incomplete(self):
        self.assertEqual(FailureCategory.FLOW_INCOMPLETE, "FLOW_INCOMPLETE")

    def test_unknown(self):
        self.assertEqual(FailureCategory.UNKNOWN, "UNKNOWN")

    def test_all_seven_categories_present(self):
        cats = {
            FailureCategory.AUTH_REJECTED,
            FailureCategory.SELECTOR_NOT_FOUND,
            FailureCategory.SEMANTIC_MISMATCH,
            FailureCategory.NAV_TIMEOUT,
            FailureCategory.ASSERTION_FAILED,
            FailureCategory.FLOW_INCOMPLETE,
            FailureCategory.UNKNOWN,
        }
        self.assertEqual(len(cats), 7)


# ═══════════════════════════════════════════════════════════════════════
# Suite 3 — _trigger_label()
# ═══════════════════════════════════════════════════════════════════════

class TestTriggerLabel(unittest.TestCase):
    """_trigger_label() derives a human-readable label from a step dict."""

    def test_role_selector_returns_name(self):
        step = {"action": "click",
                "selector": {"strategy": "role",
                             "value": {"role": "button", "name": "Submit"}}}
        self.assertEqual(_trigger_label(step), "Submit")

    def test_role_with_no_name_returns_role(self):
        step = {"action": "click",
                "selector": {"strategy": "role",
                             "value": {"role": "button"}}}
        self.assertEqual(_trigger_label(step), "button")

    def test_testid_selector_returns_value_string(self):
        step = {"action": "click",
                "selector": {"strategy": "testid", "value": "submit-btn"}}
        self.assertEqual(_trigger_label(step), "submit-btn")

    def test_navigate_returns_url(self):
        step = {"action": "navigate", "url": "https://app.com/login"}
        self.assertEqual(_trigger_label(step), "https://app.com/login")

    def test_fill_returns_value(self):
        step = {"action": "fill",
                "selector": {"strategy": "testid", "value": "email"},
                "value": "user@test.com"}
        # selector value is a string (not dict) — returned as label
        self.assertEqual(_trigger_label(step), "email")

    def test_empty_step_returns_empty_string(self):
        result = _trigger_label({})
        self.assertIsInstance(result, str)

    def test_no_selector_uses_value_field(self):
        step = {"action": "fill", "value": "hello"}
        self.assertEqual(_trigger_label(step), "hello")


# ═══════════════════════════════════════════════════════════════════════
# Suite 4 — _is_signal_failure()
# ═══════════════════════════════════════════════════════════════════════

class TestSignalFailure(unittest.TestCase):
    """_is_signal_failure() should suppress analytics/CDN noise."""

    def test_same_origin_is_signal(self):
        self.assertTrue(_is_signal_failure(
            "https://app.com/api/v1/users", "app.com"))

    def test_google_analytics_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://www.google-analytics.com/collect", "app.com"))

    def test_googletagmanager_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://www.googletagmanager.com/gtag/js", "app.com"))

    def test_png_image_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://app.com/images/logo.png", "app.com"))

    def test_woff_font_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://fonts.gstatic.com/s/roboto.woff2", "app.com"))

    def test_css_file_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://cdn.example.com/styles/main.css", "app.com"))

    def test_api_endpoint_is_signal(self):
        self.assertTrue(_is_signal_failure(
            "https://api.myapp.com/v2/auth/login", "myapp.com"))

    def test_hotjar_suppressed(self):
        # noise domain "hotjar.com" must appear in the netloc substring
        self.assertFalse(_is_signal_failure(
            "https://script.hotjar.com/api/v2/track", "app.com"))

    def test_sentry_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://o12345.ingest.sentry.io/api/...", "app.com"))

    def test_svg_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://app.com/icons/icon.svg", "app.com"))

    def test_map_file_suppressed(self):
        self.assertFalse(_is_signal_failure(
            "https://app.com/static/bundle.js.map", "app.com"))


# ═══════════════════════════════════════════════════════════════════════
# Suite 5 — _detect_unknown_state()
# ═══════════════════════════════════════════════════════════════════════

class TestDetectUnknownState(unittest.TestCase):
    """URL-based unknown-state detection via DB lookup."""

    def _db_with(self, locators):
        """Return a mock LocatorDB whose get_all() returns locators."""
        db = MagicMock()
        db.get_all = MagicMock(return_value=locators)
        return db

    def test_unknown_when_no_locators(self):
        db = self._db_with([])
        self.assertTrue(_detect_unknown_state(db, "https://app.com/new", ""))

    def test_known_when_locators_exist(self):
        db = self._db_with([{"role": "button", "name": "Submit"}])
        self.assertFalse(_detect_unknown_state(db, "https://app.com/login", ""))

    def test_db_called_with_correct_url(self):
        db = self._db_with([])
        _detect_unknown_state(db, "https://app.com/page", "hash123")
        db.get_all.assert_called_once_with("https://app.com/page", valid_only=True)

    def test_dom_hash_not_used_in_detection(self):
        """dom_hash argument is accepted but should not affect the result."""
        db = self._db_with([{"role": "button"}])
        # Even with a non-matching hash, known URL → not unknown
        self.assertFalse(_detect_unknown_state(db, "https://app.com/x", "different_hash"))

    def test_multiple_locators_returns_known(self):
        db = self._db_with([
            {"role": "button", "name": "Login"},
            {"role": "textbox", "name": "Email"},
        ])
        self.assertFalse(_detect_unknown_state(db, "https://app.com/login", ""))


# ═══════════════════════════════════════════════════════════════════════
# Suite 6 — _build_locator() — strategy dispatch
# ═══════════════════════════════════════════════════════════════════════

class TestBuildLocatorBasic(unittest.TestCase):
    """_build_locator() dispatches to the correct Playwright ctx method."""

    def _ctx(self):
        """Build a mock Playwright context with all locator methods."""
        ctx = MagicMock()
        ctx.get_by_test_id = MagicMock(return_value=MagicMock())
        ctx.get_by_role    = MagicMock(return_value=MagicMock())
        ctx.get_by_label   = MagicMock(return_value=MagicMock())
        ctx.get_by_placeholder = MagicMock(return_value=MagicMock())
        ctx.get_by_text    = MagicMock(return_value=MagicMock())
        ctx.get_by_alt_text = MagicMock(return_value=MagicMock())
        ctx.locator        = MagicMock(return_value=MagicMock())
        # OR-locator chaining (.or_() returns a new locator)
        for attr in ("get_by_test_id", "get_by_role", "get_by_label",
                     "get_by_placeholder", "get_by_text", "get_by_alt_text",
                     "locator"):
            mock_loc = getattr(ctx, attr).return_value
            mock_loc.or_ = MagicMock(return_value=mock_loc)
            mock_loc.nth = MagicMock(return_value=mock_loc)
        return ctx

    def test_testid_calls_get_by_test_id(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "testid", "value": "submit-btn"})
        ctx.get_by_test_id.assert_called_once_with("submit-btn")

    def test_testid_returns_or_locator(self):
        ctx = self._ctx()
        result = _build_locator(ctx, {"strategy": "testid", "value": "email"})
        self.assertIsNotNone(result)

    def test_role_calls_get_by_role_with_name(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "role",
                             "value": {"role": "button", "name": "Submit"}})
        ctx.get_by_role.assert_called_once_with("button", name="Submit")

    def test_role_without_name_calls_get_by_role_no_name(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "role",
                             "value": {"role": "heading"}})
        ctx.get_by_role.assert_called_once_with("heading")

    def test_label_calls_get_by_label(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "label", "value": "Email"})
        ctx.get_by_label.assert_called_once_with("Email")

    def test_placeholder_calls_get_by_placeholder(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "placeholder",
                             "value": "Enter email..."})
        ctx.get_by_placeholder.assert_called_once_with("Enter email...")

    def test_text_calls_get_by_text(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "text", "value": "Sign Up"})
        ctx.get_by_text.assert_called_once_with("Sign Up")

    def test_alt_text_calls_get_by_alt_text(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "alt_text", "value": "Logo"})
        ctx.get_by_alt_text.assert_called_once_with("Logo")

    def test_aria_label_calls_locator(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "aria-label", "value": "Close"})
        ctx.locator.assert_called_with('[aria-label="Close"]')

    def test_css_calls_locator_directly(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "css", "value": ".submit-btn"})
        ctx.locator.assert_called_with(".submit-btn")

    def test_id_prepends_hash(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "id", "value": "login-btn"})
        ctx.locator.assert_called_with("#login-btn")

    def test_xpath_prepends_xpath_prefix(self):
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "xpath",
                             "value": "//button[@type='submit']"})
        ctx.locator.assert_called_with("xpath=//button[@type='submit']")

    def test_aria_role_strategy_normalised_to_role(self):
        """AI sometimes uses a role name (e.g. 'button') as the strategy."""
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "button",
                             "value": {"role": "button", "name": "OK"}})
        ctx.get_by_role.assert_called()

    def test_testid_dict_value_extracted(self):
        """testid strategy accepts {testid: '...'} as value."""
        ctx = self._ctx()
        _build_locator(ctx, {"strategy": "testid",
                             "value": {"testid": "my-widget"}})
        ctx.get_by_test_id.assert_called_once_with("my-widget")


# ═══════════════════════════════════════════════════════════════════════
# Suite 7 — _build_locator() edge cases / None handling
# ═══════════════════════════════════════════════════════════════════════

class TestBuildLocatorEdgeCases(unittest.TestCase):

    def _ctx(self):
        ctx = MagicMock()
        ctx.get_by_test_id = MagicMock(return_value=MagicMock())
        ctx.get_by_role    = MagicMock(return_value=MagicMock())
        ctx.locator        = MagicMock(return_value=MagicMock())
        for attr in ("get_by_test_id", "get_by_role", "locator"):
            mock_loc = getattr(ctx, attr).return_value
            mock_loc.or_ = MagicMock(return_value=mock_loc)
            mock_loc.nth = MagicMock(return_value=mock_loc)
        return ctx

    def test_none_selector_returns_none(self):
        ctx = self._ctx()
        self.assertIsNone(_build_locator(ctx, None))

    def test_empty_selector_returns_none(self):
        ctx = self._ctx()
        self.assertIsNone(_build_locator(ctx, {}))

    def test_missing_value_returns_none(self):
        ctx = self._ctx()
        self.assertIsNone(_build_locator(ctx, {"strategy": "testid"}))

    def test_unknown_strategy_returns_none(self):
        ctx = self._ctx()
        result = _build_locator(ctx, {"strategy": "magic_selector", "value": "x"})
        self.assertIsNone(result)

    def test_exception_in_ctx_returns_none(self):
        ctx = MagicMock()
        ctx.get_by_test_id = MagicMock(side_effect=Exception("Playwright crash"))
        ctx.locator        = MagicMock(side_effect=Exception("Playwright crash"))
        ctx.get_by_role    = MagicMock(side_effect=Exception("Playwright crash"))
        # OR-locator method also fails
        result = _build_locator(ctx, {"strategy": "testid", "value": "btn"})
        # Should catch and return None
        self.assertIsNone(result)

    def test_testid_prefix_returns_nth(self):
        ctx = self._ctx()
        result = _build_locator(ctx, {
            "strategy": "testid_prefix",
            "value": {"prefix": "product-card-", "index": 2},
        })
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════════════
# Suite 8 — _safe_count()
# ═══════════════════════════════════════════════════════════════════════

class TestSafeCount(unittest.TestCase):
    """_safe_count wraps locator.count() and returns 0 on any error."""

    def test_returns_count_on_success(self):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=3)
        result = _run(_safe_count(loc))
        self.assertEqual(result, 3)

    def test_returns_zero_on_exception(self):
        loc = MagicMock()
        loc.count = AsyncMock(side_effect=Exception("selector invalid"))
        result = _run(_safe_count(loc))
        self.assertEqual(result, 0)

    def test_returns_zero_when_count_is_zero(self):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        result = _run(_safe_count(loc))
        self.assertEqual(result, 0)

    def test_returns_one(self):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        result = _run(_safe_count(loc))
        self.assertEqual(result, 1)


# ═══════════════════════════════════════════════════════════════════════
# Suite 9 — resolve_locator() happy path (primary found)
# ═══════════════════════════════════════════════════════════════════════

def _make_page_with_locator(count=1):
    """Return a mock Page whose locators return the given count."""
    mock_loc = MagicMock()
    mock_loc.count = AsyncMock(return_value=count)
    mock_loc.first = mock_loc
    mock_loc.wait_for = AsyncMock()
    mock_loc.or_ = MagicMock(return_value=mock_loc)
    mock_loc.nth = MagicMock(return_value=mock_loc)

    page = MagicMock()
    page.frames = []
    page.get_by_test_id     = MagicMock(return_value=mock_loc)
    page.get_by_role        = MagicMock(return_value=mock_loc)
    page.get_by_label       = MagicMock(return_value=mock_loc)
    page.get_by_placeholder = MagicMock(return_value=mock_loc)
    page.get_by_text        = MagicMock(return_value=mock_loc)
    page.get_by_alt_text    = MagicMock(return_value=mock_loc)
    page.locator            = MagicMock(return_value=mock_loc)
    return page, mock_loc


def _make_db(locators=None):
    db = MagicMock()
    db.get_by_id  = MagicMock(return_value=None)  # no element_id chain
    db.get_all    = MagicMock(return_value=locators or [])
    db.mark_unique = MagicMock()
    return db


class TestResolveLocatorPrimary(unittest.TestCase):

    def test_primary_testid_found_returns_locator(self):
        page, loc = _make_page_with_locator(count=1)
        db = _make_db()
        result, strategy = _run(resolve_locator(
            page, _TESTID_SEL, None, db, "https://app.com"
        ))
        self.assertIsNotNone(result)

    def test_primary_testid_strategy_in_result(self):
        page, loc = _make_page_with_locator(count=1)
        db = _make_db()
        result, strategy = _run(resolve_locator(
            page, _TESTID_SEL, None, db, "https://app.com"
        ))
        self.assertIn("testid", strategy)

    def test_primary_role_found_returns_locator(self):
        page, loc = _make_page_with_locator(count=1)
        db = _make_db()
        result, strategy = _run(resolve_locator(
            page, _ROLE_SEL, None, db, "https://app.com"
        ))
        self.assertIsNotNone(result)

    def test_multiple_matches_returns_first(self):
        """count > 1 → returns loc.first with '(first of N)' strategy."""
        page, loc = _make_page_with_locator(count=3)
        db = _make_db()
        result, strategy = _run(resolve_locator(
            page, _ROLE_SEL, None, db, "https://app.com"
        ))
        self.assertIsNotNone(result)
        self.assertIn("first of 3", strategy)

    def test_unique_element_marks_db(self):
        page, _ = _make_page_with_locator(count=1)
        sel = {**_TESTID_SEL, "element_id": "el-001"}
        db = _make_db()
        _run(resolve_locator(page, sel, None, db, "https://app.com"))
        db.mark_unique.assert_called_with("el-001", True)

    def test_non_unique_marks_db_false(self):
        page, _ = _make_page_with_locator(count=3)
        sel = {**_TESTID_SEL, "element_id": "el-002"}
        db = _make_db()
        _run(resolve_locator(page, sel, None, db, "https://app.com"))
        db.mark_unique.assert_called_with("el-002", False)


# ═══════════════════════════════════════════════════════════════════════
# Suite 10 — resolve_locator() fallback path
# ═══════════════════════════════════════════════════════════════════════

class TestResolveLocatorFallback(unittest.TestCase):

    def test_fallback_used_when_primary_not_found(self):
        """Primary count=0 → tries fallback → fallback count=1 → returns it."""
        call_count = {"n": 0}

        fallback_loc = MagicMock()
        fallback_loc.count  = AsyncMock(return_value=1)
        fallback_loc.first  = fallback_loc
        fallback_loc.wait_for = AsyncMock()
        fallback_loc.or_    = MagicMock(return_value=fallback_loc)
        fallback_loc.nth    = MagicMock(return_value=fallback_loc)

        primary_loc = MagicMock()
        primary_loc.count   = AsyncMock(return_value=0)
        primary_loc.first   = primary_loc
        primary_loc.wait_for = AsyncMock()
        primary_loc.or_     = MagicMock(return_value=primary_loc)
        primary_loc.nth     = MagicMock(return_value=primary_loc)

        page = MagicMock()
        page.frames = []

        def locator_side_effect(css_str):
            return fallback_loc

        page.get_by_test_id     = MagicMock(return_value=primary_loc)
        page.get_by_role        = MagicMock(return_value=primary_loc)
        page.get_by_label       = MagicMock(return_value=fallback_loc)
        page.get_by_placeholder = MagicMock(return_value=primary_loc)
        page.get_by_text        = MagicMock(return_value=primary_loc)
        page.get_by_alt_text    = MagicMock(return_value=primary_loc)
        page.locator            = MagicMock(return_value=primary_loc)

        db = _make_db()

        primary_sel  = {"strategy": "testid", "value": "missing-btn"}
        fallback_sel = {"strategy": "label", "value": "Submit"}

        result, strategy = _run(resolve_locator(
            page, primary_sel, fallback_sel, db, "https://app.com"
        ))
        self.assertIsNotNone(result)
        self.assertIn("fallback", strategy)

    def test_fallback_strategy_in_result(self):
        """fallback strategy name should appear in the strategy string."""
        # Build two completely separate locator mocks so primary=0 and fallback=1
        primary_loc = MagicMock()
        primary_loc.count    = AsyncMock(return_value=0)
        primary_loc.first    = primary_loc
        primary_loc.wait_for = AsyncMock()
        primary_loc.or_      = MagicMock(return_value=primary_loc)
        primary_loc.nth      = MagicMock(return_value=primary_loc)

        fallback_loc = MagicMock()
        fallback_loc.count    = AsyncMock(return_value=1)
        fallback_loc.first    = fallback_loc
        fallback_loc.wait_for = AsyncMock()
        fallback_loc.or_      = MagicMock(return_value=fallback_loc)
        fallback_loc.nth      = MagicMock(return_value=fallback_loc)

        page = MagicMock()
        page.frames             = []
        page.get_by_test_id     = MagicMock(return_value=primary_loc)
        page.get_by_role        = MagicMock(return_value=primary_loc)
        page.get_by_label       = MagicMock(return_value=fallback_loc)
        page.get_by_placeholder = MagicMock(return_value=primary_loc)
        page.get_by_text        = MagicMock(return_value=primary_loc)
        page.get_by_alt_text    = MagicMock(return_value=primary_loc)
        page.locator            = MagicMock(return_value=primary_loc)
        db = _make_db()

        primary_sel  = {"strategy": "testid", "value": "no-such-btn"}
        fallback_sel = {"strategy": "label", "value": "Submit"}
        _, strategy = _run(resolve_locator(
            page, primary_sel, fallback_sel, db, "https://app.com"
        ))
        self.assertIn("fallback", strategy)


# ═══════════════════════════════════════════════════════════════════════
# Suite 11 — resolve_locator() AI rediscovery path
# ═══════════════════════════════════════════════════════════════════════

class TestResolveLocatorAIPath(unittest.TestCase):

    def test_ai_rediscovery_called_when_primary_and_fallback_fail(self):
        """When all selectors return 0 and AI_REDISCOVERY=True, _ai_rediscover is called."""
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        ai_loc = MagicMock()
        ai_mock_client = MagicMock()

        with patch("probe.AI_REDISCOVERY", True), \
             patch("probe._ai_rediscover", new=AsyncMock(return_value=(ai_loc, "ai:rediscovered"))):
            result, strategy = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com",
                ai_client=ai_mock_client,
            ))
        self.assertIs(result, ai_loc)
        self.assertEqual(strategy, "ai:rediscovered")

    def test_ai_rediscovery_skipped_when_disabled(self):
        """With AI_REDISCOVERY=False, _ai_rediscover should NOT be called."""
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False), \
             patch("probe._ai_rediscover", new=AsyncMock(return_value=(MagicMock(), "ai"))) as mock_ai:
            result, _ = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com",
                ai_client=MagicMock(),
            ))
        mock_ai.assert_not_called()

    def test_ai_rediscovery_skipped_when_no_client(self):
        """No ai_client → AI path skipped regardless of AI_REDISCOVERY flag."""
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", True), \
             patch("probe._ai_rediscover", new=AsyncMock(return_value=(MagicMock(), "ai"))) as mock_ai:
            _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com",
                ai_client=None,
            ))
        mock_ai.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Suite 12 — resolve_locator() → (None, reason) when everything fails
# ═══════════════════════════════════════════════════════════════════════

class TestResolveLocatorNone(unittest.TestCase):

    def test_returns_none_when_all_fail(self):
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False):
            result, reason = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com"
            ))
        self.assertIsNone(result)

    def test_failure_reason_contains_strategy(self):
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False):
            _, reason = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com"
            ))
        self.assertIn("testid", reason)

    def test_failure_reason_contains_value(self):
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False):
            _, reason = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com"
            ))
        self.assertIn("submit-btn", reason)

    def test_empty_dict_selector_returns_none(self):
        """An empty selector dict (no strategy/value) cannot find any element."""
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False):
            result, _ = _run(resolve_locator(
                page, {}, None, db, "https://app.com"
            ))
        self.assertIsNone(result)

    def test_returns_string_reason(self):
        page, _ = _make_page_with_locator(count=0)
        db = _make_db()

        with patch("probe.AI_REDISCOVERY", False):
            result, reason = _run(resolve_locator(
                page, _TESTID_SEL, None, db, "https://app.com"
            ))
        self.assertIsInstance(reason, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
