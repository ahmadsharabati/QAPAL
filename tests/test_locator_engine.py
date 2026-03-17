"""
test_locator_engine.py — Unit tests for the locator intelligence engine
=======================================================================
Tests: ranker.py, parser.py, patcher.py, scaffold.py
All pure-function tests — no browser, no network, no AI keys needed.

Run:
    python tests/test_locator_engine.py
    python -m pytest tests/test_locator_engine.py -v
"""

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===================================================================
# Ranker tests
# ===================================================================

from ranker import (
    STRATEGY_SCORES,
    SelectorCandidate,
    SelectorGrade,
    format_grade,
    grade,
    rank_candidates,
    score_selector,
)


class TestScoreSelector(unittest.TestCase):
    """Test the score_selector weighted scoring function."""

    def test_testid_unique_visible_enabled_high_score(self):
        s = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True)
        self.assertGreater(s, 0.90)

    def test_css_non_unique_lower_score(self):
        s = score_selector("css", count=5, visible=True, in_viewport=True, enabled=True)
        self.assertLess(s, 0.60)

    def test_not_found_low_score(self):
        s = score_selector("testid", count=0, visible=False, in_viewport=False, enabled=False)
        # count=0 means uniqueness=0, but strategy score still contributes
        self.assertLess(s, 0.60)

    def test_role_unique_visible(self):
        s = score_selector("role", count=1, visible=True, in_viewport=True, enabled=True)
        self.assertGreater(s, 0.80)

    def test_invisible_element_penalized(self):
        visible = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True)
        hidden = score_selector("testid", count=1, visible=False, in_viewport=False, enabled=True)
        self.assertGreater(visible, hidden)

    def test_disabled_element_penalized(self):
        enabled = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True)
        disabled = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=False)
        self.assertGreater(enabled, disabled)

    def test_history_bonus(self):
        no_history = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True)
        good_history = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True, hit_count=10, miss_count=0)
        self.assertGreaterEqual(good_history, no_history)

    def test_bad_history_penalty(self):
        no_history = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True)
        bad_history = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True, hit_count=0, miss_count=10)
        self.assertLessEqual(bad_history, no_history)

    def test_unknown_strategy_defaults_to_low_score(self):
        s = score_selector("unknown_strategy", count=1, visible=True, in_viewport=True, enabled=True)
        # Unknown strategy gets 0.1 base, but other factors (unique, visible, etc.) boost it
        self.assertLess(s, 0.70)

    def test_score_bounded_0_to_1(self):
        s = score_selector("testid", count=1, visible=True, in_viewport=True, enabled=True, hit_count=1000, miss_count=0)
        self.assertLessEqual(s, 1.0)
        self.assertGreaterEqual(s, 0.0)


class TestGrade(unittest.TestCase):
    """Test grade/format_grade functions."""

    def test_high_score_is_grade_a(self):
        self.assertEqual(grade(0.95), SelectorGrade.A)
        self.assertEqual(grade(0.81), SelectorGrade.A)

    def test_medium_score_is_grade_b(self):
        self.assertEqual(grade(0.7), SelectorGrade.B)
        self.assertEqual(grade(0.61), SelectorGrade.B)

    def test_low_score_is_grade_c(self):
        self.assertEqual(grade(0.5), SelectorGrade.C)

    def test_very_low_is_grade_d(self):
        self.assertEqual(grade(0.3), SelectorGrade.D)

    def test_zero_is_grade_f(self):
        self.assertEqual(grade(0.0), SelectorGrade.F)
        self.assertEqual(grade(0.15), SelectorGrade.F)

    def test_format_grade_includes_letter_and_score(self):
        g = format_grade(0.88)
        self.assertIn("A", g)
        self.assertIn("0.88", g)

    def test_format_grade_f(self):
        g = format_grade(0.0)
        self.assertIn("F", g)


class TestRankCandidates(unittest.TestCase):
    """Test rank_candidates sorting."""

    def test_sorts_by_score_descending(self):
        candidates = [
            SelectorCandidate(strategy="css", value=".btn", unique=False, score=0.3, expression='page.locator(".btn")'),
            SelectorCandidate(strategy="testid", value="submit", unique=True, score=0.95, expression='page.get_by_test_id("submit")'),
            SelectorCandidate(strategy="role", value={"role": "button"}, unique=True, score=0.8, expression='page.get_by_role("button")'),
        ]
        ranked = rank_candidates(candidates)
        self.assertEqual(ranked[0].strategy, "testid")
        self.assertEqual(ranked[1].strategy, "role")
        self.assertEqual(ranked[2].strategy, "css")

    def test_empty_list(self):
        self.assertEqual(rank_candidates([]), [])

    def test_single_candidate(self):
        c = SelectorCandidate(strategy="testid", value="x", unique=True, score=0.9, expression="x")
        self.assertEqual(rank_candidates([c]), [c])


class TestStrategyScores(unittest.TestCase):
    """Test strategy score ordering."""

    def test_testid_highest(self):
        self.assertEqual(max(STRATEGY_SCORES, key=STRATEGY_SCORES.get), "testid")

    def test_css_lower_than_role(self):
        self.assertLess(STRATEGY_SCORES["css"], STRATEGY_SCORES["role"])

    def test_xpath_lowest(self):
        self.assertEqual(min(STRATEGY_SCORES, key=STRATEGY_SCORES.get), "xpath")


# ===================================================================
# Parser tests
# ===================================================================

from parser import (
    ParsedSelector,
    _classify_locator,
    _qapal_to_python,
    _qapal_to_typescript,
    detect_language,
    parse_file,
    qapal_to_expression,
    selector_to_qapal,
)


class TestDetectLanguage(unittest.TestCase):

    def test_python(self):
        self.assertEqual(detect_language("tests/test_login.py"), "python")

    def test_typescript_spec(self):
        self.assertEqual(detect_language("tests/login.spec.ts"), "typescript")

    def test_javascript(self):
        self.assertEqual(detect_language("tests/login.spec.js"), "typescript")

    def test_tsx(self):
        self.assertEqual(detect_language("tests/Login.tsx"), "typescript")

    def test_unknown_defaults_to_typescript(self):
        self.assertEqual(detect_language("tests/data.yml"), "typescript")


class TestClassifyLocator(unittest.TestCase):

    def test_data_testid(self):
        self.assertEqual(_classify_locator('[data-testid="submit"]'), ("testid", "submit"))

    def test_data_test(self):
        self.assertEqual(_classify_locator('[data-test="email"]'), ("testid", "email"))

    def test_data_cy(self):
        self.assertEqual(_classify_locator('[data-cy="login-btn"]'), ("testid", "login-btn"))

    def test_aria_label(self):
        self.assertEqual(_classify_locator('[aria-label="Close"]'), ("aria_label", "Close"))

    def test_id_selector(self):
        self.assertEqual(_classify_locator("#main-content"), ("id", "main-content"))

    def test_css_class(self):
        self.assertEqual(_classify_locator(".submit-btn"), ("css", ".submit-btn"))

    def test_complex_css(self):
        self.assertEqual(_classify_locator("div.container > button"), ("css", "div.container > button"))


class TestParsePythonFile(unittest.TestCase):

    def _write_tmp(self, content: str, suffix=".py") -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8")
        tmp.write(textwrap.dedent(content))
        tmp.close()
        return tmp.name

    def tearDown(self):
        # Clean temp files
        for attr in ("_tmpfile",):
            path = getattr(self, attr, None)
            if path and os.path.exists(path):
                os.unlink(path)

    def test_parse_get_by_test_id(self):
        path = self._write_tmp('''
            from playwright.sync_api import Page
            def test_login(page: Page):
                page.get_by_test_id("email").fill("user@test.com")
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "testid")
        self.assertEqual(results[0].value, "email")
        self.assertEqual(results[0].language, "python")

    def test_parse_get_by_role_with_name(self):
        path = self._write_tmp('''
            def test_login(page):
                page.get_by_role("button", name="Submit").click()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "role")
        self.assertEqual(results[0].value, {"role": "button", "name": "Submit"})

    def test_parse_get_by_text(self):
        path = self._write_tmp('''
            def test_home(page):
                page.get_by_text("Welcome back").is_visible()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "text")
        self.assertEqual(results[0].value, "Welcome back")

    def test_parse_get_by_label(self):
        path = self._write_tmp('''
            def test_form(page):
                page.get_by_label("Username").fill("admin")
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "label")
        self.assertEqual(results[0].value, "Username")

    def test_parse_get_by_placeholder(self):
        path = self._write_tmp('''
            def test_search(page):
                page.get_by_placeholder("Search...").fill("query")
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "placeholder")
        self.assertEqual(results[0].value, "Search...")

    def test_parse_locator_css(self):
        path = self._write_tmp('''
            def test_click(page):
                page.locator(".submit-btn").click()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "css")
        self.assertEqual(results[0].value, ".submit-btn")

    def test_parse_locator_testid(self):
        path = self._write_tmp('''
            def test_click(page):
                page.locator('[data-testid="submit"]').click()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "testid")
        self.assertEqual(results[0].value, "submit")

    def test_parse_locator_id(self):
        path = self._write_tmp('''
            def test_click(page):
                page.locator("#main").click()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].selector_type, "id")
        self.assertEqual(results[0].value, "main")

    def test_parse_multiple_selectors(self):
        path = self._write_tmp('''
            def test_flow(page):
                page.get_by_test_id("email").fill("a@b.com")
                page.get_by_test_id("password").fill("secret")
                page.get_by_role("button", name="Login").click()
        ''')
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 3)

    def test_empty_file(self):
        path = self._write_tmp("# No selectors here\npass\n")
        self._tmpfile = path
        results = parse_file(path)
        self.assertEqual(len(results), 0)

    def test_nonexistent_file(self):
        results = parse_file("/tmp/does_not_exist_12345.py")
        self.assertEqual(results, [])


class TestParseTypeScriptFile(unittest.TestCase):

    def _write_tmp(self, content: str) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".spec.ts", mode="w", delete=False, encoding="utf-8")
        tmp.write(textwrap.dedent(content))
        tmp.close()
        return tmp.name

    def test_parse_getByTestId(self):
        path = self._write_tmp('''
            import { test } from '@playwright/test';
            test('login', async ({ page }) => {
                await page.getByTestId('email').fill('user@test.com');
            });
        ''')
        results = parse_file(path)
        self.assertTrue(any(s.selector_type == "testid" and s.value == "email" for s in results))
        os.unlink(path)

    def test_parse_getByRole_with_name(self):
        path = self._write_tmp('''
            test('btn', async ({ page }) => {
                await page.getByRole('button', { name: 'Submit' }).click();
            });
        ''')
        results = parse_file(path)
        self.assertTrue(any(
            s.selector_type == "role" and s.value == {"role": "button", "name": "Submit"}
            for s in results
        ))
        os.unlink(path)

    def test_parse_getByText(self):
        path = self._write_tmp('''
            test('text', async ({ page }) => {
                await page.getByText('Welcome').isVisible();
            });
        ''')
        results = parse_file(path)
        self.assertTrue(any(s.selector_type == "text" and s.value == "Welcome" for s in results))
        os.unlink(path)

    def test_parse_ts_locator_css(self):
        path = self._write_tmp('''
            test('css', async ({ page }) => {
                await page.locator('.btn-primary').click();
            });
        ''')
        results = parse_file(path)
        self.assertTrue(any(s.selector_type == "css" and s.value == ".btn-primary" for s in results))
        os.unlink(path)

    def test_language_detected_as_typescript(self):
        path = self._write_tmp('''
            test('x', async ({ page }) => {
                await page.getByTestId('x').click();
            });
        ''')
        results = parse_file(path)
        self.assertTrue(all(s.language == "typescript" for s in results))
        os.unlink(path)


class TestSelectorToQapal(unittest.TestCase):

    def test_testid(self):
        p = ParsedSelector("f.py", 1, "testid", "email", 'page.get_by_test_id("email")', None, "python")
        q = selector_to_qapal(p)
        self.assertEqual(q["strategy"], "testid")
        self.assertEqual(q["value"], "email")

    def test_role(self):
        p = ParsedSelector("f.py", 1, "role", {"role": "button", "name": "X"}, 'page.get_by_role("button", name="X")', None, "python")
        q = selector_to_qapal(p)
        self.assertEqual(q["strategy"], "role")
        self.assertEqual(q["value"]["role"], "button")

    def test_css(self):
        p = ParsedSelector("f.py", 1, "css", ".btn", 'page.locator(".btn")', None, "python")
        q = selector_to_qapal(p)
        self.assertEqual(q["strategy"], "css")

    def test_aria_label(self):
        p = ParsedSelector("f.py", 1, "aria_label", "Close", 'page.locator("[aria-label=Close]")', None, "python")
        q = selector_to_qapal(p)
        self.assertEqual(q["strategy"], "aria-label")


class TestQapalToExpression(unittest.TestCase):

    def test_testid_python(self):
        expr = _qapal_to_python("testid", "email")
        self.assertEqual(expr, 'page.get_by_test_id("email")')

    def test_testid_typescript(self):
        expr = _qapal_to_typescript("testid", "email")
        self.assertEqual(expr, "page.getByTestId('email')")

    def test_role_python(self):
        expr = _qapal_to_python("role", {"role": "button", "name": "Submit"})
        self.assertEqual(expr, 'page.get_by_role("button", name="Submit")')

    def test_role_typescript(self):
        expr = _qapal_to_typescript("role", {"role": "button", "name": "Submit"})
        self.assertEqual(expr, "page.getByRole('button', { name: 'Submit' })")

    def test_role_no_name_python(self):
        expr = _qapal_to_python("role", {"role": "heading"})
        self.assertEqual(expr, 'page.get_by_role("heading")')

    def test_text_python(self):
        expr = _qapal_to_python("text", "Hello World")
        self.assertEqual(expr, 'page.get_by_text("Hello World")')

    def test_label_python(self):
        expr = _qapal_to_python("label", "Username")
        self.assertEqual(expr, 'page.get_by_label("Username")')

    def test_placeholder_python(self):
        expr = _qapal_to_python("placeholder", "Search...")
        self.assertEqual(expr, 'page.get_by_placeholder("Search...")')

    def test_css_python(self):
        expr = _qapal_to_python("css", ".submit")
        self.assertEqual(expr, 'page.locator(".submit")')

    def test_id_python(self):
        expr = _qapal_to_python("id", "main")
        self.assertEqual(expr, 'page.locator("#main")')

    def test_aria_label_python(self):
        expr = _qapal_to_python("aria-label", "Close dialog")
        self.assertIn("aria-label", expr)
        self.assertIn("Close dialog", expr)

    def test_roundtrip_qapal_to_expression(self):
        """qapal_to_expression dispatches correctly."""
        py = qapal_to_expression({"strategy": "testid", "value": "x"}, "python")
        ts = qapal_to_expression({"strategy": "testid", "value": "x"}, "typescript")
        self.assertIn("get_by_test_id", py)
        self.assertIn("getByTestId", ts)


# ===================================================================
# Patcher tests
# ===================================================================

from patcher import (
    Patch,
    apply_patch,
    apply_patches,
    format_patch_summary,
    generate_patch,
    preview_patches,
)


class TestApplyPatch(unittest.TestCase):

    def test_apply_single_patch(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        tmp.write('    page.locator(".btn").click()\n')
        tmp.close()

        patch = Patch(
            file_path=tmp.name,
            line_number=1,
            old_expression='page.locator(".btn")',
            new_expression='page.get_by_test_id("submit")',
            old_selector={"strategy": "css", "value": ".btn"},
            new_selector={"strategy": "testid", "value": "submit"},
            confidence=0.95,
            reason="test",
        )
        result = apply_patch(patch)
        self.assertTrue(result)

        content = Path(tmp.name).read_text()
        self.assertIn('page.get_by_test_id("submit")', content)
        self.assertNotIn('page.locator(".btn")', content)
        os.unlink(tmp.name)

    def test_apply_patch_preserves_indentation(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        tmp.write('        page.locator(".btn").click()\n')
        tmp.close()

        patch = Patch(
            file_path=tmp.name, line_number=1,
            old_expression='page.locator(".btn")',
            new_expression='page.get_by_test_id("submit")',
            old_selector={}, new_selector={}, confidence=0.95, reason="",
        )
        apply_patch(patch)
        content = Path(tmp.name).read_text()
        self.assertTrue(content.startswith("        "))  # 8 spaces preserved
        os.unlink(tmp.name)

    def test_apply_patch_nonexistent_file(self):
        patch = Patch(
            file_path="/tmp/nonexistent_12345.py", line_number=1,
            old_expression="x", new_expression="y",
            old_selector={}, new_selector={}, confidence=0.5, reason="",
        )
        self.assertFalse(apply_patch(patch))

    def test_apply_patch_wrong_line(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        tmp.write('line1\nline2\npage.locator(".btn").click()\n')
        tmp.close()

        patch = Patch(
            file_path=tmp.name, line_number=1,  # wrong line — it's on line 3
            old_expression='page.locator(".btn")',
            new_expression='page.get_by_test_id("x")',
            old_selector={}, new_selector={}, confidence=0.9, reason="",
        )
        # Should search nearby lines and find it
        result = apply_patch(patch)
        # Line 1 doesn't have it, nearby search checks line 2 (offset -1/+1/+2)
        # Line 3 is offset +2 from line 1, which IS checked
        self.assertTrue(result)
        os.unlink(tmp.name)


class TestApplyPatches(unittest.TestCase):

    def test_multiple_patches_bottom_to_top(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        tmp.write('page.locator(".a").click()\npage.locator(".b").click()\npage.locator(".c").click()\n')
        tmp.close()

        patches = [
            Patch(tmp.name, 1, 'page.locator(".a")', 'page.get_by_test_id("a")', {}, {}, 0.9, ""),
            Patch(tmp.name, 3, 'page.locator(".c")', 'page.get_by_test_id("c")', {}, {}, 0.9, ""),
        ]
        succeeded, failed = apply_patches(patches)
        self.assertEqual(succeeded, 2)
        self.assertEqual(failed, 0)

        content = Path(tmp.name).read_text()
        self.assertIn('get_by_test_id("a")', content)
        self.assertIn('get_by_test_id("c")', content)
        self.assertIn('page.locator(".b")', content)  # untouched
        os.unlink(tmp.name)


class TestPreviewPatches(unittest.TestCase):

    def test_produces_unified_diff(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
        tmp.write('page.locator(".btn").click()\n')
        tmp.close()

        patches = [
            Patch(tmp.name, 1, 'page.locator(".btn")', 'page.get_by_test_id("submit")', {}, {}, 0.95, ""),
        ]
        diff = preview_patches(patches)
        self.assertIn("---", diff)
        self.assertIn("+++", diff)
        self.assertIn("-page.locator", diff)
        self.assertIn("+page.get_by_test_id", diff)
        os.unlink(tmp.name)


class TestFormatPatchSummary(unittest.TestCase):

    def test_no_patches(self):
        self.assertIn("No patches", format_patch_summary([]))

    def test_with_patches(self):
        patches = [
            Patch("/tmp/f.py", 10, 'page.locator(".x")', 'page.get_by_test_id("x")', {}, {}, 0.9, "upgraded"),
        ]
        summary = format_patch_summary(patches)
        self.assertIn("1 selector replacement", summary)
        self.assertIn("f.py:10", summary)


class TestGeneratePatch(unittest.TestCase):

    def test_generates_correct_patch(self):
        parsed = ParsedSelector(
            file_path="test.py", line_number=5,
            selector_type="css", value=".btn",
            full_expression='page.locator(".btn")',
            action="click", language="python",
        )
        new_sel = {"strategy": "testid", "value": "submit"}
        patch = generate_patch(parsed, new_sel, confidence=0.95)

        self.assertEqual(patch.file_path, "test.py")
        self.assertEqual(patch.line_number, 5)
        self.assertEqual(patch.old_expression, 'page.locator(".btn")')
        self.assertIn("get_by_test_id", patch.new_expression)
        self.assertEqual(patch.confidence, 0.95)

    def test_typescript_patch(self):
        parsed = ParsedSelector(
            file_path="test.spec.ts", line_number=10,
            selector_type="css", value=".btn",
            full_expression="page.locator('.btn')",
            action="click", language="typescript",
        )
        new_sel = {"strategy": "testid", "value": "submit"}
        patch = generate_patch(parsed, new_sel, confidence=0.9)
        self.assertIn("getByTestId", patch.new_expression)


# ===================================================================
# Scaffold tests
# ===================================================================

from scaffold import (
    _element_label,
    _url_to_name,
    generate_python_scaffold,
    generate_typescript_scaffold,
)


class FakeElement:
    """Minimal element mock for scaffold tests."""
    def __init__(self, role="button", name="Submit", testid=None, aria_label=None, tag="button",
                 best_selector=None, confidence=0.9):
        self.role = role
        self.name = name
        self.testid = testid
        self.aria_label = aria_label
        self.tag = tag
        self.best_selector = best_selector or {"strategy": "role", "value": {"role": role, "name": name}}
        self.confidence = confidence


class TestUrlToName(unittest.TestCase):

    def test_simple_path(self):
        self.assertEqual(_url_to_name("https://app.com/login"), "login")

    def test_nested_path(self):
        self.assertEqual(_url_to_name("https://app.com/auth/login"), "auth_login")

    def test_root_path(self):
        self.assertEqual(_url_to_name("https://app.com/"), "home")

    def test_hyphenated_path(self):
        self.assertEqual(_url_to_name("https://app.com/my-account"), "my_account")

    def test_truncates_long_paths(self):
        name = _url_to_name("https://app.com/" + "/".join(["a"] * 100))
        self.assertLessEqual(len(name), 60)


class TestElementLabel(unittest.TestCase):

    def test_role_and_name(self):
        elem = FakeElement(role="button", name="Submit")
        self.assertIn("Button", _element_label(elem))
        self.assertIn("Submit", _element_label(elem))

    def test_testid_fallback(self):
        elem = FakeElement(role="input", name=None, testid="email-input")
        label = _element_label(elem)
        self.assertIn("email-input", label)

    def test_empty_element(self):
        elem = FakeElement(role=None, name=None, testid=None, tag="div")
        self.assertEqual(_element_label(elem), "div")


class TestGeneratePythonScaffold(unittest.TestCase):

    def test_contains_import(self):
        scaffold = generate_python_scaffold("https://app.com/login", [])
        self.assertIn("from playwright.sync_api import Page, expect", scaffold)

    def test_contains_test_function(self):
        scaffold = generate_python_scaffold("https://app.com/login", [])
        self.assertIn("def test_login(page: Page):", scaffold)

    def test_contains_goto(self):
        scaffold = generate_python_scaffold("https://app.com/login", [])
        self.assertIn('page.goto("https://app.com/login"', scaffold)

    def test_contains_element_comments(self):
        elements = [FakeElement(role="button", name="Submit", confidence=0.9)]
        scaffold = generate_python_scaffold("https://app.com/", elements)
        self.assertIn("Submit", scaffold)
        self.assertIn("#", scaffold)  # comment line

    def test_custom_function_name(self):
        scaffold = generate_python_scaffold("https://app.com/", [], function_name="test_custom")
        self.assertIn("def test_custom(page: Page):", scaffold)

    def test_generated_header(self):
        scaffold = generate_python_scaffold("https://app.com/", [])
        self.assertIn("Auto-generated scaffold by QAPAL", scaffold)


class TestGenerateTypeScriptScaffold(unittest.TestCase):

    def test_contains_import(self):
        scaffold = generate_typescript_scaffold("https://app.com/login", [])
        self.assertIn("import { test, expect } from '@playwright/test'", scaffold)

    def test_contains_test_block(self):
        scaffold = generate_typescript_scaffold("https://app.com/login", [])
        self.assertIn("test('login page'", scaffold)

    def test_contains_goto(self):
        scaffold = generate_typescript_scaffold("https://app.com/login", [])
        self.assertIn("await page.goto('https://app.com/login')", scaffold)

    def test_contains_element_comments(self):
        elements = [FakeElement(role="link", name="Home", confidence=0.85)]
        scaffold = generate_typescript_scaffold("https://app.com/", elements)
        self.assertIn("Home", scaffold)
        self.assertIn("//", scaffold)


# ===================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
