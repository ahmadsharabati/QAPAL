"""
explorer.py — QAPal Autonomous Exploration Engine
===================================================
Vision-guided, DFS-based autonomous app exploration.

Instead of executing frozen JSON plans, the explorer navigates an app
autonomously — observing the screen, deciding the next action, and
recording UX findings along the way.

Architecture:
  - Hybrid approach: DOM/a11y extraction (free) + targeted VLM calls (expensive)
  - DFS with backtracking: systematically explores app states
  - Two-tier history compression: drops old screenshots, summarises steps via LLM
  - Cycle detection: tracks visited (URL, DOM-hash) pairs to avoid loops

Usage:
    explorer = Explorer(db, vision_client, ai_client)
    async with explorer:
        trace = await explorer.explore("https://app.com", goal="Test the checkout flow")

Env vars:
    QAPAL_EXPLORE_MAX_STEPS     — max actions per session (default: 30)
    QAPAL_EXPLORE_MAX_DEPTH     — max navigation depth   (default: 8)
    QAPAL_EXPLORE_SCREENSHOT_DIR — where to save exploration screenshots
"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page

from locator_db import LocatorDB, _normalize_url
from crawler import crawl_page, wait_for_stable, _build_context, A11Y_JS
from semantic_extractor import extract_semantic_context, compute_dom_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Config ────────────────────────────────────────────────────────────

MAX_STEPS          = int(os.getenv("QAPAL_EXPLORE_MAX_STEPS", "30"))
MAX_DEPTH          = int(os.getenv("QAPAL_EXPLORE_MAX_DEPTH", "8"))
SCREENSHOT_DIR     = Path(os.getenv("QAPAL_EXPLORE_SCREENSHOT_DIR", "reports/exploration"))
HISTORY_WINDOW     = 8     # keep last N full screenshots in context; summarise older
VISION_BUDGET      = 15    # max vision calls per session (cost guard)


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class ExplorationStep:
    step_index:      int
    url:             str
    action:          str                # "click", "fill", "navigate", ...
    target:          str                # human-readable description of what was acted on
    screenshot_path: str   = ""
    dom_hash:        str   = ""
    a11y_summary:    str   = ""         # compact textual summary of interactive elements
    timestamp:       float = 0.0
    vision_used:     bool  = False
    observation:     str   = ""         # what the VLM observed (if vision was used)


@dataclass
class ExplorationTrace:
    """Complete record of an exploration session."""
    session_id:       str
    start_url:        str
    goal:             str
    steps:            list[ExplorationStep] = field(default_factory=list)
    ux_findings:      list[dict]            = field(default_factory=list)
    pages_visited:    int                   = 0
    vision_calls:     int                   = 0
    duration_ms:      int                   = 0
    started_at:       str                   = ""
    finished_at:      str                   = ""


# ── VLM prompts ──────────────────────────────────────────────────────

_DECIDE_ACTION_SYSTEM = """\
You are an autonomous QA explorer testing a web application.
Your job is to systematically explore the app and find UX issues.

You have access to:
1. A screenshot of the current page
2. A list of interactive elements extracted from the DOM

Based on these, decide the SINGLE next action to take.

Rules:
- Prefer unexplored areas (elements you haven't clicked yet)
- Don't repeat the same action twice in a row
- If you see a form, try filling it with realistic test data
- If you see an error or broken layout, note it as a UX finding
- When you've explored the current page fully, navigate to a new page
- Return "done" when you believe you've covered the app adequately

Respond with ONLY valid JSON (no markdown, no commentary):
{
  "action": "click" | "fill" | "navigate" | "scroll" | "done",
  "target": "description of what to interact with",
  "selector": {"strategy": "testid|role|text|css", "value": "..."},
  "value": "text to fill (only for fill action)",
  "reasoning": "brief explanation of why this action",
  "ux_finding": null | {"severity": "high|medium|low", "category": "...", "description": "..."}
}"""

_OBSERVE_PAGE_SYSTEM = """\
You are a UX expert examining a web page screenshot.
Identify any UX issues visible in the screenshot.

Evaluate against these criteria:
1. Layout: overlapping elements, misaligned text, broken grid
2. Readability: text too small, low contrast, truncated content
3. Navigation: unclear where to click, missing breadcrumbs, dead-end pages
4. Forms: missing labels, unclear validation, confusing field order
5. Visual hierarchy: unclear primary action, competing CTAs
6. Accessibility: missing alt text indicators, touch targets too small
7. Consistency: inconsistent spacing, mixed icon styles, font mismatches
8. Error states: unclear error messages, missing feedback

Respond with ONLY valid JSON (no markdown):
{
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "category": "layout|readability|navigation|forms|hierarchy|accessibility|consistency|errors",
      "description": "specific description of the issue",
      "location": "where on the page (top-left, form area, navbar, etc.)"
    }
  ],
  "page_summary": "one-sentence summary of what this page is"
}"""

_SUMMARISE_HISTORY_PROMPT = """\
Summarise the following exploration steps into a brief paragraph.
Focus on: which pages were visited, what actions were taken, what was found.
Keep it under 100 words.

Steps:
{steps}"""


# ── Explorer ─────────────────────────────────────────────────────────

class Explorer:
    """
    Autonomous app explorer with vision-guided navigation.

    Uses a hybrid approach:
      1. DOM/a11y extraction identifies interactive elements (free)
      2. VLM decides what to do next (targeted, ~5-10 calls/session)
      3. VLM evaluates page quality at key moments (layout, UX)
    """

    def __init__(
        self,
        db:            LocatorDB,
        vision_client  = None,     # VisionClient instance
        ai_client      = None,     # AIClient for history summarisation
        headless:      bool = True,
        credentials:   Optional[dict] = None,
        state_graph    = None,
    ):
        self._db          = db
        self._vision      = vision_client
        self._ai          = ai_client
        self._headless    = headless
        self._credentials = credentials
        self._sg          = state_graph
        self._browser:    Optional[Browser] = None
        self._pw          = None

    async def __aenter__(self):
        self._pw = await async_playwright().__aenter__()
        self._pw.selectors.set_test_id_attribute("data-test")
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        return self

    async def __aexit__(self, *exc):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.__aexit__(*exc)

    # ── Main entry point ─────────────────────────────────────────────

    async def explore(
        self,
        start_url:  str,
        goal:       str       = "Explore the application and find UX issues",
        max_steps:  int       = MAX_STEPS,
        max_depth:  int       = MAX_DEPTH,
    ) -> ExplorationTrace:
        """
        Autonomously explore the app starting from start_url.
        Returns a complete ExplorationTrace with all steps and UX findings.
        """
        session_id = hashlib.md5(
            f"{start_url}-{time.time()}".encode()
        ).hexdigest()[:10]

        trace = ExplorationTrace(
            session_id  = session_id,
            start_url   = start_url,
            goal        = goal,
            started_at  = datetime.now(timezone.utc).isoformat(),
        )

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        session_dir = SCREENSHOT_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()

        ctx  = await _build_context(self._browser, self._db, start_url, self._credentials)
        page = await ctx.new_page()

        visited_states: set = set()   # (url_pattern, dom_hash) pairs
        depth = 0

        try:
            # Initial navigation
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
            await wait_for_stable(page)

            # Crawl the initial page (populates DB for free)
            await crawl_page(page, start_url, self._db, state_graph=self._sg)

            for step_idx in range(max_steps):
                current_url = _normalize_url(page.url)
                html        = await page.content()
                dom_hash    = compute_dom_hash(html)
                state_key   = (self._url_pattern(current_url), dom_hash)

                # Take screenshot
                shot_path = str(session_dir / f"step_{step_idx:03d}.png")
                await page.screenshot(path=shot_path, full_page=False)

                # Extract interactive elements (free — no AI)
                a11y_elements = await self._extract_elements(page)
                a11y_summary  = self._summarise_elements(a11y_elements)

                # Decide if we need vision for this page
                is_new_state    = state_key not in visited_states
                use_vision      = is_new_state and trace.vision_calls < VISION_BUDGET and self._vision is not None
                visited_states.add(state_key)

                # Vision: observe the page for UX issues (only on new states)
                observation = ""
                if use_vision:
                    shot_bytes = Path(shot_path).read_bytes()
                    observation = await self._observe_page(shot_bytes)
                    trace.vision_calls += 1
                    page_findings = self._parse_observation(observation, current_url, step_idx, shot_path)
                    trace.ux_findings.extend(page_findings)

                # Decide next action
                action_decision = await self._decide_next_action(
                    page, shot_path, a11y_summary, trace.steps, goal, step_idx,
                )
                trace.vision_calls += 1 if action_decision.get("_used_vision") else 0

                # Record the step
                step = ExplorationStep(
                    step_index      = step_idx,
                    url             = current_url,
                    action          = action_decision.get("action", "unknown"),
                    target          = action_decision.get("target", ""),
                    screenshot_path = shot_path,
                    dom_hash        = dom_hash,
                    a11y_summary    = a11y_summary[:200],
                    timestamp       = time.monotonic() - t0,
                    vision_used     = use_vision or action_decision.get("_used_vision", False),
                    observation     = observation[:300] if observation else "",
                )
                trace.steps.append(step)

                # Check for inline UX finding from the action decision
                if action_decision.get("ux_finding"):
                    finding = action_decision["ux_finding"]
                    finding["step_index"]      = step_idx
                    finding["url"]             = current_url
                    finding["screenshot_path"] = shot_path
                    trace.ux_findings.append(finding)

                # Execute the decided action
                if action_decision["action"] == "done":
                    break

                try:
                    await self._execute_action(page, action_decision)
                    await wait_for_stable(page)
                except Exception as e:
                    step.observation += f" | Action failed: {e}"

                # Track navigation depth
                new_url = _normalize_url(page.url)
                if new_url != current_url:
                    depth += 1
                    trace.pages_visited += 1
                    # Crawl newly discovered pages
                    await crawl_page(page, new_url, self._db, state_graph=self._sg)

                if depth > max_depth:
                    break

        except Exception as e:
            trace.ux_findings.append({
                "severity":    "high",
                "category":    "errors",
                "description": f"Exploration crashed: {e}",
                "url":         start_url,
                "step_index":  len(trace.steps),
            })
        finally:
            await ctx.close()

        trace.duration_ms  = int((time.monotonic() - t0) * 1000)
        trace.finished_at  = datetime.now(timezone.utc).isoformat()
        trace.pages_visited = len({s.url for s in trace.steps})

        # Save trace to disk
        trace_path = session_dir / "trace.json"
        self._save_trace(trace, trace_path)

        return trace

    # ── Element extraction (free — no AI) ────────────────────────────

    async def _extract_elements(self, page: Page) -> list:
        try:
            return await page.evaluate(A11Y_JS)
        except Exception:
            return []

    def _summarise_elements(self, elements: list) -> str:
        """Compact text summary of interactive elements for the VLM prompt."""
        lines = []
        for el in elements[:40]:  # cap to avoid prompt bloat
            role = el.get("role", "?")
            name = el.get("name", "")
            tid  = (el.get("loc") or {}).get("testid", "")
            tag  = el.get("tag", "")
            actionable = el.get("actionable", True)
            if not actionable:
                continue
            parts = [f"[{role}]"]
            if name:
                parts.append(f'"{name}"')
            if tid:
                parts.append(f"testid={tid}")
            if tag and tag not in ("button", "a", "input"):
                parts.append(f"<{tag}>")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    # ── VLM: observe page ────────────────────────────────────────────

    async def _observe_page(self, screenshot_bytes: bytes) -> str:
        if not self._vision:
            return ""
        try:
            return await self._vision.aanalyze_screenshot(
                screenshot_bytes,
                "Examine this web page for UX issues. Evaluate layout, readability, "
                "navigation clarity, form usability, visual hierarchy, and accessibility.",
                system_prompt=_OBSERVE_PAGE_SYSTEM,
                max_tokens=2048,
            )
        except Exception as e:
            return f"Vision error: {e}"

    def _parse_observation(
        self, raw: str, url: str, step_index: int, screenshot_path: str,
    ) -> list:
        """Parse VLM observation into structured findings."""
        try:
            data = json.loads(self._extract_json(raw))
            findings = data.get("findings", [])
            for f in findings:
                f["url"]             = url
                f["step_index"]      = step_index
                f["screenshot_path"] = screenshot_path
                f["source"]          = "vision_observation"
            return findings
        except (json.JSONDecodeError, ValueError):
            return []

    # ── VLM: decide next action ──────────────────────────────────────

    async def _decide_next_action(
        self,
        page:          Page,
        screenshot_path: str,
        a11y_summary:  str,
        history:       list[ExplorationStep],
        goal:          str,
        step_index:    int,
    ) -> dict:
        """Use VLM or AI to decide the next exploration action."""

        # Build history summary (two-tier compression)
        history_text = self._compress_history(history)

        prompt = (
            f"GOAL: {goal}\n\n"
            f"CURRENT URL: {page.url}\n\n"
            f"EXPLORATION HISTORY:\n{history_text}\n\n"
            f"INTERACTIVE ELEMENTS ON THIS PAGE:\n{a11y_summary}\n\n"
            f"Step {step_index + 1}/{MAX_STEPS}. Decide the next action."
        )

        # Prefer vision if available and within budget
        if self._vision:
            try:
                shot_bytes = Path(screenshot_path).read_bytes()
                raw = await self._vision.aanalyze_screenshot(
                    shot_bytes, prompt,
                    system_prompt=_DECIDE_ACTION_SYSTEM,
                    max_tokens=1024,
                )
                result = json.loads(self._extract_json(raw))
                result["_used_vision"] = True
                return result
            except Exception:
                pass

        # Fallback: text-only AI decision
        if self._ai:
            try:
                raw = await self._ai.acomplete(
                    prompt,
                    system_prompt=_DECIDE_ACTION_SYSTEM,
                    max_tokens=1024,
                )
                result = json.loads(self._extract_json(raw))
                result["_used_vision"] = False
                return result
            except Exception:
                pass

        # Last resort: heuristic exploration
        return self._heuristic_next_action(a11y_summary, history)

    # ── History compression ──────────────────────────────────────────

    def _compress_history(self, steps: list[ExplorationStep]) -> str:
        """Two-tier compression: recent steps in detail, older ones summarised."""
        if not steps:
            return "(no actions taken yet)"

        recent = steps[-HISTORY_WINDOW:]
        older  = steps[:-HISTORY_WINDOW]

        parts = []
        if older:
            older_text = "; ".join(
                f"step {s.step_index}: {s.action} '{s.target}' on {s.url}"
                for s in older
            )
            parts.append(f"[Earlier: {older_text}]")

        for s in recent:
            line = f"  Step {s.step_index}: {s.action} '{s.target}' @ {s.url}"
            if s.observation:
                line += f" — {s.observation[:80]}"
            parts.append(line)

        return "\n".join(parts)

    # ── Heuristic fallback ───────────────────────────────────────────

    def _heuristic_next_action(
        self, a11y_summary: str, history: list[ExplorationStep],
    ) -> dict:
        """When no AI is available, pick the next unvisited element."""
        visited_targets = {s.target for s in history}
        for line in a11y_summary.split("\n"):
            if not line.strip():
                continue
            # Extract the element name from the summary line
            target = line.strip()
            if target not in visited_targets:
                if "[link]" in target or "[button]" in target:
                    return {
                        "action":   "click",
                        "target":   target,
                        "selector": {"strategy": "text", "value": target.split('"')[1] if '"' in target else target},
                        "reasoning": "Heuristic: clicking next unvisited element",
                        "_used_vision": False,
                    }
        return {"action": "done", "target": "exploration complete", "_used_vision": False}

    # ── Action execution ─────────────────────────────────────────────

    async def _execute_action(self, page: Page, decision: dict) -> None:
        """Execute a single exploration action on the page."""
        action   = decision.get("action", "")
        selector = decision.get("selector", {})
        value    = decision.get("value", "")

        locator = self._resolve_selector(page, selector)

        if action == "click":
            await locator.first.click(timeout=10_000)
        elif action == "fill":
            await locator.first.fill(value or "test@example.com", timeout=10_000)
        elif action == "scroll":
            await page.evaluate("window.scrollBy(0, 500)")
        elif action == "navigate":
            url = decision.get("value") or decision.get("target", "")
            if url.startswith("http"):
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # "done" is handled by the caller

    def _resolve_selector(self, page: Page, selector: dict):
        """Convert a selector dict to a Playwright locator."""
        strategy = selector.get("strategy", "text")
        value    = selector.get("value", "")

        if strategy == "testid":
            return page.get_by_test_id(value)
        if strategy == "role":
            if isinstance(value, dict):
                return page.get_by_role(value.get("role", "button"), name=value.get("name"))
            return page.get_by_role("button", name=value)
        if strategy == "text":
            return page.get_by_text(value, exact=False)
        if strategy == "css":
            return page.locator(value)
        # Fallback
        return page.get_by_text(value, exact=False)

    # ── Helpers ──────────────────────────────────────────────────────

    def _url_pattern(self, url: str) -> str:
        """Normalise URL to pattern for state deduplication."""
        parsed = urlparse(url)
        path   = parsed.path.rstrip("/") or "/"
        return f"{parsed.netloc}{path}"

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON object from text that may contain markdown fences."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text  = "\n".join(lines)
        # Find first { to last }
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            return text[start:end + 1]
        return text

    def _save_trace(self, trace: ExplorationTrace, path: Path) -> None:
        """Serialise exploration trace to JSON."""
        data = {
            "session_id":    trace.session_id,
            "start_url":     trace.start_url,
            "goal":          trace.goal,
            "started_at":    trace.started_at,
            "finished_at":   trace.finished_at,
            "duration_ms":   trace.duration_ms,
            "pages_visited": trace.pages_visited,
            "vision_calls":  trace.vision_calls,
            "total_steps":   len(trace.steps),
            "steps": [
                {
                    "step_index":      s.step_index,
                    "url":             s.url,
                    "action":          s.action,
                    "target":          s.target,
                    "screenshot_path": s.screenshot_path,
                    "dom_hash":        s.dom_hash,
                    "timestamp_s":     round(s.timestamp, 2),
                    "vision_used":     s.vision_used,
                    "observation":     s.observation,
                }
                for s in trace.steps
            ],
            "ux_findings": trace.ux_findings,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
