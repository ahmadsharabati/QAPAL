"""
planner.py — QAPal Test Planner
================================
Phase 2: Query DB -> call AI once -> return frozen plan.

The planner is the ONLY place AI is involved in normal test creation.
One AI call per test case. The output is a self-contained execution plan
that the executor runs without touching the DB.

All config from environment variables (.env supported via python-dotenv).

Usage:
    from planner import Planner
    from ai_client import AIClient

    client  = AIClient.from_env()
    planner = Planner(db, client)
    plan    = planner.create_plan(test_case)
    executor.run(plan)
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from locator_db import LocatorDB, _normalize_url, DYNAMIC_ID_RE as _DYNAMIC_ID_RE
from ai_client import AIClient

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Prompts ───────────────────────────────────────────────────────────

_SYSTEM = """You are a test planning assistant for QAPal, a deterministic UI test automation system.

Your job: map a human-written test case to exact UI element selectors from a pre-crawled database.

RULES:
1. ONLY use selectors from the "Available Locators" section below.
2. Never invent selectors or element_ids.
3. Every step that targets an element needs: selector + element_id + fallback (if available).
4. Navigation steps only need an "url" field.
5. Return valid JSON only — no markdown, no explanation.

SELECTOR FORMAT:
  {"strategy": "testid",      "value": "submit-btn"}
  {"strategy": "role",        "value": {"role": "button", "name": "Submit"}}
  {"strategy": "css",         "value": "form > button.primary"}
  {"strategy": "placeholder", "value": "Email address"}
  {"strategy": "label",       "value": "Email"}

SUPPORTED ACTIONS:
  navigate, click, dblclick, fill, type, clear, press, select,
  check, uncheck, hover, focus, blur, scroll, wait, screenshot, evaluate

SUPPORTED ASSERTION TYPES:
  url_equals, url_contains, url_matches
  title_equals, title_contains
  element_exists, element_not_exists
  element_visible, element_hidden
  element_enabled, element_disabled
  element_checked, element_unchecked
  element_contains_text, element_text_equals
  element_value, element_value_equals, element_value_contains
  element_count, element_attribute, element_has_class, element_in_viewport
  javascript"""

_PLAN_PROMPT = """## Test Case
ID:   {test_id}
Name: {test_name}

### Steps (human intent)
{steps}

### Assertions (human intent)
{assertions}

## Semantic Context (page structure)
{semantic_contexts}

## Navigation Graph
{navigation_graph}

## Available Locators
{locators}

## Output Format
STEP 1: Briefly explain your reasoning. For each human step, identify the exact [element_id] from the Available Locators list above.
STEP 2: Output the execution plan inside a ```json ... ``` block.

Example Output:
Reasoning:
- Navigate to login page.
- Email input is [abc123] — role=textbox name="Email".
- Sign In button is [def456] — role=button name="Sign In".

```json
{{
  "test_id": "{test_id}",
  "steps": [
    {{"action": "navigate", "url": "https://..."}},
    {{
      "action": "fill",
      "selector":  {{"strategy": "role", "value": {{"role": "textbox", "name": "Email"}}}},
      "fallback":  {{"strategy": "testid", "value": "email-input"}},
      "element_id": "abc123",
      "value": "user@example.com"
    }},
    {{
      "action": "click",
      "selector":  {{"strategy": "role", "value": {{"role": "button", "name": "Sign In"}}}},
      "element_id": "def456"
    }}
  ],
  "assertions": [
    {{"type": "url_contains", "value": "/dashboard"}},
    {{
      "type": "element_visible",
      "selector":  {{"strategy": "role", "value": {{"role": "button", "name": "Log Out"}}}},
      "element_id": "ghi789"
    }}
  ]
}}
```"""


# ── Locator formatter ─────────────────────────────────────────────────

def _prune_list_items(locators: List[dict]) -> List[dict]:
    """Collapse repeated list-item locators that share the same testid prefix.

    When the same product grid / search result / card pattern repeats N times
    (e.g. data-testid='product-01KKEXF...', 'product-01KKF0W...', ...),
    the AI does not need to see every ULID.  Showing all of them wastes tokens
    and tempts the AI to hard-code a specific stale ID.

    Instead, keep ONE representative and replace the rest with a synthetic
    sentinel that tells the AI to use ``testid_prefix`` strategy.
    """
    from collections import defaultdict
    prefix_groups: dict = defaultdict(list)
    non_list: List[dict] = []

    for loc in locators:
        chain = loc.get("locators", {}).get("chain", [])
        if not chain:
            non_list.append(loc)
            continue
        primary_val = str(chain[0].get("value", ""))
        m = _DYNAMIC_ID_RE.search(primary_val)
        if m:
            prefix = primary_val[: m.start() + 1]  # e.g. "product-"
            prefix_groups[prefix].append(loc)
        else:
            non_list.append(loc)

    result = list(non_list)
    for prefix, group in prefix_groups.items():
        # Keep the highest-confidence representative
        rep = max(group, key=lambda l: (
            l.get("locators", {}).get("confidence") == "high",
            l.get("history", {}).get("hit_count", 0),
        ))
        if len(group) > 1:
            # Inject a synthetic hint as a plain-string sentinel (not a real DB record)
            hint = {
                "_hint": True,
                "id": rep.get("id", ""),
                "url": rep.get("url", ""),
                "identity": rep.get("identity", {}),
                "locators": {
                    "chain": [{"strategy": "testid_prefix", "value": prefix}],
                    "confidence": "high",
                    "actionable": True,
                },
                "history": {"hit_count": len(group)},
                "_list_count": len(group),
                "_prefix": prefix,
            }
            result.append(hint)
        else:
            result.append(rep)

    return result


# UI widget testids that should never appear in test plans — they are persistent
# floating elements (chat, cookie banners, etc.) that the AI tends to hallucinate clicks on.
_WIDGET_TESTIDS = frozenset({
    "chat-toggle", "chat-widget", "cookie-banner", "cookie-accept",
    "cookie-consent", "cookie-close", "gdpr-accept", "gdpr-close",
    "intercom-frame", "crisp-chatbox", "zendesk-widget",
})


def _format_locators(locators: List[dict], max_items: int = 100, group_by_url: bool = False) -> str:
    if not locators:
        return "(none — run crawler first)"

    # Strip persistent UI widgets that pollute plans with irrelevant clicks
    locators = [
        loc for loc in locators
        if loc.get("locators", {}).get("test_id") not in _WIDGET_TESTIDS
    ]

    # Prune repeated list-item entries (same testid prefix with different ULIDs)
    locators = _prune_list_items(locators)

    # Sort: high confidence first, then by hit count
    sorted_locs = sorted(
        locators,
        key=lambda x: (
            0 if x.get("locators", {}).get("confidence") == "high" else 1,
            -x.get("history", {}).get("hit_count", 0),
        ),
    )

    def _fmt_one(loc: dict) -> str:
        # Special rendering for list-item group sentinels created by _prune_list_items
        if loc.get("_hint"):
            prefix = loc.get("_prefix", "")
            count  = loc.get("_list_count", 0)
            ident  = loc.get("identity", {})
            role   = ident.get("role", "")
            # Compact single-line hint to minimise token usage
            return (
                f'[LIST x{count}] {role}: testid_prefix("{prefix}")'
                f'  → {{"strategy":"testid_prefix","value":"{prefix}","index":0}}'
            )

        ident  = loc.get("identity", {})
        ldata  = loc.get("locators", {})
        chain  = ldata.get("chain", [])
        eid    = loc.get("id", "")
        role   = ident.get("role", "")
        name   = ident.get("name", "")
        cont   = ident.get("container", "")
        conf   = ldata.get("confidence", "low")
        hits   = loc.get("history", {}).get("hit_count", 0)

        label = f'[{eid}] {role}: "{name}"' if name else f"[{eid}] {role}"
        if cont:
            label += f" (in {cont})"
        label += f"  [{conf}, hits={hits}]"

        if chain:
            primary = chain[0]
            strat   = primary.get("strategy", "")
            val     = primary.get("value", "")
            label  += f"\n    primary:  {strat}({val})"
            if len(chain) > 1:
                fb    = chain[1]
                label += f"\n    fallback: {fb.get('strategy')}({fb.get('value')})"

        if not ldata.get("actionable", True):
            label += "\n    [NOT ACTIONABLE]"
        return label

    if group_by_url:
        # Group by URL, capping per-URL to ensure all pages get representation.
        # Form elements are ALWAYS included (never truncated) since they are the most
        # important for test generation.  Non-form elements share the remaining budget.
        from collections import OrderedDict
        url_groups: dict = OrderedDict()
        for loc in sorted_locs:
            url = loc.get("url", "unknown")
            url_groups.setdefault(url, []).append(loc)

        # Count guaranteed (form) elements across all pages
        guaranteed_total = sum(
            sum(1 for l in locs if l.get("identity", {}).get("container") == "form")
            for locs in url_groups.values()
        )
        remaining_budget = max(0, max_items - guaranteed_total)
        non_form_per_url = max(3, remaining_budget // max(len(url_groups), 1))

        sections = []
        total = 0
        for url, locs in url_groups.items():
            form_locs     = [l for l in locs if l.get("identity", {}).get("container") == "form"]
            non_form_locs = [l for l in locs if l.get("identity", {}).get("container") != "form"]
            page_locs     = form_locs + non_form_locs[:non_form_per_url]
            if not page_locs:
                continue
            section_lines = [f"### {url}"]
            section_lines.extend(_fmt_one(l) for l in page_locs)
            sections.append("\n\n".join(section_lines))
            total += len(page_locs)
        return "\n\n---\n\n".join(sections)

    lines = [_fmt_one(loc) for loc in sorted_locs[:max_items]]
    return "\n\n".join(lines)


# ── Semantic context formatter ─────────────────────────────────────────

def _format_semantic_contexts(states: List[dict]) -> str:
    """Format semantic context dicts for injection into AI prompts."""
    if not states:
        return "(none — run: python main.py semantic --urls ...)"
    lines = []
    for state in states:
        ctx  = state.get("semantic_context") or {}
        url  = state.get("url", "")
        page = ctx.get("page", "")
        desc = ctx.get("description", "")
        line = f"[{url}]"
        if page: line += f" {page}"
        if desc: line += f": {desc}"
        lines.append(line)

        h   = ctx.get("headings",         [])
        b   = ctx.get("buttons",          [])
        lk  = ctx.get("links",            [])
        t   = ctx.get("tables",           [])
        f   = ctx.get("forms",            [])
        inp = ctx.get("inputs",           [])
        err = ctx.get("error_containers", [])

        if h:   lines.append(f"  Headings:         {', '.join(h[:5])}")
        if b:   lines.append(f"  Buttons:          {', '.join(b[:10])}")
        if lk:  lines.append(f"  Links:            {', '.join(lk[:10])}")
        if t:   lines.append(f"  Tables:           {', '.join(t[:5])}")
        if f:   lines.append(f"  Forms:            {', '.join(f[:5])}")
        if inp:
            # Compact format: "Email (email, testid=email-field) [required]"
            parts = []
            for i in inp[:12]:
                label   = i.get("label") or i.get("placeholder") or i.get("type", "input")
                details = i["type"]
                if i.get("testid"):
                    details += f", testid={i['testid']}"
                s = f"{label} ({details})"
                if i.get("required"):
                    s += " [required]"
                parts.append(s)
            lines.append(f"  Form inputs:      {' | '.join(parts)}")
        if err: lines.append(f"  Error containers: {', '.join(err[:8])}")
    return "\n".join(lines)


# ── Step / assertion formatting ───────────────────────────────────────

def _format_steps(steps: List[dict]) -> str:
    if not steps:
        return "(none)"
    lines = []
    for i, s in enumerate(steps, 1):
        action = s.get("action", "?")
        target = s.get("target", {})
        role   = target.get("role", "")
        name   = target.get("name", "")

        if action == "navigate":
            lines.append(f"{i}. navigate → {s.get('url', '?')}")
        elif action == "fill":
            lines.append(f"{i}. fill [{role} '{name}'] with: {s.get('value', '')}")
        elif action == "click":
            lines.append(f"{i}. click [{role} '{name}']")
        elif action == "press":
            lines.append(f"{i}. press {s.get('key', '?')} on [{role} '{name}']")
        elif action == "select":
            choice = s.get("label") or s.get("value") or s.get("index")
            lines.append(f"{i}. select '{choice}' from [{role} '{name}']")
        elif action == "wait":
            d = s.get("duration") or s.get("for_url_contains") or s.get("for_url")
            lines.append(f"{i}. wait: {d}")
        else:
            lines.append(f"{i}. {action} [{role} '{name}']")
    return "\n".join(lines)


def _format_assertions(assertions: List[dict]) -> str:
    if not assertions:
        return "(generate from test intent)"
    lines = []
    for a in assertions:
        atype  = a.get("type", "?")
        value  = a.get("value", "")
        target = a.get("target", {})
        role   = target.get("role", "")
        name   = target.get("name", "")
        tstr   = f" [{role} '{name}']" if (role or name) else ""
        lines.append(f"- {atype}{tstr}: {value}".rstrip(": "))
    return "\n".join(lines)


# ── Response parser ───────────────────────────────────────────────────

def _parse_plan(text: str, test_id: str, locator_map: dict) -> dict:
    text = text.strip()
    # Extract JSON from markdown fences — handles both ```json and plain ```
    import re as _re
    json_match = _re.search(r'```json\s*(.*?)\s*```', text, _re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()
    elif "```" in text:
        parts = text.split("```")
        for part in parts[1:]:
            candidate = part.lstrip("json").strip()
            if candidate.startswith("{"):
                text = candidate
                break

    try:
        plan = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI returned invalid JSON: {e}\nPreview: {text[:300]}")

    plan.setdefault("test_id", test_id)
    plan.setdefault("steps", [])
    plan.setdefault("assertions", [])

    # Validate element_ids — flag invented ones
    for item in plan["steps"] + plan["assertions"]:
        action = item.get("action")
        atype = item.get("type")
        
        needs_target = False
        if action and action not in ("navigate", "refresh", "go_back", "go_forward", "wait", "screenshot", "evaluate"):
            needs_target = True
        elif atype and atype not in ("url_equals", "url_contains", "url_matches", "title_equals", "title_contains", "javascript"):
            needs_target = True
        elif "selector" in item:
            needs_target = True

        eid = item.get("element_id")
        
        if needs_target and not eid:
            item["_invalid_element_id"] = True
            item["_needs_review"]       = True
        elif eid and eid not in locator_map:
            item["_invalid_element_id"] = True
            item["_needs_review"]       = True

    return plan


# ── Planner ───────────────────────────────────────────────────────────

class PlanningError(Exception):
    pass


class Planner:
    """
    Generates execution plans by querying the DB and calling AI once.

    Usage:
        planner = Planner(db, ai_client)
        plan    = planner.create_plan(test_case)
    """

    def __init__(
        self,
        db:           LocatorDB,
        ai_client:    Optional[AIClient] = None,
        max_locators: int                = 100,
        state_graph                      = None,
    ):
        self._db           = db
        self._ai           = ai_client
        self._max_locators = max_locators
        self._state_graph  = state_graph
        self._cache: dict  = {}

    def create_plan(
        self,
        test_case:  dict,
        cache_key:  Optional[str] = None,
        force:      bool          = False,
    ) -> dict:
        """
        Create an execution plan from a test case.
        Raises PlanningError if planning fails.
        """
        if cache_key and not force and cache_key in self._cache:
            return self._cache[cache_key]

        if not self._ai:
            raise PlanningError(
                "No AI client configured. "
                "Call Planner(db, AIClient.from_env()) or set QAPAL_AI_PROVIDER."
            )

        tc_id   = test_case.get("id", "unknown")
        tc_name = test_case.get("name", tc_id)

        # Gather all URLs referenced in the test
        urls = set()
        if test_case.get("url"):
            urls.add(_normalize_url(test_case["url"]))
        for step in test_case.get("steps", []):
            if step.get("action") == "navigate" and step.get("url"):
                urls.add(_normalize_url(step["url"]))

        # Load locators for those URLs
        locators = []
        for url in urls:
            locators.extend(self._db.get_all(url, valid_only=True))

        if not locators:
            raise PlanningError(
                f"No locators found for test '{tc_id}'. "
                f"Run the crawler on: {list(urls)}"
            )

        locator_map = {loc["id"]: loc for loc in locators}

        # Load semantic contexts for the referenced URLs
        states = [s for s in (self._db.get_state(u) for u in urls) if s]

        nav_graph = (
            self._state_graph.format_for_prompt(urls=list(urls))
            if self._state_graph is not None
            else "(no navigation graph — run tests first to record page transitions)"
        )

        prompt = _PLAN_PROMPT.format(
            test_id           = tc_id,
            test_name         = tc_name,
            steps             = _format_steps(test_case.get("steps", [])),
            assertions        = _format_assertions(test_case.get("assertions", [])),
            semantic_contexts = _format_semantic_contexts(states),
            navigation_graph  = nav_graph,
            locators          = _format_locators(locators, self._max_locators),
        )

        max_retries = 3
        plan = None
        attempt = 0
        base_prompt = prompt

        for attempt in range(max_retries):
            try:
                raw  = self._ai.complete(prompt, system_prompt=_SYSTEM, max_tokens=4096)
                plan = _parse_plan(raw, tc_id, locator_map)

                invalid_items = [
                    item for item in plan.get("steps", []) + plan.get("assertions", [])
                    if item.get("_invalid_element_id")
                ]

                if not invalid_items:
                    break  # clean plan — no hallucinated IDs

                bad_ids = [item.get("element_id") for item in invalid_items]
                print(f"  ⚠ [attempt {attempt + 1}] hallucinated element_ids: {bad_ids} — retrying")
                prompt = base_prompt + (
                    f"\n\nCRITICAL ERROR: In your previous response you invented these element_ids: {bad_ids}.\n"
                    f"element_ids MUST be copied verbatim from the bracketed IDs in Available Locators (e.g. [a3f92b...]).\n"
                    f"NEVER invent sequential IDs like elem1, elem2, etc.\n"
                    f"Output the corrected JSON plan now."
                )

            except ValueError as e:
                print(f"  ⚠ [attempt {attempt + 1}] invalid JSON — retrying")
                prompt += f"\n\nCRITICAL ERROR: Your output was not valid JSON. Error: {e}\nOutput ONLY valid JSON inside a ```json ... ``` block."
            except Exception as e:
                raise PlanningError(f"AI call failed: {e}")

        if plan is None:
            raise PlanningError(f"AI failed to return a valid plan after {max_retries} attempts.")

        plan["_meta"] = {
            "test_id":     tc_id,
            "test_name":   tc_name,
            "planned_at":  datetime.now(timezone.utc).isoformat(),
            "locators":    len(locators),
            "ai_model":    self._ai.model,
            "attempts":    attempt + 1,
        }

        if cache_key:
            self._cache[cache_key] = plan

        return plan

    def clear_cache(self):
        self._cache.clear()