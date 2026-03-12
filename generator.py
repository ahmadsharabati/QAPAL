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

from locator_db import LocatorDB
from planner import PlanningError, _format_locators, _format_semantic_contexts, _parse_plan
from ai_client import AIClient

# Regex that matches trailing dynamic ID suffixes: ULID (26 base32 chars), UUID, or long hex (>=16).
# Supports both dash-separated testid values (product-01KKF...) and slash-separated URL segments
# (/product/01KKF...). The separator character is included in the match start so that the prefix
# (e.g. "product-" or "/product/") is preserved when stripping.
_DYNAMIC_ID_RE = re.compile(
    r"[-/]([0-9A-Za-z]{26}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9a-fA-F]{16,})$"
)


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
    ):
        self._db           = db
        self._ai           = ai_client
        self._max_locators = max_locators
        self._max_cases    = max_cases
        self._state_graph  = state_graph

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

        if self._max_cases:
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
            raw = self._ai.complete(prompt, system_prompt=_GENERATOR_SYSTEM, max_tokens=4096, temperature=0)
        except Exception as e:
            raise PlanningError(f"AI call failed: {e}")

        return self._parse_plans(raw, locator_map, base_url=urls[0] if urls else "")

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
            fixed.append(a)

        return {**plan, "assertions": fixed}

    def _parse_plans(self, text: str, locator_map: dict, base_url: str = "") -> List[dict]:
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

                # Fix URL assertions using nav-graph URL tracking
                if self._state_graph:
                    plan_data = self._fix_url_assertions(plan_data)

                plan_data["_meta"] = {
                    "source":      "prd_generator",
                    "planned_at":  datetime.now(timezone.utc).isoformat(),
                    "locators":    len(locator_map),
                    "ai_model":    self._ai.model,
                }
                parsed_plans.append(plan_data)
            except Exception as e:
                # Even if one plan fails validation, we should try the rest
                parsed_plans.append({"test_id": test_id, "_planning_error": str(e)})

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
