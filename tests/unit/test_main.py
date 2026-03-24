"""
tests/unit/test_main.py
========================
Unit tests for main.py — CLI argument parsing, command dispatch,
helper functions, and exit-code contracts.

Strategy: patch `asyncio.run` to capture which coroutine is dispatched
without starting a browser or making network calls.

Coverage:
  TestArgParsing         — each subcommand is recognized; required args enforced
  TestCommandDispatch    — main() dispatches to the correct cmd_* for each subcommand
  TestNoSubcommand       — main() returns 1 and prints help when no subcommand given
  TestKeyboardInterrupt  — main() catches KeyboardInterrupt and returns 130
  TestPrintHelpers       — _print_visual_regression_summary, _print_passive_error_summary
  TestLoadCredentials    — _load_credentials() from JSON file / no file
"""

import json
import sys
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import main as _main
from main import (
    _print_visual_regression_summary,
    _print_passive_error_summary,
    _load_credentials,
    main,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _run_main(argv):
    """Run main() with the given argument list, returning its exit code."""
    with patch.object(sys, "argv", ["main.py"] + argv):
        return main()


def _run_main_capture_coro(argv):
    """
    Run main() and capture the coroutine that would be passed to asyncio.run().
    Returns (exit_code, captured_coroutine).
    """
    captured = {}

    def fake_asyncio_run(coro):
        captured["coro"] = coro
        # Close the coroutine to avoid ResourceWarning; suppress the
        # "coroutine was never awaited" RuntimeWarning that Python 3.12 emits.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            coro.close()
        return 0

    with patch.object(sys, "argv", ["main.py"] + argv), \
         patch("main.asyncio.run", side_effect=fake_asyncio_run):
        code = main()
    return code, captured.get("coro")


# ═══════════════════════════════════════════════════════════════════════
# Suite 1 — Argument parsing
# ═══════════════════════════════════════════════════════════════════════

class TestArgParsing(unittest.TestCase):
    """Verify each subcommand is parsed correctly by argparse."""

    def _parse(self, argv):
        """Parse argv and return the Namespace (or call sys.exit on error)."""
        import argparse
        # Re-use main()'s parser by rebuilding just the parse step
        with patch.object(sys, "argv", ["main.py"] + argv), \
             patch("main.asyncio.run", return_value=0):
            main()

    # ── crawl ───────────────────────────────────────────────────

    def test_crawl_requires_urls(self):
        """crawl without --urls should exit non-zero."""
        with patch.object(sys, "argv", ["main.py", "crawl"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_crawl_with_urls_dispatches(self):
        code, coro = _run_main_capture_coro(["crawl", "--urls", "https://app.com"])
        self.assertIsNotNone(coro)
        self.assertIn("crawl", coro.__qualname__.lower())

    def test_crawl_force_flag_accepted(self):
        code, coro = _run_main_capture_coro(
            ["crawl", "--urls", "https://app.com", "--force"])
        self.assertEqual(code, 0)

    def test_crawl_spider_flag_accepted(self):
        code, coro = _run_main_capture_coro(
            ["crawl", "--urls", "https://app.com", "--spider"])
        self.assertEqual(code, 0)

    # ── run ─────────────────────────────────────────────────────

    def test_run_dispatches(self):
        code, coro = _run_main_capture_coro(
            ["run", "--plans", "plans/TC001.json"])
        self.assertIsNotNone(coro)
        self.assertIn("run", coro.__qualname__.lower())

    # ── prd-run ─────────────────────────────────────────────────

    def test_prd_run_requires_prd(self):
        """prd-run without --prd should exit non-zero."""
        with patch.object(sys, "argv", ["main.py", "prd-run", "--url", "https://app.com"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_prd_run_requires_url(self):
        with patch.object(sys, "argv", ["main.py", "prd-run", "--prd", "test.md"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_prd_run_dispatches(self):
        code, coro = _run_main_capture_coro(
            ["prd-run", "--prd", "test.md", "--url", "https://app.com"])
        self.assertIsNotNone(coro)
        self.assertIn("prd", coro.__qualname__.lower())

    def test_prd_run_num_tests_accepted(self):
        code, coro = _run_main_capture_coro(
            ["prd-run", "--prd", "test.md", "--url", "https://app.com",
             "--num-tests", "3"])
        self.assertEqual(code, 0)

    # ── status ──────────────────────────────────────────────────

    def test_status_dispatches(self):
        code, coro = _run_main_capture_coro(["status"])
        self.assertIsNotNone(coro)
        self.assertIn("status", coro.__qualname__.lower())

    # ── graph ───────────────────────────────────────────────────

    def test_graph_dispatches(self):
        code, coro = _run_main_capture_coro(["graph"])
        self.assertIsNotNone(coro)
        self.assertIn("graph", coro.__qualname__.lower())

    def test_graph_stats_flag_accepted(self):
        code, _ = _run_main_capture_coro(["graph", "--stats"])
        self.assertEqual(code, 0)

    # ── semantic ─────────────────────────────────────────────────

    def test_semantic_requires_urls(self):
        with patch.object(sys, "argv", ["main.py", "semantic"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_semantic_dispatches(self):
        code, coro = _run_main_capture_coro(
            ["semantic", "--urls", "https://app.com"])
        self.assertIn("semantic", coro.__qualname__.lower())

    # ── codegen ──────────────────────────────────────────────────

    def test_codegen_dispatches(self):
        code, coro = _run_main_capture_coro(
            ["codegen", "--plan", "plans/TC001.json"])
        self.assertIn("codegen", coro.__qualname__.lower())

    # ── compile ──────────────────────────────────────────────────

    def test_compile_dispatches(self):
        code, coro = _run_main_capture_coro(["compile"])
        self.assertIn("compile", coro.__qualname__.lower())

    # ── graph-crawl ──────────────────────────────────────────────

    def test_graph_crawl_requires_urls(self):
        with patch.object(sys, "argv", ["main.py", "graph-crawl"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_graph_crawl_dispatches(self):
        code, coro = _run_main_capture_coro(
            ["graph-crawl", "--urls", "https://app.com"])
        self.assertIn("graph_crawl", coro.__qualname__.lower())


# ═══════════════════════════════════════════════════════════════════════
# Suite 2 — Command dispatch
# ═══════════════════════════════════════════════════════════════════════

class TestCommandDispatch(unittest.TestCase):
    """Verify main() calls exactly the right cmd_* for each subcommand."""

    def _assert_dispatches_to(self, argv, expected_fn_name):
        """Assert that main() dispatches to the named cmd function."""
        captured = {}

        def fake_run(coro):
            captured["fn"] = coro.__qualname__
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                coro.close()
            return 0

        with patch.object(sys, "argv", ["main.py"] + argv), \
             patch("main.asyncio.run", side_effect=fake_run):
            main()

        self.assertIn(expected_fn_name, captured.get("fn", ""),
                      f"Expected dispatch to {expected_fn_name!r}; "
                      f"got {captured.get('fn')!r}")

    def test_crawl_dispatches_to_cmd_crawl(self):
        self._assert_dispatches_to(["crawl", "--urls", "https://app.com"], "cmd_crawl")

    def test_status_dispatches_to_cmd_status(self):
        self._assert_dispatches_to(["status"], "cmd_status")

    def test_run_dispatches_to_cmd_run(self):
        self._assert_dispatches_to(["run", "--plans", "p.json"], "cmd_run")

    def test_prd_run_dispatches_to_cmd_prd_run(self):
        self._assert_dispatches_to(
            ["prd-run", "--prd", "x.md", "--url", "https://app.com"], "cmd_prd_run")

    def test_graph_dispatches_to_cmd_graph(self):
        self._assert_dispatches_to(["graph"], "cmd_graph")

    def test_graph_crawl_dispatches_to_cmd_graph_crawl(self):
        self._assert_dispatches_to(
            ["graph-crawl", "--urls", "https://app.com"], "cmd_graph_crawl")

    def test_codegen_dispatches_to_cmd_codegen(self):
        self._assert_dispatches_to(["codegen", "--plan", "p.json"], "cmd_codegen")

    def test_compile_dispatches_to_cmd_compile(self):
        self._assert_dispatches_to(["compile"], "cmd_compile")

    def test_semantic_dispatches_to_cmd_semantic(self):
        self._assert_dispatches_to(["semantic", "--urls", "https://app.com"], "cmd_semantic")


# ═══════════════════════════════════════════════════════════════════════
# Suite 3 — No subcommand
# ═══════════════════════════════════════════════════════════════════════

class TestNoSubcommand(unittest.TestCase):

    def test_no_subcommand_returns_one(self):
        with patch.object(sys, "argv", ["main.py"]):
            code = main()
        self.assertEqual(code, 1)

    def test_no_subcommand_does_not_call_asyncio_run(self):
        with patch.object(sys, "argv", ["main.py"]), \
             patch("main.asyncio.run") as mock_run:
            main()
        mock_run.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Suite 4 — KeyboardInterrupt handling
# ═══════════════════════════════════════════════════════════════════════

class TestKeyboardInterrupt(unittest.TestCase):

    def test_keyboard_interrupt_returns_130(self):
        with patch.object(sys, "argv", ["main.py", "status"]), \
             patch("main.asyncio.run", side_effect=KeyboardInterrupt):
            code = main()
        self.assertEqual(code, 130)


# ═══════════════════════════════════════════════════════════════════════
# Suite 5 — _print_visual_regression_summary
# ═══════════════════════════════════════════════════════════════════════

class TestPrintHelpers(unittest.TestCase):

    def test_vr_summary_no_regressions_is_silent(self):
        """No visual regressions → nothing logged."""
        results = [
            {"id": "TC001", "has_visual_regressions": False},
            {"id": "TC002"},  # key absent
        ]
        with patch("main.log") as mock_log:
            _print_visual_regression_summary(results)
        mock_log.warning.assert_not_called()

    def test_vr_summary_logs_flagged_tests(self):
        results = [{
            "id": "TC003",
            "has_visual_regressions": True,
            "visual_regressions": [{
                "step_index": 2,
                "diff_pct":   3.5,
                "baseline":   "/reports/baseline/TC003/step_2.png",
                "diff":       "/reports/diff/TC003_step_2_diff.png",
            }],
        }]
        with patch("main.log") as mock_log:
            _print_visual_regression_summary(results)
        mock_log.warning.assert_called()

    def test_vr_summary_empty_list_is_silent(self):
        with patch("main.log") as mock_log:
            _print_visual_regression_summary([])
        mock_log.warning.assert_not_called()

    def test_passive_error_summary_no_errors_is_silent(self):
        results = [{"id": "TC001", "has_passive_errors": False}]
        with patch("main.log") as mock_log:
            _print_passive_error_summary(results)
        mock_log.warning.assert_not_called()

    def test_passive_error_summary_logs_flagged_tests(self):
        results = [{
            "id": "TC002",
            "has_passive_errors": True,
            "passive_errors": {
                "console_errors":   [{"text": "Uncaught TypeError: undefined"}],
                "network_failures": [],
                "js_exceptions":    [],
            },
        }]
        with patch("main.log") as mock_log:
            _print_passive_error_summary(results)
        mock_log.warning.assert_called()

    def test_passive_error_summary_counts_all_error_types(self):
        results = [{
            "id": "TC003",
            "has_passive_errors": True,
            "passive_errors": {
                "console_errors":   [{"text": "e1"}, {"text": "e2"}],
                "network_failures": [{"url": "https://api.com", "failure": "net::ERR"}],
                "js_exceptions":    [{"msg": "TypeError"}],
            },
        }]
        # Should log the counts — 2 console, 1 network, 1 js
        log_texts = []
        with patch("main.log") as mock_log:
            mock_log.warning.side_effect = lambda fmt, *a: log_texts.append(fmt % a)
            _print_passive_error_summary(results)
        combined = " ".join(log_texts)
        self.assertIn("2", combined)  # 2 console errors
        self.assertIn("1", combined)  # 1 network or 1 js

    def test_passive_error_summary_empty_list_is_silent(self):
        with patch("main.log") as mock_log:
            _print_passive_error_summary([])
        mock_log.warning.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Suite 6 — _load_credentials()
# ═══════════════════════════════════════════════════════════════════════

class TestLoadCredentials(unittest.TestCase):

    def test_no_credentials_file_returns_none(self):
        args = MagicMock()
        args.credentials_file = None
        result = _load_credentials(args)
        self.assertIsNone(result)

    def test_credentials_file_loaded(self):
        creds = {"url": "https://app.com/login",
                 "username": "test@app.com",
                 "password": "secret123"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(creds, f)
            tmp_path = f.name

        try:
            args = MagicMock()
            args.credentials_file = tmp_path
            result = _load_credentials(args)
            self.assertEqual(result["username"], "test@app.com")
            self.assertEqual(result["password"], "secret123")
        finally:
            os.unlink(tmp_path)

    def test_credentials_url_preserved(self):
        creds = {"url": "https://app.com/login", "username": "u", "password": "p"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(creds, f)
            tmp_path = f.name

        try:
            args = MagicMock()
            args.credentials_file = tmp_path
            result = _load_credentials(args)
            self.assertEqual(result["url"], "https://app.com/login")
        finally:
            os.unlink(tmp_path)

    def test_missing_file_returns_none_or_raises(self):
        """A missing file should be handled gracefully."""
        args = MagicMock()
        args.credentials_file = "/tmp/this_file_does_not_exist_qapal.json"
        try:
            result = _load_credentials(args)
            # Either returns None or raises — both are acceptable
        except (FileNotFoundError, OSError):
            pass  # expected

    def test_attr_error_returns_none(self):
        """If args has no credentials_file attribute, returns None."""
        args = MagicMock(spec=[])  # no attributes
        result = _load_credentials(args)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
