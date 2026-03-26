"""
generator.py — QAPal PRD Test Generator
=========================================
Reads a PRD and a list of available locators, then outputs fully-mapped 
execution plans in a single AI call.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from locator_db import LocatorDB, DYNAMIC_ID_RE as _DYNAMIC_ID_RE
from planner import PlanningError, _format_locators, _format_semantic_contexts, _parse_plan
from ai_client import AIClient
from _log import get_logger
from _tokens import get_token_tracker

log = get_logger("generator")


_GENERATOR_SYSTEM = """/no_think
You are a senior QA automation engineer. Your ONLY job is to generate test plans that CATCH REAL BUGS.

A test that passes even when the feature is BROKEN is WORTHLESS. Reject any assertion that does not verify a state change.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 THE ONE RULE THAT OVERRIDES EVERYTHING ELSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SELF-CHECK: Before writing each assertion, ask: "Would this assertion PASS if the feature I'm testing was completely broken?"
  → YES → the assertion is useless. Replace it with one that fails when the feature breaks.
  → NO  → the assertion is valid. Keep it.

Examples of this self-check:
  "element_exists on a product card after sorting" → Would it pass if sort was broken? YES. Products existed before. USELESS.
  "javascript checking prices[0] <= prices[1]" → Would it pass if sort was broken? NO. Prices could be [50, 10]. VALID.
  "element_exists on a product after filtering" → Would it pass if filter was broken? YES. Products were there before. USELESS.
  "element_checked on the brand filter checkbox" → Would it pass if filter was broken? NO. Checkbox stays unchecked. VALID.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 MANDATORY ASSERTION RULES BY TEST TYPE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

◆ SORT TESTS — REQUIRED assertions (both mandatory):
  1. element_count on product items with operator "at_least" and count 1  (products rendered)
  2. javascript assertion that compares the first and second item's price/name to verify ORDER changed
     Script pattern: "(() => { const items = [...document.querySelectorAll('[data-test^=product-]')]; const vals = items.map(el => parseFloat(el.textContent.match(/\\d+\\.\\d+/)?.[0] || '999')).filter(n => n > 0); return vals.length >= 2 && vals[0] <= vals[1]; })()"
  ✗ NEVER use element_exists or element_visible as the only assertion for sort tests
  ✗ NEVER use element_value_equals on the sort dropdown — its internal value differs from the label text

◆ FILTER TESTS (brand/category/checkbox) — REQUIRED assertions (both mandatory):
  1. element_checked on the filter checkbox that was clicked
  2. element_count on result items with operator "less_than" and a realistic unfiltered count (use 9 for ~9 unfiltered products)
     OR element_text_contains on a result count label if Semantic Context shows one
  ✗ NEVER use element_exists alone — products existed before filtering

◆ SEARCH TESTS — REQUIRED assertions (all three mandatory):
  1. element_count on result items with operator "at_least" and count 1
  2. element_text_contains on the FIRST result item verifying it contains part of the search term
  3. element_value_equals or element_value_contains on the search input (query was retained)
  ✗ NEVER assert element_exists on a product that existed before the search

◆ FORM SUBMISSION TESTS — REQUIRED assertions:
  1. Check Semantic Context "error_containers" for the real dynamic notification element.
     ONLY use an error_container selector if it is NOT listed in "_static_elements".
     If no dynamic error container exists: assert url_contains on the current page path.
  2. element_text_contains on the dynamic notification with a key word (e.g. "Invalid", "success")
     ONLY if the error_container is confirmed dynamic (not in _static_elements).
  ✗ NEVER assert url_contains if the URL does not change (confirm from Navigation Graph first)
  ✗ NEVER assert element_visible on the submit button (it may disappear after clicking)
  ✗ NEVER use a selector from "_static_elements" to verify form submission outcome

◆ QUANTITY / COUNTER TESTS — REQUIRED:
  1. element_value_equals on the quantity input with the EXACT expected number (e.g. "2" after increment)
  ✗ NEVER assert element_disabled on quantity buttons — behavior varies widely between sites

◆ NAVIGATION / LINK TESTS — REQUIRED (both mandatory):
  1. url_contains on the target path (from Navigation Graph)
  2. element_text_contains or element_visible on a page heading confirming the destination
  ✗ A test that ONLY navigates and checks the URL is NOT worth generating

◆ LOGIN TESTS (no real credentials provided):
  1. element_visible on the login form container (form stays visible because credentials are fake/wrong)
  ✗ NEVER assert URL change or element_hidden on "Sign in" — login WILL FAIL with placeholder credentials

◆ REGISTRATION TESTS (no real credentials provided):
  1. If Navigation Graph shows /register → /login redirect: assert url_contains "/auth/login"
  2. If no redirect shown: assert url_contains on current path + element_visible on form
  3. Use a realistic but fake email: "test_qa_20260326@mailtest.dev"
  ✗ NEVER assert element_hidden on form elements after registration

◆ PRODUCT DETAIL / CONTENT TESTS — REQUIRED:
  1. url_contains with the product URL path
  2. element_visible or element_text_contains on the product name/title
  3. element_visible on the price element

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TEST VARIETY — WHAT TO GENERATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For each PRD user flow, generate:
  (A) One HAPPY PATH test — the expected successful flow with behavioral outcome assertions
  (B) One VALIDATION / EDGE CASE test — IF the flow has a form or state-dependent behavior:
      - Form validation: submit with empty required fields → assert validation error appears
      - Counter edge case: decrement quantity to minimum → assert value stays at "1"
      - Search with no results: search for a nonsense string → assert empty state or count=0
      - Filter combination: apply two filters → assert both checkboxes checked + count reduced

DO NOT generate tests that:
  - Only navigate to a URL and assert that URL (trivial, catches nothing)
  - Only click a link and assert the destination URL (trivial navigation test)
  - Duplicate another test's steps and assertions with minor name variations
  - Assert that elements exist which were already there before any action

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PLAN STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Every step is explicit. NO conditional logic. Plans are 100% deterministic.
2. Hidden elements (dropdown/modal): first click the trigger to reveal them.
3. Return valid JSON only — no markdown fences, no explanation.
4. Every plan must be self-contained, runnable from a fresh browser with no prior state.
   - Cart-dependent pages: add items to cart first.
   - Wizard flows: include ALL preceding steps from step 1.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 URLS & NAVIGATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

5. NAVIGATION URLS: always absolute. NEVER relative.
   Navigate directly to pages with known URLs — do NOT click through menus to reach them.
6. DYNAMIC IDs: NEVER navigate to a URL containing a ULID/UUID not copied from Base URLs/Nav Graph/Locators.
7. URL assertions: ONLY assert url_contains with paths confirmed in Navigation Graph or Base URLs.
   NEVER guess redirect URLs after form submission.
8. For SPAs (Angular, React, Vue): prefer element_visible / element_text_contains over url_contains.
   SPAs often don't change the URL on clicks or searches.
9. NEVER assert element_hidden unless certain the element disappears.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 SELECTORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ARIA roles: button→"button", a[href]→"link", input[text/email/password]→"textbox", input[checkbox]→"checkbox", select→"combobox".

  RULE A — If Available Locators shows `primary: testid(...)`, MUST use that testid. Do NOT use role if testid exists.
  RULE B — Elements with NO quoted name (empty accessible name): MUST use their testid. Cannot use role+name.
  RULE C — testid values ONLY from verbatim Available Locators. NEVER invent testid values.
  RULE D — List/card items: use testid_prefix when locators show "[LIST xN] testid_prefix(...)".
            {"strategy":"testid_prefix","value":"product-","index":0} = first card; index:1 = second.
            NEVER use role+name for cards (names contain dynamic prices/ratings).
  RULE E — NEVER navigate to a URL with a dynamic ID not from Base URLs/Nav Graph/Locators.
  RULE F — NEVER use locators marked [NOT ACTIONABLE].

LOCATOR FORMATS:
  {"strategy":"testid","value":"login-submit"}
  {"strategy":"testid_prefix","value":"product-","index":0}
  {"strategy":"role","value":{"role":"button","name":"Login"}}
  {"strategy":"role","value":{"role":"textbox","name":"Email address *"}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ACTIONS & FORMS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUPPORTED ACTIONS: navigate, click, fill, type, clear, press, select, check, uncheck, hover, focus, scroll, wait
WAIT ACTION: use "duration" (milliseconds) — NOT "timeout". E.g. {"action":"wait","duration":3000}
SELECT: use "label" (visible text), not internal "value". E.g. "label":"Germany" not "value":"DE".
FORM COMPLETENESS: fill ALL required fields. Use verbatim text — no styling symbols like ~ or *.
  Skip optional file upload fields (type=file / "attachment" / "upload" in name) — NEVER fill these.
  For select/combobox: use EXACT label from "options" in Available Locators.
AUTHENTICATED FLOWS: navigate login → fill email → fill password → click submit (explicit click required).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 UNIVERSAL RULES — APPLY TO EVERY SITE, EVERY TEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

① SUBMIT CLICK IS ALWAYS EXPLICIT
  Forms are NEVER submitted by filling fields alone. The LAST step of any form test MUST be:
    {"action":"click","selector":{"strategy":"testid","value":"<submit-testid>"}}
  This applies to: login, register, contact, search, checkout, forgot-password — every form.

② SORT/FILTER REQUIRE A WAIT STEP (SPAs re-render asynchronously)
  After any select/check that triggers a list re-render, add BEFORE the assertions:
    {"action":"wait","duration":3000}

③ NEVER ASSERT SEARCH INPUT VALUE AFTER PRESSING ENTER
  SPA frameworks (Angular/React/Vue) navigate on search submit and clear the input field.
  element_value_equals on a search box after pressing Enter will ALWAYS fail on SPAs.

④ NEVER USE BARE CSS TAG SELECTORS IN ASSERTIONS
  Forbidden: "h1", "h2", "p", ".price", ".title", ".name", "span", "div", "form", "a"
  These are fragile. Use only: testid, role+name, or explicit data-attribute CSS selectors
  like [data-test="quantity"], [data-test="login-error"].

⑤ STATIC ELEMENTS ARE USELESS FOR ASSERTIONS
  Any element visible before AND after your action cannot prove your action worked.
  Check Semantic Context for "_static_elements" — these are always present (e.g. nav bars,
  documentation banners) and must NEVER be used to verify an action succeeded.
  Specifically: if an element appears in "_static_elements", do NOT assert its text.

⑥ NEVER FILL FILE UPLOAD INPUTS
  Inputs of type=file require OS file picker — they cannot be filled with text.
  Skip any field whose name contains "attachment", "upload", "file", or whose type is "file".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FULL ASSERTION CATALOG — USE ALL TYPES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

URL:        url_equals, url_contains
PAGE:       title_contains
EXISTENCE:  element_exists, element_not_exists
COUNT:      element_count  → {"type":"element_count","selector":{...},"count":9,"operator":"less_than"}
            operators: "equals" | "at_least" | "at_most" | "greater_than" | "less_than"
VISIBILITY: element_visible, element_hidden
STATE:      element_enabled, element_disabled, element_checked, element_unchecked
CONTENT:    element_text_equals, element_text_contains, element_value_equals, element_value_contains
ATTRIBUTE:  element_attribute  → {"type":"element_attribute","selector":{...},"attribute":"href","value":"/docs"}
CUSTOM:     javascript  → {"type":"javascript","script":"<expression that returns true/false>"}
            USE FOR: sort order, price range verification, counting DOM nodes, reading computed values
"""

_GENERATOR_PROMPT = """## Base URLs
{base_urls}

## Test Credentials
{credentials_section}

## Product Requirements Document
{prd_content}

## Semantic Context
MINE THIS for assertions:
  - "error_containers": selectors where success/error messages appear → use for element_visible after form submit
  - "form_inputs": every field and its testid → use for element_value_equals after filling
  - "headings": page headings → use for element_text_contains to confirm you're on the right page
{semantic_contexts}

## Navigation Graph
{navigation_graph}

## Available Locators
{locators}

## Output Format
Return a JSON array — no markdown fences, no explanation.

REFERENCE EXAMPLES (these show what GOOD assertions look like — adapt selectors to the actual locators above):

[
  {{
    "test_id": "TC_ex_sort_price_low_high",
    "name": "Sort by Price Low-High reorders product list correctly",
    "steps": [
      {{"action": "navigate", "url": "https://example.com/category/hand-tools"}},
      {{"action": "select", "selector": {{"strategy": "testid", "value": "sort"}}, "label": "Price (Low - High)"}}
    ],
    "assertions": [
      {{"type": "element_count", "selector": {{"strategy": "testid_prefix", "value": "product-"}}, "count": 1, "operator": "at_least"}},
      {{"type": "javascript", "script": "(() => {{ const cards = [...document.querySelectorAll('[data-test^=product-]')]; const prices = cards.map(c => parseFloat(c.textContent.match(/\\\\d+\\\\.\\\\d+/)?.[0]||'999')).filter(n=>n>0); return prices.length >= 2 && prices[0] <= prices[1]; }})()"}}
    ]
  }},
  {{
    "test_id": "TC_ex_filter_brand",
    "name": "Filter by first brand reduces visible products and keeps checkbox checked",
    "steps": [
      {{"action": "navigate", "url": "https://example.com/category/power-tools"}},
      {{"action": "check", "selector": {{"strategy": "testid_prefix", "value": "brand-", "index": 0}}}}
    ],
    "assertions": [
      {{"type": "element_checked", "selector": {{"strategy": "testid_prefix", "value": "brand-", "index": 0}}}},
      {{"type": "element_count", "selector": {{"strategy": "testid_prefix", "value": "product-"}}, "count": 9, "operator": "less_than"}}
    ]
  }},
  {{
    "test_id": "TC_ex_search_hammer",
    "name": "Searching for Hammer returns relevant results with query retained",
    "steps": [
      {{"action": "navigate", "url": "https://example.com"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "search-query"}}, "value": "Hammer"}},
      {{"action": "press", "selector": {{"strategy": "testid", "value": "search-query"}}, "key": "Enter"}}
    ],
    "assertions": [
      {{"type": "element_count", "selector": {{"strategy": "testid_prefix", "value": "product-"}}, "count": 1, "operator": "at_least"}},
      {{"type": "element_text_contains", "selector": {{"strategy": "testid_prefix", "value": "product-", "index": 0}}, "value": "Hammer"}},
      {{"type": "element_value_equals", "selector": {{"strategy": "testid", "value": "search-query"}}, "value": "Hammer"}}
    ]
  }},
  {{
    "test_id": "TC_ex_contact_form",
    "name": "Contact form submission shows success notification",
    "steps": [
      {{"action": "navigate", "url": "https://example.com/contact"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "first-name"}}, "value": "Alice"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "last-name"}}, "value": "Smith"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "email"}}, "value": "alice@mailtest.dev"}},
      {{"action": "select", "selector": {{"strategy": "testid", "value": "subject"}}, "label": "Customer service"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "message"}}, "value": "This is a test message for QA purposes."}},
      {{"action": "click", "selector": {{"strategy": "testid", "value": "contact-submit"}}}}
    ],
    "assertions": [
      {{"type": "element_visible", "selector": {{"strategy": "css", "value": "[data-test='notification-bar']"}}}},
      {{"type": "element_text_contains", "selector": {{"strategy": "css", "value": "[data-test='notification-bar']"}}, "value": "Thank"}}
    ]
  }},
  {{
    "test_id": "TC_ex_quantity_increment",
    "name": "Incrementing product quantity updates the value to 2",
    "steps": [
      {{"action": "navigate", "url": "https://example.com/product/KNOWN_ID"}},
      {{"action": "click", "selector": {{"strategy": "testid", "value": "increase-quantity"}}}}
    ],
    "assertions": [
      {{"type": "element_value_equals", "selector": {{"strategy": "testid", "value": "quantity"}}, "value": "2"}}
    ]
  }},
  {{
    "test_id": "TC_ex_search_no_results",
    "name": "Searching for a nonsense term returns zero results",
    "steps": [
      {{"action": "navigate", "url": "https://example.com"}},
      {{"action": "fill", "selector": {{"strategy": "testid", "value": "search-query"}}, "value": "xzxzxzxz_no_match"}},
      {{"action": "press", "selector": {{"strategy": "testid", "value": "search-query"}}, "key": "Enter"}}
    ],
    "assertions": [
      {{"type": "element_count", "selector": {{"strategy": "testid_prefix", "value": "product-"}}, "count": 0, "operator": "equals"}}
    ]
  }}
]

FINAL REMINDER — apply SELF-CHECK to every assertion before writing it:
  "Would this assertion PASS if the feature was completely broken?"
  → If YES: replace it. → If NO: keep it."""

_VALIDATOR_PROMPT = """\
You are a QA automation validator. You will be given a test plan (JSON) and a list of available
UI locators for the page under test. Your job is to check every selector in the plan steps and
assertions, and replace any selector whose value does not appear in the locators list with the
closest available locator from the list.

Rules:
- Output ONLY the corrected plan as a single valid JSON object. No prose, no markdown fences.
- Preserve all fields (test_id, name, steps, assertions, _meta, etc.) exactly — only update selectors.
- If a selector already matches a locator in the list, leave it unchanged.
- Prefer `role` or `label` strategy over `testid` when available — testid attributes require JavaScript to execute first and may not be present at page load; role/label selectors are stable from initial render.
- If no reasonable match exists, keep the original selector unchanged.

PLAN:
{plan}

AVAILABLE LOCATORS FOR THIS PAGE:
{locators}
"""


class TestGenerator:
    """
    Generates execution plans from a PRD by querying the DB and calling AI once.
    """

    def __init__(
        self,
        db:                   LocatorDB,
        ai_client:            Optional[AIClient] = None,
        max_locators:         int                = 80,
        max_cases:            bool               = False,
        state_graph                              = None,
        num_tests:            Optional[int]      = None,
        negative_tests:       bool               = False,
        compiled_model_path:  Optional[str]      = None,
        logger:               Optional[any]      = None,
    ):
        self._db                  = db
        self._ai                  = ai_client
        self._max_locators        = max_locators
        self._max_cases           = max_cases
        self._state_graph         = state_graph
        self._num_tests           = num_tests  # explicit count; overrides max_cases/default-5
        self._negative_tests      = negative_tests
        self._compiled_model_path = compiled_model_path
        self._log                 = logger or log

    def generate_plans_from_prd(self, prd_content: str, urls: List[str], credentials: Optional[dict] = None) -> List[dict]:
        """
        Create execution plans from a PRD string.
        Raises PlanningError if generation fails.
        """
        if not self._ai:
            raise PlanningError(
                "No AI client configured. "
                "Call TestGenerator(db, AIClient.from_env()) or set QAPAL_AI_PROVIDER."
            )

        # Load all locators, deduplicated to avoid flooding the prompt with 40 copies
        # of the same navigation links. Nav elements (container="nav") are global:
        # only the first occurrence is kept. Page-specific elements (forms, products)
        # are kept per URL.
        #
        # Filtering strategy to stay within token limits:
        # - Exclude /admin/* pages — not relevant for user-facing tests
        # - For /product/* pages, keep only the most-crawled representative page
        all_locs = self._db.get_all_locators(valid_only=True)
        print(f"DEBUG: Generator found {len(all_locs)} locators in DB")
        self._log.info("Planning with %d total locators from DB", len(all_locs))
        self._log.info("Planning with %d total locators from DB", len(all_locs))

        # Find the single most-crawled product page (most hit_count sum)
        from urllib.parse import urlparse
        from collections import defaultdict
        product_page_hits: dict = defaultdict(int)
        for loc in all_locs:
            u = loc.get("url", "")
            if "/product/" in u:
                product_page_hits[u] += loc.get("history", {}).get("hit_count", 0)
        best_product_page = max(product_page_hits, key=product_page_hits.get) if product_page_hits else None

        seen_nav: set = set()         # (role, name) for nav elements — global dedup
        seen_page: set = set()        # (url, role, name) for other elements — per-URL dedup
        locators: list = []
        for loc in all_locs:
            url       = loc.get("url", "")
            role      = loc.get("identity", {}).get("role", "")
            name      = loc.get("identity", {}).get("name", "")
            container = loc.get("identity", {}).get("container", "")

            # Skip admin pages entirely — irrelevant for typical user PRDs
            if "/admin" in url:
                continue
            # Skip elements marked not actionable (e.g. mobile-only hidden buttons)
            if not loc.get("locators", {}).get("actionable", True):
                continue
            # For product pages: only keep the representative page
            parsed = urlparse(url)
            if "/product/" in parsed.path and url != best_product_page:
                continue

            if container == "nav":
                key = (role, name)
                if key not in seen_nav:
                    seen_nav.add(key)
                    locators.append(loc)
            else:
                key = (url, role, name)
                if key not in seen_page:
                    seen_page.add(key)
                    locators.append(loc)

        # Fall back to seed-URL-only if DB is empty
        if not locators:
            for url in urls:
                locators.extend(self._db.get_all(url, valid_only=True))

        if not locators:
            raise PlanningError(
                f"No locators found. Run graph-crawl or crawl first to build the locator context."
            )

        locator_map = {loc["id"]: loc for loc in locators}

        # Load semantic contexts for the referenced URLs
        states = [s for s in (self._db.get_state(u) for u in urls) if s]

        if self._num_tests is not None:
            n = self._num_tests
            instruction = (
                f"\n\nGenerate EXACTLY {n} test case{'s' if n != 1 else ''} covering the most "
                f"important user flows described in the PRD. "
                f"Do NOT exceed {n} test case{'s' if n != 1 else ''} total."
            )
        elif self._max_cases:
            instruction = "\n\nCRITICAL: Generate the MAXIMUM number of most helpful and meaningful test cases that comprehensively cover the requirements in the PRD. Do not limit yourself to just one."
        else:
            instruction = (
                "\n\nGenerate EXACTLY 5 test cases covering the most important user flows "
                "described in the PRD. Choose the 5 flows with the highest business impact "
                "(e.g. authentication, core feature usage, key user journeys). "
                "Do NOT exceed 5 test cases total."
            )

        nav_graph = (
            self._state_graph.format_for_prompt(urls=urls, min_count=2)
            if self._state_graph is not None
            else "(no navigation graph — run tests first to record page transitions)"
        )

        if credentials:
            # Determine the most-likely landing URL after login from the nav graph
            login_url = credentials.get("url", "")
            landing_url = ""
            if self._state_graph and login_url:
                from urllib.parse import urlparse
                login_path = urlparse(login_url).path or "/"
                all_transitions = self._state_graph.all_transitions()
                # Identify login submit transitions by trigger label or testid.
                # Excludes nav-link clicks (Register, Forgot Password, Home, etc.)
                # recorded while browsing from the login page.
                _SUBMIT_LABELS = {
                    # English
                    "login", "login-submit", "submit", "sign in", "log in",
                    "signin", "sign-in", "log-in", "continue", "proceed",
                    "authenticate", "access", "enter", "go", "next",
                    "confirm", "ok", "done", "send", "verify",
                }
                login_submit_edges = [
                    t for t in all_transitions
                    if login_url in t.get("from_url", "")
                    and (
                        t.get("trigger", {}).get("label", "").lower() in _SUBMIT_LABELS
                        or "submit" in str(
                            (t.get("trigger", {}).get("selector") or {}).get("value", "")
                        ).lower()
                        or "login" in str(
                            (t.get("trigger", {}).get("selector") or {}).get("value", "")
                        ).lower()
                    )
                ]
                if login_submit_edges:
                    best = max(login_submit_edges, key=lambda t: t.get("traversal_count", 0))
                    landing_url = best.get("to_url", "")
            landing_hint = f"\n  Landing URL : {landing_url}  ← URL after successful login — assert this URL" if landing_url else ""
            creds_section = (
                f"  Login URL : {credentials.get('url', '')}\n"
                f"  Username  : {credentials.get('username', '')}\n"
                f"  Password  : {credentials.get('password', '')}"
                f"{landing_hint}\n"
                "  (Use these exact values in test steps that perform login)"
            )
        else:
            creds_section = (
                "  (no credentials provided — use placeholder values for form fields)\n"
                "  IMPORTANT: Login will FAIL with placeholder credentials. For login tests:\n"
                "    - Fill email and password with placeholders, click submit\n"
                "    - Assert that the login FORM is visible (element_visible on the form/page)\n"
                "    - Do NOT assert successful login (no url change, no element_hidden 'Sign in')\n"
                "  For registration tests: fill all fields, click submit, assert element_visible on the form"
            )

        # Use compiled model when available and fresh — dramatically reduces token usage
        compiled_locators_text = None
        if self._compiled_model_path:
            try:
                from site_compiler import SiteCompiler
                model = SiteCompiler.load(self._compiled_model_path)
                if model and not model.is_stale(max_age_minutes=120):
                    compiled_locators_text = model.format_for_prompt()
                    log.info("  [compile] Using compiled model (%d locators → ~%d tokens)",
                             model.locator_count, len(compiled_locators_text) // 4)
            except Exception as e:
                log.warning("[compile] Compiled model load failed, using raw locators: %s", e)

        locators_section = compiled_locators_text or _format_locators(locators, self._max_locators, group_by_url=True)

        prompt = _GENERATOR_PROMPT.format(
            base_urls           = "\n".join(f"  - {u}" for u in urls),
            credentials_section = creds_section,
            prd_content         = prd_content + instruction,
            semantic_contexts   = _format_semantic_contexts(states),
            navigation_graph    = nav_graph,
            locators            = locators_section,
        )

        try:
            # Scale max_tokens based on requested test count (~500 tokens per test case)
            base_tokens = 4096 if compiled_locators_text else 8192
            n_tests = self._num_tests or 5
            plan_max_tokens = max(base_tokens, n_tests * 500)
            raw = self._ai.complete(prompt, system_prompt=_GENERATOR_SYSTEM, max_tokens=plan_max_tokens, temperature=0)
        except Exception as e:
            raise PlanningError(f"AI call failed: {e}")

        positive_plans = self._parse_plans(raw, locator_map, base_url=urls[0] if urls else "", credentials=credentials, locators=locators)

        if self._negative_tests:
            neg_plans = self._generate_negative_plans(positive_plans, locators, urls)
            positive_plans.extend(neg_plans)

        return positive_plans

    # ── Negative + boundary test generation ───────────────────────────

    _NEGATIVE_SYSTEM = """You are a security-aware QA engineer generating NEGATIVE and BOUNDARY test cases.

RULES:
1. For each positive test that fills a form, generate ONE failure-path test using wrong/missing inputs.
   - Wrong credentials → assert error message visible using the error_containers from context
   - Missing required field → assert validation error visible
   - Test ID: append "_neg" to the positive test's test_id (e.g. TC001_login → TC001_login_neg)
2. For each positive test that fills a form, generate ONE boundary test with edge-case inputs:
   - Use empty string for one required field
   - Use a 256-character string for a text input
   - Use value: <script>alert(1)</script> for a text input (XSS probe)
   - Use value: ' OR 1=1-- for a text input (SQLi probe)
   - Test ID: append "_boundary" to the positive test's test_id (e.g. TC001_login → TC001_login_boundary)
3. Assertions MUST check element_visible on an error container, NOT url navigation.
   Use the exact error_containers selectors provided in the context.
4. Use IDENTICAL selectors from the positive plan — never invent new ones.
5. Output: JSON array only, same schema as positive plans. No markdown.
"""

    _NEGATIVE_PROMPT = """## Positive Plans (reuse their selectors exactly, flip inputs to invalid)
{positive_plans_json}

## Error Containers Available on This Site
{error_containers}

## Available Locators (for additional context)
{locators_summary}

Generate negative and boundary test cases. Output JSON array only — no markdown.
"""

    def _generate_negative_plans(self, positive_plans: list, locators: list, urls: list) -> list:
        """
        Generate negative (wrong-input) and boundary (edge-case) test plans from
        the existing positive plans. One AI call for all plans combined.
        """
        if not self._ai or not positive_plans:
            return []

        # Only generate negatives for plans that have fill steps (form tests)
        form_plans = [
            p for p in positive_plans
            if any(s.get("action") == "fill" for s in p.get("steps", []))
            and "_planning_error" not in p
        ]
        if not form_plans:
            return []

        # Collect error containers from semantic context
        error_containers: list = []
        if self._db:
            states = self._db.get_all_states() if hasattr(self._db, "get_all_states") else []
            for state in states:
                ctx = state.get("semantic_context") or {}
                error_containers.extend(ctx.get("error_containers", []))
            error_containers = list(dict.fromkeys(error_containers))[:10]  # dedupe, cap at 10

        locators_summary = _format_locators(locators, max_items=40, group_by_url=True)

        prompt = self._NEGATIVE_PROMPT.format(
            positive_plans_json = json.dumps(form_plans, indent=2),
            error_containers    = "\n".join(f"  - {e}" for e in error_containers) or "  (none detected — use role=alert or .error)",
            locators_summary    = locators_summary,
        )

        try:
            raw = self._ai.complete(prompt, system_prompt=self._NEGATIVE_SYSTEM, max_tokens=4096, temperature=0)
        except Exception as e:
            log.warning("negative test generation skipped: %s", e)
            return []

        try:
            locator_map = {loc.get("id", ""): loc for loc in locators}
            neg_plans = self._parse_plans(raw, locator_map, base_url=urls[0] if urls else "")
            # Mark them so reports can distinguish positive vs negative
            for p in neg_plans:
                p.setdefault("_meta", {})["test_type"] = (
                    "boundary" if p.get("test_id", "").endswith("_boundary") else "negative"
                )
            return neg_plans
        except Exception as e:
            log.warning("negative test parsing failed: %s", e)
            return []

    def _validate_plan_with_small_model(self, plan: dict, locators: list) -> dict:
        """
        One cheap small-model pass to fix any remaining selector mismatches after
        the rule-based post-processor chain. Non-fatal — returns original plan on any error.

        Uses the provider's small_model (Haiku for Anthropic, gpt-4o-mini for OpenAI)
        so the cost is ~10% of the main generation call.
        """
        if not self._ai:
            return plan

        steps_with_selectors = [s for s in plan.get("steps", []) if s.get("selector")]
        assertions_with_sel  = [a for a in plan.get("assertions", []) if a.get("selector")]
        if not steps_with_selectors and not assertions_with_sel:
            return plan

        # Build compact locator list for this plan's starting URL
        start_url = next(
            (s.get("url") for s in plan.get("steps", []) if s.get("action") == "navigate"),
            ""
        )
        relevant_locs = [
            {
                "role":   loc.get("identity", {}).get("role", ""),
                "name":   loc.get("identity", {}).get("name", ""),
                "testid": next(
                    (c.get("value") for c in loc.get("locators", {}).get("chain", [])
                     if c.get("strategy") == "testid"),
                    None
                ),
            }
            for loc in locators
            if not start_url or loc.get("url", "").rstrip("/") == start_url.rstrip("/")
        ][:50]  # cap at 50 entries to keep prompt small

        if not relevant_locs:
            return plan

        prompt = _VALIDATOR_PROMPT.format(
            plan=json.dumps(plan, indent=2),
            locators=json.dumps(relevant_locs, indent=2),
        )
        try:
            raw = self._ai.complete(
                prompt,
                max_tokens=2048,
                temperature=0,
                model_override=self._ai.small_model,
            )
            # Extract JSON from response
            corrected = None
            raw = raw.strip()
            if raw.startswith("{"):
                corrected = json.loads(raw)
            elif "```" in raw:
                for part in raw.split("```")[1:]:
                    candidate = part.lstrip("json").strip()
                    if candidate.startswith("{"):
                        corrected = json.loads(candidate.split("```")[0].strip())
                        break
            if corrected and isinstance(corrected, dict) and "steps" in corrected:
                return corrected
        except Exception:
            pass  # validator failure is non-fatal
        return plan

    @staticmethod
    def _strip_dynamic_id(s: str) -> str:
        """Strip a trailing ULID/UUID/hex suffix from a string, e.g.
        'product-01KKEXF1FCV...' → 'product-'"""
        m = _DYNAMIC_ID_RE.search(s)
        return s[: m.start() + 1] if m else s

    def _fix_url_assertions(self, plan: dict) -> dict:
        """
        Post-process a generated plan to fix URL assertions that don't match the
        nav graph. Simulates URL state through the steps and replaces url_contains /
        url_equals assertions when the asserted path doesn't match the expected URL.

        Dynamic IDs (ULIDs/UUIDs) in testid values or URL paths are stripped so that
        assertions use generic prefixes (e.g. '/product/' not '/product/01KKF...').
        """
        from urllib.parse import urlparse
        from locator_db import _normalize_url

        steps      = plan.get("steps", [])
        assertions = plan.get("assertions", [])
        if not assertions:
            return plan

        if self._state_graph is None:
            return plan

        transitions = self._state_graph.all_transitions()
        current_url = ""
        nav_graph_resolved = False  # True only when a click matched a nav graph transition
        nav_graph_lost = False      # True once we lose nav-graph tracking (sticky)

        for step in steps:
            action = step.get("action", "")
            if action == "navigate":
                current_url = step.get("url", current_url)
                nav_graph_resolved = True
                nav_graph_lost = False  # navigate resets tracking
            elif action == "click" and current_url:
                sel   = step.get("selector", {})
                val   = sel.get("value", "")
                strategy = sel.get("strategy", "")
                # Derive the click label from the selector
                if strategy in ("testid", "testid_prefix"):
                    click_label = str(val)
                    # Look up actual role from DB — buttons are actions (stay on page),
                    # links/unknown navigate (may change URL)
                    click_role = self._lookup_testid_role(str(val), current_url)
                elif isinstance(val, dict):
                    click_label = str(val.get("name", "") or val.get("role", ""))
                    click_role = str(val.get("role", "")).lower()
                elif not val and sel.get("name"):
                    click_label = str(sel.get("name", ""))
                    click_role = ""
                else:
                    click_label = str(val)
                    click_role = ""

                # If we already lost nav-graph tracking, don't try to resolve further
                if nav_graph_lost:
                    continue

                click_prefix = self._strip_dynamic_id(click_label)
                norm_cur = _normalize_url(current_url)

                def _trig_sel_val(t):
                    return str((t.get("trigger", {}).get("selector") or {}).get("value", ""))

                matches = [
                    t for t in transitions
                    if norm_cur in t.get("from_url", "")
                    and (
                        click_label in t.get("trigger", {}).get("label", "")
                        or click_label == _trig_sel_val(t)
                        or (click_prefix != click_label and (
                            click_prefix in t.get("trigger", {}).get("label", "")
                            or click_prefix in _trig_sel_val(t)
                        ))
                    )
                ]
                if matches:
                    best = max(matches, key=lambda t: t["traversal_count"])
                    current_url = best["to_url"]
                    nav_graph_resolved = True
                else:
                    # No nav graph transition — we've lost tracking.
                    # Once lost, it stays lost until the next navigate step.
                    # This prevents button clicks after an unresolved navigation
                    # from incorrectly re-asserting the old URL.
                    if click_role in ("link", "") or strategy in ("testid", "testid_prefix"):
                        nav_graph_resolved = False
                        nav_graph_lost = True
                    # Button clicks (actions, not navigation) don't lose tracking
                    # but also don't update the URL

        if not current_url:
            return plan

        curr_path = urlparse(current_url).path
        # If the path contains a dynamic ID, use only the path prefix as the assertion
        # value (e.g. '/product/01KKF...' → '/product/') so it matches any instance.
        curr_path = self._strip_dynamic_id(curr_path)

        fixed = []
        for a in assertions:
            atype = a.get("type", "")
            aval  = str(a.get("value", ""))
            if atype in ("url_contains", "url_equals"):
                # Normalise asserted value: strip dynamic IDs for comparison
                aval_stripped = self._strip_dynamic_id(aval)
                url_stripped  = self._strip_dynamic_id(current_url)
                # Check if asserted value is consistent with the tracked URL.
                # Only override if the nav graph fully tracked the destination URL
                # through all steps. If tracking was lost (unresolved click), trust AI.
                if nav_graph_resolved and not nav_graph_lost and aval_stripped not in url_stripped and url_stripped not in aval_stripped:
                    # Replace with url_contains of the expected (generic) path
                    fixed.append({**a, "type": "url_contains", "value": curr_path,
                                   "_auto_fixed": True, "_original_value": aval})
                    continue
                # Assertion contains a dynamic ID (ULID/UUID) — strip it to a generic
                # prefix so the assertion works for any instance.
                if _DYNAMIC_ID_RE.search(aval):
                    # When nav graph lost tracking, use the AI's own path (stripped)
                    # instead of the stale tracked URL.
                    path_to_use = self._strip_dynamic_id(urlparse(aval).path) if nav_graph_lost else curr_path
                    fixed.append({**a, "type": "url_contains", "value": path_to_use,
                                   "_auto_fixed": True, "_original_value": aval})
                    continue
                # url_equals is too strict — query strings / hashes can be appended by
                # any interaction (form submit, SPA routing). Downgrade to url_contains.
                if atype == "url_equals":
                    fixed.append({**a, "type": "url_contains",
                                   "_auto_fixed": True, "_original_value": aval})
                    continue
            fixed.append(a)

        return {**plan, "assertions": fixed}

    def _simulate_final_url(self, steps: list) -> str:
        """
        Walk the step list and simulate URL state through navigate + click transitions
        using the nav graph. Returns the best-guess final URL after all steps complete.
        Falls back to the last navigate URL when no nav graph transition matches a click.
        """
        from locator_db import _normalize_url

        if self._state_graph is None:
            for s in reversed(steps):
                if s.get("action") == "navigate":
                    return s.get("url", "")
            return ""

        transitions = self._state_graph.all_transitions()
        current_url = ""

        for step in steps:
            action = step.get("action", "")
            if action == "navigate":
                current_url = step.get("url", current_url)
            elif action == "click" and current_url:
                sel = step.get("selector", {})
                val = sel.get("value", "")
                if sel.get("strategy") in ("testid", "testid_prefix"):
                    click_label = str(val)
                elif isinstance(val, dict):
                    click_label = str(val.get("name", "") or val.get("role", ""))
                elif not val and sel.get("name"):
                    click_label = str(sel.get("name", ""))
                else:
                    click_label = str(val)
                click_prefix = self._strip_dynamic_id(click_label)
                norm_cur = _normalize_url(current_url)

                def _tsv(t):
                    return str((t.get("trigger", {}).get("selector") or {}).get("value", ""))

                matches = [
                    t for t in transitions
                    if norm_cur in t.get("from_url", "")
                    and (
                        click_label in t.get("trigger", {}).get("label", "")
                        or click_label == _tsv(t)
                        or (click_prefix != click_label and (
                            click_prefix in t.get("trigger", {}).get("label", "")
                            or click_prefix in _tsv(t)
                        ))
                    )
                ]
                if matches:
                    current_url = max(matches, key=lambda t: t["traversal_count"])["to_url"]

        return current_url

    def _fix_element_assertions(self, plan: dict) -> dict:
        """
        Post-processor: validate element_visible / element_exists assertions against
        the locator DB. If the asserted selector cannot be found in the DB, replace
        the assertion with a safe url_contains fallback using the current page path.

        This catches AI hallucinations like role+name selectors for elements that
        don't exist on the page (e.g. "Pliers More information" link).

        Only validates role-strategy assertions — testid assertions are left as-is
        because they're checked at run-time by the executor against real attributes.
        """
        if not self._db:
            return plan

        from urllib.parse import urlparse
        from locator_db import _normalize_url

        assertions = plan.get("assertions", [])
        steps      = plan.get("steps", [])

        # Simulate URL state through all steps (navigate + click nav-graph transitions)
        # so we use the final page URL, not just the last navigate URL.
        final_url = self._simulate_final_url(steps)
        page_path = self._strip_dynamic_id(urlparse(final_url).path) if final_url else ""

        # Build a set of known (role, name) pairs from the locator DB for the final page.
        # Also include all locators when the final URL is a dynamic product page —
        # those pages share structure with other crawled pages.
        known_role_names: set = set()
        if final_url:
            norm = _normalize_url(final_url)
            final_path_prefix = self._strip_dynamic_id(urlparse(final_url).path)
            for loc in self._db._locs.all():
                loc_url = loc.get("url", "")
                loc_path = self._strip_dynamic_id(urlparse(loc_url).path) if loc_url else ""
                # Match exact URL OR same dynamic path prefix (e.g. /product/ matches any product page)
                if _normalize_url(loc_url) == norm or (
                    final_path_prefix and loc_path == final_path_prefix
                ):
                    ident = loc.get("identity", {})
                    role  = ident.get("role", "").lower()
                    name  = ident.get("name", "").lower()
                    if role:
                        known_role_names.add((role, name))

        fixed = []
        for a in assertions:
            atype = a.get("type", "")
            if atype not in ("element_visible", "element_exists"):
                fixed.append(a)
                continue

            sel = a.get("selector", {}) or {}
            strategy = sel.get("strategy", "")

            # Only validate role-based assertions — testid is checked live
            if strategy != "role":
                fixed.append(a)
                continue

            val  = sel.get("value", {}) or {}
            role = str(val.get("role", "")).lower()
            name = str(val.get("name", "")).lower()

            # Check if this (role, name) pair exists in the locator DB
            if (role, name) in known_role_names:
                fixed.append(a)
                continue

            # Also accept if any known element's name starts with the asserted name
            # (handles slight wording differences like "Add to cart" vs "Add to Cart")
            if any(r == role and n.startswith(name) for r, n in known_role_names if len(name) >= 8):
                fixed.append(a)
                continue

            # Hallucinated element — replace with url_contains fallback if we
            # have a known page path; otherwise keep the original assertion unchanged
            # (emitting url_contains with empty value would match any URL).
            if page_path:
                fallback = {"type": "url_contains", "value": page_path,
                            "_auto_fixed": True, "_original_value": sel}
                fixed.append(fallback)
            else:
                fixed.append(a)

        return {**plan, "assertions": fixed}

    def _fix_malformed_selectors(self, plan: dict) -> dict:
        """
        Repair selectors where the model nested the value incorrectly.

        LLaMA-family models sometimes generate:
            {"strategy": "testid", "value": {"testid": "add-to-cart"}}
        instead of:
            {"strategy": "testid", "value": "add-to-cart"}

        Only applies to strategies whose value MUST be a plain string.
        Strategies that legitimately use a dict value (role, role_container)
        are left untouched.
        """
        # These strategies always take a plain string value — never a dict.
        _STRING_VALUE_STRATEGIES = frozenset({
            "testid", "testid_prefix", "css", "text", "label",
            "placeholder", "aria_label", "aria-label",
        })

        def _unwrap(sel: dict) -> dict:
            if not isinstance(sel, dict):
                return sel
            strategy = sel.get("strategy", "")
            value    = sel.get("value")
            # Only touch strategies that must have a string value
            if strategy not in _STRING_VALUE_STRATEGIES:
                return sel
            if not isinstance(value, dict):
                return sel
            # pattern: {"strategy":"testid","value":{"testid":"foo"}}
            if strategy in value and isinstance(value[strategy], str):
                sel = dict(sel)
                sel["value"] = value[strategy]
                return sel
            # pattern: {"strategy":"testid","value":{"value":"foo"}}
            if "value" in value and isinstance(value["value"], str) and "strategy" not in value:
                sel = dict(sel)
                sel["value"] = value["value"]
                return sel
            # pattern: fully nested selector {"strategy":X,"value":{"strategy":X,"value":"foo"}}
            if "strategy" in value and "value" in value:
                return _unwrap(value)
            return sel

        for step in plan.get("steps", []):
            if "selector" in step:
                step["selector"] = _unwrap(step["selector"])
            if "fallback" in step:
                step["fallback"] = _unwrap(step["fallback"])
        for assertion in plan.get("assertions", []):
            if "selector" in assertion:
                assertion["selector"] = _unwrap(assertion["selector"])
        return plan

    def _fix_selector_strategies(self, plans: list) -> list:
        """
        Post-processor: replace `testid` / `testid_prefix` selectors with stable
        role/label/placeholder alternatives when available in the locator DB chain.

        Two cases handled:
          1. Hallucinated testid: not in DB at all → replace via keyword search
          2. Real testid with stable alternative: in DB but the same locator has
             a label/placeholder/role chain entry → prefer semantic selector over testid
             (handles Angular/React apps where data-testid is only set after JS bootstrap)
        """
        if not self._db:
            return plans

        # Build the set of testid values that actually exist in the locator DB,
        # and a map from testid value → locator entry (for chain inspection).
        known_testids: set = set()
        known_prefixes: set = set()
        testid_to_loc: dict = {}   # testid_value → first locator entry with that testid
        for loc in self._db._locs.all():
            for c in loc.get("locators", {}).get("chain", []):
                if c.get("strategy") == "testid":
                    val = c.get("value", "")
                    known_testids.add(val)
                    testid_to_loc.setdefault(val, loc)
                elif c.get("strategy") == "testid_prefix":
                    known_prefixes.add(c.get("value", ""))

        def _is_hallucinated(sel: dict) -> bool:
            strategy = sel.get("strategy", "")
            value = sel.get("value", "")
            if strategy == "testid":
                return str(value) not in known_testids
            if strategy == "testid_prefix":
                return str(value) not in known_prefixes
            return False

        def _stable_alternative(sel: dict) -> Optional[dict]:
            """
            For a testid that IS in the DB, return the best stable alternative
            from the same locator's chain (label > placeholder > role).
            Returns None if no stable alternative exists (keep testid as-is).
            """
            if sel.get("strategy") != "testid":
                return None
            value = str(sel.get("value", ""))
            loc = testid_to_loc.get(value)
            if not loc:
                return None
            chain = loc.get("locators", {}).get("chain", [])
            for c in chain:
                if c.get("strategy") == "label" and c.get("value"):
                    return {"strategy": "label", "value": c["value"], "_auto_fixed": True}
            for c in chain:
                if c.get("strategy") == "placeholder" and c.get("value"):
                    return {"strategy": "placeholder", "value": c["value"], "_auto_fixed": True}
            for c in chain:
                if c.get("strategy") in ("role", "role_container"):
                    val = c.get("value", {})
                    role_name = (
                        c.get("name")
                        or (val.get("name") if isinstance(val, dict) else None)
                    )
                    role_str = val.get("role", "") if isinstance(val, dict) else str(val)
                    if role_name:
                        return {"strategy": "role",
                                "value": {"role": role_str, "name": role_name},
                                "_auto_fixed": True}
            return None

        def _get_replacement(sel: dict, url: str) -> Optional[dict]:
            if _is_hallucinated(sel):
                return self._find_best_role_selector(sel, url)
            return _stable_alternative(sel)

        for plan in plans:
            steps = plan.get("steps", [])
            curr_url = ""
            for step in steps:
                if step.get("action") == "navigate":
                    curr_url = step.get("url", curr_url)
                sel = step.get("selector") or {}
                if sel.get("strategy") in ("testid", "testid_prefix"):
                    replacement = _get_replacement(sel, curr_url)
                    if replacement:
                        step["selector"] = replacement

            last_url = ""
            for s in steps:
                if s.get("action") == "navigate":
                    last_url = s.get("url", last_url)
            for assertion in plan.get("assertions", []):
                sel = assertion.get("selector") or {}
                if sel and sel.get("strategy") in ("testid", "testid_prefix"):
                    replacement = _get_replacement(sel, last_url)
                    if replacement:
                        assertion["selector"] = replacement

        return plans

    def _fix_role_mismatches(self, plans: list) -> list:
        """
        Post-processor (P0.3): validate role strategy selectors against the locator DB.

        The AI sometimes generates the wrong ARIA role — e.g. role=button when the DB
        has role=link for the same element name. This pass corrects any mismatch so the
        executor's get_by_role() call matches the real DOM.

        Runs unconditionally on all sites (unlike _fix_selector_strategies which only
        applies to no-testid sites).
        """
        if not self._db:
            return plans

        from locator_db import _normalize_url

        for plan in plans:
            steps = plan.get("steps", [])
            curr_url = ""
            for step in steps:
                if step.get("action") == "navigate":
                    curr_url = step.get("url", curr_url)
                # Track URL changes through link clicks
                elif step.get("action") == "click":
                    click_sel = step.get("selector") or {}
                    click_val = click_sel.get("value", {})
                    if isinstance(click_val, dict) and click_val.get("role") == "link":
                        link_dest = self._find_link_destination(click_val.get("name", ""), curr_url)
                        if link_dest:
                            curr_url = link_dest
                sel = step.get("selector") or {}
                if sel.get("strategy") == "role" and isinstance(sel.get("value"), dict):
                    plan_role = sel["value"].get("role", "")
                    plan_name = sel["value"].get("name", "")
                    if plan_role and plan_name and curr_url:
                        db_match = self._find_by_name_in_db(plan_name, curr_url)
                        if db_match:
                            if db_match["role"] != plan_role:
                                sel["value"]["role"] = db_match["role"]
                                sel["_role_corrected"] = True
                            if db_match["name"] != plan_name:
                                sel["value"]["name"] = db_match["name"]
                                sel["_name_corrected"] = True
                        else:
                            # No name match — try to find element by role + testid fallback
                            testid_match = self._find_testid_for_role(plan_role, plan_name, curr_url)
                            if testid_match:
                                sel["strategy"] = "testid"
                                sel["value"] = testid_match
                                sel["_testid_fallback"] = True

            # Also fix role/name in assertions
            for assertion in plan.get("assertions", []):
                sel = assertion.get("selector") or {}
                if sel.get("strategy") == "role" and isinstance(sel.get("value"), dict):
                    plan_role = sel["value"].get("role", "")
                    plan_name = sel["value"].get("name", "")
                    if plan_role and plan_name and curr_url:
                        db_match = self._find_by_name_in_db(plan_name, curr_url)
                        if db_match:
                            if db_match["role"] != plan_role:
                                sel["value"]["role"] = db_match["role"]
                                sel["_role_corrected"] = True
                            if db_match["name"] != plan_name:
                                sel["value"]["name"] = db_match["name"]
                                sel["_name_corrected"] = True
                        else:
                            testid_match = self._find_testid_for_role(plan_role, plan_name, curr_url)
                            if testid_match:
                                sel["strategy"] = "testid"
                                sel["value"] = testid_match
                                sel["_testid_fallback"] = True

        return plans

    def _find_by_name_in_db(self, name: str, url: str) -> Optional[dict]:
        """
        Find a locator DB entry whose accessible name matches `name`
        scoped to `url`. Returns {role, name} or None.

        Match strategy (in priority order):
          1. Exact name match (case-insensitive)
          2. Trivial difference only — trailing/leading punctuation or whitespace
             e.g. "Password *" ↔ "Password", "Email address" ↔ "Email address *"
             but NOT "Save" ↔ "Save Draft" (different words = different element)
        """
        if not self._db or not url:
            return None

        from locator_db import _normalize_url
        norm_url = _normalize_url(url)
        name_lc  = name.strip().lower()
        if not name_lc:
            return None

        exact   = None
        partial = None
        for loc in self._db._locs.all():
            if _normalize_url(loc.get("url", "")) != norm_url:
                continue
            ident    = loc.get("identity", {})
            db_role  = ident.get("role", "")
            db_name  = ident.get("name", "").strip().lower()
            if not db_role or not db_name:
                continue
            if db_name == name_lc:
                exact = {"role": db_role, "name": ident.get("name", "")}
                break
            if exact is None and partial is None:
                if db_name.startswith(name_lc) or name_lc.startswith(db_name):
                    partial = {"role": db_role, "name": ident.get("name", "")}

        return exact or partial

    def _find_link_destination(self, link_name: str, from_url: str) -> Optional[str]:
        """
        Look up where a named link goes from a given page, using the nav graph transitions.
        Returns destination URL or None.
        """
        if not self._db or not from_url or not link_name:
            return None
        from locator_db import _normalize_url
        norm_from = _normalize_url(from_url)
        name_lc = link_name.strip().lower()

        # Check locator DB for links with href-like testids or known destinations
        for loc in self._db._locs.all():
            if _normalize_url(loc.get("url", "")) != norm_from:
                continue
            ident = loc.get("identity", {})
            if ident.get("role") != "link":
                continue
            db_name = ident.get("name", "").strip().lower()
            if db_name != name_lc:
                continue
            # Found the link — check if the chain has an href-based locator
            # or look up transitions in the nav graph
            break
        else:
            return None

        # Search nav graph transitions for this from_url
        try:
            transitions = self._db._db.table("transitions").all()
        except Exception:
            return None
        for t in transitions:
            if _normalize_url(t.get("from_url", "")) != norm_from:
                continue
            to_url = t.get("to_url", "")
            # Check if link name appears in the to_url path
            if name_lc.replace(" ", "-").replace("?", "") in to_url.lower():
                return to_url
            # Check trigger element (may be dict or string)
            trigger = t.get("trigger", "")
            trigger_str = str(trigger.get("name", "")) if isinstance(trigger, dict) else str(trigger)
            if name_lc in trigger_str.lower():
                return to_url
        return None

    def _find_testid_for_role(self, role: str, hallucinated_name: str, url: str) -> Optional[str]:
        """
        When _find_by_name_in_db returns None (name is completely wrong),
        try to find a testid for an element with the same role on the same page
        whose name contains keywords from the hallucinated name.

        Returns testid string or None.
        """
        if not self._db or not url:
            return None
        from locator_db import _normalize_url
        norm_url = _normalize_url(url)
        # Extract meaningful keywords from the hallucinated name
        name_words = set(hallucinated_name.lower().replace("*", "").split())
        name_words -= {"the", "a", "an", "your", "my", "is", "for", "and", "or"}
        if not name_words:
            return None

        for loc in self._db._locs.all():
            if _normalize_url(loc.get("url", "")) != norm_url:
                continue
            ident = loc.get("identity", {})
            if ident.get("role", "") != role:
                continue
            chain = loc.get("locators", {}).get("chain", [])
            testid = None
            for c in chain:
                if c.get("strategy") == "testid":
                    testid = c.get("value")
                    break
            if not testid:
                continue
            # Check if any keyword from hallucinated name overlaps with element name or testid
            db_name = ident.get("name", "").lower()
            db_testid = testid.lower().replace("-", " ").replace("_", " ")
            db_words = set(db_name.split()) | set(db_testid.split())
            db_words -= {"the", "a", "an", "your", "my", "is", "for", "and", "or"}
            if name_words & db_words:
                return testid
        return None

    def _lookup_testid_role(self, testid_value: str, url: str) -> str:
        """
        Look up the ARIA role of an element by its testid value and page URL.
        Returns the role string (e.g. "button", "link") or "" if not found.

        Used by _fix_url_assertions to decide whether a testid click is a
        navigation (link → may change URL) or an action (button → stays on page).
        """
        if not self._db or not url:
            return ""
        from locator_db import _normalize_url
        norm_url = _normalize_url(url)
        for loc in self._db._locs.all():
            if _normalize_url(loc.get("url", "")) != norm_url:
                continue
            chain = loc.get("locators", {}).get("chain", [])
            for c in chain:
                if c.get("strategy") == "testid" and c.get("value") == testid_value:
                    return loc.get("identity", {}).get("role", "")
                if c.get("strategy") == "testid_prefix" and testid_value.startswith(c.get("value", "\x00")):
                    return loc.get("identity", {}).get("role", "")
        return ""  # not found → treat as unknown

    def _find_best_role_selector(self, sel: dict, url: str) -> Optional[dict]:
        """
        Search the locator DB for the best role-based selector matching the given
        testid value string. Returns a `role` strategy dict, or None if no match.
        """
        if not self._db or not url:
            return None

        value = str(sel.get("value", ""))
        # Derive a keyword from the testid value: strip dynamic IDs and trailing separators
        keyword = self._strip_dynamic_id(value).rstrip("-_ ").lower()
        if not keyword:
            return None

        from locator_db import _normalize_url
        norm_url = _normalize_url(url)

        best: Optional[dict] = None
        best_score = -1
        for loc in self._db._locs.all():
            if _normalize_url(loc.get("url", "")) != norm_url:
                continue
            ident = loc.get("identity", {})
            role  = ident.get("role", "")
            name  = ident.get("name", "")
            if not role or not name:
                continue
            name_lc = name.lower()
            # Score by keyword overlap — prefer longer / more specific matches
            if keyword in name_lc or name_lc in keyword:
                score = len(set(keyword.split()) & set(name_lc.split())) + len(keyword)
                if score > best_score:
                    best_score = score
                    best = {"strategy": "role", "value": {"role": role, "name": name},
                            "_auto_fixed": True, "_original_value": sel}

        return best

    def _inject_login_if_missing(self, plan: dict, credentials: dict) -> dict:
        """
        Generic post-processor: if the plan visits an auth-required URL but has
        no login sequence, prepend login steps automatically.

        Auth-required URLs are detected via the nav graph: any URL that only
        appears as a transition target FROM the login page (not reachable from
        unauthenticated pages) is considered auth-only.

        Works for any site — no site-specific URL patterns.
        """
        login_url = credentials.get("url", "")
        username  = credentials.get("username", "")
        password  = credentials.get("password", "")
        if not (login_url and username and password):
            return plan

        steps = plan.get("steps", [])

        # Detect existing login sequence: plan already has a fill on 'email'/'username'
        # and a fill on 'password', so no injection needed.
        # Only check testid-strategy selectors — role selectors have dict values that
        # would stringify to "{'role': 'textbox', 'name': 'Email'}" and never match.
        has_login = any(
            s.get("action") == "fill"
            and (s.get("selector") or {}).get("strategy") == "testid"
            and str((s.get("selector") or {}).get("value", "")).lower()
                in ("email", "username", "user", "user_email")
            for s in steps
        )
        if has_login:
            return plan

        # Determine auth-only URL set from nav graph
        auth_only_urls: set = set()
        if self._state_graph:
            from urllib.parse import urlparse
            all_transitions = self._state_graph.all_transitions()
            # Collect all URLs reachable WITHOUT going through the login page
            publicly_reachable: set = set()
            for t in all_transitions:
                from_u = t.get("from_url", "")
                to_u   = t.get("to_url", "")
                if login_url not in from_u:
                    publicly_reachable.add(to_u)
            # Auth-only = reachable only via login transitions
            for t in all_transitions:
                to_u = t.get("to_url", "")
                if to_u and to_u not in publicly_reachable:
                    auth_only_urls.add(to_u)

        # Check if any navigate step goes to an auth-only URL
        needs_login = any(
            s.get("action") == "navigate" and s.get("url", "") in auth_only_urls
            for s in steps
        )
        if not needs_login:
            return plan

        # Find email/password testid names from the locator DB (generic discovery).
        # Falls back to English keyword defaults ("email", "password", "login-submit")
        # when the DB has no data for this login URL or when the site uses non-English
        # field names (e.g. "utilisateur", "auth-email"). Works for most sites in practice
        # since these defaults match the most common testid conventions.
        email_testid    = "email"
        password_testid = "password"
        submit_testid   = "login-submit"
        if self._db:
            from tinydb import Query as _Q
            _q = _Q()
            login_locs = self._db._locs.search(
                _q.url.test(lambda u: login_url in u)
            )
            for loc in login_locs:
                chains = loc.get("locators", {}).get("chain", [])
                identity = loc.get("identity", {})
                tag  = identity.get("tag", "")
                role = identity.get("role", "")
                name = identity.get("name", "").lower()
                for c in chains:
                    if c.get("strategy") == "testid":
                        v = c["value"]
                        if tag == "input" and role == "textbox" and any(
                            kw in name for kw in ("email", "user", "login", "identifier")
                        ):
                            email_testid = v
                        elif tag == "input" and role in ("textbox", "") and "password" in name:
                            password_testid = v
                        elif tag == "button" and any(
                            kw in name for kw in ("login", "sign in", "submit", "log in")
                        ):
                            submit_testid = v

        login_steps = [
            {"action": "navigate",  "url": login_url},
            {"action": "fill",      "selector": {"strategy": "testid", "value": email_testid},    "value": username},
            {"action": "fill",      "selector": {"strategy": "testid", "value": password_testid}, "value": password},
            {"action": "click",     "selector": {"strategy": "testid", "value": submit_testid}},
        ]
        log.info("  [auto-inject] login steps prepended to %s (auth-only URL detected)",
                 plan.get("test_id"))
        return {**plan, "steps": login_steps + steps}

    def _find_cart_nav_testid(self) -> Optional[str]:
        """
        Look up the testid of the cart navigation element from the locator DB.
        Searches for link elements (<a> / role=link) whose accessible name or
        testid value contains a cart-related keyword.
        Generic — discovers the testid from crawled data, no hardcoding.
        """
        if not self._db:
            return None
        cart_keywords = ("cart", "basket", "bag", "trolley")
        for loc in self._db._locs.all():
            identity = loc.get("identity", {})
            tag  = identity.get("tag", "")
            role = identity.get("role", "")
            name = identity.get("name", "").lower()
            if tag not in ("a",) and role not in ("link",):
                continue
            chains = loc.get("locators", {}).get("chain", [])
            for c in chains:
                if c.get("strategy") == "testid":
                    testid_val = str(c["value"]).lower()
                    if any(kw in testid_val or kw in name for kw in cart_keywords):
                        return str(c["value"])
        return None

    def _inject_cart_prerequisite(self, plan: dict) -> dict:
        """
        Generic post-processor for cart/checkout prerequisite states.

        Three independent repairs applied in order:

        0. PRODUCT URL: replace direct navigation to /product/<dynamic-ID> with
           navigate to category + testid_prefix click. Direct product URLs contain
           ULIDs that change periodically and may point to out-of-stock items.

        1. ADD-TO-CART: if the plan navigates to a cart-required URL (e.g. /checkout,
           /order, /cart) but has no add-to-cart step, prepend:
           navigate to category → click first product → click add-to-cart.

        2. NAV-CART: if the plan navigates to a cart-required URL but has no prior
           click on the cart navigation element (discovered from the locator DB),
           inject that click just before the first cart-required navigate step so
           the browser reaches the cart page via UI interaction.

        Works for any site — cart nav testid is discovered from crawled locators,
        not hardcoded.
        """
        steps = list(plan.get("steps", []))

        def _step_val(s):
            return str((s.get("selector") or {}).get("value", "")).lower()

        # ── Repair 0: replace direct product-URL navigations with category click ─
        # Direct /product/<ULID> URLs use dynamic IDs that change and may be stale
        # or point to out-of-stock items. Replace with category → testid_prefix click.
        from urllib.parse import urlparse
        new_steps = []
        for s in steps:
            if s.get("action") == "navigate":
                path = urlparse(s.get("url", "")).path
                if "/product/" in path and _DYNAMIC_ID_RE.search(path):
                    # Find a category URL from the DB
                    category_url = ""
                    if self._db:
                        from tinydb import Query as _Q
                        _q = _Q()
                        cat_locs = self._db._locs.search(
                            _q.url.test(lambda u: "/category/" in u)
                        )
                        if cat_locs:
                            category_url = cat_locs[0].get("url", "")
                    if category_url:
                        new_steps.append({"action": "navigate", "url": category_url})
                        new_steps.append({
                            "action": "click",
                            "selector": {"strategy": "testid_prefix", "value": "product-", "index": 0},
                        })
                        continue  # skip the original product-URL navigate
            new_steps.append(s)
        if new_steps != steps:
            log.debug("[auto-fix] product URL navigate replaced with category+testid_prefix in %s",
                      plan.get("test_id"))
        steps = new_steps

        cart_required_patterns = ("/checkout", "/order", "/cart", "/basket", "/payment")

        # Detect whether any navigate step goes directly to a cart-required URL
        def _is_cart_navigate(s):
            return (
                s.get("action") == "navigate"
                and any(p in s.get("url", "") for p in cart_required_patterns)
            )

        needs_cart = any(_is_cart_navigate(s) for s in steps)
        if not needs_cart:
            plan["steps"] = steps  # persist Repair 0 changes even when cart repairs are not needed
            return plan

        # ── Repair 1: inject add-to-cart if missing ───────────────────────
        has_add_to_cart = any(
            s.get("action") == "click"
            and _step_val(s) in ("add-to-cart", "add_to_cart", "addtocart", "add-to-basket")
            for s in steps
        )
        if not has_add_to_cart:
            category_url = ""
            product_testid = None
            if self._db:
                from tinydb import Query as _Q
                _q = _Q()
                cat_locs = self._db._locs.search(
                    _q.url.test(lambda u: "/category/" in u)
                )
                if cat_locs:
                    category_url = cat_locs[0].get("url", "")
                    for loc in cat_locs:
                        for c in loc.get("locators", {}).get("chain", []):
                            v = str(c.get("value", ""))
                            if c.get("strategy") == "testid" and v.startswith("product-"):
                                product_testid = v
                                break
                        if product_testid:
                            break

            if category_url:
                cart_steps = [
                    {"action": "navigate", "url": category_url},
                    {
                        "action": "click",
                        "selector": {
                            "strategy": "testid_prefix" if not product_testid else "testid",
                            "value":    "product-" if not product_testid else product_testid,
                            **({"index": 0} if not product_testid else {}),
                        },
                    },
                    {
                        "action": "click",
                        "selector": {"strategy": "testid", "value": "add-to-cart"},
                        "timeout": 15000,
                    },
                ]
                log.info("  [auto-inject] add-to-cart steps prepended to %s", plan.get("test_id"))
                steps = cart_steps + steps

        # ── Repair 2: inject cart-nav click before cart navigate if missing ─
        # Discover the cart navigation testid from the locator DB (generic).
        cart_nav_testid = self._find_cart_nav_testid()
        if cart_nav_testid:
            has_cart_nav = any(
                s.get("action") == "click" and _step_val(s) == cart_nav_testid.lower()
                for s in steps
            )
            if not has_cart_nav:
                first_cart_nav_idx = next(
                    (i for i, s in enumerate(steps) if _is_cart_navigate(s)),
                    None,
                )
                if first_cart_nav_idx is not None:
                    nav_step = {
                        "action":   "click",
                        "selector": {"strategy": "testid", "value": cart_nav_testid},
                        "timeout":  10000,
                    }
                    steps = steps[:first_cart_nav_idx] + [nav_step] + steps[first_cart_nav_idx:]
                    log.info("  [auto-inject] cart-nav click (testid=%r) injected in %s",
                             cart_nav_testid, plan.get("test_id"))

        return {**plan, "steps": steps}

    def _is_auth_step(self, step: dict) -> bool:
        """Heuristic check if a step is part of an auth flow (fill email/pass)."""
        action = step.get("action")
        if action not in ("fill", "type"): return False
        sel = step.get("selector", {})
        val = str(sel.get("value", "")).lower()
        return any(x in val for x in ("email", "user", "pass", "login"))

    def _sanitize_plan(self, plan: dict) -> Optional[dict]:
        """Final sanity check for AI plans to catch loops and orphaned starts."""
        steps = plan.get("steps", [])
        if not steps: 
            log.warning("Plan %s has no steps", plan.get("test_id"))
            return None
        
        # 1. Length check (Task 4.3)
        if len(steps) > 25:
             log.warning("Plan %s too long (%d steps), truncating", plan.get("test_id"), len(steps))
             plan["steps"] = steps[:25]
             steps = plan["steps"]

        # 2. Duplicate consecutive steps (hallucinated loops)
        sanitized_steps = []
        for s in steps:
            if not sanitized_steps:
                sanitized_steps.append(s)
                continue
            last = sanitized_steps[-1]
            if s.get("action") == last.get("action") and s.get("selector") == last.get("selector") and s.get("value") == last.get("value"):
                continue # skip duplicate
            sanitized_steps.append(s)
        plan["steps"] = sanitized_steps

        # 3. Start step coherence
        first_action = sanitized_steps[0].get("action")
        if first_action not in ("navigate", "wait") and not self._is_auth_step(sanitized_steps[0]):
             # If it starts with 'click' without context, it's likely a garbage plan.
             log.warning("Plan %s starts with orphaned action: %s", plan.get("test_id"), first_action)
             return None
             
        return plan

    def _parse_plans(self, text: str, locator_map: dict, base_url: str = "", credentials: Optional[dict] = None, locators: Optional[list] = None) -> List[dict]:
        text = text.strip()
        # Strip <think>...</think> reasoning blocks emitted by reasoning models
        # (NVIDIA nemotron, MiniMax, DeepSeek-R1, etc.)
        import re as _re
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts[1:]:
                candidate = part.lstrip("json").strip()
                if candidate.startswith("["):
                    text = candidate
                    break

        try:
            plans_data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract only the first complete JSON array (model may append extra text/arrays)
            if text.startswith("["):
                depth, end = 0, -1
                in_str, escape = False, False
                for i, ch in enumerate(text):
                    if escape:          escape = False; continue
                    if ch == "\\":      escape = True;  continue
                    if ch == '"':       in_str = not in_str; continue
                    if in_str:          continue
                    if ch == "[":       depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:  end = i; break
                if end != -1:
                    try:
                        plans_data = json.loads(text[:end + 1])
                    except json.JSONDecodeError as e2:
                        raise PlanningError(f"AI returned invalid JSON: {e2}\nPreview: {text[:300]}")
                else:
                    # Truncated output — try to recover complete plan objects
                    # Find last complete "}" at depth=1 and close the array
                    _log.warning("AI output truncated — attempting partial recovery")
                    last_complete = -1
                    d2, in_s2, esc2 = 0, False, False
                    for i, ch in enumerate(text):
                        if esc2:          esc2 = False; continue
                        if ch == "\\":    esc2 = True;  continue
                        if ch == '"':     in_s2 = not in_s2; continue
                        if in_s2:         continue
                        if ch in "[{":    d2 += 1
                        elif ch in "]}":
                            d2 -= 1
                            if d2 == 1 and ch == "}":
                                last_complete = i
                    if last_complete > 0:
                        # Trim trailing comma + close array
                        recovered = text[:last_complete + 1].rstrip().rstrip(",") + "\n]"
                        try:
                            plans_data = json.loads(recovered)
                            _log.info("Recovered %d complete plan(s) from truncated output", len(plans_data))
                        except json.JSONDecodeError as e3:
                            raise PlanningError(f"AI returned unclosed JSON array (recovery failed): {e3}\nPreview: {text[:300]}")
                    else:
                        raise PlanningError(f"AI returned unclosed JSON array\nPreview: {text[:300]}")
            else:
                raise PlanningError(f"AI returned non-JSON response\nPreview: {text[:300]}")

        if not isinstance(plans_data, list):
            # If AI returns a single object instead of an array, wrap it
            if isinstance(plans_data, dict):
                plans_data = [plans_data]
            else:
                raise PlanningError("AI did not return a JSON array of plans.")

        parsed_plans = []
        for i, plan_data in enumerate(plans_data):
            test_id = plan_data.get("test_id", f"PRD_TC_{i+1}")
            try:
                plan_data.setdefault("test_id", test_id)
                plan_data.setdefault("steps", [])
                plan_data.setdefault("assertions", [])

                # Resolve relative navigate URLs and correct domain typos
                if base_url:
                    from urllib.parse import urljoin, urlparse
                    base_domain = urlparse(base_url).netloc  # e.g. "practicesoftwaretesting.com"
                    for step in plan_data["steps"]:
                        if step.get("action") == "navigate":
                            url = step.get("url", "")
                            if not url:
                                continue
                            if not url.startswith(("http://", "https://")):
                                step["url"] = urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))
                            else:
                                # Correct domain typos: if the AI generated a URL with a similar
                                # domain (e.g. "practicessoftwaretesting.com" vs
                                # "practicesoftwaretesting.com"), replace with correct domain.
                                parsed = urlparse(url)
                                gen_domain = parsed.netloc
                                if gen_domain and gen_domain != base_domain:
                                    from difflib import SequenceMatcher
                                    ratio = SequenceMatcher(None, gen_domain, base_domain).ratio()
                                    if ratio >= 0.85:  # very similar domain → likely a typo
                                        step["url"] = url.replace(gen_domain, base_domain, 1)
                            # Also reject external domains not related to base_url
                            parsed_final = urlparse(step["url"])
                            if parsed_final.netloc and parsed_final.netloc != base_domain:
                                # Replace with base URL if domain doesn't match at all
                                if "example.com" in parsed_final.netloc or \
                                   "localhost" in parsed_final.netloc:
                                    step["url"] = base_url

                # Validate element_ids — flag invented ones
                for item in plan_data["steps"] + plan_data["assertions"]:
                    eid = item.get("element_id")
                    if eid and eid not in locator_map:
                        item["_invalid_element_id"] = True
                        item["_needs_review"]       = True

                # Prerequisite injectors run BEFORE _fix_url_assertions so the
                # URL simulation sees the complete step sequence.
                if credentials:
                    plan_data = self._inject_login_if_missing(plan_data, credentials)
                plan_data = self._inject_cart_prerequisite(plan_data)

                # Fix URL assertions using nav-graph URL tracking (runs last,
                # after all prerequisite steps have been injected).
                if self._state_graph:
                    plan_data = self._fix_url_assertions(plan_data)

                # Replace hallucinated element assertions with safe url_contains fallback.
                plan_data = self._fix_element_assertions(plan_data)

                # ── Phase 4: Plan Sanitization ─────────────────────
                plan_data = self._sanitize_plan(plan_data)
                if not plan_data:
                    continue

                parsed_plans.append(plan_data)
            except Exception as e:
                # Even if one plan fails validation, we should try the rest
                parsed_plans.append({"test_id": test_id, "_planning_error": str(e)})

        # ── Post-processor pipeline ──────────────────────────────────

        # P0: Fix assertion schema — AI sometimes puts selector in "value" instead of "selector"
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            for a in p.get("assertions", []):
                val = a.get("value")
                if isinstance(val, dict) and "strategy" in val and "selector" not in a:
                    a["selector"] = val
                    a.pop("value", None)
                    a["_schema_fixed"] = True

        # P0.02: Fix assertion param naming — AI sometimes uses "text" instead of "value"
        # for content assertions (element_text_contains, element_text_equals, etc.)
        _CONTENT_ASSERTIONS = {"element_text_contains", "element_text_equals", "element_text_matches",
                               "element_value_contains", "element_value_equals"}
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            for a in p.get("assertions", []):
                if a.get("type") in _CONTENT_ASSERTIONS:
                    if "text" in a and "value" not in a:
                        a["value"] = a.pop("text")
                        a["_param_fixed"] = True

        # P0.05: Fix assertion-as-action — AI sometimes uses assertion types as step actions
        _ASSERTION_TO_WAIT_STATE = {
            "element_visible": "visible",
            "element_exists":  "attached",
            "element_hidden":  "hidden",
        }
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            for s in p.get("steps", []):
                action = s.get("action", "")
                if action in _ASSERTION_TO_WAIT_STATE and s.get("selector"):
                    s["action"] = "wait"
                    s["state"]  = _ASSERTION_TO_WAIT_STATE[action]

        # P0.1: Fix wait schema — AI sometimes puts duration in "value" instead of "duration"
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            for s in p.get("steps", []):
                if s.get("action") == "wait" and "value" in s and "duration" not in s:
                    val = s.pop("value")
                    if isinstance(val, (int, float)):
                        s["duration"] = int(val)

        # P0.3: Remove empty fill steps that target file/upload inputs.
        # Empty fills on text inputs are kept — they may intentionally clear a field.
        _FILE_INPUT_KEYWORDS = frozenset({"attachment", "file", "upload", "document", "image", "photo", "avatar", "resume", "cv"})
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            steps = p.get("steps", [])
            if steps:
                cleaned = []
                for s in steps:
                    if s.get("action") == "fill" and not s.get("value"):
                        # Check if the selector name suggests a file input
                        sel_val = s.get("selector", {}).get("value", "")
                        sel_name = sel_val.get("name", "") if isinstance(sel_val, dict) else str(sel_val)
                        if any(kw in sel_name.lower() for kw in _FILE_INPUT_KEYWORDS):
                            continue  # drop empty fill on file inputs
                    cleaned.append(s)
                p["steps"] = cleaned

        # P0.5: Remove vacuous assertions (url_contains with empty/trivial value)
        for p in parsed_plans:
            if "_planning_error" in p:
                continue
            assertions = p.get("assertions", [])
            if assertions:
                meaningful = []
                for a in assertions:
                    val = a.get("value", "")
                    if a.get("type") in ("url_contains", "url_equals", "url_matches"):
                        if not val or str(val).strip() in ("", "/", "?", ".*", ".*.*", "null", "undefined"):
                            continue  # skip vacuous URL assertion
                    meaningful.append(a)
                p["assertions"] = meaningful

        # P0.6: Remove assertions on static elements (always-present elements that prove nothing)
        # Build the global set of static element selectors from all states in the DB
        _static_selectors: set[str] = set()
        if self._db:
            _all_states = self._db.all_states() if hasattr(self._db, "all_states") else []
            for _st in _all_states:
                _ctx = _st.get("semantic_context") or {}
                for _sel in _ctx.get("_static_elements", []):
                    # normalise: strip quotes, lower, and store both forms
                    _static_selectors.add(_sel.strip())
                    _static_selectors.add(_sel.strip().replace("'", '"').replace('"', "'"))

        if _static_selectors:
            # Normalise selectors: strip quotes for quote-agnostic comparison
            def _norm_sel(s: str) -> str:
                return s.strip().replace("'", "").replace('"', "").replace(" ", "").lower()

            _static_normalised = {_norm_sel(s) for s in _static_selectors}

            for p in parsed_plans:
                if "_planning_error" in p:
                    continue
                kept = []
                for a in p.get("assertions", []):
                    sel = a.get("selector", {})
                    sel_val = sel.get("value", "") if isinstance(sel, dict) else ""
                    sel_str = str(sel_val).strip() if sel_val else ""
                    is_static = (sel_str in _static_selectors or
                                 _norm_sel(sel_str) in _static_normalised)
                    if is_static:
                        a["_dropped_static"] = True
                        continue  # remove assertion on static element
                    kept.append(a)
                if len(kept) < len(p.get("assertions", [])):
                    dropped = len(p.get("assertions", [])) - len(kept)
                    log.debug("P0.6: dropped %d static-element assertion(s) from %s",
                                  dropped, p.get("test_id", "?"))
                    # If all assertions were dropped, add a url_contains fallback
                    if not kept:
                        # Find the last navigate step to infer a meaningful URL path.
                        # For form-submission pages (register, login, contact, forgot-password),
                        # the URL CHANGES after submit — use the known redirect target instead.
                        _FORM_REDIRECT_PATHS = {
                            "/auth/register":        "/auth/login",
                            "/auth/login":           "/account",
                            "/auth/forgot-password": "/auth/forgot-password",
                        }
                        nav_url = None
                        for step in reversed(p.get("steps", [])):
                            if step.get("action") == "navigate":
                                nav_url = step.get("url", "")
                                break
                        if nav_url:
                            from urllib.parse import urlparse as _urlparse
                            path = _urlparse(nav_url).path.rstrip("/") or "/"
                            # Use known post-submit redirect if available
                            fallback_path = _FORM_REDIRECT_PATHS.get(path, path)
                            kept = [{"type": "url_contains", "value": fallback_path,
                                     "_auto_added": "P0.6_fallback"}]
                            log.debug("P0.6: added url_contains fallback '%s'", fallback_path)
                p["assertions"] = kept

        # P1: Normalize malformed selectors: LLaMA sometimes generates
        # {"strategy":"testid","value":{"testid":"foo"}} instead of {"strategy":"testid","value":"foo"}
        parsed_plans = [self._fix_malformed_selectors(p) for p in parsed_plans]

        # P2: Fix testid selectors — replace with stable role/label alternatives from DB.
        parsed_plans = self._fix_selector_strategies(parsed_plans)

        # P3: Fix role mismatches — correct role=button when DB has role=link, etc.
        parsed_plans = self._fix_role_mismatches(parsed_plans)

        # P4: Small-model validation — DISABLED by default.
        # The small model frequently undoes deterministic fixes from P1-P3.
        # Enable via QAPAL_SMALL_MODEL_VALIDATOR=true if needed.
        if os.getenv("QAPAL_SMALL_MODEL_VALIDATOR", "false").lower() == "true" and locators:
            parsed_plans = [
                self._validate_plan_with_small_model(p, locators)
                if "_planning_error" not in p else p
                for p in parsed_plans
            ]

        # Stamp _meta on all successfully parsed plans
        for plan_data in parsed_plans:
            if "_planning_error" in plan_data or "_meta" in plan_data:
                continue
            plan_data["_meta"] = {
                "source":      "prd_generator",
                "planned_at":  datetime.now(timezone.utc).isoformat(),
                "locators":    len(locator_map),
                "ai_model":    self._ai.model,
            }

        return parsed_plans


if __name__ == "__main__":
    # Standalone smoke test
    import asyncio
    
    async def test():
        db = LocatorDB()
        try:
            ai = AIClient.from_env()
            gen = TestGenerator(db, ai, max_cases=True)
            log.info("Generator initialized. Running smoke test...")
            prd = "# Login\nUser must enter email and password."
            # Mock some locators in DB if empty for the test
            db.upsert("https://example.com", {
                "role": "textbox", "name": "Email", "tag": "input",
                "loc": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
                "actionable": True
            })
            plans = gen.generate_plans_from_prd(prd, ["https://example.com"])
            log.info("Generated %d plans.", len(plans))
            for p in plans:
                log.info(" - %s: %d steps", p.get("test_id"), len(p.get("steps", [])))
        except Exception as e:
            log.error("Smoke test failed: %s", e)
        finally:
            db.close()

    asyncio.run(test())
