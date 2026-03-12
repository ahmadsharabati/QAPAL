"""
test_unit.py — QAPal unit tests
================================
No network. No tinydb. No playwright. No AI keys needed.
Only stdlib + the pure functions in each module.

Run:
    cd /path/to/qapal
    python tests/test_unit.py
    python tests/test_unit.py -v
"""

import json
import os
import re
import sys
import types
import unittest


# ── Inject mocks BEFORE any qapal imports ────────────────────────────

def _mock_tinydb():
    class _Table:
        def __init__(self): self._data = {}

        def all(self):              return list(self._data.values())

        def get(self, cond):        return None  # tests use pure helpers only

        def insert(self, doc):      self._data[id(doc)] = doc

        def update(self, *a, **k):  pass

        def remove(self, *a, **k):  return []

        def truncate(self):         self._data.clear()

    class _TinyDB:
        def __init__(self, *a, **k): self._tables = {}

        def table(self, name):
            if name not in self._tables:
                self._tables[name] = _Table()
            return self._tables[name]

        def close(self): pass

    tinydb = types.ModuleType("tinydb")
    tinydb.TinyDB = _TinyDB
    tinydb.Query = lambda: types.SimpleNamespace()
    mw = types.ModuleType("tinydb.middlewares")
    mw.CachingMiddleware = lambda cls: cls
    st = types.ModuleType("tinydb.storages")
    st.JSONStorage = object
    sys.modules["tinydb"] = tinydb
    sys.modules["tinydb.middlewares"] = mw
    sys.modules["tinydb.storages"] = st


def _mock_playwright():
    pw = types.ModuleType("playwright")
    pa = types.ModuleType("playwright.async_api")

    class _PWError(Exception): pass

    pa.Error = _PWError
    for name in ("Browser", "BrowserContext", "Frame", "Locator", "Page",
                 "async_playwright"):
        setattr(pa, name, type(name, (), {}))

    sys.modules["playwright"] = pw
    sys.modules["playwright.async_api"] = pa


def _mock_anthropic_openai():
    for name in ("anthropic", "openai"):
        sys.modules[name] = types.ModuleType(name)


_mock_tinydb()
_mock_playwright()
_mock_anthropic_openai()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Now safe to import ────────────────────────────────────────────────

from locator_db import (
    _normalise_name,
    _name_pattern,
    _make_id,
    _build_chain,
    _make_frame,
    _normalize_url,
)
from executor import _step_pass, _step_fail, _assert_pass, _assert_fail, _build_locator
from planner import _format_locators, _format_steps, _format_assertions, _parse_plan
from ai_client import AIClient, _AnthropicClient, _OpenAIClient


# ══════════════════════════════════════════════════════════════════════
# _normalize_url
# ══════════════════════════════════════════════════════════════════════

class TestNormalizeUrl(unittest.TestCase):

    def test_strips_query(self):
        self.assertEqual(_normalize_url("https://app.com/page?tab=1"), "https://app.com/page")

    def test_strips_fragment(self):
        self.assertEqual(_normalize_url("https://app.com/page#top"), "https://app.com/page")

    def test_strips_both(self):
        self.assertEqual(_normalize_url("https://app.com/page?q=1#top"), "https://app.com/page")

    def test_preserves_path(self):
        self.assertEqual(_normalize_url("https://app.com/a/b/c"), "https://app.com/a/b/c")

    def test_empty_string(self):
        self.assertEqual(_normalize_url(""), "")


# ══════════════════════════════════════════════════════════════════════
# _normalise_name
# ══════════════════════════════════════════════════════════════════════

class TestNormaliseName(unittest.TestCase):

    def test_counter_stripped(self):
        self.assertEqual(_normalise_name("Cart (3)"), "Cart")
        self.assertEqual(_normalise_name("Cart (10)"), "Cart")

    def test_order_id_stripped(self):
        self.assertEqual(_normalise_name("Order #1234"), "Order")

    def test_date_stripped(self):
        self.assertEqual(_normalise_name("Report 2024-03-04"), "Report")

    def test_today_stripped(self):
        self.assertNotIn("Today", _normalise_name("Today's tasks"))

    def test_result_count_stripped(self):
        self.assertEqual(_normalise_name("12 results"), "")
        self.assertEqual(_normalise_name("5 items"), "")

    def test_price_stripped(self):
        self.assertEqual(_normalise_name("Pay $99.99"), "Pay")

    def test_stable_name_unchanged(self):
        self.assertEqual(_normalise_name("Submit"), "Submit")
        self.assertEqual(_normalise_name("Email address"), "Email address")

    def test_empty(self):
        self.assertEqual(_normalise_name(""), "")

    def test_result_is_trimmed(self):
        result = _normalise_name("Cart (3)")
        self.assertEqual(result, result.strip())


# ══════════════════════════════════════════════════════════════════════
# _name_pattern
# ══════════════════════════════════════════════════════════════════════

class TestNamePattern(unittest.TestCase):

    def test_returns_none_for_stable_name(self):
        self.assertIsNone(_name_pattern("Submit"))
        self.assertIsNone(_name_pattern("Email address"))

    def test_counter_pattern_matches_other_counts(self):
        p = _name_pattern("Cart (3)")
        self.assertIsNotNone(p)
        self.assertTrue(re.search(p, "Cart (4)"))
        self.assertTrue(re.search(p, "Cart (999)"))

    def test_order_id_pattern_matches_other_ids(self):
        p = _name_pattern("Order #1234")
        self.assertIsNotNone(p)
        self.assertTrue(re.search(p, "Order #5678"))

    def test_empty_returns_none(self):
        self.assertIsNone(_name_pattern(""))

    def test_fully_dynamic_name_returns_none(self):
        # "12 results" strips to "" — no stable anchor
        self.assertIsNone(_name_pattern("12 results"))


# ══════════════════════════════════════════════════════════════════════
# _make_id
# ══════════════════════════════════════════════════════════════════════

class TestMakeId(unittest.TestCase):

    def _id(self, url="https://x.com", role="button", name="Save",
            container="", frame="main", dom=""):
        return _make_id(url, role, name, container, frame, dom)

    def test_deterministic(self):
        self.assertEqual(self._id(), self._id())

    def test_dynamic_name_same_key(self):
        """Cart (3) and Cart (4) must map to the same document."""
        id3 = _make_id("https://x.com", "button", "Cart (3)", "", "main", "")
        id4 = _make_id("https://x.com", "button", "Cart (4)", "", "main", "")
        self.assertEqual(id3, id4)

    def test_dom_path_disambiguates_repeated_elements(self):
        id1 = _make_id("https://x.com", "button", "Delete", "", "main", "tr:nth(1)>td")
        id2 = _make_id("https://x.com", "button", "Delete", "", "main", "tr:nth(2)>td")
        self.assertNotEqual(id1, id2)

    def test_different_url_different_id(self):
        self.assertNotEqual(self._id(url="https://a.com"), self._id(url="https://b.com"))

    def test_different_role_different_id(self):
        self.assertNotEqual(self._id(role="button"), self._id(role="textbox"))

    def test_different_container_different_id(self):
        self.assertNotEqual(self._id(container="dialog"), self._id(container="form"))

    def test_different_frame_different_id(self):
        self.assertNotEqual(self._id(frame="main"), self._id(frame="iframe1"))

    def test_output_is_16_char_hex(self):
        doc_id = self._id()
        self.assertEqual(len(doc_id), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in doc_id))


# ══════════════════════════════════════════════════════════════════════
# _build_chain
# ══════════════════════════════════════════════════════════════════════

class TestBuildChain(unittest.TestCase):

    def _el(self, **kw):
        base = {
            "role": "button", "name": "Save", "tag": "button",
            "testid": None, "ariaLabel": "", "placeholder": None,
            "loc": {}, "actionable": True,
        }
        base.update(kw)
        return base

    def test_testid_is_first(self):
        chain = _build_chain(self._el(testid="save-btn"), "form")
        self.assertEqual(chain[0]["strategy"], "testid")

    def test_testid_unique_is_true(self):
        chain = _build_chain(self._el(testid="save-btn"), "")
        self.assertTrue(chain[0]["unique"])

    def test_role_entry_unique_is_none(self):
        """Uniqueness must be unverified at crawl time — executor checks at runtime."""
        chain = _build_chain(self._el(), "")
        role_entries = [e for e in chain if e["strategy"] == "role"]
        self.assertGreater(len(role_entries), 0)
        for entry in role_entries:
            self.assertIsNone(entry["unique"], "role entries must start as unique=None")

    def test_role_plus_container_added_with_container(self):
        chain = _build_chain(self._el(), "dialog")
        strategies = [e["strategy"] for e in chain]
        self.assertIn("role+container", strategies)

    def test_no_role_plus_container_without_container(self):
        chain = _build_chain(self._el(), "")
        strategies = [e["strategy"] for e in chain]
        self.assertNotIn("role+container", strategies)

    def test_aria_label_included(self):
        chain = _build_chain(self._el(ariaLabel="Close dialog"), "")
        strategies = [e["strategy"] for e in chain]
        self.assertIn("aria-label", strategies)

    def test_placeholder_included_for_textbox(self):
        chain = _build_chain(self._el(role="textbox", name="", placeholder="Enter email"), "")
        strategies = [e["strategy"] for e in chain]
        self.assertIn("placeholder", strategies)

    def test_empty_element_returns_empty_chain(self):
        el = self._el(role="", name="", testid=None, ariaLabel="", placeholder=None)
        chain = _build_chain(el, "")
        self.assertEqual(chain, [])

    def test_css_fallback_unique_is_false(self):
        el = self._el(loc={"strategy": "css", "value": "form > button"})
        chain = _build_chain(el, "")
        css = [e for e in chain if e["strategy"] == "css"]
        for entry in css:
            self.assertFalse(entry["unique"])

    def test_testid_before_role_in_chain(self):
        chain = _build_chain(self._el(testid="btn"), "form")
        strategies = [e["strategy"] for e in chain]
        ti = strategies.index("testid")
        ri = strategies.index("role")
        self.assertLess(ti, ri)


# ══════════════════════════════════════════════════════════════════════
# _make_frame
# ══════════════════════════════════════════════════════════════════════

class TestMakeFrame(unittest.TestCase):

    def test_main(self):
        f = _make_frame({"frameId": "main"})
        self.assertEqual(f["type"], "main")
        self.assertEqual(f["url"], "main")
        self.assertFalse(f["cross_origin"])
        self.assertTrue(f["accessible"])

    def test_defaults_to_main_when_absent(self):
        f = _make_frame({})
        self.assertEqual(f["type"], "main")

    def test_accessible_iframe(self):
        f = _make_frame({"frameId": "https://widget.io", "frameName": "pay", "crossOrigin": False})
        self.assertEqual(f["type"], "iframe")
        self.assertEqual(f["url"], "https://widget.io")
        self.assertEqual(f["name"], "pay")
        self.assertTrue(f["accessible"])

    def test_cross_origin_not_accessible(self):
        f = _make_frame({"frameId": "https://other.com", "crossOrigin": True})
        self.assertTrue(f["cross_origin"])
        self.assertFalse(f["accessible"])


# ══════════════════════════════════════════════════════════════════════
# executor — result shape builders
# ══════════════════════════════════════════════════════════════════════

class TestStepResultBuilders(unittest.TestCase):

    def _step(self, action="click"):
        return {"action": action, "selector": None}

    def test_pass_has_correct_fields(self):
        r = _step_pass(self._step(), detail="done", strategy="role")
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["action"], "click")
        self.assertEqual(r["detail"], "done")
        self.assertEqual(r["strategy"], "role")

    def test_pass_empty_defaults(self):
        r = _step_pass(self._step())
        self.assertEqual(r["detail"], "")
        self.assertEqual(r["strategy"], "page")

    def test_fail_has_reason_and_screenshot(self):
        r = _step_fail(self._step(), reason="not found", screenshot="/tmp/x.png")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["reason"], "not found")
        self.assertEqual(r["screenshot"], "/tmp/x.png")

    def test_fail_screenshot_defaults_none(self):
        r = _step_fail(self._step(), reason="err")
        self.assertIsNone(r["screenshot"])


class TestAssertionResultBuilders(unittest.TestCase):

    def _a(self, atype="url_contains"):
        return {"type": atype}

    def test_assert_pass_with_actual(self):
        r = _assert_pass(self._a(), actual="/dashboard")
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["type"], "url_contains")
        self.assertEqual(r["actual"], "/dashboard")

    def test_assert_pass_without_actual(self):
        r = _assert_pass(self._a())
        self.assertNotIn("actual", r)

    def test_assert_fail_with_actual(self):
        r = _assert_fail(self._a("element_visible"), reason="timeout", actual=False)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["reason"], "timeout")
        self.assertEqual(r["actual"], False)

    def test_assert_fail_without_actual(self):
        r = _assert_fail(self._a(), reason="err")
        self.assertNotIn("actual", r)


# ══════════════════════════════════════════════════════════════════════
# executor — _build_locator dispatch (no browser call)
# ══════════════════════════════════════════════════════════════════════

class TestBuildLocatorDispatch(unittest.TestCase):
    """
    _build_locator picks the right Playwright method per strategy.
    We pass a spy context that records which method was called.
    """

    class _Spy:
        """Records which locator method was called and with what args."""

        def __init__(self):
            self.calls = []

        def _record(self, _method, *a, **k):
            self.calls.append((_method, a, k))
            return object()  # a non-None return so _build_locator succeeds

        def get_by_test_id(self, v):       return self._record("get_by_test_id", v)

        def get_by_role(self, r, **k):     return self._record("get_by_role", r, **k)

        def get_by_label(self, v):         return self._record("get_by_label", v)

        def get_by_placeholder(self, v):   return self._record("get_by_placeholder", v)

        def get_by_text(self, v):          return self._record("get_by_text", v)

        def get_by_alt_text(self, v):      return self._record("get_by_alt_text", v)

        def locator(self, v):              return self._record("locator", v)

    def _call(self, selector):
        spy = self._Spy()
        _build_locator(spy, selector)
        return spy.calls

    def test_testid_strategy(self):
        calls = self._call({"strategy": "testid", "value": "submit-btn"})
        self.assertEqual(calls[0][0], "get_by_test_id")

    def test_role_strategy(self):
        calls = self._call({"strategy": "role", "value": {"role": "button", "name": "OK"}})
        self.assertEqual(calls[0][0], "get_by_role")

    def test_label_strategy(self):
        calls = self._call({"strategy": "label", "value": "Email"})
        self.assertEqual(calls[0][0], "get_by_label")

    def test_placeholder_strategy(self):
        calls = self._call({"strategy": "placeholder", "value": "Enter email"})
        self.assertEqual(calls[0][0], "get_by_placeholder")

    def test_css_strategy(self):
        calls = self._call({"strategy": "css", "value": "form > button"})
        self.assertEqual(calls[0][0], "locator")

    def test_aria_label_strategy(self):
        calls = self._call({"strategy": "aria-label", "value": "Close"})
        self.assertEqual(calls[0][0], "locator")

    def test_missing_value_returns_none(self):
        result = _build_locator(self._Spy(), {"strategy": "testid"})  # no value key
        self.assertIsNone(result)

    def test_unknown_strategy_returns_none(self):
        result = _build_locator(self._Spy(), {"strategy": "magic", "value": "x"})
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════════════════
# ai_client — from_env factory
# ══════════════════════════════════════════════════════════════════════

class TestAIClientFromEnv(unittest.TestCase):
    _keys = (
        "QAPAL_AI_PROVIDER", "QAPAL_AI_MODEL", "QAPAL_AI_BASE_URL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
    )

    def setUp(self):
        for k in self._keys:
            os.environ.pop(k, None)

    tearDown = setUp

    def test_missing_anthropic_key_raises(self):
        os.environ["QAPAL_AI_PROVIDER"] = "anthropic"
        with self.assertRaises(EnvironmentError):
            AIClient.from_env()

    def test_missing_openai_key_raises(self):
        os.environ["QAPAL_AI_PROVIDER"] = "openai"
        with self.assertRaises(EnvironmentError):
            AIClient.from_env()

    def test_grok_without_key_returns_client_with_dummy_key(self):
        # grok always sets a base_url (api.x.ai/v1), so missing key is not caught
        # at factory time — the API call itself will fail. This is intentional.
        os.environ["QAPAL_AI_PROVIDER"] = "grok"
        client = AIClient.from_env()
        self.assertIsInstance(client, _OpenAIClient)
        self.assertEqual(client._api_key, "dummy")

    def test_unknown_provider_raises_value_error(self):
        os.environ["QAPAL_AI_PROVIDER"] = "gemini"
        with self.assertRaises(ValueError):
            AIClient.from_env()

    def test_anthropic_key_returns_anthropic_client(self):
        os.environ["QAPAL_AI_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.assertIsInstance(AIClient.from_env(), _AnthropicClient)

    def test_anthropic_default_model_contains_claude(self):
        os.environ["QAPAL_AI_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.assertIn("claude", AIClient.from_env().model)

    def test_openai_key_returns_openai_client(self):
        os.environ["QAPAL_AI_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertIsInstance(AIClient.from_env(), _OpenAIClient)

    def test_grok_returns_openai_client_with_xai_base_url(self):
        os.environ["QAPAL_AI_PROVIDER"] = "grok"
        os.environ["XAI_API_KEY"] = "xai-test"
        client = AIClient.from_env()
        self.assertIsInstance(client, _OpenAIClient)
        self.assertIn("x.ai", client._base_url)

    def test_custom_model_overrides_default(self):
        os.environ["QAPAL_AI_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        os.environ["QAPAL_AI_MODEL"] = "claude-opus-4"
        self.assertEqual(AIClient.from_env().model, "claude-opus-4")

    def test_custom_base_url_passed_through(self):
        os.environ["QAPAL_AI_PROVIDER"] = "openai"
        os.environ["QAPAL_AI_BASE_URL"] = "http://localhost:11434/v1"
        client = AIClient.from_env()
        self.assertIsInstance(client, _OpenAIClient)
        self.assertEqual(client._base_url, "http://localhost:11434/v1")

    def test_acomplete_is_awaitable(self):
        """acomplete must return a coroutine — not blow up before the API call."""
        os.environ["QAPAL_AI_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        client = AIClient.from_env()
        import inspect
        # Don't await it — just confirm it produces a coroutine
        coro = client.acomplete("test")
        self.assertTrue(inspect.iscoroutine(coro))
        coro.close()  # prevent ResourceWarning


# ══════════════════════════════════════════════════════════════════════
# planner — pure helpers
# ══════════════════════════════════════════════════════════════════════

class TestFormatLocators(unittest.TestCase):

    def _loc(self, eid, role, name, confidence="high", hits=1, chain=None):
        return {
            "id": eid,
            "identity": {
                "role": role, "name": name, "container": "",
                "tag": "button", "dom_path": "", "frame": {},
            },
            "locators": {
                "chain": chain or [{"strategy": "role",
                                    "value": {"role": role, "name": name},
                                    "unique": None}],
                "confidence": confidence,
                "actionable": True,
            },
            "history": {"hit_count": hits},
        }

    def test_empty_list_explains_next_step(self):
        self.assertIn("none", _format_locators([]).lower())

    def test_element_id_appears_in_output(self):
        self.assertIn("abc123", _format_locators([self._loc("abc123", "button", "Submit")]))

    def test_max_items_respected(self):
        locs = [self._loc(f"id{i}", "button", f"B{i}") for i in range(20)]
        result = _format_locators(locs, max_items=5)
        self.assertEqual(result.count("[id"), 5)

    def test_high_confidence_sorted_before_low(self):
        locs = [
            self._loc("low1", "button", "L", confidence="low", hits=100),
            self._loc("high1", "button", "H", confidence="high", hits=1),
        ]
        result = _format_locators(locs)
        self.assertLess(result.index("high1"), result.index("low1"))


class TestFormatSteps(unittest.TestCase):

    def test_navigate_shows_url(self):
        r = _format_steps([{"action": "navigate", "url": "https://app.com/login"}])
        self.assertIn("https://app.com/login", r)

    def test_fill_shows_value(self):
        r = _format_steps([{"action": "fill",
                            "target": {"role": "textbox", "name": "Email"},
                            "value": "user@test.com"}])
        self.assertIn("user@test.com", r)

    def test_empty_list_says_none(self):
        self.assertIn("none", _format_steps([]).lower())

    def test_all_common_actions_no_crash(self):
        steps = [
            {"action": "navigate", "url": "https://x.com"},
            {"action": "click", "target": {"role": "button", "name": "OK"}},
            {"action": "fill", "target": {"role": "textbox", "name": "Q"}, "value": "v"},
            {"action": "press", "target": {"role": "textbox", "name": "Q"}, "key": "Enter"},
            {"action": "select", "target": {"role": "combobox", "name": "C"}, "label": "UK"},
            {"action": "wait", "duration": 500},
            {"action": "check", "target": {"role": "checkbox", "name": "T"}},
            {"action": "hover", "target": {"role": "button", "name": "More"}},
            {"action": "scroll", "direction": "down"},
            {"action": "screenshot"},
        ]
        result = _format_steps(steps)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_steps_numbered(self):
        steps = [{"action": "navigate", "url": "https://x.com"},
                 {"action": "click", "target": {"role": "button", "name": "Go"}}]
        result = _format_steps(steps)
        self.assertIn("1.", result)
        self.assertIn("2.", result)


class TestFormatAssertions(unittest.TestCase):

    def test_empty(self):
        self.assertIn("generate", _format_assertions([]).lower())

    def test_url_contains(self):
        r = _format_assertions([{"type": "url_contains", "value": "/dashboard"}])
        self.assertIn("url_contains", r)
        self.assertIn("/dashboard", r)

    def test_element_assertion_includes_name(self):
        r = _format_assertions([{
            "type": "element_visible",
            "target": {"role": "button", "name": "Log Out"},
            "value": "",
        }])
        self.assertIn("Log Out", r)

    def test_all_common_types_no_crash(self):
        assertions = [
            {"type": "url_equals", "value": "https://x.com"},
            {"type": "title_contains", "value": "Dashboard"},
            {"type": "element_visible", "target": {"role": "button", "name": "Save"}, "value": ""},
            {"type": "element_contains_text", "target": {"role": "main", "name": ""}, "value": "Welcome"},
            {"type": "element_count", "target": {"role": "listitem", "name": ""}, "value": 3},
        ]
        result = _format_assertions(assertions)
        self.assertIsInstance(result, str)


class TestParsePlan(unittest.TestCase):

    def _valid(self, extra=None):
        d = {"test_id": "TC001", "steps": [], "assertions": []}
        if extra:
            d.update(extra)
        return json.dumps(d)

    def test_valid_json_parsed(self):
        plan = _parse_plan(self._valid(), "TC001", {})
        self.assertEqual(plan["test_id"], "TC001")

    def test_steps_and_assertions_preserved(self):
        raw = json.dumps({
            "test_id": "TC001",
            "steps": [{"action": "navigate", "url": "https://x.com"}],
            "assertions": [{"type": "url_contains", "value": "x.com"}],
        })
        plan = _parse_plan(raw, "TC001", {})
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(len(plan["assertions"]), 1)

    def test_strips_markdown_fences(self):
        raw = "```json\n" + self._valid() + "\n```"
        plan = _parse_plan(raw, "TC001", {})
        self.assertEqual(plan["test_id"], "TC001")

    def test_strips_markdown_fences_no_language(self):
        raw = "```\n" + self._valid() + "\n```"
        plan = _parse_plan(raw, "TC001", {})
        self.assertEqual(plan["test_id"], "TC001")

    def test_invented_element_id_flagged(self):
        raw = json.dumps({
            "test_id": "TC001",
            "steps": [{"action": "click",
                       "selector": {"strategy": "role", "value": {}},
                       "element_id": "made_up_id"}],
            "assertions": [],
        })
        plan = _parse_plan(raw, "TC001", locator_map={})
        self.assertTrue(plan["steps"][0].get("_invalid_element_id"))
        self.assertTrue(plan["steps"][0].get("_needs_review"))

    def test_known_element_id_not_flagged(self):
        raw = json.dumps({
            "test_id": "TC001",
            "steps": [{"action": "click",
                       "selector": {"strategy": "role", "value": {}},
                       "element_id": "real_id"}],
            "assertions": [],
        })
        plan = _parse_plan(raw, "TC001", locator_map={"real_id": {}})
        self.assertNotIn("_invalid_element_id", plan["steps"][0])

    def test_missing_steps_defaults_to_empty_list(self):
        plan = _parse_plan('{"test_id":"TC001","assertions":[]}', "TC001", {})
        self.assertEqual(plan["steps"], [])

    def test_missing_assertions_defaults_to_empty_list(self):
        plan = _parse_plan('{"test_id":"TC001","steps":[]}', "TC001", {})
        self.assertEqual(plan["assertions"], [])

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            _parse_plan("this is not json", "TC001", {})

    def test_test_id_injected_when_missing(self):
        plan = _parse_plan('{"steps":[],"assertions":[]}', "TC999", {})
        self.assertEqual(plan["test_id"], "TC999")


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
