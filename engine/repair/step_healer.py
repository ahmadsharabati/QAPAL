"""
engine/repair/step_healer.py — Surgical Step Recovery Engine
=============================================================
Provides targeted repair for a single failed test step.
Unlike replanner.py, it does NOT replan the whole test — it only
tries to "fix" the current interaction to get past a bottleneck.
"""

import json
import logging
from typing import Any, Dict, Optional

from ai_client import AIClient
from locator_db import LocatorDB
from planner import _format_locators

log = logging.getLogger("repair.healer")

_HEALER_SYSTEM = """You are a surgical UI test repair assistant for QAPAL.

Your goal: Fix a SINGLE failed test step so the test can continue.

STRICT RULES:
1. Return EXACTLY ONE step that replaces the failed one.
2. The ACTION type (click, fill, type, etc.) MUST match the original failed step's action.
3. The TARGET ELEMENT must be semantically equivalent (same role, similar name/context).
4. Do NOT change the intent of the test. If the original was "Login", do not repair into "Signup".
5. Use "Available Locators" as the primary source of truth for the current state.
6. Return ONLY a JSON object — no explanation, no markdown.

ACTION SYNTAX:
{"action": "click", "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}}}
{"action": "fill", "selector": {"strategy": "text", "value": "Email"}, "value": "user@test.com"}
"""

_HEALER_PROMPT = """## Context
URL: {current_url}
Error: {error_reason}

## Failed Step (The Intent to Preserve)
{failed_step}

## Available Locators at Current URL
{available_locators}

## Task
Identify a replacement element that preserves the ORIGINAL INTENT of the failed step.
Constraint: You MUST use the same 'action' as the failed step.

Respond with a JSON object only:
{{
  "action": "...",
  "selector": {{ "strategy": "...", "value": "..." }}
}}"""


class StepHealer:
    """
    Surgical repair agent for single-step failures.
    """

    def __init__(self, ai_client: AIClient, db: LocatorDB):
        self._ai = ai_client
        self._db = db

    async def repair_step(
        self,
        failed_step: dict,
        error_reason: str,
        current_url: str,
        available_locators: list,
    ) -> Optional[dict]:
        """
        Attempt to generate a replacement for failed_step.
        Returns the new step as a dict, or None on failure.
        """
        log.warning("repairing failed step: %s (reason: %s)", failed_step.get("action"), error_reason)

        prompt = _HEALER_PROMPT.format(
            current_url=current_url,
            error_reason=error_reason,
            failed_step=json.dumps(failed_step, indent=2),
            available_locators=_format_locators(available_locators, max_items=50),
        )

        try:
            raw = await self._ai.acomplete(
                prompt,
                system_prompt=_HEALER_SYSTEM,
                max_tokens=500,
                model_override=self._ai.small_model,
            )
            return self._parse_step(raw)
        except Exception as e:
            log.error("StepHealer AI call failed: %s", e)
            return None

    def _parse_step(self, text: str) -> Optional[dict]:
        text = text.strip()
        if "```" in text:
            # Extract JSON block if present
            parts = text.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{") and p.endswith("}"):
                    text = p
                    break
        
        try:
            step = json.loads(text)
            if not isinstance(step, dict) or "action" not in step:
                log.error("StepHealer returned invalid step shape")
                return None
            
            # Mark it so we can track it in reports
            step["_healed"] = True
            return step
        except json.JSONDecodeError:
            log.error("StepHealer returned invalid JSON: %s", text[:100])
            return None
