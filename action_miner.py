"""
action_miner.py — QAPal UI Action Miner
========================================
Clusters raw locators from the DB into named workflow actions.

Pure heuristics — no AI, no I/O.  Takes a list of locator dicts (as returned
by LocatorDB.get_all_locators()) and produces a compact list of Actions, each
with named parameters mapped to exact selectors.

Usage:
    miner  = ActionMiner()
    result = miner.mine(url, locators_for_url)
    # result: list[Action]
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ActionParam:
    name:     str    # e.g. "email", "password", "query"
    action:   str    # "fill", "click", "select", "check"
    selector: dict   # exact QAPAL selector dict


@dataclass
class Action:
    name:       str   # e.g. "login", "search", "add_to_cart"
    params:     list  # list[ActionParam]
    source_url: str = ""


# ── Role / testid patterns ───────────────────────────────────────────────────

_FILL_ROLES     = frozenset({"textbox", "searchbox"})
_SELECT_ROLES   = frozenset({"combobox"})
_CHECKBOX_ROLES = frozenset({"checkbox", "radio"})
_CLICK_ROLES    = frozenset({"button", "link"})

_SUBMIT_TESTIDS = frozenset({
    "login-submit", "login-btn", "login-button", "signin-btn", "signin-submit",
    "submit", "form-submit", "btn-submit",
})

_CART_RE     = re.compile(r"add.?to.?cart|cart.?add|btn.?cart|add.?cart",         re.I)
_CHECKOUT_RE = re.compile(r"proceed.?to.?checkout|checkout.?btn|to.?checkout",     re.I)
_SEARCH_RE   = re.compile(r"search.?submit|search.?btn|btn.?search|search.?go",    re.I)
_REGISTER_RE = re.compile(r"register.?submit|register.?btn|create.?account|signup", re.I)

# ULID / numeric suffix in testids → indicates a list-item prefix
_LIST_ITEM_RE = re.compile(r"^([a-z][a-z0-9_\-]+?)-([0-9A-Z]{4,}|[0-9]+)$")


# ── Selector helpers ─────────────────────────────────────────────────────────

def _best_selector(loc: dict) -> dict:
    """Return the most stable QAPAL selector dict for a locator record."""
    locs = loc.get("locators") or {}
    tid  = locs.get("test_id")
    if tid:
        return {"strategy": "testid", "value": tid}
    role_raw = locs.get("role")
    if role_raw:
        role = role_raw.get("role", "") if isinstance(role_raw, dict) else str(role_raw)
        name = (loc.get("identity") or {}).get("name", "")
        if role and name:
            return {"strategy": "role", "value": {"role": role, "name": name}}
        elif role:
            return {"strategy": "role", "value": {"role": role}}
    al = locs.get("aria_label")
    if al:
        return {"strategy": "aria_label", "value": al}
    ph = locs.get("placeholder")
    if ph:
        return {"strategy": "placeholder", "value": ph}
    name = (loc.get("identity") or {}).get("name", "")
    if name:
        return {"strategy": "text", "value": name}
    return {"strategy": "css", "value": "unknown"}


def _param_name(loc: dict) -> str:
    """Derive a short snake_case parameter name from a locator."""
    tid  = ((loc.get("locators") or {}).get("test_id") or "").lower()
    name = ((loc.get("identity") or {}).get("name")    or "").lower()
    raw  = tid or name
    raw  = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    raw  = re.sub(r"_?(input|field|box|textbox|txt|address)$", "", raw)
    return raw or "value"


def _input_action(loc: dict) -> str:
    """Map a locator's role to the appropriate QAPAL action verb."""
    locs = loc.get("locators") or {}
    role_raw = locs.get("role")
    role = (role_raw.get("role", "") if isinstance(role_raw, dict) else str(role_raw or "")).lower()
    ident_role = ((loc.get("identity") or {}).get("role") or "").lower()
    r = role or ident_role
    if r in _SELECT_ROLES:
        return "select"
    if r in _CHECKBOX_ROLES:
        return "check"
    return "fill"


# ── Miner ────────────────────────────────────────────────────────────────────

class ActionMiner:
    """
    Clusters locators on a single page URL into named workflow Actions.

    All heuristics, no I/O.  Call mine(url, locators) → list[Action].
    """

    def mine(self, url: str, locators: list) -> list[Action]:
        actions: list[Action] = []

        # Partition by role
        inputs  = [l for l in locators if self._is_input(l)]
        buttons = [l for l in locators if (l.get("identity") or {}).get("role") == "button"]
        links   = [l for l in locators if (l.get("identity") or {}).get("role") == "link"]

        actions += self._mine_forms(url, inputs, buttons)
        used_names = {a.name for a in actions}
        actions += self._mine_standalone_buttons(url, buttons, used_names)
        actions += self._mine_nav(url, links)
        actions += self._mine_lists(url, locators)

        return actions

    # ── Input detection ──────────────────────────────────────────────────

    def _is_input(self, loc: dict) -> bool:
        r = ((loc.get("identity") or {}).get("role") or "").lower()
        return r in _FILL_ROLES | _SELECT_ROLES | _CHECKBOX_ROLES

    # ── Form detection ───────────────────────────────────────────────────

    def _mine_forms(self, url: str, inputs: list, buttons: list) -> list[Action]:
        from collections import defaultdict
        by_container: dict = defaultdict(list)
        for loc in inputs:
            c = ((loc.get("identity") or {}).get("container") or "unknown").lower()
            by_container[c].append(loc)

        actions   = []
        seen_containers: set = set()

        for container, fields in by_container.items():
            if container in seen_containers:
                continue
            action_name = self._infer_form_name(container, fields, url)
            if not action_name:
                continue
            seen_containers.add(container)

            # Submit button: same container, or testid in submit set
            form_buttons = [
                b for b in buttons
                if ((b.get("identity") or {}).get("container") or "").lower() == container
                or (b.get("locators") or {}).get("test_id", "") in _SUBMIT_TESTIDS
            ]

            params: list[ActionParam] = []
            for f in fields:
                params.append(ActionParam(
                    name=_param_name(f),
                    action=_input_action(f),
                    selector=_best_selector(f),
                ))
            for b in form_buttons[:1]:
                params.append(ActionParam(name="submit", action="click", selector=_best_selector(b)))

            if params:
                actions.append(Action(name=action_name, params=params, source_url=url))

        return actions

    def _infer_form_name(self, container: str, fields: list, url: str) -> str:
        c    = container.lower()
        u    = url.lower()
        tids = " ".join((f.get("locators") or {}).get("test_id", "") or "" for f in fields).lower()
        names = " ".join((f.get("identity") or {}).get("name", "") or "" for f in fields).lower()
        ctx  = f"{c} {tids} {names} {u}"

        if any(k in ctx for k in ("login", "signin", "sign-in")):
            return "login"
        if any(k in ctx for k in ("register", "signup", "sign-up", "create-account")):
            return "register"
        if "search" in ctx:
            return "search"
        if any(k in ctx for k in ("checkout", "payment", "billing", "address")):
            return "checkout"
        if any(k in ctx for k in ("contact", "message", "enquiry")):
            return "contact"
        if any(k in ctx for k in ("review", "rating")):
            return "submit_review"
        # Generic: only emit if >= 2 inputs
        if len(fields) >= 2:
            slug = re.sub(r"[^a-z0-9]+", "_", c).strip("_")
            return f"fill_{slug}" if slug and slug != "unknown" else "submit_form"
        return ""

    # ── Standalone buttons ───────────────────────────────────────────────

    def _mine_standalone_buttons(self, url: str, buttons: list, used: set) -> list[Action]:
        actions = []
        for b in buttons:
            tid  = (b.get("locators") or {}).get("test_id", "") or ""
            name = (b.get("identity") or {}).get("name", "") or ""

            action_name: Optional[str] = None
            if _CART_RE.search(tid) or "add to cart" in name.lower():
                action_name = "add_to_cart"
            elif _CHECKOUT_RE.search(tid) or "proceed to checkout" in name.lower():
                action_name = "proceed_to_checkout"
            elif _REGISTER_RE.search(tid):
                action_name = "register"
            elif _SEARCH_RE.search(tid):
                action_name = "search"

            if action_name and action_name not in used:
                used.add(action_name)
                actions.append(Action(
                    name=action_name,
                    params=[ActionParam(name="button", action="click", selector=_best_selector(b))],
                    source_url=url,
                ))
        return actions

    # ── Navigation links ─────────────────────────────────────────────────

    def _mine_nav(self, url: str, links: list) -> list[Action]:
        nav_links = [
            l for l in links
            if ((l.get("identity") or {}).get("container") or "").lower() == "nav"
        ]
        if not nav_links:
            return []
        params: list[ActionParam] = []
        seen_names: set = set()
        for l in nav_links:
            name = (l.get("identity") or {}).get("name", "") or ""
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if not slug or slug in seen_names:
                continue
            seen_names.add(slug)
            params.append(ActionParam(name=slug, action="click", selector=_best_selector(l)))
        if not params:
            return []
        return [Action(name="navigate", params=params, source_url=url)]

    # ── List / card items ────────────────────────────────────────────────

    def _mine_lists(self, url: str, locators: list) -> list[Action]:
        prefix_counts: Counter = Counter()
        for l in locators:
            tid = (l.get("locators") or {}).get("test_id", "") or ""
            m = _LIST_ITEM_RE.match(tid)
            if m:
                prefix_counts[m.group(1) + "-"] += 1

        actions = []
        for prefix, count in prefix_counts.items():
            if count < 2:
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", prefix.rstrip("-")).strip("_")
            actions.append(Action(
                name=f"select_{slug}",
                params=[ActionParam(
                    name="index",
                    action="click",
                    selector={"strategy": "testid_prefix", "value": prefix, "index": 0},
                )],
                source_url=url,
            ))
        return actions
