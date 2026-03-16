"""
tests/test_contracts.py — Contract tests for actions.py, assertions.py, state_graph.py
=======================================================================================
Tests the planner↔executor contract layer.
No network. No browser. No DB. No AI keys needed.

Run:
    python tests/test_contracts.py
    python -m pytest tests/test_contracts.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ══════════════════════════════════════════════════════════════════════
# actions.py — validate_action, validate_selector, registry
# ══════════════════════════════════════════════════════════════════════

from actions import (
    ACTIONS,
    ActionType,
    ActionCategory,
    get_action,
    get_all_actions,
    get_actions_by_category,
    validate_action,
    validate_selector as action_validate_selector,
)


class TestActionsRegistry(unittest.TestCase):

    def test_all_19_action_types_present(self):
        expected = {at.value for at in ActionType}
        self.assertEqual(set(ACTIONS.keys()), expected)

    def test_each_action_has_name_matching_key(self):
        for key, defn in ACTIONS.items():
            self.assertEqual(defn.name, key, f"Action key '{key}' name mismatch")

    def test_each_action_has_description(self):
        for key, defn in ACTIONS.items():
            self.assertTrue(defn.description.strip(), f"Action '{key}' has empty description")

    def test_each_action_has_category(self):
        for key, defn in ACTIONS.items():
            self.assertIsInstance(defn.category, ActionCategory, f"Action '{key}' bad category")

    def test_navigation_actions_do_not_require_target(self):
        nav = ["navigate", "refresh", "go_back", "go_forward"]
        for name in nav:
            self.assertFalse(ACTIONS[name].requires_target,
                             f"Navigation action '{name}' should not require target")

    def test_interaction_actions_require_target(self):
        target_required = ["click", "dblclick", "fill", "type", "clear", "press",
                           "select", "check", "uncheck", "hover", "focus", "blur"]
        for name in target_required:
            self.assertTrue(ACTIONS[name].requires_target,
                            f"Action '{name}' should require target")

    def test_wait_does_not_require_target(self):
        self.assertFalse(ACTIONS["wait"].requires_target)

    def test_screenshot_does_not_require_target(self):
        self.assertFalse(ACTIONS["screenshot"].requires_target)

    def test_get_action_known(self):
        defn = get_action("click")
        self.assertIsNotNone(defn)
        self.assertEqual(defn.name, "click")

    def test_get_action_unknown_returns_none(self):
        self.assertIsNone(get_action("teleport"))

    def test_get_all_actions_returns_all(self):
        all_actions = get_all_actions()
        self.assertEqual(len(all_actions), len(ACTIONS))

    def test_get_actions_by_category_navigation(self):
        nav = get_actions_by_category(ActionCategory.NAVIGATION)
        names = {a.name for a in nav}
        self.assertIn("navigate", names)
        self.assertIn("refresh", names)
        self.assertIn("go_back", names)
        self.assertIn("go_forward", names)

    def test_get_actions_by_category_excludes_other_categories(self):
        nav = get_actions_by_category(ActionCategory.NAVIGATION)
        names = {a.name for a in nav}
        self.assertNotIn("click", names)
        self.assertNotIn("fill", names)

    def test_navigate_requires_url_param(self):
        defn = get_action("navigate")
        param_names = {p.name for p in defn.params}
        self.assertIn("url", param_names)
        url_param = next(p for p in defn.params if p.name == "url")
        self.assertTrue(url_param.required)

    def test_fill_has_value_param(self):
        defn = get_action("fill")
        param_names = {p.name for p in defn.params}
        self.assertIn("value", param_names)

    def test_press_has_key_param(self):
        defn = get_action("press")
        param_names = {p.name for p in defn.params}
        self.assertIn("key", param_names)

    def test_wait_has_duration_param(self):
        defn = get_action("wait")
        param_names = {p.name for p in defn.params}
        self.assertIn("duration", param_names)

    def test_scroll_has_direction_param(self):
        defn = get_action("scroll")
        param_names = {p.name for p in defn.params}
        self.assertIn("direction", param_names)

    def test_each_action_has_at_least_one_example(self):
        for key, defn in ACTIONS.items():
            self.assertGreater(len(defn.examples), 0,
                               f"Action '{key}' has no examples")


class TestValidateAction(unittest.TestCase):

    def test_valid_navigate(self):
        ok, errors = validate_action({"action": "navigate", "url": "https://app.com"})
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_valid_click(self):
        ok, errors = validate_action({
            "action": "click",
            "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}},
        })
        self.assertTrue(ok)

    def test_valid_fill(self):
        ok, errors = validate_action({
            "action": "fill",
            "selector": {"strategy": "testid", "value": "email-input"},
            "value": "user@example.com",
        })
        self.assertTrue(ok)

    def test_valid_press(self):
        ok, errors = validate_action({
            "action": "press",
            "selector": {"strategy": "testid", "value": "search"},
            "key": "Enter",
        })
        self.assertTrue(ok)

    def test_valid_wait(self):
        ok, errors = validate_action({"action": "wait", "duration": 500})
        self.assertTrue(ok)

    def test_valid_scroll(self):
        ok, errors = validate_action({"action": "scroll", "direction": "down"})
        self.assertTrue(ok)

    def test_valid_screenshot(self):
        ok, errors = validate_action({"action": "screenshot"})
        self.assertTrue(ok)

    def test_missing_action_field(self):
        ok, errors = validate_action({"url": "https://app.com"})
        self.assertFalse(ok)
        self.assertTrue(any("action" in e.lower() for e in errors))

    def test_empty_action_field(self):
        ok, errors = validate_action({"action": ""})
        self.assertFalse(ok)

    def test_unknown_action(self):
        ok, errors = validate_action({"action": "teleport"})
        self.assertFalse(ok)
        self.assertTrue(any("unknown" in e.lower() or "teleport" in e for e in errors))

    def test_click_missing_selector(self):
        ok, errors = validate_action({"action": "click"})
        self.assertFalse(ok)
        self.assertTrue(any("selector" in e for e in errors))

    def test_fill_missing_selector(self):
        ok, errors = validate_action({"action": "fill", "value": "hello"})
        self.assertFalse(ok)
        self.assertTrue(any("selector" in e for e in errors))

    def test_navigate_missing_url(self):
        ok, errors = validate_action({"action": "navigate"})
        self.assertFalse(ok)
        self.assertTrue(any("url" in e for e in errors))

    def test_navigate_invalid_wait_until_enum(self):
        ok, errors = validate_action({
            "action": "navigate",
            "url": "https://app.com",
            "wait_until": "instantly",
        })
        self.assertFalse(ok)
        self.assertTrue(any("wait_until" in e or "instantly" in e for e in errors))

    def test_navigate_valid_wait_until_enum(self):
        for val in ["load", "domcontentloaded", "networkidle", "commit"]:
            ok, errors = validate_action({
                "action": "navigate",
                "url": "https://app.com",
                "wait_until": val,
            })
            self.assertTrue(ok, f"wait_until='{val}' should be valid, got errors: {errors}")

    def test_wait_negative_duration_invalid(self):
        ok, errors = validate_action({"action": "wait", "duration": -1})
        self.assertFalse(ok)

    def test_selector_validated_inline(self):
        ok, errors = validate_action({
            "action": "click",
            "selector": {"strategy": "role"},  # missing value
        })
        self.assertFalse(ok)
        self.assertTrue(any("value" in e for e in errors))

    def test_fallback_selector_validated_too(self):
        ok, errors = validate_action({
            "action": "click",
            "selector": {"strategy": "testid", "value": "btn"},
            "fallback": {"strategy": "role"},  # missing value
        })
        self.assertFalse(ok)
        self.assertTrue(any("fallback" in e for e in errors))

    def test_all_action_types_accept_minimal_valid_input(self):
        """Smoke test: every action type must validate without crashing."""
        minimal = {
            "navigate":    {"action": "navigate", "url": "https://x.com"},
            "refresh":     {"action": "refresh"},
            "go_back":     {"action": "go_back"},
            "go_forward":  {"action": "go_forward"},
            "click":       {"action": "click", "selector": {"strategy": "testid", "value": "x"}},
            "dblclick":    {"action": "dblclick", "selector": {"strategy": "testid", "value": "x"}},
            "hover":       {"action": "hover", "selector": {"strategy": "testid", "value": "x"}},
            "focus":       {"action": "focus", "selector": {"strategy": "testid", "value": "x"}},
            "blur":        {"action": "blur", "selector": {"strategy": "testid", "value": "x"}},
            "fill":        {"action": "fill", "selector": {"strategy": "testid", "value": "x"}, "value": "v"},
            "type":        {"action": "type", "selector": {"strategy": "testid", "value": "x"}, "text": "v"},
            "clear":       {"action": "clear", "selector": {"strategy": "testid", "value": "x"}},
            "press":       {"action": "press", "selector": {"strategy": "testid", "value": "x"}, "key": "Enter"},
            "select":      {"action": "select", "selector": {"strategy": "testid", "value": "x"}, "label": "Option"},
            "check":       {"action": "check", "selector": {"strategy": "testid", "value": "x"}},
            "uncheck":     {"action": "uncheck", "selector": {"strategy": "testid", "value": "x"}},
            "scroll":      {"action": "scroll", "direction": "down"},
            "wait":        {"action": "wait", "duration": 500},
            "screenshot":  {"action": "screenshot"},
            "evaluate":    {"action": "evaluate", "script": "return 1"},
        }
        for name, action in minimal.items():
            ok, errors = validate_action(action)
            self.assertTrue(ok, f"Action '{name}' failed validation: {errors}")


class TestActionValidateSelector(unittest.TestCase):

    def test_valid_testid(self):
        errors = action_validate_selector({"strategy": "testid", "value": "submit-btn"})
        self.assertEqual(errors, [])

    def test_valid_role(self):
        errors = action_validate_selector({"strategy": "role", "value": {"role": "button", "name": "OK"}})
        self.assertEqual(errors, [])

    def test_valid_css(self):
        errors = action_validate_selector({"strategy": "css", "value": "form > button"})
        self.assertEqual(errors, [])

    def test_valid_text(self):
        errors = action_validate_selector({"strategy": "text", "value": "Click here"})
        self.assertEqual(errors, [])

    def test_valid_label(self):
        errors = action_validate_selector({"strategy": "label", "value": "Email"})
        self.assertEqual(errors, [])

    def test_valid_placeholder(self):
        errors = action_validate_selector({"strategy": "placeholder", "value": "Enter email"})
        self.assertEqual(errors, [])

    def test_valid_aria_label(self):
        errors = action_validate_selector({"strategy": "aria-label", "value": "Close"})
        self.assertEqual(errors, [])

    def test_not_a_dict(self):
        errors = action_validate_selector("role:button")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("object" in e for e in errors))

    def test_missing_strategy(self):
        errors = action_validate_selector({"value": "submit"})
        self.assertTrue(len(errors) > 0)

    def test_missing_value(self):
        errors = action_validate_selector({"strategy": "testid"})
        self.assertTrue(len(errors) > 0)

    def test_unknown_strategy(self):
        errors = action_validate_selector({"strategy": "magic", "value": "x"})
        self.assertTrue(len(errors) > 0)

    def test_role_value_must_be_dict(self):
        errors = action_validate_selector({"strategy": "role", "value": "button"})
        self.assertTrue(len(errors) > 0)

    def test_role_value_missing_role_key(self):
        errors = action_validate_selector({"strategy": "role", "value": {"name": "Submit"}})
        self.assertTrue(len(errors) > 0)

    def test_role_plus_container_is_valid(self):
        errors = action_validate_selector({
            "strategy": "role+container",
            "value": {"role": "button", "name": "OK"},
        })
        self.assertEqual(errors, [])


# ══════════════════════════════════════════════════════════════════════
# assertions.py — validate_assertion, validate_selector, registry
# ══════════════════════════════════════════════════════════════════════

from assertions import (
    ASSERTIONS,
    AssertionType,
    AssertionCategory,
    get_assertion,
    get_all_assertions,
    get_assertions_by_category,
    validate_assertion,
    validate_selector as assertion_validate_selector,
)


class TestAssertionsRegistry(unittest.TestCase):

    def test_all_assertion_types_present(self):
        expected = {at.value for at in AssertionType}
        self.assertEqual(set(ASSERTIONS.keys()), expected)

    def test_each_assertion_has_type_matching_key(self):
        for key, defn in ASSERTIONS.items():
            self.assertEqual(defn.type, key, f"Assertion key '{key}' type mismatch")

    def test_each_assertion_has_description(self):
        for key, defn in ASSERTIONS.items():
            self.assertTrue(defn.description.strip(), f"Assertion '{key}' has empty description")

    def test_each_assertion_has_category(self):
        for key, defn in ASSERTIONS.items():
            self.assertIsInstance(defn.category, AssertionCategory,
                                  f"Assertion '{key}' bad category")

    def test_url_assertions_do_not_need_target(self):
        for name in ["url_equals", "url_contains", "url_matches"]:
            self.assertFalse(ASSERTIONS[name].needs_target,
                             f"URL assertion '{name}' should not need target")

    def test_element_assertions_need_target(self):
        needs_target = [
            "element_exists", "element_not_exists", "element_visible", "element_hidden",
            "element_enabled", "element_disabled", "element_checked", "element_unchecked",
            "element_text_equals", "element_text_contains", "element_value_equals",
        ]
        for name in needs_target:
            self.assertTrue(ASSERTIONS[name].needs_target,
                            f"Element assertion '{name}' should need target")

    def test_title_assertions_do_not_need_target(self):
        for name in ["title_equals", "title_contains"]:
            self.assertFalse(ASSERTIONS[name].needs_target,
                             f"Title assertion '{name}' should not need target")

    def test_get_assertion_known(self):
        defn = get_assertion("element_visible")
        self.assertIsNotNone(defn)
        self.assertEqual(defn.type, "element_visible")

    def test_get_assertion_unknown_returns_none(self):
        self.assertIsNone(get_assertion("purple_unicorn"))

    def test_get_all_assertions_returns_all(self):
        self.assertEqual(len(get_all_assertions()), len(ASSERTIONS))

    def test_get_assertions_by_category_url(self):
        url_assertions = get_assertions_by_category(AssertionCategory.URL)
        names = {a.type for a in url_assertions}
        self.assertIn("url_equals", names)
        self.assertIn("url_contains", names)
        self.assertIn("url_matches", names)

    def test_get_assertions_by_category_excludes_other(self):
        url_assertions = get_assertions_by_category(AssertionCategory.URL)
        names = {a.type for a in url_assertions}
        self.assertNotIn("element_visible", names)

    def test_url_assertions_require_value_param(self):
        for name in ["url_equals", "url_contains"]:
            defn = get_assertion(name)
            param_names = {p.name for p in defn.params}
            self.assertIn("value", param_names)

    def test_element_text_equals_has_value_param(self):
        defn = get_assertion("element_text_equals")
        param_names = {p.name for p in defn.params}
        self.assertIn("value", param_names)

    def test_element_count_has_count_param(self):
        defn = get_assertion("element_count")
        param_names = {p.name for p in defn.params}
        self.assertIn("count", param_names)

    def test_each_assertion_has_at_least_one_example(self):
        for key, defn in ASSERTIONS.items():
            self.assertGreater(len(defn.examples), 0,
                               f"Assertion '{key}' has no examples")


class TestValidateAssertion(unittest.TestCase):

    def _sel(self):
        return {"strategy": "testid", "value": "elem"}

    def test_valid_url_equals(self):
        ok, errors = validate_assertion({"type": "url_equals", "value": "https://app.com"})
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_valid_url_contains(self):
        ok, errors = validate_assertion({"type": "url_contains", "value": "/dashboard"})
        self.assertTrue(ok)

    def test_valid_element_visible(self):
        ok, errors = validate_assertion({
            "type": "element_visible",
            "selector": self._sel(),
        })
        self.assertTrue(ok)

    def test_valid_element_text_equals(self):
        ok, errors = validate_assertion({
            "type": "element_text_equals",
            "selector": self._sel(),
            "value": "Welcome",
        })
        self.assertTrue(ok)

    def test_valid_element_count(self):
        ok, errors = validate_assertion({
            "type": "element_count",
            "selector": {"strategy": "role", "value": {"role": "listitem", "name": ""}},
            "count": 5,
        })
        self.assertTrue(ok)

    def test_valid_title_contains(self):
        ok, errors = validate_assertion({"type": "title_contains", "value": "Dashboard"})
        self.assertTrue(ok)

    def test_missing_type_field(self):
        ok, errors = validate_assertion({"value": "/dashboard"})
        self.assertFalse(ok)
        self.assertTrue(any("type" in e.lower() for e in errors))

    def test_empty_type_field(self):
        ok, errors = validate_assertion({"type": ""})
        self.assertFalse(ok)

    def test_unknown_type(self):
        ok, errors = validate_assertion({"type": "magic_check"})
        self.assertFalse(ok)
        self.assertTrue(any("unknown" in e.lower() or "magic_check" in e for e in errors))

    def test_url_equals_missing_value(self):
        ok, errors = validate_assertion({"type": "url_equals"})
        self.assertFalse(ok)
        self.assertTrue(any("value" in e for e in errors))

    def test_element_visible_missing_selector(self):
        ok, errors = validate_assertion({"type": "element_visible"})
        self.assertFalse(ok)
        self.assertTrue(any("selector" in e for e in errors))

    def test_element_hidden_missing_selector(self):
        ok, errors = validate_assertion({"type": "element_hidden"})
        self.assertFalse(ok)

    def test_element_text_equals_missing_both(self):
        ok, errors = validate_assertion({"type": "element_text_equals"})
        self.assertFalse(ok)
        error_str = " ".join(errors)
        self.assertIn("selector", error_str)
        self.assertIn("value", error_str)

    def test_selector_validated_inline(self):
        ok, errors = validate_assertion({
            "type": "element_visible",
            "selector": {"strategy": "role"},  # missing value
        })
        self.assertFalse(ok)

    def test_all_assertion_types_accept_minimal_valid_input(self):
        """Smoke test: every assertion type must validate without crashing."""
        sel = self._sel()
        minimal = {
            "url_equals":         {"type": "url_equals", "value": "https://x.com"},
            "url_contains":       {"type": "url_contains", "value": "/path"},
            "url_matches":        {"type": "url_matches", "pattern": "^https://"},
            "title_equals":       {"type": "title_equals", "value": "Home"},
            "title_contains":     {"type": "title_contains", "value": "Home"},
            "element_exists":     {"type": "element_exists", "selector": sel},
            "element_not_exists": {"type": "element_not_exists", "selector": sel},
            "element_visible":    {"type": "element_visible", "selector": sel},
            "element_hidden":     {"type": "element_hidden", "selector": sel},
            "element_enabled":    {"type": "element_enabled", "selector": sel},
            "element_disabled":   {"type": "element_disabled", "selector": sel},
            "element_checked":    {"type": "element_checked", "selector": sel},
            "element_unchecked":  {"type": "element_unchecked", "selector": sel},
            "element_focused":    {"type": "element_focused", "selector": sel},
            "element_editable":   {"type": "element_editable", "selector": sel},
            "element_readonly":   {"type": "element_readonly", "selector": sel},
            "element_in_viewport":{"type": "element_in_viewport", "selector": sel},
            "element_text_equals":    {"type": "element_text_equals", "selector": sel, "value": "OK"},
            "element_text_contains":  {"type": "element_text_contains", "selector": sel, "value": "OK"},
            "element_text_matches":   {"type": "element_text_matches", "selector": sel, "pattern": "^OK"},
            "element_value_equals":   {"type": "element_value_equals", "selector": sel, "value": "v"},
            "element_value_contains": {"type": "element_value_contains", "selector": sel, "value": "v"},
            "element_attribute":  {"type": "element_attribute", "selector": sel, "attribute": "href", "value": "/"},
            "element_has_class":  {"type": "element_has_class", "selector": sel, "class": "active"},
            "element_has_style":  {"type": "element_has_style", "selector": sel, "property": "color", "value": "red"},
            "element_count":      {"type": "element_count", "selector": sel, "count": 3},
            "javascript":         {"type": "javascript", "script": "return true"},
        }
        for name, assertion in minimal.items():
            ok, errors = validate_assertion(assertion)
            self.assertTrue(ok, f"Assertion '{name}' failed validation: {errors}")


class TestAssertionValidateSelector(unittest.TestCase):

    def test_valid_testid(self):
        errors = assertion_validate_selector({"strategy": "testid", "value": "btn"})
        self.assertEqual(errors, [])

    def test_valid_role(self):
        errors = assertion_validate_selector({"strategy": "role", "value": {"role": "button", "name": "OK"}})
        self.assertEqual(errors, [])

    def test_not_a_dict(self):
        errors = assertion_validate_selector("testid:btn")
        self.assertTrue(len(errors) > 0)

    def test_missing_strategy(self):
        errors = assertion_validate_selector({"value": "btn"})
        self.assertTrue(len(errors) > 0)

    def test_missing_value(self):
        errors = assertion_validate_selector({"strategy": "testid"})
        self.assertTrue(len(errors) > 0)

    def test_unknown_strategy(self):
        errors = assertion_validate_selector({"strategy": "magic", "value": "x"})
        self.assertTrue(len(errors) > 0)

    def test_role_value_must_be_dict(self):
        errors = assertion_validate_selector({"strategy": "role", "value": "button"})
        self.assertTrue(len(errors) > 0)

    def test_role_value_missing_role_key(self):
        errors = assertion_validate_selector({"strategy": "role", "value": {"name": "OK"}})
        self.assertTrue(len(errors) > 0)


# ══════════════════════════════════════════════════════════════════════
# state_graph.py — pure helper functions
# ══════════════════════════════════════════════════════════════════════

from state_graph import (
    _path_label,
    _make_edge_id,
    _normalize_name_for_hash,
    compute_semantic_hash,
    classify_page_change,
)


class TestPathLabel(unittest.TestCase):

    def test_returns_path_only(self):
        self.assertEqual(_path_label("https://app.com/login"), "/login")

    def test_root_url(self):
        self.assertEqual(_path_label("https://app.com/"), "/")

    def test_strips_query(self):
        self.assertEqual(_path_label("https://app.com/search?q=test"), "/search")

    def test_strips_fragment(self):
        self.assertEqual(_path_label("https://app.com/page#section"), "/page")

    def test_deep_path(self):
        self.assertEqual(_path_label("https://app.com/a/b/c"), "/a/b/c")

    def test_malformed_url_returns_input(self):
        # Should not raise — returns the raw string on error
        result = _path_label("not_a_real_url")
        self.assertIsInstance(result, str)

    def test_empty_string(self):
        result = _path_label("")
        self.assertIsInstance(result, str)


class TestMakeEdgeId(unittest.TestCase):

    def test_deterministic(self):
        id1 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        id2 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        self.assertEqual(id1, id2)

    def test_16_char_hex(self):
        edge_id = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        self.assertEqual(len(edge_id), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in edge_id))

    def test_different_from_url(self):
        id1 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        id2 = _make_edge_id("https://x.com", "https://b.com", "click", "Login")
        self.assertNotEqual(id1, id2)

    def test_different_to_url(self):
        id1 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        id2 = _make_edge_id("https://a.com", "https://c.com", "click", "Login")
        self.assertNotEqual(id1, id2)

    def test_different_action(self):
        id1 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        id2 = _make_edge_id("https://a.com", "https://b.com", "fill", "Login")
        self.assertNotEqual(id1, id2)

    def test_different_label(self):
        id1 = _make_edge_id("https://a.com", "https://b.com", "click", "Login")
        id2 = _make_edge_id("https://a.com", "https://b.com", "click", "Logout")
        self.assertNotEqual(id1, id2)


class TestNormalizeNameForHash(unittest.TestCase):

    def test_strips_price(self):
        self.assertNotIn("$", _normalize_name_for_hash("Pay $19.99"))

    def test_strips_counter(self):
        self.assertNotIn("(3)", _normalize_name_for_hash("Cart (3)"))

    def test_strips_timestamp(self):
        self.assertNotIn("12:30", _normalize_name_for_hash("Meeting at 12:30"))

    def test_strips_iso_date(self):
        self.assertNotIn("2024-03-15", _normalize_name_for_hash("Report 2024-03-15"))

    def test_stable_name_unchanged(self):
        # "Submit" has no dynamic patterns — normalization should keep core text
        result = _normalize_name_for_hash("Submit")
        self.assertIn("submit", result)  # lowercased but present

    def test_returns_lowercase(self):
        result = _normalize_name_for_hash("MyButton")
        self.assertEqual(result, result.lower())

    def test_returns_stripped(self):
        result = _normalize_name_for_hash("  hello  ")
        self.assertEqual(result, result.strip())


class TestComputeSemanticHash(unittest.TestCase):

    def _node(self, role, name):
        return {"role": role, "name": name}

    def test_deterministic(self):
        snap = [self._node("button", "Submit"), self._node("link", "Home")]
        self.assertEqual(compute_semantic_hash(snap), compute_semantic_hash(snap))

    def test_order_independent(self):
        snap1 = [self._node("button", "Submit"), self._node("link", "Home")]
        snap2 = [self._node("link", "Home"), self._node("button", "Submit")]
        self.assertEqual(compute_semantic_hash(snap1), compute_semantic_hash(snap2))

    def test_different_content_different_hash(self):
        snap1 = [self._node("button", "Submit")]
        snap2 = [self._node("button", "Cancel")]
        self.assertNotEqual(compute_semantic_hash(snap1), compute_semantic_hash(snap2))

    def test_dynamic_values_normalized(self):
        """Cart (3) and Cart (4) must produce the same hash."""
        snap1 = [self._node("button", "Cart (3)")]
        snap2 = [self._node("button", "Cart (4)")]
        self.assertEqual(compute_semantic_hash(snap1), compute_semantic_hash(snap2))

    def test_price_normalized(self):
        snap1 = [self._node("button", "Pay $9.99")]
        snap2 = [self._node("button", "Pay $19.99")]
        self.assertEqual(compute_semantic_hash(snap1), compute_semantic_hash(snap2))

    def test_presentational_roles_excluded(self):
        """'none', 'presentation', 'generic' nodes should not affect the hash."""
        snap_clean = [self._node("button", "Submit")]
        snap_with_noise = [
            self._node("button", "Submit"),
            self._node("none", ""),
            self._node("presentation", ""),
            self._node("generic", "wrapper"),
        ]
        self.assertEqual(compute_semantic_hash(snap_clean),
                         compute_semantic_hash(snap_with_noise))

    def test_empty_snapshot(self):
        result = compute_semantic_hash([])
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 16)

    def test_16_char_hex(self):
        snap = [self._node("button", "OK")]
        result = compute_semantic_hash(snap)
        self.assertEqual(len(result), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))


class TestClassifyPageChange(unittest.TestCase):

    def _node(self, role, name):
        return {"role": role, "name": name}

    def test_url_changed_is_navigation(self):
        snap = [self._node("button", "Submit")]
        result = classify_page_change(snap, snap, "https://a.com", "https://b.com")
        self.assertEqual(result, "navigation")

    def test_url_same_no_change_is_none(self):
        snap = [self._node("button", "Submit")]
        result = classify_page_change(snap, snap, "https://a.com", "https://a.com")
        self.assertEqual(result, "none")

    def test_dialog_role_appears_is_modal(self):
        before = [self._node("button", "Open")]
        after  = [self._node("button", "Open"), self._node("dialog", "Confirm")]
        result = classify_page_change(before, after, "https://a.com", "https://a.com")
        self.assertEqual(result, "modal")

    def test_alertdialog_role_appears_is_modal(self):
        before = [self._node("button", "Delete")]
        after  = [self._node("button", "Delete"), self._node("alertdialog", "Are you sure?")]
        result = classify_page_change(before, after, "https://a.com", "https://a.com")
        self.assertEqual(result, "modal")

    def test_content_change_without_modal_is_partial(self):
        before = [self._node("button", "Submit")]
        after  = [self._node("button", "Submit"), self._node("link", "New Item")]
        result = classify_page_change(before, after, "https://a.com", "https://a.com")
        self.assertEqual(result, "partial")

    def test_dynamic_value_change_is_none(self):
        """Changing counter/price shouldn't register as a structural change."""
        before = [self._node("button", "Cart (2)")]
        after  = [self._node("button", "Cart (3)")]
        result = classify_page_change(before, after, "https://a.com", "https://a.com")
        self.assertEqual(result, "none")

    def test_url_change_takes_priority_over_dialog(self):
        """If URL also changed, it's navigation — not modal."""
        before = [self._node("button", "OK")]
        after  = [self._node("dialog", "Modal")]
        result = classify_page_change(before, after, "https://a.com", "https://b.com")
        self.assertEqual(result, "navigation")

    def test_empty_before_and_after(self):
        result = classify_page_change([], [], "https://a.com", "https://a.com")
        self.assertEqual(result, "none")

    def test_presentational_roles_ignored_for_partial(self):
        """Adding a 'none' role element alone should NOT trigger a partial change."""
        before = [self._node("button", "Submit")]
        after  = [self._node("button", "Submit"), self._node("none", "")]
        result = classify_page_change(before, after, "https://a.com", "https://a.com")
        self.assertEqual(result, "none")


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
