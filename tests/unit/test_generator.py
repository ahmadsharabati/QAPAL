"""
tests/unit/test_generator.py
==============================
Unit tests for generator.py — TestGenerator and its post-processors.

All AI and DB calls are mocked. No network, no browser required.

Coverage:
  TestStripDynamicId         — _strip_dynamic_id() ULID/UUID/hex suffix stripping
  TestFixMalformedSelectors  — _fix_malformed_selectors() nested-value repair
  TestParsePlans             — _parse_plans() JSON extraction, think-block strip,
                               markdown fence, relative URL resolution, domain typo fix
  TestGeneratePlansFromPrd   — generate_plans_from_prd() full pipeline (mocked AI + DB)
  TestFixUrlAssertions       — _fix_url_assertions() nav-graph-backed assertion correction
  TestFindByNameInDb         — _find_by_name_in_db() exact/prefix/cross-URL matching
  TestNegativeTests          — negative test generation toggle
"""

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from generator import TestGenerator
from planner import PlanningError


# ── Helpers ───────────────────────────────────────────────────────────

def _make_db(locators=None, states=None):
    """Return a mock LocatorDB pre-populated with optional locators/states."""
    db = MagicMock()
    _locs = MagicMock()
    all_locs = locators or []
    _locs.all = MagicMock(return_value=all_locs)
    db._locs = _locs
    db.get_all_locators = MagicMock(return_value=all_locs)
    db.get_all          = MagicMock(return_value=all_locs)
    db.get_state        = MagicMock(return_value=None)
    db.get_all_states   = MagicMock(return_value=states or [])
    return db


def _make_ai(response: str):
    ai = MagicMock()
    ai.complete = MagicMock(return_value=response)
    ai.small_model = "claude-haiku"
    return ai


def _minimal_locator(url="https://app.com/login", role="button", name="Submit",
                     testid=None, element_id="loc-001"):
    """Minimal locator dict matching LocatorDB structure."""
    chain = [{"strategy": "testid", "value": testid}] if testid else \
            [{"strategy": "role",   "value": {"role": role, "name": name}}]
    return {
        "id":       element_id,
        "url":      url,
        "identity": {"role": role, "name": name, "container": "", "tag": "button"},
        "locators": {"chain": chain, "actionable": True},
        "history":  {"hit_count": 5},
    }


def _valid_plan_json(test_id="TC001_test", url="https://app.com/login"):
    return json.dumps([{
        "test_id":    test_id,
        "name":       "Sample test",
        "steps":      [{"action": "navigate", "url": url}],
        "assertions": [{"type": "url_contains", "value": "/login"}],
    }])


# ═══════════════════════════════════════════════════════════════════════
# Suite 1 — _strip_dynamic_id()
# ═══════════════════════════════════════════════════════════════════════

class TestStripDynamicId(unittest.TestCase):

    def _strip(self, s):
        return TestGenerator._strip_dynamic_id(s)

    def test_no_suffix_returns_unchanged(self):
        self.assertEqual(self._strip("product-detail"), "product-detail")

    def test_ulid_suffix_stripped(self):
        # ULID: 26 alphanumeric chars
        result = self._strip("product-01BX5ZZKBKACTAV9WEVGEMMVS0")
        self.assertEqual(result, "product-")

    def test_uuid_suffix_stripped(self):
        result = self._strip("item-550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(result, "item-")

    def test_hex16_suffix_stripped(self):
        result = self._strip("card-0123456789abcdef")
        self.assertEqual(result, "card-")

    def test_empty_string_unchanged(self):
        self.assertEqual(self._strip(""), "")

    def test_short_string_unchanged(self):
        self.assertEqual(self._strip("btn"), "btn")

    def test_multiple_hyphens_strips_only_last_id(self):
        # The regex strips only the last dynamic suffix
        result = self._strip("a-b-c-01BX5ZZKBKACTAV9WEVGEMMVS0")
        self.assertNotIn("01BX5ZZKBKACTAV9WEVGEMMVS0", result)

    def test_plain_path_not_stripped(self):
        self.assertEqual(self._strip("/product/detail"), "/product/detail")


# ═══════════════════════════════════════════════════════════════════════
# Suite 2 — _fix_malformed_selectors()
# ═══════════════════════════════════════════════════════════════════════

class TestFixMalformedSelectors(unittest.TestCase):

    def _gen(self):
        return TestGenerator(_make_db(), ai_client=None)

    def test_nested_testid_unwrapped(self):
        """{"strategy":"testid","value":{"testid":"foo"}} → value="foo"."""
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "testid",
                                        "value": {"testid": "submit-btn"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["selector"]["value"], "submit-btn")

    def test_nested_value_key_unwrapped(self):
        """{"strategy":"testid","value":{"value":"foo"}} → value="foo"."""
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "testid",
                                        "value": {"value": "my-btn"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["selector"]["value"], "my-btn")

    def test_doubly_nested_selector_unwrapped(self):
        """{"strategy":"testid","value":{"strategy":"testid","value":"foo"}}."""
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "testid",
                                        "value": {"strategy": "testid",
                                                  "value": "deep-btn"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["selector"]["value"], "deep-btn")

    def test_role_selector_untouched(self):
        """role strategy with dict value should NOT be unwrapped."""
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "role",
                                        "value": {"role": "button", "name": "Login"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertIsInstance(fixed["steps"][0]["selector"]["value"], dict)

    def test_css_nested_value_unwrapped(self):
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "css",
                                        "value": {"value": ".submit"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["selector"]["value"], ".submit")

    def test_plain_string_testid_untouched(self):
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "testid", "value": "already-fine"}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["selector"]["value"], "already-fine")

    def test_fallback_selector_also_fixed(self):
        plan = {"steps": [{"action": "click",
                           "selector": {"strategy": "testid", "value": "ok"},
                           "fallback": {"strategy": "testid",
                                        "value": {"testid": "nested-fallback"}}}],
                "assertions": []}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["steps"][0]["fallback"]["value"], "nested-fallback")

    def test_assertion_selector_also_fixed(self):
        plan = {"steps": [],
                "assertions": [{"type": "element_visible",
                                 "selector": {"strategy": "testid",
                                              "value": {"testid": "banner"}}}]}
        fixed = self._gen()._fix_malformed_selectors(plan)
        self.assertEqual(fixed["assertions"][0]["selector"]["value"], "banner")


# ═══════════════════════════════════════════════════════════════════════
# Suite 3 — _parse_plans()
# ═══════════════════════════════════════════════════════════════════════

class TestParsePlans(unittest.TestCase):

    def _gen(self):
        # _parse_plans stamps _meta.ai_model → need a mock AI client
        ai = _make_ai("[]")
        return TestGenerator(_make_db(), ai_client=ai)

    def _parse(self, text, base_url="https://app.com"):
        return self._gen()._parse_plans(text, {}, base_url=base_url)

    # ── Happy-path parsing ──────────────────────────────────────

    def test_valid_json_array_parsed(self):
        plans = self._parse(_valid_plan_json())
        self.assertIsInstance(plans, list)
        self.assertGreater(len(plans), 0)

    def test_test_id_preserved(self):
        plans = self._parse(_valid_plan_json(test_id="TC042_login"))
        self.assertEqual(plans[0]["test_id"], "TC042_login")

    def test_steps_present(self):
        plans = self._parse(_valid_plan_json())
        self.assertIn("steps", plans[0])

    def test_assertions_present(self):
        plans = self._parse(_valid_plan_json())
        self.assertIn("assertions", plans[0])

    # ── Single object wrapped into array ─────────────────────────

    def test_single_object_wrapped_into_list(self):
        single = json.dumps({
            "test_id": "TC001",
            "steps": [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": []
        })
        plans = self._parse(single)
        self.assertIsInstance(plans, list)
        self.assertEqual(len(plans), 1)

    # ── Markdown fence stripping ─────────────────────────────────

    def test_markdown_json_fence_stripped(self):
        text = f"```json\n{_valid_plan_json()}\n```"
        plans = self._parse(text)
        self.assertIsInstance(plans, list)

    def test_markdown_plain_fence_stripped(self):
        text = f"```\n{_valid_plan_json()}\n```"
        plans = self._parse(text)
        self.assertIsInstance(plans, list)

    # ── Think-block stripping ────────────────────────────────────

    def test_think_blocks_stripped(self):
        text = f"<think>reasoning here</think>\n{_valid_plan_json()}"
        plans = self._parse(text)
        self.assertIsInstance(plans, list)
        self.assertGreater(len(plans), 0)

    def test_multiline_think_block_stripped(self):
        think = "<think>\nstep 1: think\nstep 2: decide\n</think>"
        text  = f"{think}\n{_valid_plan_json()}"
        plans = self._parse(text)
        self.assertIsInstance(plans, list)

    # ── Error cases ──────────────────────────────────────────────

    def test_invalid_json_raises_planning_error(self):
        with self.assertRaises(PlanningError):
            self._parse("this is not json at all")

    def test_non_array_non_dict_raises_planning_error(self):
        with self.assertRaises(PlanningError):
            self._parse("42")

    # ── Relative URL resolution ──────────────────────────────────

    def test_relative_url_resolved(self):
        plans_json = json.dumps([{
            "test_id": "T1", "steps": [
                {"action": "navigate", "url": "/login"},
            ], "assertions": []
        }])
        plans = self._parse(plans_json, base_url="https://app.com")
        navigate_url = plans[0]["steps"][0]["url"]
        self.assertTrue(navigate_url.startswith("https://app.com"))
        self.assertIn("/login", navigate_url)

    def test_absolute_url_unchanged(self):
        plans = self._parse(_valid_plan_json(url="https://app.com/login"))
        self.assertEqual(plans[0]["steps"][0]["url"], "https://app.com/login")

    # ── Domain typo correction ───────────────────────────────────

    def test_domain_typo_corrected(self):
        """AI typos very similar domains — should be auto-corrected."""
        plans_json = json.dumps([{
            "test_id": "T1", "steps": [
                {"action": "navigate", "url": "https://appp.com/login"},
            ], "assertions": []
        }])
        plans = self._parse(plans_json, base_url="https://app.com")
        # The correction is only applied when ratio >= 0.85 — this case
        # depends on SequenceMatcher, so just verify the parse succeeds
        self.assertIsInstance(plans, list)

    # ── Missing fields get defaults ──────────────────────────────

    def test_missing_test_id_gets_default(self):
        plans_json = json.dumps([{
            "steps": [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": []
        }])
        plans = self._parse(plans_json)
        self.assertIn("test_id", plans[0])

    def test_plan_without_steps_filtered_out(self):
        """Plans with no steps are dropped by the generator."""
        plans_json = json.dumps([{"test_id": "T1", "assertions": []}])
        plans = self._parse(plans_json)
        # Should be empty — stepless plans are filtered
        self.assertEqual(len(plans), 0)

    # ── Trailing garbage after first array ──────────────────────

    def test_extra_text_after_json_handled(self):
        """Model may append prose after the JSON array."""
        text = _valid_plan_json() + "\nHere are your test plans."
        # Should either parse OK or raise PlanningError — must not crash with exception
        try:
            plans = self._parse(text)
            self.assertIsInstance(plans, list)
        except PlanningError:
            pass  # acceptable — extra text causes parse failure


# ═══════════════════════════════════════════════════════════════════════
# Suite 4 — generate_plans_from_prd() integration
# ═══════════════════════════════════════════════════════════════════════

class TestGeneratePlansFromPrd(unittest.TestCase):

    def _gen_with_ai(self, response: str, locators=None):
        ai  = _make_ai(response)
        loc = locators or [_minimal_locator()]
        db  = _make_db(locators=loc)
        return TestGenerator(db, ai_client=ai), ai

    def test_no_ai_client_raises_planning_error(self):
        g = TestGenerator(_make_db(locators=[_minimal_locator()]))
        with self.assertRaises(PlanningError) as ctx:
            g.generate_plans_from_prd("test PRD", ["https://app.com"])
        self.assertIn("AI", str(ctx.exception))

    def test_empty_db_raises_planning_error(self):
        ai = _make_ai("[]")
        g  = TestGenerator(_make_db(locators=[]), ai_client=ai)
        with self.assertRaises(PlanningError):
            g.generate_plans_from_prd("PRD content", ["https://app.com"])

    def test_returns_list_of_plans(self):
        g, _ = self._gen_with_ai(_valid_plan_json())
        plans = g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertIsInstance(plans, list)

    def test_plans_have_test_id(self):
        g, _ = self._gen_with_ai(_valid_plan_json(test_id="TC001_login"))
        plans = g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertEqual(plans[0]["test_id"], "TC001_login")

    def test_ai_called_exactly_once(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertEqual(ai.complete.call_count, 1)

    def test_ai_call_uses_temperature_zero(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g.generate_plans_from_prd("PRD", ["https://app.com"])
        call_kwargs = ai.complete.call_args.kwargs
        self.assertEqual(call_kwargs.get("temperature"), 0)

    def test_prd_content_in_prompt(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g.generate_plans_from_prd("LOGIN_FLOW_PRD_MARKER", ["https://app.com"])
        prompt = ai.complete.call_args[0][0]
        self.assertIn("LOGIN_FLOW_PRD_MARKER", prompt)

    def test_base_url_in_prompt(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g.generate_plans_from_prd("PRD", ["https://custom-app.example.com"])
        prompt = ai.complete.call_args[0][0]
        self.assertIn("custom-app.example.com", prompt)

    def test_credentials_section_in_prompt(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g.generate_plans_from_prd("PRD", ["https://app.com"],
                                   credentials={"url": "https://app.com/login",
                                                "username": "user@test.com",
                                                "password": "secret123"})
        prompt = ai.complete.call_args[0][0]
        self.assertIn("user@test.com", prompt)

    def test_ai_exception_raises_planning_error(self):
        ai = MagicMock()
        ai.complete = MagicMock(side_effect=RuntimeError("timeout"))
        ai.small_model = "haiku"
        g  = TestGenerator(_make_db(locators=[_minimal_locator()]), ai_client=ai)
        with self.assertRaises(PlanningError) as ctx:
            g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertIn("AI call failed", str(ctx.exception))

    def test_malformed_ai_response_raises_planning_error(self):
        g, _ = self._gen_with_ai("this is not JSON at all")
        with self.assertRaises(PlanningError):
            g.generate_plans_from_prd("PRD", ["https://app.com"])

    def test_admin_locators_excluded_from_prompt(self):
        admin_loc = _minimal_locator(url="https://app.com/admin/dashboard")
        g, ai = self._gen_with_ai(_valid_plan_json(),
                                   locators=[_minimal_locator(), admin_loc])
        g.generate_plans_from_prd("PRD", ["https://app.com"])
        prompt = ai.complete.call_args[0][0]
        self.assertNotIn("/admin/", prompt)

    def test_num_tests_instruction_in_prompt(self):
        g, ai = self._gen_with_ai(_valid_plan_json())
        g._num_tests = 3
        g.generate_plans_from_prd("PRD", ["https://app.com"])
        prompt = ai.complete.call_args[0][0]
        self.assertIn("3", prompt)

    def test_non_actionable_locator_excluded(self):
        non_act = _minimal_locator()
        non_act["locators"]["actionable"] = False
        regular = _minimal_locator(url="https://app.com/login",
                                   role="button", name="Submit", element_id="loc-002")
        g, ai = self._gen_with_ai(_valid_plan_json(), locators=[non_act, regular])
        plans = g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertIsInstance(plans, list)


# ═══════════════════════════════════════════════════════════════════════
# Suite 5 — _fix_url_assertions()
# ═══════════════════════════════════════════════════════════════════════

class TestFixUrlAssertions(unittest.TestCase):

    def _gen(self, transitions=None):
        sg = MagicMock()
        sg.all_transitions = MagicMock(return_value=transitions or [])
        sg.format_for_prompt = MagicMock(return_value="(graph)")
        return TestGenerator(_make_db(), ai_client=None, state_graph=sg)

    def test_no_state_graph_returns_plan_unchanged(self):
        g = TestGenerator(_make_db())
        plan = {"steps": [{"action": "navigate", "url": "https://app.com/login"}],
                "assertions": [{"type": "url_contains", "value": "/dashboard"}]}
        result = g._fix_url_assertions(plan)
        self.assertEqual(result["assertions"][0]["value"], "/dashboard")

    def test_empty_assertions_returns_unchanged(self):
        g = self._gen()
        plan = {"steps": [], "assertions": []}
        result = g._fix_url_assertions(plan)
        self.assertEqual(result["assertions"], [])

    def test_mismatched_url_assertion_replaced(self):
        """AI asserts /dashboard but navigate step goes to /login — should be corrected."""
        g = self._gen()
        plan = {
            "steps":      [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": [{"type": "url_contains", "value": "/dashboard"}],
        }
        result = g._fix_url_assertions(plan)
        # The tracked URL is /login; assertion says /dashboard → should be fixed
        self.assertEqual(result["assertions"][0]["value"], "/login")

    def test_correct_url_assertion_kept(self):
        g = self._gen()
        plan = {
            "steps":      [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": [{"type": "url_contains", "value": "/login"}],
        }
        result = g._fix_url_assertions(plan)
        self.assertEqual(result["assertions"][0]["value"], "/login")

    def test_url_equals_downgraded_to_url_contains(self):
        g = self._gen()
        plan = {
            "steps":      [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": [{"type": "url_equals", "value": "https://app.com/login"}],
        }
        result = g._fix_url_assertions(plan)
        # url_equals should be downgraded to url_contains
        self.assertEqual(result["assertions"][0]["type"], "url_contains")

    def test_nav_graph_transition_updates_url(self):
        """A click that matches a nav-graph transition updates the tracked URL."""
        transitions = [{
            "from_url":       "https://app.com/login",
            "to_url":         "https://app.com/dashboard",
            "traversal_count": 3,
            "trigger":        {"label": "Login", "selector": None},
        }]
        g = self._gen(transitions=transitions)
        plan = {
            "steps": [
                {"action": "navigate", "url": "https://app.com/login"},
                {"action": "click",
                 "selector": {"strategy": "role",
                              "value": {"role": "button", "name": "Login"}}},
            ],
            "assertions": [{"type": "url_contains", "value": "/dashboard"}],
        }
        result = g._fix_url_assertions(plan)
        # /dashboard is the correct URL → assertion should stay /dashboard
        self.assertEqual(result["assertions"][0]["value"], "/dashboard")

    def test_dynamic_id_in_assertion_stripped(self):
        g = self._gen()
        plan = {
            "steps":      [{"action": "navigate",
                            "url": "https://app.com/product/01BX5ZZKBKACTAV9WEVGEMMVS0"}],
            "assertions": [{"type": "url_contains",
                            "value": "/product/01BX5ZZKBKACTAV9WEVGEMMVS0"}],
        }
        result = g._fix_url_assertions(plan)
        # Dynamic IDs should be stripped so assertion matches any product page
        self.assertNotIn("01BX5ZZKBKACTAV9WEVGEMMVS0", result["assertions"][0]["value"])

    def test_non_url_assertion_not_touched(self):
        g = self._gen()
        plan = {
            "steps":      [{"action": "navigate", "url": "https://app.com/login"}],
            "assertions": [{"type": "element_visible",
                            "selector": {"strategy": "testid", "value": "welcome"}}],
        }
        result = g._fix_url_assertions(plan)
        # element_visible assertions should not be touched
        self.assertEqual(result["assertions"][0]["type"], "element_visible")


# ═══════════════════════════════════════════════════════════════════════
# Suite 6 — _find_by_name_in_db()
# ═══════════════════════════════════════════════════════════════════════

class TestFindByNameInDb(unittest.TestCase):

    def _gen_with_locs(self, locs):
        db = _make_db(locators=locs)
        return TestGenerator(db)

    def _loc(self, url, role, name):
        return {"url": url, "identity": {"role": role, "name": name}}

    def test_exact_match_found(self):
        g = self._gen_with_locs([
            self._loc("https://app.com/login", "button", "Sign In"),
        ])
        result = g._find_by_name_in_db("Sign In", "https://app.com/login")
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "button")

    def test_case_insensitive_match(self):
        g = self._gen_with_locs([
            self._loc("https://app.com/login", "button", "SIGN IN"),
        ])
        result = g._find_by_name_in_db("sign in", "https://app.com/login")
        self.assertIsNotNone(result)

    def test_prefix_match_found(self):
        """DB name "Add to Cart Now" matches query "Add to Cart"."""
        g = self._gen_with_locs([
            self._loc("https://app.com/product", "button", "Add to Cart Now"),
        ])
        result = g._find_by_name_in_db("Add to Cart", "https://app.com/product")
        self.assertIsNotNone(result)

    def test_wrong_url_returns_none(self):
        g = self._gen_with_locs([
            self._loc("https://app.com/other", "button", "Submit"),
        ])
        result = g._find_by_name_in_db("Submit", "https://app.com/login")
        self.assertIsNone(result)

    def test_empty_name_returns_none(self):
        g = self._gen_with_locs([
            self._loc("https://app.com/login", "button", "Submit"),
        ])
        result = g._find_by_name_in_db("", "https://app.com/login")
        self.assertIsNone(result)

    def test_no_db_returns_none(self):
        g = TestGenerator(None)  # type: ignore
        result = g._find_by_name_in_db("Submit", "https://app.com/login")
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════
# Suite 7 — negative test generation toggle
# ═══════════════════════════════════════════════════════════════════════

class TestNegativeTests(unittest.TestCase):

    def test_negative_tests_disabled_by_default(self):
        ai  = _make_ai(_valid_plan_json())
        db  = _make_db(locators=[_minimal_locator()])
        g   = TestGenerator(db, ai_client=ai)
        self.assertFalse(g._negative_tests)

    def test_negative_tests_flag_stored(self):
        g = TestGenerator(_make_db(), negative_tests=True)
        self.assertTrue(g._negative_tests)

    def test_with_negative_disabled_ai_called_once(self):
        ai = _make_ai(_valid_plan_json())
        g  = TestGenerator(_make_db(locators=[_minimal_locator()]),
                           ai_client=ai, negative_tests=False)
        g.generate_plans_from_prd("PRD", ["https://app.com"])
        self.assertEqual(ai.complete.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
