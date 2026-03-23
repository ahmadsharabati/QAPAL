"""
feature_generator.py — Unified Test Generation
================================================
Three input modes, one output format:
  1. PRD mode      — delegates to TestGenerator (existing flow)
  2. Plain text    — natural language sentences → test plans
  3. Auto-discover — crawl a site and infer testable features
"""

import json
from typing import List, Optional

from locator_db import LocatorDB
from ai_client import AIClient
from generator import TestGenerator
from planner import _format_locators, _format_semantic_contexts
from _log import get_logger

log = get_logger("feature_gen")


# ── System prompts ────────────────────────────────────────────────────

_TEXT_SYSTEM = """You are a test automation engineer for QAPal, a deterministic UI test automation system.

Your job: convert natural language test descriptions into deterministic test execution plans.

RULES:
1. Each sentence or bullet the user provides becomes one test case.
2. EVERY step is explicit. NO conditional logic. Plans are 100% deterministic.
3. Pre-existing elements: use exact locator from Available Locators. NEVER invent testids.
4. ARIA roles: button→"button", a[href]→"link", input[text/email/password]→"textbox", input[checkbox]→"checkbox", select→"combobox".
5. Return valid JSON only — no markdown, no explanation.
6. NAVIGATION URLS: always absolute. NEVER relative.
7. ASSERTION ACCURACY — derive post-action URL from Navigation Graph. NEVER invent URLs.
8. FORM COMPLETENESS: fill ALL visible fields.
9. testid strategy ONLY for values that appear verbatim in Available Locators.
10. If a page requires login or cart state, include those prerequisite steps.

SELECTOR FORMAT:
  {"strategy":"testid","value":"login-submit"}
  {"strategy":"role","value":{"role":"button","name":"Login"}}
  {"strategy":"role","value":{"role":"textbox","name":"Email address *"}}

SUPPORTED ACTIONS: navigate, click, fill, type, clear, press, select, check, uncheck, hover, focus, scroll, wait
SUPPORTED ASSERTIONS: url_equals, url_contains, element_exists, element_visible, element_hidden,
  element_contains_text, element_text_equals, element_value_equals

SELECT: use "label" (visible text), not "value". E.g. "label":"Germany".

Output Format — JSON array of plans:
[{"test_id": "TC001_...", "name": "...", "steps": [...], "assertions": [...]}]
"""

_DISCOVERY_SYSTEM = """You are a test automation engineer for QAPal, a deterministic UI test automation system.

Your job: given a complete map of a web application (pages, interactive elements, navigation paths), identify the most important user flows and generate test plans for them.

PRIORITIZATION (highest to lowest):
1. Authentication flows (login, registration, logout)
2. Form submissions (contact, search, checkout)
3. CRUD operations (create, read, update, delete)
4. Navigation flows (multi-page journeys)
5. Content verification (text, images, elements present)

RULES:
1. EVERY step is explicit. NO conditional logic. Plans are 100% deterministic.
2. Use ONLY elements present in Available Locators. NEVER invent selectors.
3. ARIA roles: button→"button", a[href]→"link", input→"textbox", checkbox→"checkbox", select→"combobox".
4. Return valid JSON only — no markdown, no explanation.
5. NAVIGATION URLS: always absolute. NEVER relative.
6. ASSERTION ACCURACY — derive post-action URL from Navigation Graph. NEVER invent URLs.
7. testid strategy ONLY for values that appear verbatim in Available Locators.
8. Every plan must be self-contained — runnable from a fresh browser.

SELECTOR FORMAT:
  {"strategy":"testid","value":"login-submit"}
  {"strategy":"role","value":{"role":"button","name":"Login"}}

SUPPORTED ACTIONS: navigate, click, fill, type, clear, press, select, check, uncheck, hover, focus, scroll, wait
SUPPORTED ASSERTIONS: url_equals, url_contains, element_exists, element_visible, element_hidden,
  element_contains_text, element_text_equals, element_value_equals

SELECT: use "label" (visible text), not "value".

Output Format — JSON array of plans:
[{"test_id": "TC001_...", "name": "...", "steps": [...], "assertions": [...]}]
"""

_TEXT_PROMPT = """## Base URLs
{base_urls}

## Test Credentials
{credentials_section}

## User's Test Descriptions
{user_text}

## Semantic Context
{semantic_contexts}

## Navigation Graph
{navigation_graph}

## Available Locators
{locators}

Convert each test description above into a deterministic test plan. Return a JSON array."""

_DISCOVERY_PROMPT = """## Base URLs
{base_urls}

## Test Credentials
{credentials_section}

## Semantic Context
{semantic_contexts}

## Navigation Graph
{navigation_graph}

## Available Locators
{locators}

Analyze the site map above. Identify the {num_tests} most important user flows and generate test plans for each. Return a JSON array."""


class FeatureTestGenerator:
    """
    Unified test generator — wraps TestGenerator for PRD mode,
    adds plain-text and auto-discovery modes.
    """

    def __init__(
        self,
        db:                  LocatorDB,
        ai_client:           Optional[AIClient] = None,
        state_graph=None,
        num_tests:           Optional[int] = None,
        max_locators:        int           = 80,
        negative_tests:      bool          = False,
        compiled_model_path: Optional[str] = None,
    ):
        self._db          = db
        self._ai          = ai_client
        self._state_graph = state_graph
        self._num_tests   = num_tests
        self._max_locators = max_locators
        self._negative_tests = negative_tests
        self._compiled_model_path = compiled_model_path

        # Compose the existing TestGenerator for PRD mode + plan parsing
        self._gen = TestGenerator(
            db=db,
            ai_client=ai_client,
            max_locators=max_locators,
            state_graph=state_graph,
            num_tests=num_tests,
            negative_tests=negative_tests,
            compiled_model_path=compiled_model_path,
        )

    # ── Mode 1: PRD (delegates to TestGenerator) ─────────────────────

    def generate_from_prd(
        self, prd_content: str, urls: List[str], credentials: Optional[dict] = None,
    ) -> List[dict]:
        """Generate plans from a PRD file. Pass-through to existing generator."""
        return self._gen.generate_plans_from_prd(prd_content, urls, credentials)

    # ── Mode 2: Plain text ───────────────────────────────────────────

    def generate_from_text(
        self, text: str, urls: List[str], credentials: Optional[dict] = None,
    ) -> List[dict]:
        """Generate plans from natural language test descriptions."""
        if not self._ai:
            from planner import PlanningError
            raise PlanningError("No AI client configured.")

        locators, states, nav_graph, creds_section = self._build_context(urls, credentials)
        locators_section = _format_locators(locators, self._max_locators, group_by_url=True)

        prompt = _TEXT_PROMPT.format(
            base_urls           = "\n".join(f"  - {u}" for u in urls),
            credentials_section = creds_section,
            user_text           = text,
            semantic_contexts   = _format_semantic_contexts(states),
            navigation_graph    = nav_graph,
            locators            = locators_section,
        )

        raw = self._ai.complete(prompt, system_prompt=_TEXT_SYSTEM, max_tokens=8192, temperature=0)
        plans = self._gen._parse_plans(
            raw, {loc["id"]: loc for loc in locators},
            base_url=urls[0] if urls else "", credentials=credentials, locators=locators,
        )
        log.info("  [text] Generated %d plan(s) from plain text", len(plans))
        return plans

    # ── Mode 3: Auto-discover ────────────────────────────────────────

    def generate_from_discovery(
        self, urls: List[str], credentials: Optional[dict] = None,
    ) -> List[dict]:
        """Infer testable features from the site and generate plans."""
        if not self._ai:
            from planner import PlanningError
            raise PlanningError("No AI client configured.")

        locators, states, nav_graph, creds_section = self._build_context(urls, credentials)
        locators_section = _format_locators(locators, self._max_locators, group_by_url=True)

        num = self._num_tests or 5
        prompt = _DISCOVERY_PROMPT.format(
            base_urls           = "\n".join(f"  - {u}" for u in urls),
            credentials_section = creds_section,
            semantic_contexts   = _format_semantic_contexts(states),
            navigation_graph    = nav_graph,
            locators            = locators_section,
            num_tests           = num,
        )

        raw = self._ai.complete(prompt, system_prompt=_DISCOVERY_SYSTEM, max_tokens=8192, temperature=0)
        plans = self._gen._parse_plans(
            raw, {loc["id"]: loc for loc in locators},
            base_url=urls[0] if urls else "", credentials=credentials, locators=locators,
        )
        log.info("  [discover] Generated %d plan(s) from auto-discovery", len(plans))
        return plans

    # ── Shared context builder ───────────────────────────────────────

    def _build_context(self, urls, credentials):
        """Load locators, semantic states, nav graph, and credentials section."""
        all_locs = self._db.get_all_locators(valid_only=True)

        # Basic dedup (same as generator.py but simpler — no PRD filtering)
        seen: set = set()
        locators: list = []
        for loc in all_locs:
            url  = loc.get("url", "")
            role = loc.get("identity", {}).get("role", "")
            name = loc.get("identity", {}).get("name", "")
            if "/admin" in url:
                continue
            if not loc.get("locators", {}).get("actionable", True):
                continue
            key = (url, role, name)
            if key not in seen:
                seen.add(key)
                locators.append(loc)

        if not locators:
            for url in urls:
                locators.extend(self._db.get_all(url, valid_only=True))

        states = [s for s in (self._db.get_state(u) for u in urls) if s]

        nav_graph = (
            self._state_graph.format_for_prompt(urls=urls, min_count=2)
            if self._state_graph is not None
            else "(no navigation graph)"
        )

        if credentials:
            creds_section = (
                f"  Login URL : {credentials.get('url', '')}\n"
                f"  Username  : {credentials.get('username', '')}\n"
                f"  Password  : {credentials.get('password', '')}\n"
                "  (Use these exact values in test steps that perform login)"
            )
        else:
            creds_section = "  (no credentials provided)"

        return locators, states, nav_graph, creds_section
