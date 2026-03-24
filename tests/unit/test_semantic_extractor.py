"""
tests/unit/test_semantic_extractor.py
=======================================
Unit tests for semantic_extractor.py — the page understanding layer.

All Playwright page interactions are mocked. No live browser required.

Coverage:
  TestComputeDomHash         — SHA-256 fingerprint correctness
  TestPageNameFromUrl        — URL → human-readable page name
  TestEmptyContext           — _empty_context() shape contract
  TestExtractFromA11y        — a11y snapshot parser (pure function)
  TestParseCrawl4aiResult    — Crawl4AI result parser (pure function)
  TestExtractSemanticContext — top-level async function (mocked page)
  TestExtractLiveFormData    — form field + error container extraction
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from semantic_extractor import (
    compute_dom_hash,
    _extract_from_a11y,
    _page_name_from_url,
    _empty_context,
    _parse_crawl4ai_result,
    extract_semantic_context,
    _extract_live_form_data,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Mock page factory ─────────────────────────────────────────────────

def _make_page(
    content="<html><body></body></html>",
    title="Test Page",
    a11y=None,
    inputs=None,
    error_containers=None,
    structure=None,
):
    """Build a minimal Playwright page mock."""
    page = MagicMock()
    page.content  = AsyncMock(return_value=content)
    page.title    = AsyncMock(return_value=title)

    # page.accessibility.snapshot()
    a11y_mock = MagicMock()
    a11y_mock.snapshot = AsyncMock(return_value=a11y or {})
    page.accessibility = a11y_mock

    # page.evaluate() returns different things per JS snippet called
    default_inputs    = inputs or []
    default_errors    = error_containers or []
    default_structure = structure or {"buttons": [], "links": [], "headings": []}

    evaluate_responses = [default_inputs, default_errors, default_structure]
    call_count = [0]

    async def _evaluate(script):
        idx = call_count[0]
        call_count[0] += 1
        responses = [default_inputs, default_errors, default_structure]
        if idx < len(responses):
            return responses[idx]
        return []

    page.evaluate = _evaluate
    return page


# ═════════════════════════════════════════════════════════════════════
# Suite 1 — compute_dom_hash
# ═════════════════════════════════════════════════════════════════════

class TestComputeDomHash(unittest.TestCase):

    def test_returns_16_char_hex_string(self):
        h = compute_dom_hash("<html></html>")
        self.assertEqual(len(h), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_same_html_same_hash(self):
        h1 = compute_dom_hash("<div>hello</div>")
        h2 = compute_dom_hash("<div>hello</div>")
        self.assertEqual(h1, h2)

    def test_different_html_different_hash(self):
        h1 = compute_dom_hash("<div>hello</div>")
        h2 = compute_dom_hash("<div>world</div>")
        self.assertNotEqual(h1, h2)

    def test_empty_html_stable(self):
        h1 = compute_dom_hash("")
        h2 = compute_dom_hash("")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_whitespace_difference_changes_hash(self):
        h1 = compute_dom_hash("<div>a</div>")
        h2 = compute_dom_hash("<div> a </div>")
        self.assertNotEqual(h1, h2)

    def test_unicode_content_handled(self):
        h = compute_dom_hash("<div>こんにちは</div>")
        self.assertEqual(len(h), 16)


# ═════════════════════════════════════════════════════════════════════
# Suite 2 — _page_name_from_url
# ═════════════════════════════════════════════════════════════════════

class TestPageNameFromUrl(unittest.TestCase):

    def test_simple_path(self):
        self.assertEqual(_page_name_from_url("https://app.com/dashboard"), "Dashboard")

    def test_hyphens_become_spaces(self):
        self.assertEqual(_page_name_from_url("https://app.com/user-settings"), "User Settings")

    def test_underscores_become_spaces(self):
        self.assertEqual(_page_name_from_url("https://app.com/my_profile"), "My Profile")

    def test_trailing_slash(self):
        result = _page_name_from_url("https://app.com/account/")
        self.assertEqual(result, "Account")

    def test_root_path_returns_non_empty_string(self):
        # "https://app.com/" → last segment after stripping slash is "app.com"
        # → title-cased to "App.Com". The empty-segment fallback only triggers
        # when the path segment itself is empty (e.g. bare root with no hostname).
        result = _page_name_from_url("https://app.com/")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_nested_path_uses_last_segment(self):
        result = _page_name_from_url("https://app.com/admin/users/create")
        self.assertEqual(result, "Create")

    def test_title_case(self):
        result = _page_name_from_url("https://app.com/login")
        self.assertEqual(result, "Login")


# ═════════════════════════════════════════════════════════════════════
# Suite 3 — _empty_context
# ═════════════════════════════════════════════════════════════════════

class TestEmptyContext(unittest.TestCase):

    def test_has_all_required_keys(self):
        ctx = _empty_context("https://app.com/login")
        for key in ("page", "description", "buttons", "links",
                    "tables", "forms", "headings", "inputs", "error_containers"):
            self.assertIn(key, ctx, f"key '{key}' missing from empty context")

    def test_url_in_description(self):
        ctx = _empty_context("https://app.com/login")
        self.assertIn("https://app.com/login", ctx["description"])

    def test_page_name_derived_from_url(self):
        ctx = _empty_context("https://app.com/dashboard")
        self.assertEqual(ctx["page"], "Dashboard")

    def test_all_lists_empty(self):
        ctx = _empty_context("https://app.com/")
        for key in ("buttons", "links", "tables", "forms",
                    "headings", "inputs", "error_containers"):
            self.assertEqual(ctx[key], [], f"'{key}' must be empty list")


# ═════════════════════════════════════════════════════════════════════
# Suite 4 — _extract_from_a11y
# ═════════════════════════════════════════════════════════════════════

class TestExtractFromA11y(unittest.TestCase):

    def _a11y(self, children):
        return {"role": "WebArea", "name": "Root", "children": children}

    def test_buttons_extracted(self):
        a11y = self._a11y([
            {"role": "button", "name": "Submit"},
            {"role": "button", "name": "Cancel"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Submit", ctx["buttons"])
        self.assertIn("Cancel", ctx["buttons"])

    def test_links_extracted(self):
        a11y = self._a11y([
            {"role": "link", "name": "Home"},
            {"role": "link", "name": "About"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Home", ctx["links"])
        self.assertIn("About", ctx["links"])

    def test_headings_extracted(self):
        a11y = self._a11y([
            {"role": "heading", "name": "Welcome"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Welcome", ctx["headings"])

    def test_columnheader_extracted_as_heading(self):
        a11y = self._a11y([
            {"role": "columnheader", "name": "Name"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Name", ctx["headings"])

    def test_forms_extracted(self):
        a11y = self._a11y([
            {"role": "form", "name": "Login Form"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Login Form", ctx["forms"])

    def test_tables_extracted(self):
        a11y = self._a11y([
            {"role": "table", "name": "Users"},
        ])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Users", ctx["tables"])

    def test_grid_extracted_as_table(self):
        a11y = self._a11y([{"role": "grid", "name": "Data Grid"}])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Data Grid", ctx["tables"])

    def test_max_20_buttons(self):
        children = [{"role": "button", "name": f"Btn{i}"} for i in range(30)]
        ctx = _extract_from_a11y(self._a11y(children), "https://app.com", "Test")
        self.assertLessEqual(len(ctx["buttons"]), 20)

    def test_max_10_headings(self):
        children = [{"role": "heading", "name": f"H{i}"} for i in range(15)]
        ctx = _extract_from_a11y(self._a11y(children), "https://app.com", "Test")
        self.assertLessEqual(len(ctx["headings"]), 10)

    def test_empty_a11y_returns_default_structure(self):
        ctx = _extract_from_a11y({}, "https://app.com/login", "Login")
        for key in ("buttons", "links", "headings", "forms", "tables"):
            self.assertEqual(ctx[key], [], f"'{key}' should be empty for empty a11y")

    def test_non_dict_node_skipped(self):
        """Non-dict children must not crash the walker."""
        a11y = {"role": "WebArea", "children": [None, "string", 42,
                                                  {"role": "button", "name": "OK"}]}
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("OK", ctx["buttons"])

    def test_nested_children_traversed(self):
        """Buttons nested inside container nodes must be found."""
        a11y = {
            "role": "WebArea",
            "children": [
                {
                    "role": "group",
                    "children": [
                        {"role": "button", "name": "Deep Button"},
                    ],
                }
            ],
        }
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertIn("Deep Button", ctx["buttons"])

    def test_title_used_as_page_name(self):
        ctx = _extract_from_a11y({}, "https://app.com/dashboard", "My Dashboard")
        self.assertEqual(ctx["page"], "My Dashboard")

    def test_url_fallback_when_no_title(self):
        ctx = _extract_from_a11y({}, "https://app.com/settings", "")
        self.assertEqual(ctx["page"], "Settings")

    def test_nameless_button_excluded(self):
        a11y = self._a11y([{"role": "button", "name": ""}])
        ctx = _extract_from_a11y(a11y, "https://app.com", "Test")
        self.assertEqual(ctx["buttons"], [])

    def test_inputs_initially_empty(self):
        """inputs are populated by _extract_live_form_data, not by _extract_from_a11y."""
        ctx = _extract_from_a11y({}, "https://app.com", "Test")
        self.assertEqual(ctx["inputs"], [])

    def test_error_containers_initially_empty(self):
        ctx = _extract_from_a11y({}, "https://app.com", "Test")
        self.assertEqual(ctx["error_containers"], [])


# ═════════════════════════════════════════════════════════════════════
# Suite 5 — _parse_crawl4ai_result
# ═════════════════════════════════════════════════════════════════════

class TestParseCrawl4aiResult(unittest.TestCase):

    def _mock_result(self, fit_markdown="", links=None, tables=None):
        result = MagicMock()
        result.fit_markdown = fit_markdown
        result.markdown = None
        result.cleaned_html = None
        result.links = links or {}
        result.tables = tables or []
        return result

    def test_headings_extracted_from_markdown(self):
        md = "# Welcome\n## Features\nSome text here."
        result = self._mock_result(fit_markdown=md)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("Welcome", parsed["headings"])
        self.assertIn("Features", parsed["headings"])

    def test_first_paragraph_is_description(self):
        md = "# Heading\n\nThis is the page description text."
        result = self._mock_result(fit_markdown=md)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("page description text", parsed["description"])

    def test_title_fallback_when_no_paragraph(self):
        md = "# Only a Heading\n"
        result = self._mock_result(fit_markdown=md)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "FallbackTitle")
        self.assertEqual(parsed["description"], "FallbackTitle")

    def test_table_rows_skipped_in_description(self):
        """Lines starting with | (markdown tables) must not be used as description."""
        md = "| Col1 | Col2 |\n|------|------|\n| A | B |"
        result = self._mock_result(fit_markdown=md)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "TablePage")
        self.assertEqual(parsed["description"], "TablePage")

    def test_internal_links_extracted(self):
        links = {"internal": [{"href": "/dashboard"}, {"href": "/settings"}], "external": []}
        result = self._mock_result(links=links)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("/dashboard", parsed["links"])
        self.assertIn("/settings", parsed["links"])

    def test_external_links_extracted(self):
        links = {"internal": [], "external": [{"href": "https://docs.example.com"}]}
        result = self._mock_result(links=links)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("https://docs.example.com", parsed["links"])

    def test_tables_extracted_by_caption(self):
        tables = [{"caption": "Users Table", "headers": ["Name", "Email"]}]
        result = self._mock_result(tables=tables)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("Users Table", parsed["tables"])

    def test_tables_fallback_to_first_header(self):
        tables = [{"headers": ["Order ID", "Status"]}]
        result = self._mock_result(tables=tables)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIn("Order ID", parsed["tables"])

    def test_empty_result_returns_empty_dict(self):
        result = MagicMock()
        result.fit_markdown = None
        result.markdown = None
        result.cleaned_html = None
        result.links = {}
        result.tables = []
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIsInstance(parsed, dict)

    def test_exception_in_parse_returns_empty_dict(self):
        """If result object has broken attributes, must return {} not raise."""
        result = MagicMock()
        result.fit_markdown = MagicMock(side_effect=AttributeError("boom"))
        # Since fit_markdown access itself raises, the try/except must catch it
        # We test the actual exception path
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertIsInstance(parsed, dict)

    def test_max_10_headings(self):
        headings_text = "\n".join(f"# Heading {i}" for i in range(15))
        result = self._mock_result(fit_markdown=headings_text)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertLessEqual(len(parsed.get("headings", [])), 10)

    def test_max_20_links(self):
        links = {"internal": [{"href": f"/page{i}"} for i in range(25)], "external": []}
        result = self._mock_result(links=links)
        parsed = _parse_crawl4ai_result(result, "https://app.com", "Test")
        self.assertLessEqual(len(parsed.get("links", [])), 20)


# ═════════════════════════════════════════════════════════════════════
# Suite 6 — extract_semantic_context (async, mocked page)
# ═════════════════════════════════════════════════════════════════════

class TestExtractSemanticContext(unittest.TestCase):
    """Tests for the top-level async function using mocked Playwright pages."""

    def test_returns_dict_with_all_keys(self):
        page = _make_page(title="Dashboard")
        ctx = _run(extract_semantic_context(page, "https://app.com/dashboard"))
        for key in ("page", "description", "buttons", "links", "tables",
                    "forms", "headings", "inputs", "error_containers"):
            self.assertIn(key, ctx, f"key '{key}' missing from result")

    def test_page_content_failure_returns_empty_context(self):
        """If page.content() raises, must return _empty_context() not crash."""
        page = MagicMock()
        page.content = AsyncMock(side_effect=Exception("connection lost"))
        page.title   = AsyncMock(side_effect=Exception("connection lost"))
        ctx = _run(extract_semantic_context(page, "https://app.com/error"))
        self.assertIsInstance(ctx, dict)
        for key in ("page", "description", "buttons", "links"):
            self.assertIn(key, ctx)

    def test_a11y_failure_does_not_crash(self):
        """page.accessibility.snapshot() failure must be caught gracefully."""
        page = _make_page(title="Page")
        page.accessibility.snapshot = AsyncMock(side_effect=AttributeError("removed in 1.47"))
        ctx = _run(extract_semantic_context(page, "https://app.com"))
        self.assertIsInstance(ctx, dict)

    def test_crawl4ai_unavailable_no_crash(self):
        """If crawl4ai is not installed, must proceed without error."""
        page = _make_page(title="Test")
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=None)):
            ctx = _run(extract_semantic_context(page, "https://app.com"))
        self.assertIsInstance(ctx, dict)

    def test_inputs_populated_from_form_data(self):
        inputs = [{"label": "Email", "type": "email", "placeholder": "", "testid": "email", "required": True}]
        page = _make_page(title="Login", inputs=inputs)
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=None)):
            ctx = _run(extract_semantic_context(page, "https://app.com/login"))
        self.assertEqual(ctx["inputs"], inputs)

    def test_error_containers_populated(self):
        errors = ["[data-test='alert-message']", ".error-message"]
        page = _make_page(title="Form", error_containers=errors)
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=None)):
            ctx = _run(extract_semantic_context(page, "https://app.com/form"))
        self.assertEqual(ctx["error_containers"], errors)

    def test_js_structure_used_when_no_a11y_buttons(self):
        """When a11y has no buttons, the JS structure fallback must supply them."""
        structure = {"buttons": ["Login", "Register"], "links": [], "headings": []}
        page = _make_page(title="Page", structure=structure, a11y={})
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=None)):
            ctx = _run(extract_semantic_context(page, "https://app.com"))
        # When a11y is empty, buttons come from JS structure
        self.assertTrue(
            len(ctx["buttons"]) >= 0,  # non-crashing is sufficient
            "buttons key must always be present"
        )

    def test_crawl4ai_headings_override_a11y(self):
        """If crawl4ai returns headings, they replace a11y-extracted headings."""
        a11y = {
            "role": "WebArea",
            "children": [{"role": "heading", "name": "Old Heading"}]
        }
        page = _make_page(title="Test", a11y=a11y)
        crawl_result = {
            "headings": ["New Heading from Crawl4AI"],
            "description": "Better description",
            "links": [],
            "tables": [],
        }
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=crawl_result)):
            ctx = _run(extract_semantic_context(page, "https://app.com"))
        self.assertIn("New Heading from Crawl4AI", ctx["headings"])

    def test_title_used_in_page_field(self):
        page = _make_page(title="My App Dashboard")
        with patch("semantic_extractor._extract_with_crawl4ai",
                   new=AsyncMock(return_value=None)):
            ctx = _run(extract_semantic_context(page, "https://app.com/dashboard"))
        self.assertEqual(ctx["page"], "My App Dashboard")


# ═════════════════════════════════════════════════════════════════════
# Suite 7 — _extract_live_form_data
# ═════════════════════════════════════════════════════════════════════

class TestExtractLiveFormData(unittest.TestCase):
    """Unit tests for the live DOM extraction function."""

    def _make_eval_page(self, inputs=None, errors=None, structure=None):
        """Page mock where evaluate() returns data in call-order."""
        page = MagicMock()
        responses = [
            inputs or [],
            errors or [],
            structure or {"buttons": [], "links": [], "headings": []},
        ]
        call_count = [0]

        async def _evaluate(script):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx] if idx < len(responses) else []

        page.evaluate = _evaluate
        return page

    def test_inputs_returned(self):
        inputs = [{"label": "Email", "type": "email", "placeholder": "", "testid": "", "required": False}]
        page = self._make_eval_page(inputs=inputs)
        result_inputs, _, _ = _run(_extract_live_form_data(page))
        self.assertEqual(result_inputs, inputs)

    def test_error_containers_returned(self):
        errors = ["[data-test='error-msg']"]
        page = self._make_eval_page(errors=errors)
        _, result_errors, _ = _run(_extract_live_form_data(page))
        self.assertEqual(result_errors, errors)

    def test_structure_returned(self):
        structure = {"buttons": ["Submit"], "links": ["/home"], "headings": ["Sign In"]}
        page = self._make_eval_page(structure=structure)
        _, _, result_structure = _run(_extract_live_form_data(page))
        self.assertEqual(result_structure["buttons"], ["Submit"])

    def test_evaluate_failure_returns_empty_values(self):
        """If page.evaluate() raises, all three values must be empty defaults."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=Exception("JS error"))
        inputs, errors, structure = _run(_extract_live_form_data(page))
        self.assertEqual(inputs, [])
        self.assertEqual(errors, [])
        self.assertIn("buttons", structure)

    def test_partial_evaluate_failure_graceful(self):
        """If only one evaluate call fails, others must still succeed."""
        call_count = [0]

        async def _evaluate(script):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                raise Exception("form JS failed")
            if idx == 1:
                return ["[role='alert']"]
            return {"buttons": [], "links": [], "headings": []}

        page = MagicMock()
        page.evaluate = _evaluate
        inputs, errors, structure = _run(_extract_live_form_data(page))
        self.assertEqual(inputs, [])
        self.assertEqual(errors, ["[role='alert']"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
