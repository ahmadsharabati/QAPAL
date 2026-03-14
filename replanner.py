"""
replanner.py — QAPal Unknown State Replanner
=============================================
Called ONLY when the executor hits an unknown state during test execution.

Key design decisions:
  - Returns a PATCH (replacement steps for the remaining test), not a full test.
    Executor does: steps = steps[:failure_index] + patch
  - Receives available_locators for the current URL to prevent hallucination.
  - One async AI call per recovery (enforced by Executor's replan_count cap).
  - Reuses _format_locators() from planner.py for consistent locator formatting.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional

from planner import _format_locators, PlanningError
from ai_client import AIClient


# ── Prompts ───────────────────────────────────────────────────────────

_REPLANNER_SYSTEM = """You are a test recovery assistant for QAPal, a deterministic UI test automation system.

A running test has landed on an unexpected page state. Your job: generate replacement steps that continue the test from the current state toward the original goal.

RULES:
1. Return ONLY replacement steps for the remaining test — not the full test plan.
2. Your steps REPLACE all remaining steps from the failure point onward.
3. Every step that targets an element MUST have a selector. Use locators from "Available Locators" when possible.
4. For elements not in the locator DB, use strategy "text" or "role". NEVER "css".
   CRITICAL: NEVER invent a "testid" selector. Strategy "testid" is FORBIDDEN unless the exact
   data-testid value appears verbatim in the "Available Locators" section. Default to "role" or "text".
5. ARIA ROLE RULES — pick the correct role or the step will always fail:
   * <button> elements      → role: "button"
   * <a href> elements      → role: "link",   NEVER "button"
   * <input text/email/pwd> → role: "textbox"
   * <input type="checkbox">→ role: "checkbox"
   * <select>               → role: "combobox"
6. Return valid JSON array only — no markdown, no explanation.

SELECTOR FORMAT (prefer role and text over testid):
  {"strategy": "role",   "value": {"role": "textbox", "name": "Email"}}
  {"strategy": "role",   "value": {"role": "button",  "name": "Submit"}}
  {"strategy": "role",   "value": {"role": "link",    "name": "Dashboard"}}
  {"strategy": "text",   "value": "Buy milk"}
"""

_REPLANNER_PROMPT = """## Current State
URL: {current_url}

## Page Semantic Context
{semantic_context}

## Execution History (steps already completed)
{execution_history}

## Failed Step
{failed_step}

## Remaining Steps (to be replaced by your output)
{remaining_steps}

## Original Test Assertions (the goal to reach)
{original_assertions}

## Available Locators at Current URL
{available_locators}

## Task
Generate replacement steps that recover from the failed step and continue toward the test goal.
Return a JSON array of steps only — no markdown:
[
  {{"action": "click", "selector": {{"strategy": "role", "value": {{"role": "button", "name": "..."}}}}}}
]"""


# ── Replanner ─────────────────────────────────────────────────────────

class ReplanningError(Exception):
    pass


class Replanner:
    """
    Generates a step patch for unknown state recovery.

    Called at most once per test (enforced by Executor's replan_count cap).
    """

    def __init__(self, ai_client: AIClient):
        self._ai = ai_client

    async def replan(
        self,
        execution_history:   List[dict],
        failed_step:         dict,
        current_url:         str,
        remaining_steps:     List[dict],
        semantic_context:    Optional[dict],
        available_locators:  List[dict],
        original_assertions: List[dict],
    ) -> List[dict]:
        """
        Returns a step patch: replacement steps to substitute for remaining_steps.
        Raises ReplanningError if the AI call fails or returns invalid output.
        """
        history_lines = [
            f"  {i+1}. {s.get('action', '?')} → {s.get('detail') or s.get('reason') or s.get('status', '?')}"
            for i, s in enumerate(execution_history)
        ]

        prompt = _REPLANNER_PROMPT.format(
            current_url          = current_url,
            semantic_context     = json.dumps(semantic_context or {}, indent=2),
            execution_history    = "\n".join(history_lines) or "(none)",
            failed_step          = json.dumps(failed_step, indent=2),
            remaining_steps      = json.dumps(remaining_steps, indent=2),
            original_assertions  = json.dumps(original_assertions, indent=2),
            available_locators   = _format_locators(available_locators, max_items=100),
        )

        try:
            raw = await self._ai.acomplete(
                prompt,
                system_prompt = _REPLANNER_SYSTEM,
                max_tokens    = 4096,
            )
        except Exception as e:
            raise ReplanningError(f"AI call failed: {e}")

        return self._parse_patch(raw)

    def _parse_patch(self, text: str) -> List[dict]:
        text = text.strip()
        # Strip markdown code fences
        if "```" in text:
            for part in text.split("```")[1:]:
                candidate = part.lstrip("json").strip()
                if candidate.startswith("["):
                    text = candidate
                    break
        # Strip any markdown preamble (e.g. "## Replacement Steps\n[...]")
        if not text.startswith("["):
            idx = text.find("[")
            if idx != -1:
                text = text[idx:]

        try:
            steps = json.loads(text)
        except json.JSONDecodeError as e:
            raise ReplanningError(
                f"Replanner returned invalid JSON: {e}\nPreview: {text[:300]}"
            )

        if not isinstance(steps, list):
            raise ReplanningError("Replanner must return a JSON array of steps.")
        if not steps:
            raise ReplanningError("Replanner returned an empty plan.")

        # Tag all patched steps so they're identifiable in reports
        for step in steps:
            step["_replanned"] = True

        return steps
