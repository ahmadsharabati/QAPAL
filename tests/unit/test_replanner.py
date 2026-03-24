"""
tests/unit/test_replanner.py
=============================
Unit tests for replanner.py — the unknown-state recovery engine.

All AI calls are mocked. No network, no browser required.

Coverage:
  TestPromptConstruction   — prompt fields populated correctly
  TestParsePatch           — _parse_patch() handles all input variants
  TestReplannerErrors      — AI failure and malformed output handling
  TestReplannerIntegration — full replan() call end-to-end (mocked AI)
"""

import asyncio
import json
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from replanner import Replanner, ReplanningError


# ── Fixtures ──────────────────────────────────────────────────────────

STEPS_VALID = json.dumps([
    {"action": "click", "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}}}
])

STEPS_TWO = json.dumps([
    {"action": "navigate", "url": "https://app.com/dashboard"},
    {"action": "click", "selector": {"strategy": "role", "value": {"role": "link", "name": "Settings"}}},
])

_HISTORY = [
    {"action": "navigate", "detail": "navigated to /login"},
    {"action": "fill",     "status": "pass"},
]

_FAILED_STEP = {
    "action": "click",
    "selector": {"strategy": "testid", "value": "btn-login"},
}

_REMAINING = [
    {"action": "click", "selector": {"strategy": "role", "value": {"role": "link", "name": "Dashboard"}}},
]

_ASSERTIONS = [
    {"type": "url_contains", "value": "/dashboard"},
]

_LOCATORS = [
    {"role": "button", "name": "Log in", "selector": {"strategy": "role", "value": {"role": "button", "name": "Log in"}}},
]


def _make_ai(response: str = STEPS_VALID):
    ai = MagicMock()
    ai.small_model = "claude-3-haiku-20240307"
    ai.acomplete = AsyncMock(return_value=response)
    return ai


def _run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════
# Suite 1 — Prompt construction
# ═════════════════════════════════════════════════════════════════════

class TestPromptConstruction(unittest.TestCase):
    """Verify that replan() injects every context field into the AI prompt."""

    def _captured_prompt(self, **kwargs) -> str:
        """Run replan() and return the prompt string passed to AI."""
        ai = _make_ai()
        r  = Replanner(ai)
        defaults = dict(
            execution_history   = _HISTORY,
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com/login",
            remaining_steps     = _REMAINING,
            semantic_context    = {"page": "Login"},
            available_locators  = _LOCATORS,
            original_assertions = _ASSERTIONS,
        )
        defaults.update(kwargs)
        _run(r.replan(**defaults))
        # First positional arg to acomplete is the prompt
        return ai.acomplete.call_args[0][0]

    def test_current_url_in_prompt(self):
        prompt = self._captured_prompt(current_url="https://example.com/login")
        self.assertIn("https://example.com/login", prompt)

    def test_failed_step_json_in_prompt(self):
        prompt = self._captured_prompt()
        self.assertIn("btn-login", prompt)

    def test_remaining_steps_in_prompt(self):
        prompt = self._captured_prompt()
        self.assertIn("Dashboard", prompt)

    def test_execution_history_in_prompt(self):
        prompt = self._captured_prompt()
        self.assertIn("navigate", prompt)
        self.assertIn("fill", prompt)

    def test_assertions_in_prompt(self):
        prompt = self._captured_prompt()
        self.assertIn("url_contains", prompt)

    def test_semantic_context_in_prompt(self):
        prompt = self._captured_prompt(semantic_context={"page": "LoginPage42"})
        self.assertIn("LoginPage42", prompt)

    def test_locators_in_prompt(self):
        prompt = self._captured_prompt()
        # _format_locators renders the "Available Locators" section header
        self.assertIn("Available Locators", prompt)

    def test_empty_history_renders_none_placeholder(self):
        prompt = self._captured_prompt(execution_history=[])
        self.assertIn("(none)", prompt)

    def test_none_semantic_context_does_not_crash(self):
        prompt = self._captured_prompt(semantic_context=None)
        self.assertIsInstance(prompt, str)

    def test_uses_small_model_override(self):
        ai = _make_ai()
        r  = Replanner(ai)
        _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = _REMAINING,
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        kwargs = ai.acomplete.call_args.kwargs
        self.assertEqual(kwargs.get("model_override"), "claude-3-haiku-20240307",
                         "replan must route through small_model for cost control")

    def test_system_prompt_passed_to_ai(self):
        ai = _make_ai()
        r  = Replanner(ai)
        _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        kwargs = ai.acomplete.call_args.kwargs
        sys_prompt = kwargs.get("system_prompt", "")
        self.assertIn("RULES", sys_prompt,
                      "system_prompt must be the replanner rules block")

    def test_max_tokens_passed(self):
        ai = _make_ai()
        r  = Replanner(ai)
        _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        kwargs = ai.acomplete.call_args.kwargs
        self.assertGreaterEqual(kwargs.get("max_tokens", 0), 1024,
                                "replan must request enough tokens for a multi-step patch")


# ═════════════════════════════════════════════════════════════════════
# Suite 2 — _parse_patch()
# ═════════════════════════════════════════════════════════════════════

class TestParsePatch(unittest.TestCase):
    """Unit tests for _parse_patch() — the JSON parser for AI responses."""

    def _parse(self, text: str):
        return Replanner(MagicMock())._parse_patch(text)

    # ── Happy path ──────────────────────────────────────────────────

    def test_plain_json_array_parsed(self):
        steps = self._parse(STEPS_VALID)
        self.assertIsInstance(steps, list)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "click")

    def test_two_steps_parsed(self):
        steps = self._parse(STEPS_TWO)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["action"], "navigate")
        self.assertEqual(steps[1]["action"], "click")

    def test_replanned_flag_set_on_every_step(self):
        steps = self._parse(STEPS_TWO)
        for step in steps:
            self.assertTrue(step.get("_replanned"),
                            f"step {step} missing _replanned flag")

    def test_replanned_flag_true_not_truthy(self):
        steps = self._parse(STEPS_VALID)
        self.assertIs(steps[0]["_replanned"], True)

    # ── Markdown fence stripping ──────────────────────────────────

    def test_markdown_json_fence_stripped(self):
        text = f"```json\n{STEPS_VALID}\n```"
        steps = self._parse(text)
        self.assertEqual(steps[0]["action"], "click")

    def test_markdown_plain_fence_stripped(self):
        text = f"```\n{STEPS_VALID}\n```"
        steps = self._parse(text)
        self.assertEqual(steps[0]["action"], "click")

    def test_markdown_preamble_stripped(self):
        text = "## Replacement Steps\n\nHere are the steps:\n" + STEPS_VALID
        steps = self._parse(text)
        self.assertEqual(steps[0]["action"], "click")

    def test_leading_whitespace_stripped(self):
        text = "   \n\n" + STEPS_VALID
        steps = self._parse(text)
        self.assertIsInstance(steps, list)

    # ── Error cases ───────────────────────────────────────────────

    def test_empty_array_raises(self):
        with self.assertRaises(ReplanningError) as ctx:
            self._parse("[]")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_object_not_array_raises(self):
        with self.assertRaises(ReplanningError) as ctx:
            self._parse('{"action": "click"}')
        self.assertIn("array", str(ctx.exception).lower())

    def test_invalid_json_raises(self):
        with self.assertRaises(ReplanningError) as ctx:
            self._parse("not json at all")
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_invalid_json_preview_included_in_error(self):
        garbage = "x" * 50
        with self.assertRaises(ReplanningError) as ctx:
            self._parse(garbage)
        self.assertIn("Preview", str(ctx.exception))

    def test_null_raises(self):
        with self.assertRaises(ReplanningError):
            self._parse("null")

    def test_number_raises(self):
        with self.assertRaises(ReplanningError):
            self._parse("42")

    def test_string_raises(self):
        with self.assertRaises(ReplanningError):
            self._parse('"just a string"')

    # ── Whitespace / newline handling ─────────────────────────────

    def test_steps_with_trailing_newline(self):
        text = STEPS_VALID + "\n\n"
        steps = self._parse(text)
        self.assertEqual(len(steps), 1)

    def test_compact_json(self):
        steps_compact = '[{"action":"navigate","url":"https://x.com"}]'
        steps = self._parse(steps_compact)
        self.assertEqual(steps[0]["action"], "navigate")


# ═════════════════════════════════════════════════════════════════════
# Suite 3 — AI error handling
# ═════════════════════════════════════════════════════════════════════

class TestReplannerErrors(unittest.TestCase):
    """Verify ReplanningError is raised correctly on AI and parse failures."""

    def _replan(self, ai):
        r = Replanner(ai)
        return _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))

    def test_ai_exception_raises_replanning_error(self):
        ai = MagicMock()
        ai.small_model = "claude-3-haiku-20240307"
        ai.acomplete = AsyncMock(side_effect=RuntimeError("network down"))
        with self.assertRaises(ReplanningError) as ctx:
            self._replan(ai)
        self.assertIn("AI call failed", str(ctx.exception))

    def test_ai_exception_message_forwarded(self):
        ai = MagicMock()
        ai.small_model = "fake"
        ai.acomplete = AsyncMock(side_effect=ValueError("token limit exceeded"))
        with self.assertRaises(ReplanningError) as ctx:
            self._replan(ai)
        self.assertIn("token limit exceeded", str(ctx.exception))

    def test_malformed_response_raises_replanning_error(self):
        ai = _make_ai("this is not json")
        with self.assertRaises(ReplanningError):
            self._replan(ai)

    def test_object_response_raises_replanning_error(self):
        ai = _make_ai('{"action": "click"}')
        with self.assertRaises(ReplanningError):
            self._replan(ai)

    def test_empty_steps_response_raises_replanning_error(self):
        ai = _make_ai("[]")
        with self.assertRaises(ReplanningError):
            self._replan(ai)

    def test_replanning_error_is_exception(self):
        self.assertTrue(issubclass(ReplanningError, Exception))


# ═════════════════════════════════════════════════════════════════════
# Suite 4 — Full replan() integration (mocked AI)
# ═════════════════════════════════════════════════════════════════════

class TestReplannerIntegration(unittest.TestCase):
    """End-to-end replan() with mocked AI — verifies the full call contract."""

    def test_returns_list_of_steps(self):
        ai = _make_ai(STEPS_VALID)
        r  = Replanner(ai)
        steps = _run(r.replan(
            execution_history   = _HISTORY,
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com/login",
            remaining_steps     = _REMAINING,
            semantic_context    = {"page": "Login"},
            available_locators  = _LOCATORS,
            original_assertions = _ASSERTIONS,
        ))
        self.assertIsInstance(steps, list)
        self.assertGreater(len(steps), 0)

    def test_all_steps_have_action_key(self):
        ai = _make_ai(STEPS_TWO)
        r  = Replanner(ai)
        steps = _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        for step in steps:
            self.assertIn("action", step, f"step missing action key: {step}")

    def test_all_returned_steps_are_tagged_replanned(self):
        ai = _make_ai(STEPS_TWO)
        r  = Replanner(ai)
        steps = _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        for step in steps:
            self.assertTrue(step.get("_replanned"),
                            "every step in the patch must be tagged _replanned=True")

    def test_ai_called_exactly_once(self):
        ai = _make_ai(STEPS_VALID)
        r  = Replanner(ai)
        _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        self.assertEqual(ai.acomplete.call_count, 1,
                         "replan must issue exactly one AI call")

    def test_large_locator_list_truncated_in_prompt(self):
        """_format_locators limits to max_items=100; very large lists must not crash."""
        ai = _make_ai(STEPS_VALID)
        r  = Replanner(ai)
        big_locators = [
            {"role": "button", "name": f"Btn{i}"} for i in range(200)
        ]
        steps = _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = big_locators,
            original_assertions = [],
        ))
        self.assertIsInstance(steps, list)

    def test_empty_locators_does_not_crash(self):
        ai = _make_ai(STEPS_VALID)
        r  = Replanner(ai)
        steps = _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        self.assertIsInstance(steps, list)

    def test_fence_wrapped_response_parsed_correctly(self):
        """AI sometimes wraps output in markdown fences — must still parse."""
        wrapped = f"```json\n{STEPS_VALID}\n```"
        ai = _make_ai(wrapped)
        r  = Replanner(ai)
        steps = _run(r.replan(
            execution_history   = [],
            failed_step         = _FAILED_STEP,
            current_url         = "https://app.com",
            remaining_steps     = [],
            semantic_context    = None,
            available_locators  = [],
            original_assertions = [],
        ))
        self.assertEqual(steps[0]["action"], "click")


if __name__ == "__main__":
    unittest.main(verbosity=2)
