"""
probe.py — QAPAL Locator Intelligence / Probe Engine
=====================================================
Core locator resolution, validation, and element probing.

Extracted from executor.py — this module contains the battle-tested
locator resolution chain (4-step fallback, OR-locator for testid variants,
AI rediscovery) decoupled from test execution logic.

Every command in the new CLI flows through this module:
  analyze → probe each selector
  fix     → probe + find alternatives
  generate→ probe_url to discover elements
  heal    → probe + retry + fix
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import (
    Browser,
    BrowserContext,
    Frame,
    Locator,
    Page,
    async_playwright,
    Error as PlaywrightError,
)

from locator_db import LocatorDB, _normalize_url
from _log import get_logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = get_logger("probe")


# ── Config ────────────────────────────────────────────────────────────

ACTION_TIMEOUT = int(os.getenv("QAPAL_ACTION_TIMEOUT", "10000"))
AI_REDISCOVERY = os.getenv("QAPAL_AI_REDISCOVERY", "true").lower() == "true"
SCREENSHOT_DIR = Path(os.getenv("QAPAL_SCREENSHOTS", "reports/screenshots"))


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    """Result of probing a single selector against a live page."""
    found: bool
    count: int = 0
    visible: bool = False
    enabled: bool = False
    in_viewport: bool = False
    confidence: float = 0.0
    strategy_used: str = ""
    selector: dict = field(default_factory=dict)
    alternatives: list = field(default_factory=list)  # List[SelectorCandidate]

    @property
    def grade(self) -> str:
        from ranker import format_grade
        return format_grade(self.confidence)


@dataclass
class ElementInfo:
    """An interactive element discovered on a page via probing."""
    role: str = ""
    name: str = ""
    tag: str = ""
    testid: str = ""
    elem_id: str = ""
    container: str = ""
    dom_path: str = ""
    aria_label: str = ""
    actionable: bool = False
    visible: bool = True
    chain: list = field(default_factory=list)       # selector chain from DB
    best_selector: dict = field(default_factory=dict)
    confidence: float = 0.0


# ── Lazy singleton for the small-model AI client ─────────────────────

_small_ai_client_cache = None


def _get_small_ai_client(fallback):
    global _small_ai_client_cache
    if _small_ai_client_cache is None:
        try:
            from ai_client import AIClient
            _small_ai_client_cache = AIClient.small_from_env()
        except Exception:
            _small_ai_client_cache = fallback
    return _small_ai_client_cache


# ── Frame resolution ─────────────────────────────────────────────────

def _resolve_frame(page: Page, frame_key: Optional[str]):
    """Return the frame context. None / 'main' returns the page itself."""
    if not frame_key or frame_key == "main":
        return page
    for frame in page.frames:
        if frame.name == frame_key or (frame.url and frame_key == _normalize_url(frame.url)):
            return frame
    return page


# ── Safe element counting ────────────────────────────────────────────

async def _safe_count(loc) -> int:
    """Call loc.count() safely — returns 0 on Playwright parse/selector errors."""
    try:
        return await loc.count()
    except Exception:
        return 0


# ── Build locator from selector dict ─────────────────────────────────

_ARIA_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio",
    "listitem", "listbox", "option", "menuitem", "tab", "tabpanel",
    "dialog", "alert", "img", "heading", "search",
}


def _build_locator(ctx, selector: dict) -> Optional[Locator]:
    """
    Build a Playwright Locator from a selector dict.
    Returns None on bad input.

    Handles 11+ strategies: testid, testid_prefix, role, label,
    placeholder, text, alt_text, aria-label, css, id, xpath.

    Testid uses OR-locator covering data-testid, data-test, data-cy, data-qa.
    """
    if not selector:
        return None
    strategy = selector.get("strategy", "")
    value = selector.get("value")

    # Normalise: AI sometimes uses a role name as the strategy.
    if strategy in _ARIA_ROLES:
        if isinstance(value, dict):
            role_name = value.get("name")
        elif value is None:
            role_name = selector.get("name") or selector.get("label")
        else:
            role_name = None
        value = {"role": strategy, "name": role_name} if role_name is not None else {"role": strategy}
        strategy = "role"

    if value is None:
        return None

    try:
        if strategy == "testid_prefix":
            if isinstance(value, dict):
                prefix = str(value.get("prefix") or value.get("value") or next(iter(value.values()), ""))
                idx = int(value.get("index", 0))
            else:
                prefix = str(value)
                idx = int(selector.get("index", 0))
            prefix_loc = (
                ctx.locator(f'[data-testid^="{prefix}"]')
                .or_(ctx.locator(f'[data-test^="{prefix}"]'))
                .or_(ctx.locator(f'[data-cy^="{prefix}"]'))
                .or_(ctx.locator(f'[data-qa^="{prefix}"]'))
            )
            return prefix_loc.nth(idx)

        if strategy == "testid":
            if isinstance(value, dict):
                value = value.get("testid") or value.get("value") or next(iter(value.values()), "")
            value = str(value)
            return (
                ctx.get_by_test_id(value)
                .or_(ctx.locator(f'[data-test="{value}"]'))
                .or_(ctx.locator(f'[data-cy="{value}"]'))
                .or_(ctx.locator(f'[data-qa="{value}"]'))
            )

        if strategy == "role":
            if isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str) and name.startswith("^"):
                    name = re.compile(name)
                return ctx.get_by_role(value["role"], name=name) if name is not None else ctx.get_by_role(value["role"])
            return ctx.get_by_role(str(value))

        if strategy == "label":
            return ctx.get_by_label(str(value))
        if strategy == "placeholder":
            return ctx.get_by_placeholder(str(value))
        if strategy == "text":
            return ctx.get_by_text(str(value))
        if strategy == "alt_text":
            return ctx.get_by_alt_text(str(value))
        if strategy == "aria-label":
            return ctx.locator(f'[aria-label="{value}"]')
        if strategy in ("css", "id", "xpath"):
            prefix = "xpath=" if strategy == "xpath" else ("#" if strategy == "id" else "")
            return ctx.locator(f"{prefix}{value}")
    except Exception:
        pass
    return None


# ── Full resolution chain ─────────────────────────────────────────────

async def resolve_locator(
    page:      Page,
    selector:  dict,
    fallback:  Optional[dict],
    db:        LocatorDB,
    page_url:  str,
    ai_client=None,
    frame_key: str = "main",
) -> Tuple[Optional[Locator], str]:
    """
    Resolve a selector dict to a Playwright Locator.
    Returns (locator, strategy_used) or (None, failure_reason).

    Resolution order:
      0. DB locator chain (if element_id is known)
      1. Primary selector from plan
      1b. Role+exact=False fallback (whitespace tolerance)
      2. Fallback selector
      2b. Testid prefix fallback (dynamic ID rotation)
      3. AI rediscovery (one call, if enabled)
      4. None — hard fail
    """
    ctx = _resolve_frame(page, frame_key)

    # 0. DB locator chain
    eid = selector.get("element_id") if selector else None
    if eid:
        db_record = db.get_by_id(eid)
        if db_record:
            chain = db_record.get("locators", {}).get("chain", [])
            for chain_sel in chain:
                cloc = _build_locator(ctx, chain_sel)
                if cloc is not None:
                    try:
                        await cloc.first.wait_for(state="attached", timeout=10000)
                    except Exception:
                        pass
                    ccount = await _safe_count(cloc)
                    if ccount >= 1:
                        return (cloc if ccount == 1 else cloc.first), f"db:{chain_sel.get('strategy')}"

    # 1. Primary
    count = 0
    loc = _build_locator(ctx, selector)
    if selector and loc is not None:
        try:
            await loc.first.wait_for(state="attached", timeout=10000)
        except Exception:
            pass
        count = await _safe_count(loc)
        if count == 1:
            element_id = selector.get("element_id")
            if element_id:
                db.mark_unique(element_id, True)
            return loc, selector.get("strategy", "primary")
        if count > 1:
            element_id = selector.get("element_id")
            if element_id:
                db.mark_unique(element_id, False)
            container = selector.get("container")
            if container:
                scoped = ctx.locator(container).locator(loc)
                if await _safe_count(scoped) == 1:
                    return scoped, f"{selector.get('strategy')}+container"
            return loc.first, f"{selector.get('strategy')} (first of {count})"

    # 1b. Role+exact=False fallback
    if selector and selector.get("strategy") == "role":
        val = selector.get("value")
        if isinstance(val, dict) and val.get("name") and count == 0:
            raw_name: str = val["name"]
            _strip_pattern = re.compile(r'\s*[A-Z]{2,}.*$')
            trimmed_name = _strip_pattern.sub("", raw_name).strip()
            candidates = [raw_name]
            if trimmed_name and trimmed_name != raw_name:
                candidates.append(trimmed_name)
            for cname in candidates:
                try:
                    fuzzy = ctx.get_by_role(val["role"], name=cname, exact=False)
                    fcount = await fuzzy.count()
                    if fcount == 1:
                        return fuzzy, "role:exact=False"
                    if fcount > 1:
                        return fuzzy.first, f"role:exact=False (first of {fcount})"
                except Exception:
                    pass

    # 2. Fallback selector
    if fallback:
        loc = _build_locator(ctx, fallback)
        if loc is not None:
            count = await _safe_count(loc)
            if count >= 1:
                return loc.first, f"fallback:{fallback.get('strategy')}"

    # 2b. Testid prefix fallback
    if selector and selector.get("strategy") == "testid":
        raw_val = selector.get("value", "")
        if isinstance(raw_val, dict):
            raw_val = raw_val.get("testid") or raw_val.get("value") or ""
        raw_val = str(raw_val)
        _id_suffix = re.compile(
            r"-([0-9A-Za-z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-fA-F]{16,})$"
        )
        m = _id_suffix.search(raw_val)
        if m:
            prefix = raw_val[: m.start() + 1]
            prefix_loc = (
                ctx.locator(f'[data-testid^="{prefix}"]')
                .or_(ctx.locator(f'[data-test^="{prefix}"]'))
                .or_(ctx.locator(f'[data-cy^="{prefix}"]'))
                .or_(ctx.locator(f'[data-qa^="{prefix}"]'))
            )
            try:
                pcount = await prefix_loc.count()
                if pcount >= 1:
                    return prefix_loc.first, f"testid:prefix={prefix}"
            except Exception:
                pass

    # 2c. Heuristic Fallback (Role/Text/Title search)
    # If explicit selectors fail, search for any element that matches the intent
    # of the step (role + name). This is faster than AI rediscovery.
    intent_role = selector.get("value", {}).get("role") if isinstance(selector.get("value"), dict) else ""
    intent_name = (selector.get("value", {}).get("name") if isinstance(selector.get("value"), dict) else str(selector.get("value", "")))

    if intent_name and intent_name != "?" and len(str(intent_name)) > 1:
        # Try finding by role + name first
        if intent_role in _ARIA_ROLES:
            try:
                heur_loc = ctx.get_by_role(intent_role, name=intent_name, exact=False)
                if await heur_loc.count() == 1:
                    return heur_loc, "heuristic:role_name"
            except Exception:
                pass

        # Fallback to pure text/label search
        try:
            heur_loc = ctx.get_by_text(str(intent_name), exact=False)
            if await heur_loc.count() == 1:
                return heur_loc, "heuristic:text"
        except Exception:
            pass

    # 3. AI rediscovery
    if AI_REDISCOVERY and ai_client is not None:
        loc, strategy = await _ai_rediscover(page, selector, db, page_url, ai_client)
        if loc is not None:
            return loc, strategy

    strategy = selector.get("strategy", "?") if selector else "?"
    value = selector.get("value", "?") if selector else "?"
    return None, f"Element not found \u2014 strategy={strategy} value={value}"


# ── AI rediscovery ────────────────────────────────────────────────────

async def _ai_rediscover(
    page:      Page,
    selector:  dict,
    db:        LocatorDB,
    page_url:  str,
    ai_client,
) -> Tuple[Optional[Locator], str]:
    """One AI call to find a lost element. Updates DB if found."""
    try:
        snapshot = await page.accessibility.snapshot() or {}
        snapshot_text = json.dumps(snapshot, indent=2)[:3000]
        value = selector.get("value", {})
        role = value.get("role", "") if isinstance(value, dict) else ""
        name = value.get("name", "") if isinstance(value, dict) else str(value)

        prompt = f"""Find a UI element on the page.
Target: role={role} name={name} strategy={selector.get('strategy')}

Accessibility snapshot (truncated):
{snapshot_text}

Return JSON only \u2014 the best Playwright locator:
{{"strategy": "role", "value": {{"role": "button", "name": "Submit"}}}}
Valid strategies: role, testid, css, label, placeholder, aria-label"""

        text = await _get_small_ai_client(ai_client).acomplete(prompt)
        text = text.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip().split("```")[0]

        new_sel = json.loads(text)
        loc = _build_locator(page, new_sel)
        if loc and await _safe_count(loc) >= 1:
            db.mark_ai_rediscovered(
                url=page_url,
                role=role,
                name=name,
                new_chain=[{"strategy": new_sel.get("strategy"),
                            "value": new_sel.get("value"), "unique": None}],
            )
            return loc.first, "ai_rediscovery"
    except Exception:
        pass
    return None, "ai_rediscovery_failed"


# ── Pre-action verification ──────────────────────────────────────────

async def _verify_actionable(loc: Locator, timeout: int = ACTION_TIMEOUT) -> Tuple[bool, str]:
    """Check if an element is visible, attached, and enabled."""
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.wait_for(state="attached", timeout=timeout)
        if await loc.is_disabled():
            return False, "Element is disabled"
        return True, "ok"
    except PlaywrightError as e:
        return False, str(e)


# ── Screenshot helper ────────────────────────────────────────────────

async def _screenshot(page: Page, label: str) -> str:
    """Take a diagnostic screenshot. Returns the file path."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = str(SCREENSHOT_DIR / f"{label}_{ts}.png")
    try:
        await page.screenshot(path=path, full_page=False)
    except Exception:
        pass
    return path


# ── High-level probe functions ────────────────────────────────────────

async def probe_element(
    page: Page,
    selector: dict,
    db: LocatorDB,
    page_url: str,
    ai_client=None,
    frame_key: str = "main",
) -> ProbeResult:
    """
    Probe a single selector against a live page.
    Returns a rich ProbeResult with confidence score.
    """
    from ranker import score_selector

    loc, strategy_used = await resolve_locator(
        page, selector, selector.get("fallback"), db, page_url, ai_client, frame_key,
    )

    if loc is None:
        return ProbeResult(
            found=False,
            strategy_used=strategy_used,
            selector=selector,
            confidence=0.0,
        )

    count = await _safe_count(loc)
    visible = False
    enabled = False
    in_viewport = False

    try:
        visible = await loc.is_visible()
    except Exception:
        pass
    try:
        enabled = await loc.is_enabled()
    except Exception:
        pass
    try:
        box = await loc.bounding_box()
        in_viewport = box is not None
    except Exception:
        pass

    strategy = selector.get("strategy", strategy_used) if selector else strategy_used
    confidence = score_selector(
        strategy=strategy,
        count=count,
        visible=visible,
        in_viewport=in_viewport,
        enabled=enabled,
    )

    return ProbeResult(
        found=True,
        count=count,
        visible=visible,
        enabled=enabled,
        in_viewport=in_viewport,
        confidence=confidence,
        strategy_used=strategy_used,
        selector=selector,
    )


async def probe_page(page: Page, url: str, db: LocatorDB) -> List[ElementInfo]:
    """
    Extract all interactive elements from a page and return enriched ElementInfo list.
    Uses crawler's A11Y_JS + DOM_FALLBACK_JS for element discovery.
    """
    from crawler import A11Y_JS, DOM_FALLBACK_JS
    from locator_db import _build_chain

    elements: List[ElementInfo] = []

    # Run accessibility tree extraction
    try:
        raw_a11y = await page.evaluate(A11Y_JS)
    except Exception:
        raw_a11y = []
    if not isinstance(raw_a11y, list):
        raw_a11y = []

    # Run DOM fallback for non-semantic elements
    try:
        raw_dom = await page.evaluate(DOM_FALLBACK_JS)
    except Exception:
        raw_dom = []
    if not isinstance(raw_dom, list):
        raw_dom = []

    # Merge: a11y is primary, DOM fills gaps
    seen_ids = set()
    all_raw = raw_a11y + raw_dom

    for elem in all_raw:
        if not isinstance(elem, dict):
            continue
        # Dedup by testid or role+name
        dedup_key = elem.get("testid") or f"{elem.get('role', '')}:{elem.get('name', '')}"
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        if not elem.get("actionable", False):
            continue

        # Build selector chain — _build_chain expects (element_dict, container_str)
        chain = _build_chain(elem, elem.get("container", ""))

        best = chain[0] if chain else {}

        info = ElementInfo(
            role=elem.get("role", ""),
            name=elem.get("name", ""),
            tag=elem.get("tag", ""),
            testid=elem.get("testid", ""),
            elem_id=elem.get("elemId", ""),
            container=elem.get("container", ""),
            dom_path=elem.get("domPath", ""),
            aria_label=elem.get("ariaLabel", ""),
            actionable=True,
            visible=elem.get("isVisible", True),
            chain=chain,
            best_selector=best,
            confidence=0.0,
        )
        elements.append(info)

    # Live-validate the best selector for each element
    from ranker import score_selector
    for elem_info in elements:
        if not elem_info.best_selector:
            continue
        loc = _build_locator(page, elem_info.best_selector)
        if loc is not None:
            count = await _safe_count(loc)
            visible = False
            try:
                visible = await loc.first.is_visible() if count > 0 else False
            except Exception:
                pass
            elem_info.confidence = score_selector(
                strategy=elem_info.best_selector.get("strategy", "css"),
                count=count,
                visible=visible,
                in_viewport=visible,  # approximate
                enabled=True,         # assume enabled for discovery
            )

    # Sort by confidence descending
    elements.sort(key=lambda e: e.confidence, reverse=True)
    return elements


# ── ProbeEngine class ─────────────────────────────────────────────────

class ProbeEngine:
    """
    Manages browser lifecycle for probing.
    Reuses crawler's _build_context for auth handling.

    Usage:
        async with ProbeEngine(db) as engine:
            result = await engine.probe("https://myapp.com/login", {"strategy": "testid", "value": "email"})
            elements = await engine.probe_url("https://myapp.com/login")
    """

    def __init__(
        self,
        db: LocatorDB,
        headless: Optional[bool] = None,
        credentials: Optional[dict] = None,
        ai_client=None,
        device: Optional[str] = None,
        viewport: Optional[Tuple[int, int]] = None,
    ):
        self._db = db
        self._headless = headless if headless is not None else os.getenv("QAPAL_HEADLESS", "true").lower() == "true"
        self._credentials = credentials
        self._ai_client = ai_client
        self._device_name = device or os.getenv("QAPAL_DEVICE", None)
        self._viewport = viewport
        self._device_kwargs: dict = {}

        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self):
        """Launch Playwright and browser."""
        self._pw = await async_playwright().start()

        # Resolve device preset
        if self._device_name:
            try:
                self._device_kwargs = dict(self._pw.devices[self._device_name])
            except KeyError:
                available = list(self._pw.devices.keys())[:10]
                raise ValueError(
                    f"Unknown device '{self._device_name}'. Examples: {available}"
                )
        if self._viewport:
            self._device_kwargs["viewport"] = {"width": self._viewport[0], "height": self._viewport[1]}

        self._browser = await self._pw.chromium.launch(headless=self._headless)
        return self

    async def close(self):
        """Clean up browser resources."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def _get_page(self, url: str) -> Page:
        """Get or create a page navigated to the given URL."""
        from crawler import _build_context, wait_for_stable

        if self._context is None:
            self._context = await _build_context(
                self._browser, self._db, url,
                credentials=self._credentials,
                device_kwargs=self._device_kwargs,
            )
            self._page = await self._context.new_page()

        current_url = self._page.url if self._page else ""
        if not current_url or _normalize_url(current_url) != _normalize_url(url):
            await self._page.goto(url, wait_until="domcontentloaded")
            await wait_for_stable(self._page)

        return self._page

    async def probe(self, url: str, selector: dict) -> ProbeResult:
        """Navigate to URL and probe a single selector."""
        page = await self._get_page(url)
        return await probe_element(page, selector, self._db, url, self._ai_client)

    async def probe_url(self, url: str) -> List[ElementInfo]:
        """Navigate to URL and extract all interactive elements."""
        page = await self._get_page(url)
        return await probe_page(page, url, self._db)

    async def validate_selectors(
        self, url: str, selectors: List[dict]
    ) -> List[ProbeResult]:
        """Batch-validate multiple selectors against a live page."""
        page = await self._get_page(url)
        results = []
        for sel in selectors:
            result = await probe_element(page, sel, self._db, url, self._ai_client)
            results.append(result)
        return results

    async def generate_candidates(
        self, url: str, element: ElementInfo
    ) -> list:
        """Generate and rank alternative selectors for an element."""
        from ranker import SelectorCandidate, score_selector, rank_candidates

        page = await self._get_page(url)
        candidates = []

        for chain_sel in element.chain:
            loc = _build_locator(page, chain_sel)
            if loc is None:
                continue
            count = await _safe_count(loc)
            visible = False
            try:
                visible = await loc.first.is_visible() if count > 0 else False
            except Exception:
                pass

            score = score_selector(
                strategy=chain_sel.get("strategy", "css"),
                count=count,
                visible=visible,
                in_viewport=visible,
                enabled=True,
            )

            candidates.append(SelectorCandidate(
                strategy=chain_sel.get("strategy", ""),
                value=chain_sel.get("value"),
                unique=(count == 1),
                score=score,
                expression="",  # filled by scaffold/patcher when needed
            ))

        return rank_candidates(candidates)
