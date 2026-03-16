"""
tests/test_new_modules.py — Unit tests for explorer, ux_evaluator, ux_report, vision_client
==============================================================================================
All tests are fully mocked (no network, no browser, no AI calls).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Explorer tests ────────────────────────────────────────────────────

class TestExplorerHelpers(unittest.TestCase):
    """Test Explorer helper methods that don't need a browser."""

    def _make_explorer(self):
        from explorer import Explorer
        db = MagicMock()
        return Explorer(db, vision_client=None, ai_client=None)

    def test_url_pattern_strips_trailing_slash(self):
        exp = self._make_explorer()
        self.assertEqual(exp._url_pattern("https://example.com/page/"), "example.com/page")

    def test_url_pattern_root(self):
        exp = self._make_explorer()
        self.assertEqual(exp._url_pattern("https://example.com/"), "example.com/")

    def test_url_pattern_with_query_and_fragment(self):
        exp = self._make_explorer()
        # Query/fragment are stripped by urlparse path
        result = exp._url_pattern("https://example.com/page?foo=1#bar")
        self.assertEqual(result, "example.com/page")

    def test_extract_json_clean(self):
        from explorer import Explorer
        raw = '{"action": "click", "target": "button"}'
        self.assertEqual(Explorer._extract_json(raw), raw)

    def test_extract_json_with_markdown_fences(self):
        from explorer import Explorer
        raw = '```json\n{"action": "click"}\n```'
        self.assertEqual(Explorer._extract_json(raw), '{"action": "click"}')

    def test_extract_json_with_surrounding_text(self):
        from explorer import Explorer
        raw = 'Here is my answer:\n{"action": "done"}\nThat was it.'
        self.assertEqual(Explorer._extract_json(raw), '{"action": "done"}')

    def test_extract_json_no_json(self):
        from explorer import Explorer
        raw = "No JSON here"
        self.assertEqual(Explorer._extract_json(raw), "No JSON here")

    def test_compress_history_empty(self):
        exp = self._make_explorer()
        result = exp._compress_history([])
        self.assertEqual(result, "(no actions taken yet)")

    def test_compress_history_recent_only(self):
        from explorer import ExplorationStep
        exp = self._make_explorer()
        steps = [
            ExplorationStep(step_index=0, url="https://example.com", action="click", target="button"),
            ExplorationStep(step_index=1, url="https://example.com", action="fill", target="email"),
        ]
        result = exp._compress_history(steps)
        self.assertIn("Step 0: click", result)
        self.assertIn("Step 1: fill", result)

    def test_compress_history_with_older(self):
        from explorer import ExplorationStep, HISTORY_WINDOW
        exp = self._make_explorer()
        # Create more steps than HISTORY_WINDOW
        steps = [
            ExplorationStep(step_index=i, url=f"https://example.com/p{i}", action="click", target=f"btn{i}")
            for i in range(HISTORY_WINDOW + 3)
        ]
        result = exp._compress_history(steps)
        self.assertIn("[Earlier:", result)

    def test_summarise_elements_basic(self):
        exp = self._make_explorer()
        elements = [
            {"role": "button", "name": "Submit", "tag": "button", "loc": {"testid": "submit-btn"}},
            {"role": "link", "name": "Home", "tag": "a", "loc": {}},
        ]
        result = exp._summarise_elements(elements)
        self.assertIn("[button]", result)
        self.assertIn('"Submit"', result)
        self.assertIn("testid=submit-btn", result)
        self.assertIn("[link]", result)

    def test_summarise_elements_skips_non_actionable(self):
        exp = self._make_explorer()
        elements = [
            {"role": "heading", "name": "Title", "tag": "h1", "actionable": False},
            {"role": "button", "name": "Click me", "tag": "button"},
        ]
        result = exp._summarise_elements(elements)
        self.assertNotIn("heading", result)
        self.assertIn("[button]", result)

    def test_summarise_elements_handles_non_dict(self):
        exp = self._make_explorer()
        elements = [None, "not a dict", {"role": "button", "name": "OK", "tag": "button"}]
        result = exp._summarise_elements(elements)
        self.assertIn("[button]", result)

    def test_summarise_elements_caps_at_40(self):
        exp = self._make_explorer()
        elements = [{"role": "button", "name": f"Btn{i}", "tag": "button"} for i in range(60)]
        result = exp._summarise_elements(elements)
        lines = [l for l in result.split("\n") if l.strip()]
        self.assertLessEqual(len(lines), 40)

    def test_heuristic_next_action_clicks_unvisited(self):
        from explorer import ExplorationStep
        exp = self._make_explorer()
        a11y_summary = '[button] "Submit"\n[link] "Home"\n[link] "About"'
        history = [ExplorationStep(step_index=0, url="x", action="click", target='[button] "Submit"')]
        result = exp._heuristic_next_action(a11y_summary, history)
        self.assertEqual(result["action"], "click")
        self.assertEqual(result["selector"]["value"], "Home")

    def test_heuristic_next_action_done_when_all_visited(self):
        from explorer import ExplorationStep
        exp = self._make_explorer()
        a11y_summary = '[button] "Submit"'
        history = [ExplorationStep(step_index=0, url="x", action="click", target='[button] "Submit"')]
        result = exp._heuristic_next_action(a11y_summary, history)
        self.assertEqual(result["action"], "done")

    def test_heuristic_next_action_no_quotes_safe(self):
        """Regression: _heuristic_next_action used to crash with IndexError on unquoted targets."""
        from explorer import ExplorationStep
        exp = self._make_explorer()
        a11y_summary = '[button] testid=submit'
        history = []
        result = exp._heuristic_next_action(a11y_summary, history)
        self.assertEqual(result["action"], "click")
        # Should not crash — value falls back to full target text
        self.assertIn("value", result["selector"])

    def test_parse_observation_valid(self):
        exp = self._make_explorer()
        raw = json.dumps({
            "findings": [
                {"severity": "high", "category": "layout", "description": "Overlapping buttons"}
            ],
            "page_summary": "Login page"
        })
        findings = exp._parse_observation(raw, "https://ex.com", 0, "/tmp/shot.png")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "high")
        self.assertEqual(findings[0]["url"], "https://ex.com")
        self.assertEqual(findings[0]["source"], "vision_observation")

    def test_parse_observation_invalid_json(self):
        exp = self._make_explorer()
        findings = exp._parse_observation("not json", "https://ex.com", 0, "/tmp/shot.png")
        self.assertEqual(findings, [])

    def test_save_trace_creates_json(self):
        from explorer import ExplorationTrace, ExplorationStep
        exp = self._make_explorer()
        trace = ExplorationTrace(
            session_id="test123", start_url="https://ex.com", goal="test",
            steps=[ExplorationStep(step_index=0, url="https://ex.com", action="click", target="btn")],
            ux_findings=[{"severity": "low", "description": "minor"}],
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trace.json"
            exp._save_trace(trace, path)
            data = json.loads(path.read_text())
            self.assertEqual(data["session_id"], "test123")
            self.assertEqual(len(data["steps"]), 1)
            self.assertEqual(len(data["ux_findings"]), 1)


# ── UX Evaluator tests ───────────────────────────────────────────────

class TestUXEvaluator(unittest.TestCase):
    """Test UXEvaluator DOM rule evaluation and scoring."""

    def _make_evaluator(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        return UXEvaluator(db)

    def test_evaluate_dom_results_missing_alt(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [{"src": "logo.png", "selector": "img.logo"}],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "Title"}],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        alt_findings = [f for f in findings if f.heuristic == "WCAG_ALT_TEXT"]
        self.assertEqual(len(alt_findings), 1)
        self.assertEqual(alt_findings[0].severity, "medium")
        self.assertIn("logo.png", alt_findings[0].description)

    def test_evaluate_dom_results_small_tap_targets(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [
                {"tag": "button", "text": "X", "width": 20, "height": 20, "selector": ""},
                {"tag": "a", "text": "Link", "width": 100, "height": 50, "selector": ""},  # fine
            ],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "T"}],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        tap_findings = [f for f in findings if f.heuristic == "WCAG_TAP_TARGET"]
        # Only the 20x20 button should be flagged (100x50 is above default 44px)
        self.assertEqual(len(tap_findings), 1)
        self.assertIn("20x20", tap_findings[0].description)

    def test_evaluate_dom_results_dead_links(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [{"text": "Click here", "href": "#"}],
            "heading_hierarchy": [],
            "page_has_h1": False,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        dead = [f for f in findings if f.heuristic == "UX_DEAD_LINK"]
        self.assertEqual(len(dead), 1)

    def test_evaluate_dom_results_heading_skip(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [
                {"level": 1, "text": "Title"},
                {"level": 3, "text": "Subtitle"},  # skipped H2
            ],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        skip = [f for f in findings if "skipped" in f.description]
        self.assertEqual(len(skip), 1)
        self.assertIn("H1 → H3", skip[0].description)

    def test_evaluate_dom_results_missing_h1(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 2, "text": "Sub"}],
            "page_has_h1": False,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        h1 = [f for f in findings if "H1" in f.description]
        self.assertEqual(len(h1), 1)

    def test_evaluate_dom_results_input_without_label_high(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "T"}],
            "page_has_h1": True,
            "inputs_without_labels": [
                {"tag": "input", "type": "text", "name": "email", "has_placeholder": False, "has_title": False, "selector": ""},
            ],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        label = [f for f in findings if f.heuristic == "WCAG_LABELS"]
        self.assertEqual(len(label), 1)
        self.assertEqual(label[0].severity, "high")

    def test_evaluate_dom_results_input_with_placeholder_low(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "T"}],
            "page_has_h1": True,
            "inputs_without_labels": [
                {"tag": "input", "type": "text", "name": "email", "placeholder": "Enter email",
                 "has_placeholder": True, "has_title": False, "selector": ""},
            ],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        label = [f for f in findings if f.heuristic == "WCAG_LABELS"]
        self.assertEqual(len(label), 1)
        self.assertEqual(label[0].severity, "low")

    def test_evaluate_dom_results_orphan_form(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "T"}],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [{"action": "/search", "fields": 2}],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        orphan = [f for f in findings if f.heuristic == "UX_ORPHAN_FORM"]
        self.assertEqual(len(orphan), 1)
        self.assertIn("2 fields", orphan[0].description)

    def test_evaluate_dom_results_missing_landmarks(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "T"}],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": True,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        landmarks = [f for f in findings if "landmarks" in f.description]
        self.assertEqual(len(landmarks), 1)

    def test_evaluate_dom_results_clean_page(self):
        ev = self._make_evaluator()
        data = {
            "missing_alt_text": [],
            "small_tap_targets": [],
            "empty_links": [],
            "heading_hierarchy": [{"level": 1, "text": "Home"}],
            "page_has_h1": True,
            "inputs_without_labels": [],
            "orphan_forms": [],
            "missing_landmarks": False,
        }
        findings = ev._evaluate_dom_results(data, "https://ex.com")
        self.assertEqual(len(findings), 0)


class TestUXScoring(unittest.TestCase):

    def test_score_no_findings(self):
        from ux_evaluator import UXEvaluator
        self.assertEqual(UXEvaluator.compute_score([]), 100.0)

    def test_score_deduction_high(self):
        from ux_evaluator import UXEvaluator, UXFinding
        findings = [UXFinding(heuristic="X", severity="high", category="a", description="d")]
        self.assertEqual(UXEvaluator.compute_score(findings), 92.0)

    def test_score_deduction_medium(self):
        from ux_evaluator import UXEvaluator, UXFinding
        findings = [UXFinding(heuristic="X", severity="medium", category="a", description="d")]
        self.assertEqual(UXEvaluator.compute_score(findings), 97.0)

    def test_score_deduction_low(self):
        from ux_evaluator import UXEvaluator, UXFinding
        findings = [UXFinding(heuristic="X", severity="low", category="a", description="d")]
        self.assertEqual(UXEvaluator.compute_score(findings), 99.0)

    def test_score_floors_at_zero(self):
        from ux_evaluator import UXEvaluator, UXFinding
        findings = [UXFinding(heuristic="X", severity="high", category="a", description="d")] * 20
        self.assertEqual(UXEvaluator.compute_score(findings), 0.0)

    def test_score_mixed_severities(self):
        from ux_evaluator import UXEvaluator, UXFinding
        findings = [
            UXFinding(heuristic="X", severity="high", category="a", description="d"),   # -8
            UXFinding(heuristic="X", severity="medium", category="a", description="d"), # -3
            UXFinding(heuristic="X", severity="low", category="a", description="d"),    # -1
        ]
        self.assertEqual(UXEvaluator.compute_score(findings), 88.0)


class TestUXAuditResult(unittest.TestCase):

    def test_severity_counts(self):
        from ux_evaluator import UXAuditResult, UXFinding
        audit = UXAuditResult(
            urls=["https://ex.com"],
            findings=[
                UXFinding(heuristic="X", severity="high", category="a", description="d"),
                UXFinding(heuristic="X", severity="high", category="a", description="d"),
                UXFinding(heuristic="X", severity="medium", category="a", description="d"),
                UXFinding(heuristic="X", severity="low", category="a", description="d"),
            ],
        )
        self.assertEqual(audit.severity_counts, {"high": 2, "medium": 1, "low": 1})

    def test_grade_a(self):
        from ux_evaluator import UXAuditResult
        self.assertEqual(UXAuditResult(urls=[], score=95).grade, "A")

    def test_grade_b(self):
        from ux_evaluator import UXAuditResult
        self.assertEqual(UXAuditResult(urls=[], score=85).grade, "B")

    def test_grade_c(self):
        from ux_evaluator import UXAuditResult
        self.assertEqual(UXAuditResult(urls=[], score=75).grade, "C")

    def test_grade_d(self):
        from ux_evaluator import UXAuditResult
        self.assertEqual(UXAuditResult(urls=[], score=65).grade, "D")

    def test_grade_f(self):
        from ux_evaluator import UXAuditResult
        self.assertEqual(UXAuditResult(urls=[], score=55).grade, "F")


class TestUXStaticAudit(unittest.TestCase):

    def test_static_audit_no_locators(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        db.get_all.return_value = []
        ev = UXEvaluator(db)
        findings = ev.audit_static("https://ex.com")
        self.assertEqual(len(findings), 1)
        self.assertIn("not been crawled", findings[0].description)

    def test_static_audit_button_without_name(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        db.get_all.return_value = [
            {"identity": {"role": "button", "name": ""}, "url": "https://ex.com"},
        ]
        ev = UXEvaluator(db)
        findings = ev.audit_static("https://ex.com")
        self.assertTrue(any("Button without accessible name" in f.description for f in findings))

    def test_static_audit_textbox_without_name(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        db.get_all.return_value = [
            {"identity": {"role": "textbox", "name": ""}, "url": "https://ex.com"},
        ]
        ev = UXEvaluator(db)
        findings = ev.audit_static("https://ex.com")
        self.assertTrue(any("Form input without label" in f.description for f in findings))

    def test_static_audit_link_without_text(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        db.get_all.return_value = [
            {"identity": {"role": "link", "name": ""}, "url": "https://ex.com"},
        ]
        ev = UXEvaluator(db)
        findings = ev.audit_static("https://ex.com")
        self.assertTrue(any("Link without visible text" in f.description for f in findings))

    def test_static_audit_clean_locators(self):
        from ux_evaluator import UXEvaluator
        db = MagicMock()
        db.get_all.return_value = [
            {"identity": {"role": "button", "name": "Submit"}, "url": "https://ex.com"},
            {"identity": {"role": "link", "name": "Home"}, "url": "https://ex.com"},
            {"identity": {"role": "textbox", "name": "Email"}, "url": "https://ex.com"},
        ]
        ev = UXEvaluator(db)
        findings = ev.audit_static("https://ex.com")
        self.assertEqual(len(findings), 0)


# ── UX Report tests ──────────────────────────────────────────────────

class TestUXReport(unittest.TestCase):

    def test_generate_ux_report_creates_files(self):
        from ux_evaluator import UXAuditResult, UXFinding
        from ux_report import generate_ux_report

        audit = UXAuditResult(
            urls=["https://ex.com"],
            findings=[
                UXFinding(heuristic="WCAG_ALT_TEXT", severity="medium", category="accessibility",
                          description="Image missing alt text", url="https://ex.com", source="rule"),
            ],
            score=97.0,
            audited_at="2026-03-16T12:00:00Z",
            duration_ms=1500,
            pages_audited=1,
        )

        with tempfile.TemporaryDirectory() as d:
            html_path, json_path = generate_ux_report(audit, output_dir=d)
            self.assertTrue(html_path.exists())
            self.assertTrue(json_path.exists())

            # Verify HTML content
            html = html_path.read_text()
            self.assertIn("QAPal UX Audit", html)
            self.assertIn("97.0", html)
            self.assertIn("Image missing alt text", html)

            # Verify JSON content
            data = json.loads(json_path.read_text())
            self.assertEqual(data["score"], 97.0)
            self.assertEqual(len(data["findings"]), 1)
            self.assertEqual(data["findings"][0]["heuristic"], "WCAG_ALT_TEXT")

    def test_generate_ux_report_no_findings(self):
        from ux_evaluator import UXAuditResult
        from ux_report import generate_ux_report

        audit = UXAuditResult(
            urls=["https://ex.com"],
            score=100.0,
            audited_at="2026-03-16T12:00:00Z",
            pages_audited=1,
        )

        with tempfile.TemporaryDirectory() as d:
            html_path, json_path = generate_ux_report(audit, output_dir=d)
            html = html_path.read_text()
            self.assertIn("100", html)

    def test_generate_ux_report_severity_bar(self):
        from ux_evaluator import UXAuditResult, UXFinding
        from ux_report import generate_ux_report

        audit = UXAuditResult(
            urls=["https://ex.com"],
            findings=[
                UXFinding(heuristic="X", severity="high", category="a", description="d", url="https://ex.com"),
                UXFinding(heuristic="X", severity="low", category="b", description="d2", url="https://ex.com"),
            ],
            score=91.0,
            pages_audited=1,
        )

        with tempfile.TemporaryDirectory() as d:
            html_path, _ = generate_ux_report(audit, output_dir=d)
            html = html_path.read_text()
            self.assertIn("seg-high", html)
            self.assertIn("seg-low", html)

    def test_generate_exploration_report(self):
        from explorer import ExplorationTrace, ExplorationStep
        from ux_report import generate_exploration_report

        trace = ExplorationTrace(
            session_id="test-001",
            start_url="https://ex.com",
            goal="Find bugs",
            steps=[
                ExplorationStep(step_index=0, url="https://ex.com", action="click", target="btn"),
            ],
            ux_findings=[
                {"severity": "high", "category": "layout", "description": "Broken grid", "url": "https://ex.com"},
            ],
            pages_visited=1,
            vision_calls=2,
            duration_ms=5000,
            finished_at="2026-03-16T12:00:00Z",
        )

        with tempfile.TemporaryDirectory() as d:
            html_path, json_path = generate_exploration_report(trace, output_dir=d)
            self.assertTrue(html_path.exists())
            data = json.loads(json_path.read_text())
            self.assertEqual(data["exploration"]["session_id"], "test-001")
            self.assertEqual(len(data["findings"]), 1)


# ── Vision Client tests ──────────────────────────────────────────────

class TestVisionClient(unittest.TestCase):

    def test_base_class_raises_not_implemented(self):
        from vision_client import VisionClient
        vc = VisionClient("test", "test-model")
        with self.assertRaises(NotImplementedError) as ctx:
            vc.analyze_screenshot(b"fake", "prompt")
        self.assertIn("VisionClient", str(ctx.exception))

    def test_base_class_analyze_multi_raises(self):
        from vision_client import VisionClient
        vc = VisionClient("test", "test-model")
        with self.assertRaises(NotImplementedError):
            vc.analyze_multi([b"fake"], "prompt")

    def test_from_env_no_key_raises(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {"QAPAL_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}, clear=False):
            with self.assertRaises(EnvironmentError):
                VisionClient.from_env()

    def test_from_env_anthropic(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {"QAPAL_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-test"}, clear=False):
            vc = VisionClient.from_env()
            self.assertEqual(vc.provider, "anthropic")

    def test_from_env_openai(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {"QAPAL_AI_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}, clear=False):
            vc = VisionClient.from_env()
            self.assertEqual(vc.provider, "openai")

    def test_from_env_unknown_provider(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {"QAPAL_AI_PROVIDER": "gemini"}, clear=False):
            with self.assertRaises(ValueError):
                VisionClient.from_env()

    def test_from_env_vision_provider_override(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {
            "QAPAL_VISION_PROVIDER": "openai",
            "QAPAL_AI_PROVIDER": "anthropic",
            "OPENAI_API_KEY": "sk-test",
        }, clear=False):
            vc = VisionClient.from_env()
            self.assertEqual(vc.provider, "openai")

    def test_from_env_custom_model(self):
        from vision_client import VisionClient
        with patch.dict(os.environ, {
            "QAPAL_AI_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-test",
            "QAPAL_VISION_MODEL": "claude-opus-4-6",
        }, clear=False):
            vc = VisionClient.from_env()
            self.assertEqual(vc.model, "claude-opus-4-6")


# ── UX Evaluator extract_json ────────────────────────────────────────

class TestExtractJson(unittest.TestCase):

    def test_clean_json(self):
        from ux_evaluator import _extract_json
        self.assertEqual(_extract_json('{"a": 1}'), '{"a": 1}')

    def test_markdown_fences(self):
        from ux_evaluator import _extract_json
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(_extract_json(raw), '{"a": 1}')

    def test_surrounding_text(self):
        from ux_evaluator import _extract_json
        raw = 'Here: {"a": 1} done.'
        self.assertEqual(_extract_json(raw), '{"a": 1}')

    def test_no_json(self):
        from ux_evaluator import _extract_json
        self.assertEqual(_extract_json("no json"), "no json")


if __name__ == "__main__":
    unittest.main()
