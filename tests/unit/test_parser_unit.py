"""
Unit tests for the AST-based Playwright locator parser in parser.py
Covers: single-line, multi-line, f-strings, variable tracking,
chained locators, and TypeScript fallback.
"""
import textwrap
from pathlib import Path
import tempfile
import pytest

from parser import parse_file, parse_file_ast, detect_language, selector_to_qapal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tmp(content: str, suffix: str = ".py") -> str:
    """Write content to a temp file and return its path."""
    tf = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8")
    tf.write(textwrap.dedent(content))
    tf.flush()
    return tf.name


# ---------------------------------------------------------------------------
# 1. AST Parser – Basic extraction
# ---------------------------------------------------------------------------

class TestASTBasic:
    def test_get_by_test_id(self):
        path = _write_tmp("""
            from playwright.sync_api import Page
            def test_foo(page: Page):
                page.get_by_test_id("email").fill("a@b.com")
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].selector_type == "testid"
        assert results[0].value == "email"

    def test_get_by_role_with_name(self):
        path = _write_tmp("""
            def test_foo(page):
                page.get_by_role("button", name="Submit").click()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].selector_type == "role"
        assert results[0].value == {"role": "button", "name": "Submit"}

    def test_get_by_placeholder(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_placeholder("Search…").fill("python")
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].selector_type == "placeholder"
        assert results[0].value == "Search…"

    def test_locator_id_classified(self):
        path = _write_tmp("""
            def test(page):
                page.locator("#submit-button").click()
        """)
        results = parse_file_ast(path)
        assert results[0].selector_type == "id"
        assert results[0].value == "submit-button"

    def test_locator_css_classified(self):
        path = _write_tmp("""
            def test(page):
                page.locator("div.container > span").hover()
        """)
        results = parse_file_ast(path)
        assert results[0].selector_type == "css"
        assert results[0].value == "div.container > span"

    def test_locator_testid_attribute_classified(self):
        path = _write_tmp("""
            def test(page):
                page.locator('[data-testid="login-btn"]').click()
        """)
        results = parse_file_ast(path)
        assert results[0].selector_type == "testid"
        assert results[0].value == "login-btn"


# ---------------------------------------------------------------------------
# 2. AST Parser – Multi-line & complex expressions
# ---------------------------------------------------------------------------

class TestASTMultiLine:
    def test_multiline_get_by_role(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_role(
                    "button",
                    name="Submit Form"
                ).click()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].selector_type == "role"
        assert results[0].value["name"] == "Submit Form"
        assert results[0].line_number == 3  # line of the call

    def test_locator_with_comment_inside(self):
        path = _write_tmp("""
            def test(page):
                page.locator(
                    ".footer"  # comment here
                ).hover()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].value == ".footer"

    def test_string_concatenation(self):
        path = _write_tmp("""
            def test(page):
                page.locator("button" + "-submit").click()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        assert results[0].value == "button-submit"

    def test_fstring_constant_parts(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_placeholder(f"Search {lang}").click()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        # Dynamic part represented as {v}
        assert "Search" in str(results[0].value)


# ---------------------------------------------------------------------------
# 3. AST Parser – Variable tracking
# ---------------------------------------------------------------------------

class TestASTVariableTracking:
    def test_simple_variable_substitution(self):
        path = _write_tmp("""
            def test(page):
                sel = "#main-container"
                page.locator(sel).hover()
        """)
        results = parse_file_ast(path)
        assert len(results) == 1
        # Container is CSS or ID
        assert results[0].value == "main-container"

    def test_multiple_uses_same_var(self):
        path = _write_tmp("""
            def test(page):
                btn = "login-btn"
                page.get_by_test_id(btn).click()
                page.get_by_test_id(btn).is_visible()
        """)
        results = parse_file_ast(path)
        # Should extract at least 1 (deduplication OK)
        assert any(r.value == "login-btn" for r in results)


# ---------------------------------------------------------------------------
# 4. AST Parser – Chained locators
# ---------------------------------------------------------------------------

class TestASTChained:
    def test_chained_get_by_text(self):
        path = _write_tmp("""
            def test(page):
                page.locator("div.panel").get_by_text("Confirm").click()
        """)
        results = parse_file_ast(path)
        types = {r.selector_type for r in results}
        values = [r.value for r in results]
        assert "text" in types
        assert "Confirm" in values

    def test_action_detection_on_chain(self):
        path = _write_tmp("""
            def test(page):
                page.locator(".footer").hover()
        """)
        results = parse_file_ast(path)
        assert results[0].action == "hover"


# ---------------------------------------------------------------------------
# 5. Line number accuracy
# ---------------------------------------------------------------------------

class TestASTLineNumbers:
    def test_line_number_single(self):
        path = _write_tmp("""
            def test(page):
                x = 1
                page.get_by_test_id("submit").click()
        """)
        results = parse_file_ast(path)
        # Line 4 in the dedented content (1-indexed)
        assert results[0].line_number == 4

    def test_line_number_multiline_points_to_first(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_role(
                    "button",
                    name="OK"
                ).click()
        """)
        results = parse_file_ast(path)
        assert results[0].line_number == 3


# ---------------------------------------------------------------------------
# 6. Full parse_file integration (AST vs. regex dispatch)
# ---------------------------------------------------------------------------

class TestParseFileIntegration:
    def test_python_file_uses_ast(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_test_id("email").fill("x@y.com")
        """, suffix=".py")
        results = parse_file(path)
        assert len(results) >= 1
        assert results[0].selector_type == "testid"

    def test_typescript_file_uses_regex(self):
        path = _write_tmp("""
            test('sample', async ({ page }) => {
                await page.getByTestId('submit').click();
            });
        """, suffix=".ts")
        results = parse_file(path)
        assert len(results) == 1
        assert results[0].selector_type == "testid"
        assert results[0].value == "submit"

    def test_empty_file_returns_empty(self):
        path = _write_tmp("", suffix=".py")
        results = parse_file(path)
        assert results == []


# ---------------------------------------------------------------------------
# 7. selector_to_qapal conversion
# ---------------------------------------------------------------------------

class TestSelectorToQapal:
    def test_testid_conversion(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_test_id("email").fill("a@b")
        """)
        sel = parse_file(path)[0]
        q = selector_to_qapal(sel)
        assert q == {"strategy": "testid", "value": "email"}

    def test_role_conversion_with_name(self):
        path = _write_tmp("""
            def test(page):
                page.get_by_role("button", name="Login").click()
        """)
        sel = parse_file(path)[0]
        q = selector_to_qapal(sel)
        assert q["strategy"] == "role"
        assert q["value"]["role"] == "button"
        assert q["value"]["name"] == "Login"
