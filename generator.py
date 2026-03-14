"""
generator.py — QAPal PRD Test Generator
=========================================
Reads a PRD and a list of available locators, then outputs fully-mapped 
execution plans in a single AI call.
"""

import json
import re
from datetime import datetime, timezone
from typing import List, Optional

from locator_db import LocatorDB, DYNAMIC_ID_RE as _DYNAMIC_ID_RE
from planner import PlanningError, _format_locators, _format_semantic_contexts, _parse_plan
from ai_client import AIClient


_GENERATOR_SYSTEM = """You are a test automation engineer for QAPal, a deterministic UI test automation system.

Your job: read a human-written Product Requirements Document (PRD) and a list of available UI element locators, and generate a set of deterministic test execution plans.

RULES:
1. Parse the PRD, identify test scenarios, generate steps and assertions.
2. EVERY step is explicit. NO conditional logic. Plans are 100% deterministic.
3. Hidden elements (dropdown/modal): first click the trigger to reveal them.
4. Pre-existing elements: use exact locator from Available Locators + element_id. Dynamic elements: use role or text strategy — NEVER css.
5. ARIA roles: button→"button", a[href]→"link", input[text/email/password]→"textbox", input[checkbox]→"checkbox", select→"combobox".
6. Return valid JSON only — no markdown, no explanation.
7. NAVIGATION URLS: always absolute (e.g. "https://app.com/login"). NEVER relative.
8. ASSERTION ACCURACY — derive post-action URL from Navigation Graph (highest-count edge). If NO outgoing edge exists for the action, assert url_contains the CURRENT path or assert element_visible. NEVER invent URLs.
9. FORM COMPLETENESS: fill ALL fields shown in Available Locators. Use verbatim text values — no styling symbols like ~ or *.
10. DYNAMIC IDs: NEVER navigate to a URL containing a ULID/UUID you did not copy from Base URLs, Nav Graph, or Locators.

SELECTOR FORMAT:
  RULE A — If the Available Locators entry shows a `primary: testid(...)` for an element, you MUST use
            that testid. Do NOT make up a name and use role if a testid exists in the locator list.
  RULE B — Elements shown as `button (in form)` or `button` with NO quoted name have an empty accessible
            name. You CANNOT locate them with `role+name`. You MUST use their testid.
  RULE C — testid strategy ONLY for values that appear verbatim in Available Locators. NEVER invent testid values.
  RULE F — NEVER use any locator marked [NOT ACTIONABLE] in Available Locators. These elements cannot be interacted with (e.g. mobile-only buttons hidden at desktop). Skip them entirely and use alternative locators or navigation.
  RULE D — List/card items: use testid_prefix when Available Locators shows "[LIST xN] testid_prefix(...)".
            {"strategy":"testid_prefix","value":"product-","index":0} = first card; index=1 = second.
            NEVER use role+name for cards (names contain dynamic prices/ratings).
  RULE E — NEVER navigate to a URL with a dynamic ID (ULID/UUID) not copied from Base URLs/Nav Graph/Locators.

LOCATOR EXAMPLES:
  {"strategy":"testid","value":"login-submit"}
  {"strategy":"testid_prefix","value":"product-","index":0}
  {"strategy":"role","value":{"role":"button","name":"Login"}}
  {"strategy":"role","value":{"role":"textbox","name":"Email address *"}}
  {"strategy":"text","value":"Buy milk"}

SUPPORTED ACTIONS: navigate, click, fill, type, clear, press, select, check, uncheck, hover, focus, scroll, wait

SELECT: use "label" (visible text), not "value" (HTML attribute). E.g. "label":"Germany" not "value":"DE".

AUTHENTICATED FLOWS: tests requiring login MUST start with: navigate login page → fill email → fill password → click submit.

PREREQUISITE STATES: Every plan must be fully self-contained and runnable from a fresh browser with no prior state.
  - If a page requires items in a cart (e.g. /checkout, /order), include: navigate to a product page → click add-to-cart BEFORE navigating to that page.
  - If a page requires a completed prior step in a wizard (e.g. step 3 of 4), include ALL preceding steps from step 1.
  - Never navigate directly to a mid-flow URL (checkout, confirmation, payment) without completing the prerequisite steps.

SUPPORTED ASSERTION TYPES:
  url_equals, url_contains, element_exists, element_visible, element_hidden,
  element_contains_text, element_text_equals, element_value_equals
"""

_GENERATOR_PROMPT = """## Base URLs (use these as the root for all navigate actions)
{base_urls}

## Test Credentials (use these exact values for login/registration test steps)
{credentials_section}

## Product Requirements Document
{prd_content}

## Semantic Context (page structure)
{semantic_contexts}

## Navigation Graph
{navigation_graph}

## Available Locators
{locators}

## Output Format
Return a JSON array of execution plans exactly like this — no markdown fences:
[
  {{
    "test_id": "TC001_login",
    "name": "User can log in successfully",
    "steps": [
      {{"action": "navigate", "url": "https://..."}},
      {{
        "action": "fill",
        "selector":  {{"strategy": "role", "value": {{"role": "textbox", "name": "Email address *"}}}},
        "value": "user@example.com"
      }}
    ],
    "assertions": [
      {{"type": "url_contains", "value": "/dashboard"}}
    ]
  }}
]"""

_VALIDATOR_PROMPT = """\
You are a QA automation validator. You will be given a test plan (JSON) and a list of available
UI locators for the page under test. Your job is to check every selector in the plan steps and
assertions, and replace any selector whose value does not appear in the locators list with the
closest available locator from the list.

Rules:
- Output ONLY the corrected plan as a single valid JSON object. No prose, no markdown fences.
- Preserve all fields (test_id, name, steps, assertions, _meta, etc.) exactly — only update selectors.
- If a selector already matches a locator in the list, leave it unchanged.
- Prefer `testid` strategy when a matching testid is available; otherwise use `role`.
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
        db:           LocatorDB,
        ai_client:    Optional[AIClient] = None,
        max_locators: int                = 80,
        max_cases:    bool               = False,
        state_graph                      = None,
        num_tests:    Optional[int]      = None,
    ):
        self._db           = db
        self._ai           = ai_client
        self._max_locators = max_locators
        self._max_cases    = max_cases
        self._state_graph  = state_graph
        self._num_tests    = num_tests  # explicit count; overrides max_cases/default-5

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
            creds_section = "  (no credentials provided — use placeholder values for login tests)"

        prompt = _GENERATOR_PROMPT.format(
            base_urls           = "\n".join(f"  - {u}" for u in urls),
            credentials_section = creds_section,
            prd_content         = prd_content + instruction,
            semantic_contexts   = _format_semantic_contexts(states),
            navigation_graph    = nav_graph,
            locators            = _format_locators(locators, self._max_locators, group_by_url=True),
        )

        try:
            raw = self._ai.complete(prompt, system_prompt=_GENERATOR_SYSTEM, max_tokens=8192, temperature=0)
        except Exception as e:
            raise PlanningError(f"AI call failed: {e}")

        return self._parse_plans(raw, locator_map, base_url=urls[0] if urls else "", credentials=credentials, locators=locators)

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

        for step in steps:
            action = step.get("action", "")
            if action == "navigate":
                current_url = step.get("url", current_url)
            elif action == "click" and current_url:
                sel   = step.get("selector", {})
                val   = sel.get("value", "")
                # Derive the click label from the selector (testid value or role name).
                # Also handle AI outputting "name" at the selector top level.
                if sel.get("strategy") in ("testid", "testid_prefix"):
                    click_label = str(val)
                elif isinstance(val, dict):
                    click_label = str(val.get("name", "") or val.get("role", ""))
                elif not val and sel.get("name"):
                    click_label = str(sel.get("name", ""))
                else:
                    click_label = str(val)
                # When click_label contains a dynamic ID (e.g. product-01KKF...), strip
                # it to match nav graph entries that may have a different ULID.
                click_prefix = self._strip_dynamic_id(click_label)
                norm_cur = _normalize_url(current_url)
                # Find a matching nav graph transition from the current URL.
                # Match by: (a) trigger label text, (b) trigger selector value (testid),
                # (c) prefix when click_label contains a dynamic ID.
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
                # Check if asserted value is consistent with the tracked URL
                if aval_stripped not in url_stripped and url_stripped not in aval_stripped:
                    # Replace with url_contains of the expected (generic) path
                    fixed.append({**a, "type": "url_contains", "value": curr_path,
                                   "_auto_fixed": True, "_original_value": aval})
                    continue
                # Assertion is consistent but might contain a stale/exact ULID — use
                # the generic prefix so the assertion works for any dynamic ID instance.
                if _DYNAMIC_ID_RE.search(aval):
                    fixed.append({**a, "type": "url_contains", "value": curr_path,
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

        assertions = plan.get("assertions", [])
        steps      = plan.get("steps", [])

        # Determine the expected page URL from the last navigate step
        last_url = ""
        for s in steps:
            if s.get("action") == "navigate":
                last_url = s.get("url", last_url)
        page_path = ""
        if last_url:
            from urllib.parse import urlparse
            page_path = self._strip_dynamic_id(urlparse(last_url).path)

        # Build a set of known (role, name) pairs from the locator DB for this page
        known_role_names: set = set()
        if last_url:
            from locator_db import _normalize_url
            norm = _normalize_url(last_url)
            for loc in self._db._locs.all():
                if _normalize_url(loc.get("url", "")) != norm:
                    continue
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
            if any(r == role and n.startswith(name[:6]) for r, n in known_role_names if len(name) >= 6):
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

    def _fix_selector_strategies(self, plans: list) -> list:
        """
        Post-processor: replace `testid` / `testid_prefix` selectors on sites that
        have no data-testid attributes in the locator DB.

        On plain-HTML sites (e.g. books.toscrape.com) the AI hallucinates testid
        selectors because the locator prompt doesn't clearly signal that testid is
        absent. This post-processor detects the absence globally and replaces every
        testid-strategy selector with the best matching `role` selector from the DB.
        """
        if not self._db:
            return plans

        # Check whether any crawled locator on this site has a testid chain entry.
        # If even one exists, the AI's testid usage is intentional — leave plans alone.
        has_testid = any(
            any(c.get("strategy") == "testid" for c in loc.get("locators", {}).get("chain", []))
            for loc in self._db._locs.all()
        )
        if has_testid:
            return plans

        for plan in plans:
            steps = plan.get("steps", [])
            # Fix step selectors, tracking current URL context
            curr_url = ""
            for step in steps:
                if step.get("action") == "navigate":
                    curr_url = step.get("url", curr_url)
                sel = step.get("selector") or {}
                if sel.get("strategy") in ("testid", "testid_prefix"):
                    replacement = self._find_best_role_selector(sel, curr_url)
                    if replacement:
                        step["selector"] = replacement

            # Fix assertion selectors (use last navigate URL)
            last_url = ""
            for s in steps:
                if s.get("action") == "navigate":
                    last_url = s.get("url", last_url)
            for assertion in plan.get("assertions", []):
                sel = assertion.get("selector") or {}
                if sel and sel.get("strategy") in ("testid", "testid_prefix"):
                    replacement = self._find_best_role_selector(sel, last_url)
                    if replacement:
                        assertion["selector"] = replacement

        return plans

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
        print(f"  ↪ [auto-inject] login steps prepended to {plan.get('test_id')} "
              f"(auth-only URL detected)")
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
            print(f"  ↪ [auto-fix] product URL navigate replaced with category+testid_prefix in {plan.get('test_id')}")
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
                print(f"  ↪ [auto-inject] add-to-cart steps prepended to {plan.get('test_id')}")
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
                    print(f"  ↪ [auto-inject] cart-nav click (testid={cart_nav_testid!r}) "
                          f"injected in {plan.get('test_id')}")

        return {**plan, "steps": steps}

    def _parse_plans(self, text: str, locator_map: dict, base_url: str = "", credentials: Optional[dict] = None, locators: Optional[list] = None) -> List[dict]:
        text = text.strip()
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

                parsed_plans.append(plan_data)
            except Exception as e:
                # Even if one plan fails validation, we should try the rest
                parsed_plans.append({"test_id": test_id, "_planning_error": str(e)})

        # Fix testid selectors on plain-HTML sites (operates across all plans at once
        # so the has_testid check is evaluated once for the entire site).
        parsed_plans = self._fix_selector_strategies(parsed_plans)

        # Small-model validation pass: one cheap call per plan to fix any remaining
        # selector mismatches that rule-based post-processors couldn't catch.
        if locators:
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
            print("Generator initialized. Running smoke test...")
            prd = "# Login\nUser must enter email and password."
            # Mock some locators in DB if empty for the test
            db.upsert("https://example.com", {
                "role": "textbox", "name": "Email", "tag": "input",
                "loc": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
                "actionable": True
            })
            plans = gen.generate_plans_from_prd(prd, ["https://example.com"])
            print(f"Generated {len(plans)} plans.")
            for p in plans:
                print(f" - {p.get('test_id')}: {len(p.get('steps', []))} steps")
        except Exception as e:
            print(f"Smoke test failed: {e}")
        finally:
            db.close()

    asyncio.run(test())
