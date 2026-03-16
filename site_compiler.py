"""
site_compiler.py — QAPal Site Model Compiler
=============================================
Transforms the raw locator DB into a compact compiled_model.json.

The compiled model replaces the ~27K token raw locator dump with a
~400–600 token structured summary of named workflow actions and selectors.

Usage:
    # Compile (once, after crawl):
    compiler = SiteCompiler(db)
    compiler.compile(output_path="compiled_model.json")

    # Load in planning:
    model = SiteCompiler.load("compiled_model.json")
    if model and not model.is_stale():
        prompt_section = model.format_for_prompt()
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from locator_db import LocatorDB, _url_to_pattern
from action_miner import ActionMiner, Action, _best_selector


# ── Selector → string ────────────────────────────────────────────────────────

def _sel_str(sel: dict) -> str:
    """Compact human-readable selector for the AI prompt."""
    strategy = sel.get("strategy", "?")
    value    = sel.get("value", "")
    if strategy == "testid":
        return f"testid:{value}"
    if strategy == "testid_prefix":
        return f"testid_prefix:{value}[{sel.get('index', 0)}]"
    if strategy == "role":
        if isinstance(value, dict):
            return f"role:{value.get('role','?')}/{value.get('name','')}"
        return f"role:{value}"
    if strategy == "aria_label":
        return f"aria_label:{value}"
    if strategy == "placeholder":
        return f"placeholder:{value}"
    if strategy == "text":
        return f"text:{value}"
    if strategy == "label":
        return f"label:{value}"
    return f"{strategy}:{value}"


# ── Grouping helpers ─────────────────────────────────────────────────────────

def _group_by_page(locators: list) -> dict:
    groups: dict = defaultdict(list)
    for loc in locators:
        url = loc.get("url", "")
        if not url or "/admin" in url:
            continue
        if not (loc.get("locators") or {}).get("actionable", True):
            continue
        groups[url].append(loc)
    return dict(groups)


def _pick_representatives(groups: dict) -> dict:
    """For each URL pattern (e.g. /product/:id), keep the most-locator-rich URL."""
    by_pattern: dict = defaultdict(list)
    for url in groups:
        by_pattern[_url_to_pattern(url)].append(url)
    result = {}
    for pat, urls in by_pattern.items():
        best = max(urls, key=lambda u: len(groups[u]))
        result[best] = groups[best]
    return result


# ── Compiled model ────────────────────────────────────────────────────────────

class CompiledModel:
    """Loaded compiled model. Call format_for_prompt() to include in AI prompt."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def compiled_at(self) -> str:
        return self._data.get("compiled_at", "")

    @property
    def locator_count(self) -> int:
        return self._data.get("locator_count", 0)

    def is_stale(self, max_age_minutes: int = 60) -> bool:
        ts_str = self._data.get("compiled_at", "")
        if not ts_str:
            return True
        try:
            ts  = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            return age > max_age_minutes
        except Exception:
            return True

    def format_for_prompt(self, token_budget: int = 600) -> str:
        """
        Render a compact text block suitable for inclusion in the AI planning prompt.
        token_budget: rough cap (1 token ≈ 4 chars).
        """
        lines = [
            "## Compiled Site Model",
            "USE THESE EXACT SELECTORS — do not invent others.\n",
        ]

        # Global nav
        nav = self._data.get("global_nav", [])
        if nav:
            lines.append("### Navigation (global)")
            for item in nav[:20]:
                lines.append(f"  {item['name']:<22} → click  {item['selector']}")
            lines.append("")

        # Per-page actions
        for page in self._data.get("pages", []):
            url     = page.get("url", "")
            label   = page.get("label", url)
            actions = page.get("actions", [])
            if not actions:
                continue
            lines.append(f"### {label}")
            lines.append(f"  URL: {url}")
            for act in actions:
                params_str = ", ".join(p["name"] for p in act.get("params", []))
                lines.append(f"  {act['name']}({params_str})")
                for p in act.get("params", []):
                    lines.append(f"    {p['name']:<18} → {p['action']:<7} {p['selector']}")
            lines.append("")

        result = "\n".join(lines)
        max_chars = token_budget * 4
        if len(result) > max_chars:
            result = result[:max_chars] + "\n  [...truncated — use selectors shown above...]"
        return result


# ── Compiler ─────────────────────────────────────────────────────────────────

class SiteCompiler:
    """
    Compiles the locator DB into a compact compiled_model.json.

    Typical usage (after a crawl):
        compiler = SiteCompiler(db)
        model    = compiler.compile(output_path="compiled_model.json")
        print(model.format_for_prompt())
    """

    def __init__(self, db: LocatorDB, state_graph=None):
        self._db    = db
        self._sg    = state_graph
        self._miner = ActionMiner()

    # ── Public API ───────────────────────────────────────────────────────

    def compile(self, output_path: str = "compiled_model.json") -> CompiledModel:
        all_locs = self._db.get_all_locators(valid_only=True)

        groups = _group_by_page(all_locs)
        groups = _pick_representatives(groups)

        global_nav = self._extract_global_nav(all_locs)

        pages = []
        for url, locs in sorted(groups.items()):
            actions    = self._miner.mine(url, locs)
            page_actions = [a for a in actions if a.name != "navigate"]
            if not page_actions:
                continue
            pages.append({
                "url":     url,
                "label":   self._url_label(url),
                "actions": [self._action_to_dict(a) for a in page_actions],
            })

        model_data = {
            "compiled_at":   datetime.now(timezone.utc).isoformat(),
            "locator_count": len(all_locs),
            "global_nav":    global_nav,
            "pages":         pages,
        }

        path = Path(output_path)
        path.write_text(json.dumps(model_data, indent=2), encoding="utf-8")
        return CompiledModel(model_data)

    @staticmethod
    def load(path: str = "compiled_model.json") -> Optional[CompiledModel]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return CompiledModel(data)
        except Exception:
            return None

    # ── Private helpers ──────────────────────────────────────────────────

    def _extract_global_nav(self, locators: list) -> list:
        nav_locs = [
            l for l in locators
            if ((l.get("identity") or {}).get("container") or "").lower() == "nav"
            and (l.get("identity") or {}).get("role") == "link"
        ]
        seen: set = set()
        nav = []
        for l in nav_locs:
            name = (l.get("identity") or {}).get("name", "") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            nav.append({
                "name":     name,
                "selector": _sel_str(_best_selector(l)),
            })
        return nav

    def _url_label(self, url: str) -> str:
        from urllib.parse import urlparse
        path  = urlparse(url).path.rstrip("/") or "/"
        parts = [p for p in path.split("/") if p]
        if not parts:
            return "home_page"
        return "_".join(parts[-2:]).replace("-", "_")

    def _action_to_dict(self, action: Action) -> dict:
        return {
            "name":   action.name,
            "params": [
                {
                    "name":     p.name,
                    "action":   p.action,
                    "selector": _sel_str(p.selector),
                }
                for p in action.params
            ],
        }
