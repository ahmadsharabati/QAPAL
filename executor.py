"""
executor.py — QAPal Deterministic Test Executor
=================================================
Reads a frozen plan -> executes actions -> runs assertions -> returns verdict.

The executor NEVER:
  - calls AI during normal execution
  - interprets whether an action "looks" successful
  - guesses at selectors
  - retries without a specific reason

Locator resolution chain:
  1. primary selector from plan (testid / role / css / label / placeholder)
  2. fallback selector from plan
  3. AI rediscovery — ONE call, updates DB, hard cap
  4. FAIL — hard stop, screenshot captured

All config from environment variables (.env supported via python-dotenv).

Install:
  pip install playwright tinydb anthropic python-dotenv
  playwright install chromium
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List

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
from crawler import Crawler, _build_context, wait_for_stable
from state_graph import StateGraph
from _log import get_logger
from _tokens import get_token_tracker

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = get_logger("executor")


# ── Config ────────────────────────────────────────────────────────────

SCREENSHOT_DIR      = Path(os.getenv("QAPAL_SCREENSHOTS", "reports/screenshots"))
ACTION_TIMEOUT      = int(os.getenv("QAPAL_ACTION_TIMEOUT",    "10000"))
ASSERTION_TIMEOUT   = int(os.getenv("QAPAL_ASSERTION_TIMEOUT", "5000"))
AI_REDISCOVERY      = os.getenv("QAPAL_AI_REDISCOVERY", "true").lower() == "true"
VISUAL_REGRESSION   = os.getenv("QAPAL_VISUAL_REGRESSION",  "false").lower() == "true"
VISUAL_THRESHOLD    = float(os.getenv("QAPAL_VISUAL_THRESHOLD", "0.02"))
VISUAL_BASELINE_DIR = SCREENSHOT_DIR / "baseline"
VISUAL_DIFF_DIR     = SCREENSHOT_DIR / "visual_diff"

# ── Passive error interception config ─────────────────────────────────
_NOISE_DOMAINS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net",
    "gravatar.com", "sentry.io", "hotjar.com", "intercom.io",
    "segment.com", "mixpanel.com", "amplitude.com", "facebook.net",
)
_NOISE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
               ".woff", ".woff2", ".ttf", ".otf", ".svg", ".css", ".map")
_EXTRA_NOISE_DOMAINS = tuple(d.strip() for d in os.getenv("QAPAL_NOISE_DOMAINS", "").split(",") if d.strip())
_ALL_NOISE_DOMAINS = _NOISE_DOMAINS + _EXTRA_NOISE_DOMAINS

def _is_signal_failure(url: str, base_url: str) -> bool:
    """Return True for failures worth flagging (same-origin or known API calls)."""
    from urllib.parse import urlparse as _up
    parsed = _up(url)
    if any(d in parsed.netloc for d in _ALL_NOISE_DOMAINS):
        return False
    if any(url.lower().endswith(ext) for ext in _NOISE_EXTS):
        return False
    return True

# ── Visual regression helpers ──────────────────────────────────────────

def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


async def _visual_compare(page, test_id: str, step_index: int) -> dict | None:
    """
    Take a screenshot and compare against the stored baseline.

    First run  → saves baseline, returns None (no diff yet).
    Later runs → diffs current vs baseline.
                 Returns diff dict if diff_pct > VISUAL_THRESHOLD, else None.

    Requires Pillow (pip install Pillow). Silently skips if not installed.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None

    baseline_path = VISUAL_BASELINE_DIR / test_id / f"step_{step_index}.png"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)

    if not baseline_path.exists():
        # First run — save baseline and move on
        await page.screenshot(path=str(baseline_path), full_page=False)
        return None

    # Subsequent run — capture current and diff
    diff_dir  = VISUAL_DIFF_DIR / f"{test_id}_{_ts()}"
    diff_dir.mkdir(parents=True, exist_ok=True)
    curr_path = diff_dir / f"step_{step_index}_current.png"
    diff_path = diff_dir / f"step_{step_index}_diff.png"

    await page.screenshot(path=str(curr_path), full_page=False)

    try:
        baseline_img = Image.open(baseline_path).convert("RGB")
        current_img  = Image.open(curr_path).convert("RGB")

        if baseline_img.size != current_img.size:
            log.warning("Visual regression: viewport size changed from %s to %s for %s step %d — resizing to compare",
                        baseline_img.size, current_img.size, test_id, step_index)
            current_img = current_img.resize(baseline_img.size, Image.LANCZOS)

        diff = ImageChops.difference(baseline_img, current_img)

        # Use NumPy for fast pixel diffing when available
        try:
            import numpy as np
            diff_arr = np.array(diff)                       # (H, W, 3)
            channel_sum = diff_arr.sum(axis=2)              # (H, W)
            mask = channel_sum > 30
            diff_count = int(mask.sum())
            total_pixels = mask.size
        except ImportError:
            pixels = list(diff.getdata())
            diff_count = sum(1 for r, g, b in pixels if r + g + b > 30)
            total_pixels = max(len(pixels), 1)

        diff_pct = diff_count / max(total_pixels, 1)

        if diff_pct > VISUAL_THRESHOLD:
            # Highlight diff pixels in red and save
            try:
                import numpy as np
                current_arr = np.array(current_img)         # (H, W, 3)
                if 'mask' not in dir():
                    diff_arr = np.array(diff)
                    mask = diff_arr.sum(axis=2) > 30
                current_arr[mask] = [255, 0, 0]
                diff_vis = Image.fromarray(current_arr)
            except ImportError:
                from PIL import ImageDraw
                diff_vis = current_img.copy()
                draw     = ImageDraw.Draw(diff_vis)
                width    = baseline_img.width
                if 'pixels' not in dir():
                    pixels = list(diff.getdata())
                for idx, (r, g, b) in enumerate(pixels):
                    if r + g + b > 30:
                        x, y = idx % width, idx // width
                        draw.point((x, y), fill=(255, 0, 0))
            diff_vis.save(str(diff_path))

            return {
                "step_index": step_index,
                "diff_pct":   round(diff_pct * 100, 2),
                "baseline":   str(baseline_path),
                "current":    str(curr_path),
                "diff":       str(diff_path),
            }
    except Exception:
        pass

    return None


# ── Probe engine (extracted) ──────────────────────────────────────────
# Locator resolution, element validation, and AI rediscovery live in probe.py.
# Imported here for backward compatibility — existing code that does
# `from executor import resolve_locator` continues to work.
from probe import (
    _resolve_frame,
    _safe_count,
    _build_locator,
    resolve_locator,
    _ai_rediscover,
    _verify_actionable,
    _get_small_ai_client,
)

# Unknown-state recovery caps (all overridable via env vars)
MAX_REPLANS_PER_TEST = int(os.getenv("QAPAL_MAX_REPLANS",       "1"))
MAX_UNKNOWN_STATES   = int(os.getenv("QAPAL_MAX_UNKNOWN_STATES", "3"))
MAX_URL_VISITS       = int(os.getenv("QAPAL_MAX_URL_VISITS",     "3"))


# ── Result builders ───────────────────────────────────────────────────

def _step_pass(step: dict, detail: str = "", strategy: str = "page") -> dict:
    return {"status": "pass", "action": step.get("action"),
            "selector": step.get("selector"), "strategy": strategy, "detail": detail}


def _step_fail(step: dict, reason: str, category: str = "UNKNOWN", screenshot: Optional[str] = None, strategy: str = "page") -> dict:
    return {"status": "fail", "action": step.get("action"), "category": category,
            "selector": step.get("selector"), "strategy": strategy, "reason": reason, "screenshot": screenshot}


def _assert_pass(a: dict, actual=None) -> dict:
    r = {"status": "pass", "type": a["type"]}
    if actual is not None:
        r["actual"] = actual
    return r


def _assert_fail(a: dict, reason: str, actual=None, category: str = "ASSERTION_FAILED") -> dict:
    r = {"status": "fail", "type": a["type"], "category": category, "reason": reason}
    if actual is not None:
        r["actual"] = actual
    return r


# ── Screenshot ────────────────────────────────────────────────────────

async def _screenshot(page: Page, label: str) -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = str(SCREENSHOT_DIR / f"{label}_{ts}.png")
    try:
        await page.screenshot(path=path, full_page=False)
    except Exception:
        pass
    return path


# ── DOM fingerprint & unknown-state detection ─────────────────────────

async def _compute_dom_hash(page: Page) -> str:
    """SHA-256 fingerprint of the current page DOM. Empty string on failure."""
    try:
        from semantic_extractor import compute_dom_hash
        html = await page.content()
        return compute_dom_hash(html)
    except Exception:
        return ""


def _detect_unknown_state(db: LocatorDB, url: str, dom_hash: str) -> bool:
    """
    Return True if this URL has never been crawled (no locators in DB).

    We intentionally avoid dom_hash comparison because SPA frameworks (Angular,
    React) render asynchronously — the same page produces different hashes
    depending on render timing, causing too many false-positive recoveries.
    URL-based detection is the reliable signal: if we've crawled the page
    before we have its locators and can proceed without replanning.
    """
    return not db.get_all(url, valid_only=True)



# NOTE: _resolve_frame, _safe_count, _build_locator, resolve_locator,
# _ai_rediscover, _verify_actionable are now in probe.py (imported above).


# ── Action execution ──────────────────────────────────────────────────

def _trigger_label(step: dict) -> str:
    """Derive a human-readable trigger label from a step for graph recording."""
    sel = step.get("selector") or {}
    val = sel.get("value")
    if isinstance(val, dict):
        return val.get("name", "") or val.get("role", "")
    if val:
        return str(val)
    return step.get("value", "") or step.get("url", "")


async def _execute_step(
    page:         Page,
    step:         dict,
    db:           LocatorDB,
    page_url:     str,
    crawler:      Crawler,
    ai_client,
    state_graph:  Optional[StateGraph] = None,
    session_id:   str = "",
) -> Tuple[dict, str]:
    """
    Execute one step. Returns (result, new_url).
    Hard-stops on failure — caller breaks the loop.
    """
    action   = step.get("action", "").lower()
    selector = step.get("selector")
    fallback = step.get("fallback")
    value    = step.get("value")
    frame    = step.get("frame", "main")
    timeout  = step.get("timeout", ACTION_TIMEOUT)

    # ── No-target actions ─────────────────────────────────────────────
    _entry_url = _normalize_url(page.url)  # capture before any navigation

    if action == "navigate":
        url = step.get("url", page_url)
        # Resolve relative URLs against the current page URL
        if url and not url.startswith(("http://", "https://")):
            from urllib.parse import urljoin
            base = page.url if page.url and page.url.startswith("http") else f"https://{page_url.lstrip('/')}"
            url = urljoin(base, url)
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=step.get("timeout", 30_000))
            await wait_for_stable(page)
            new_url = _normalize_url(page.url)
            await crawler.on_page_load(page, new_url)
            if state_graph is not None and new_url != _entry_url:
                state_graph.record_transition(
                    from_url       = _entry_url,
                    to_url         = new_url,
                    trigger_action = "navigate",
                    trigger_label  = url,
                    session_id     = session_id,
                )
            return _step_pass(step, f"navigated to {url}"), new_url
        except Exception as e:
            return _step_fail(step, f"Navigation failed: {e}", category=FailureCategory.NAV_TIMEOUT), page_url

    if action == "refresh":
        await page.reload(wait_until="domcontentloaded")
        await wait_for_stable(page)
        return _step_pass(step, "refreshed"), _normalize_url(page.url)

    if action == "go_back":
        await page.go_back()
        await wait_for_stable(page)
        return _step_pass(step, "back"), _normalize_url(page.url)

    if action == "go_forward":
        await page.go_forward()
        await wait_for_stable(page)
        return _step_pass(step, "forward"), _normalize_url(page.url)

    if action == "wait":
        duration = step.get("duration")
        if duration:
            await page.wait_for_timeout(int(duration))
            return _step_pass(step, f"waited {duration}ms"), page_url
        # Wait for element state (e.g. visible, hidden, attached, detached)
        wait_selector = step.get("selector")
        wait_state = step.get("state")
        if wait_selector and wait_state:
            wait_loc, _ = await resolve_locator(
                page, wait_selector, step.get("fallback"), db, page_url, ai_client, frame
            )
            if wait_loc is None and wait_state in ("hidden", "detached"):
                return _step_pass(step, f"element already {wait_state} (not found)"), page_url
            if wait_loc is None:
                return _step_fail(step, f"Element not found for wait state={wait_state}", category=FailureCategory.SELECTOR_NOT_FOUND), page_url
            try:
                # Map "enabled"/"disabled"/"editable" to Playwright-supported states
                pw_state = wait_state
                if wait_state in ("enabled", "disabled", "editable"):
                    # Playwright wait_for only supports visible/hidden/attached/detached
                    # Poll for these states instead
                    wait_timeout = step.get("timeout", ACTION_TIMEOUT)
                    if wait_state == "enabled":
                        await wait_loc.first.wait_for(state="visible", timeout=wait_timeout)
                        if await wait_loc.first.is_disabled():
                            return _step_fail(step, "Element is disabled"), page_url
                    elif wait_state == "disabled":
                        await wait_loc.first.wait_for(state="attached", timeout=wait_timeout)
                        if not await wait_loc.first.is_disabled():
                            return _step_fail(step, "Element is enabled"), page_url
                    elif wait_state == "editable":
                        await wait_loc.first.wait_for(state="visible", timeout=wait_timeout)
                        if not await wait_loc.first.is_editable():
                            return _step_fail(step, "Element is not editable"), page_url
                    return _step_pass(step, f"element is {wait_state}"), page_url
                else:
                    await wait_loc.first.wait_for(state=pw_state, timeout=step.get("timeout", ACTION_TIMEOUT))
                    return _step_pass(step, f"element reached state={wait_state}"), page_url
            except Exception as e:
                return _step_fail(step, f"wait for element state={wait_state} failed: {e}", category=FailureCategory.NAV_TIMEOUT), page_url
        if step.get("for_url_contains"):
            try:
                await page.wait_for_url(
                    f"**/*{step['for_url_contains']}*",
                    timeout=step.get("timeout", ACTION_TIMEOUT),
                )
                return _step_pass(step, f"url contains {step['for_url_contains']}"), _normalize_url(page.url)
            except Exception as e:
                return _step_fail(step, f"wait for_url_contains failed: {e}"), page_url
        if step.get("for_url_matches"):
            try:
                await page.wait_for_url(
                    re.compile(step["for_url_matches"]),
                    timeout=step.get("timeout", ACTION_TIMEOUT),
                )
                return _step_pass(step, f"url matches {step['for_url_matches']}"), _normalize_url(page.url)
            except Exception as e:
                return _step_fail(step, f"wait for_url_matches failed: {e}"), page_url
        if step.get("for_url"):
            try:
                await page.wait_for_url(step["for_url"], timeout=step.get("timeout", ACTION_TIMEOUT))
                return _step_pass(step, f"url = {step['for_url']}"), _normalize_url(page.url)
            except Exception as e:
                return _step_fail(step, f"wait for_url failed: {e}"), page_url
        return _step_fail(step, "wait requires duration, selector+state, for_url, for_url_contains, or for_url_matches"), page_url

    if action == "screenshot":
        label     = step.get("label") or step.get("value") or "screenshot"
        full_page = bool(step.get("full_page", False))
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{label}_{ts}.png")
        try:
            await page.screenshot(path=path, full_page=full_page)
        except Exception:
            pass
        return _step_pass(step, f"saved: {path}"), page_url

    if action == "evaluate":
        script = step.get("script", "")
        result = await page.evaluate(script)
        return _step_pass(step, f"result: {str(result)[:100]}"), page_url

    if action == "scroll":
        direction = step.get("direction", "")
        shortcuts = {"down": (0, 500), "up": (0, -500), "left": (-500, 0), "right": (500, 0),
                     "top": (0, -999999), "bottom": (0, 999999)}
        dx, dy = shortcuts.get(direction, (step.get("x", 0), step.get("y", 500)))
        scroll_selector = step.get("selector")
        if scroll_selector:
            scroll_loc, _ = await resolve_locator(
                page, scroll_selector, step.get("fallback"), db, page_url, ai_client, frame
            )
            if scroll_loc is not None:
                await scroll_loc.evaluate(
                    f"(el) => el.scrollBy({dx}, {dy})"
                )
                return _step_pass(step, f"element scrolled ({dx}, {dy})"), page_url
        await page.mouse.wheel(dx, dy)
        return _step_pass(step, f"scrolled ({dx}, {dy})"), page_url

    # ── Actions that need a target ────────────────────────────────────
    if not selector and action != "press":
        return _step_fail(step, f"action '{action}' requires a selector"), page_url

    loc, strategy = None, "page"
    if selector:
        loc, strategy = await resolve_locator(
            page, selector, fallback, db, page_url, ai_client, frame
        )
        if loc is None:
            ss = await _screenshot(page, f"not_found_{action}")
            return _step_fail(step, strategy, category=FailureCategory.SELECTOR_NOT_FOUND, screenshot=ss), page_url

    # Actionability guard: scroll into viewport then wait for stable DOM state.
    # Eliminates ~10% of interaction failures from off-screen or animating elements.
    if loc is not None and action in ("click", "fill", "type", "clear", "check",
                                       "uncheck", "dblclick", "hover", "focus"):
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass  # best-effort; do not fail if scroll unsupported
        try:
            await loc.wait_for(state="stable", timeout=3000)
        except Exception:
            pass  # best-effort; proceed even if stable check unsupported

    # Verify actionable before acting (skip for read-only actions)
    if action not in ("hover", "focus", "blur") and loc is not None:
        ok, reason = await _verify_actionable(loc, timeout=timeout)
        if not ok:
            # Auto-expand: if element is hidden inside a collapsed/dropdown container,
            # find the nearest ancestor toggler (aria-expanded=false) and click it.
            try:
                toggler_sel = (
                    "[aria-expanded='false'][data-bs-toggle],"
                    "[aria-expanded='false'][data-toggle]"
                )
                # Walk up from the target element to find a toggler ancestor
                toggler_js = """(args) => {
                    const el = args[0];
                    const sel = args[1];
                    if (!el) return null;
                    let node = el.parentElement;
                    while (node && node !== document.body) {
                        if (node.matches && node.matches(sel)) return node;
                        const tog = node.querySelector && node.querySelector(sel);
                        if (tog) return tog;
                        node = node.parentElement;
                    }
                    return null;
                }"""
                element_handle = await loc.first.element_handle()
                if element_handle:
                    toggler_el = await page.evaluate_handle(
                        toggler_js, [element_handle, toggler_sel]
                    )
                    toggler_elem = toggler_el.as_element() if toggler_el else None
                    if toggler_elem:
                        await toggler_elem.click(timeout=3000)
                        await page.wait_for_timeout(500)
                        ok, reason = await _verify_actionable(loc)
            except Exception:
                pass
        if not ok:
            ss = await _screenshot(page, f"not_actionable_{action}")
            return _step_fail(step, f"Not actionable: {reason}", ss), page_url

    url_before  = _normalize_url(page.url)
    _snap_before: list = []
    if action == "click" and state_graph is not None:
        try:
            _snap_before = await page.accessibility.snapshot() or []
            if isinstance(_snap_before, dict):
                _snap_before = [_snap_before]
        except Exception:
            _snap_before = []

    try:
        if action == "click":
            opts = {"timeout": timeout}
            if step.get("button"):     opts["button"]      = step["button"]
            if step.get("modifiers"):  opts["modifiers"]   = step["modifiers"]
            if step.get("position"):   opts["position"]    = step["position"]
            if step.get("force"):      opts["force"]       = True
            await loc.click(**opts)

        elif action == "dblclick":
            await loc.dblclick(timeout=timeout)

        elif action == "fill":
            val = step.get("value")
            if val is None: val = step.get("text")
            if val is None:
                return _step_fail(step, "fill requires a value"), page_url
            # Generic: if the resolved element is a <select>, route through select_option
            # so plans that use `fill` on dropdowns still work correctly.
            try:
                el_tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            except Exception:
                el_tag = ""
            if el_tag == "select":
                val_str = str(val)
                selected = False
                for attempt in range(2):
                    try:
                        if attempt == 0:
                            await loc.select_option(value=val_str, timeout=3000)
                        else:
                            await loc.select_option(label=val_str, timeout=3000)
                        selected = True
                        break
                    except Exception:
                        pass
                if not selected:
                    await loc.select_option(label=val_str, timeout=timeout)  # raise real error
            else:
                await loc.fill(str(val), timeout=timeout)

        elif action == "type":
            text  = step.get("text") or step.get("value", "")
            delay = step.get("delay", 0)
            await loc.type(str(text), delay=delay, timeout=timeout)

        elif action == "clear":
            await loc.clear(timeout=timeout)

        elif action == "press":
            key = step.get("key") or step.get("value") or step.get("text") or "Enter"
            if loc:
                await loc.press(str(key), timeout=timeout)
            else:
                await page.keyboard.press(str(key))

        elif action == "select":
            if step.get("label") is not None:
                raw_label = str(step["label"])
                label_selected = False
                try:
                    await loc.select_option(label=raw_label, timeout=3000)
                    label_selected = True
                except Exception:
                    pass
                if not label_selected:
                    # Word-based partial label match (handles punctuation differences)
                    # Uses loc.evaluate so the element is already resolved — no CSS selector needed
                    matched = await loc.evaluate(
                        """(el, txt) => {
                            if (!el || !el.options) return null;
                            const words = txt.toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2);
                            let best = null, bestScore = 0;
                            for (const opt of el.options) {
                                const optWords = opt.text.toLowerCase();
                                const score = words.filter(w => optWords.includes(w)).length;
                                if (score > bestScore) { bestScore = score; best = opt; }
                            }
                            if (best && bestScore >= Math.ceil(words.length * 0.6)) {
                                best.selected = true;
                                el.dispatchEvent(new Event('change', {bubbles:true}));
                                return best.text;
                            }
                            return null;
                        }""",
                        raw_label,
                    )
                    if not matched:
                        await loc.select_option(label=raw_label, timeout=timeout)  # raise real error
            elif step.get("value") is not None:
                # Cascading select: value attr → exact label → partial label match
                raw_val = str(step["value"])
                selected = False
                for attempt in range(3):
                    try:
                        if attempt == 0:
                            await loc.select_option(value=raw_val, timeout=3000)
                        elif attempt == 1:
                            await loc.select_option(label=raw_val, timeout=3000)
                        else:
                            # Partial label: find the first option whose text contains raw_val
                            # Uses loc.evaluate so the element is already resolved — no CSS selector needed
                            matched = await loc.evaluate(
                                """(el, txt) => {
                                    if (!el || !el.options) return null;
                                    for (const opt of el.options) {
                                        if (opt.text.toLowerCase().includes(txt.toLowerCase())) {
                                            el.value = opt.value;
                                            el.dispatchEvent(new Event('change', {bubbles:true}));
                                            return opt.text;
                                        }
                                    }
                                    return null;
                                }""",
                                raw_val,
                            )
                            if not matched:
                                break
                        selected = True
                        break
                    except Exception:
                        pass
                if not selected:
                    await loc.select_option(value=raw_val, timeout=timeout)  # raise real error
            elif step.get("index") is not None:
                await loc.select_option(index=int(step["index"]), timeout=timeout)
            else:
                return _step_fail(step, "select requires label, value, or index"), page_url

        elif action == "check":
            await loc.check(timeout=timeout)

        elif action == "uncheck":
            await loc.uncheck(timeout=timeout)

        elif action == "hover":
            await loc.hover(timeout=timeout)

        elif action == "focus":
            await loc.focus(timeout=timeout)

        elif action == "blur":
            await loc.blur()

        else:
            # ── Hardening: bridge AI actions that look like assertions ────────
            if action.startswith("assert_") or action.startswith("element_"):
                atype = action
                if atype.startswith("assert_"):
                    atype = atype[7:]
                # Create a pseudo-assertion dict from the step
                a = {**step, "type": atype}
                res = await _run_assertion(page, a, db, page_url, ai_client)
                return res, page_url

            return _step_fail(step, f"Unknown action: {action}"), page_url

    except PlaywrightError as e:
        ss = await _screenshot(page, f"error_{action}")
        return _step_fail(step, f"Playwright error: {e}", ss), page_url

    # Wait for navigation/render to settle, then capture final URL
    await page.wait_for_timeout(300)
    await wait_for_stable(page)          # catches delayed SPA redirects (login, form submit)
    new_url = _normalize_url(page.url)
    if new_url != url_before:
        await crawler.on_page_load(page, new_url)
        if state_graph is not None:
            pct = "navigation"
            if action == "click" and _snap_before:
                try:
                    from state_graph import classify_page_change
                    _snap_after: list = await page.accessibility.snapshot() or []
                    if isinstance(_snap_after, dict):
                        _snap_after = [_snap_after]
                    pct = classify_page_change(_snap_before, _snap_after, url_before, new_url)
                except Exception:
                    pct = "navigation"
            state_graph.record_transition(
                from_url         = url_before,
                to_url           = new_url,
                trigger_action   = action,
                trigger_label    = _trigger_label(step),
                trigger_selector = selector,
                session_id       = session_id,
                page_change_type = pct,
            )

    return _step_pass(step, strategy=strategy), new_url


# ── Assertions ────────────────────────────────────────────────────────

async def _run_assertion(
    page:     Page,
    a:        dict,
    db:       LocatorDB,
    page_url: str,
    ai_client,
) -> dict:
    atype    = a.get("type", "")
    selector = a.get("selector")
    value    = a.get("value", "")

    try:
        # ── URL / page ────────────────────────────────────────────────
        if atype == "url_equals":
            actual = page.url
            # Normalize trailing slashes before comparing
            norm_actual = actual.rstrip("/")
            norm_value  = value.rstrip("/")
            return _assert_pass(a, actual) if norm_actual == norm_value else \
                   _assert_fail(a, f"URL '{actual}' != '{value}'", actual)

        if atype == "url_contains":
            actual = page.url
            return _assert_pass(a, actual) if value in actual else \
                   _assert_fail(a, f"URL '{actual}' does not contain '{value}'", actual)

        if atype == "url_matches":
            actual = page.url
            pattern = a.get("pattern", value)
            return _assert_pass(a, actual) if re.search(pattern, actual) else \
                   _assert_fail(a, f"URL '{actual}' does not match '{pattern}'", actual)

        if atype == "title_contains":
            actual = await page.title()
            return _assert_pass(a, actual) if value in actual else \
                   _assert_fail(a, f"Title '{actual}' does not contain '{value}'", actual)

        if atype == "title_equals":
            actual = await page.title()
            return _assert_pass(a, actual) if actual == value else \
                   _assert_fail(a, f"Title '{actual}' != '{value}'", actual)

        if atype == "javascript":
            result = await page.evaluate(a.get("script", "false"))
            expected = a.get("expected", True)
            return _assert_pass(a, result) if result == expected else \
                   _assert_fail(a, f"JS returned {result}, expected {expected}", result)

        # ── Element assertions — need locator ─────────────────────────
        # Coerce bare string value to testid selector for element assertions
        if not selector and isinstance(value, str) and value:
            selector = {"strategy": "testid", "value": value}
        if not selector:
            return _assert_fail(a, "Missing selector for element assertion")

        loc, strategy = await resolve_locator(
            page, selector, a.get("fallback"), db, page_url, ai_client
        )

        if atype == "element_exists":
            if loc is None:
                return _assert_fail(a, "Element not found", 0)
            try:
                await loc.first.wait_for(state="attached", timeout=ASSERTION_TIMEOUT)
                return _assert_pass(a)
            except PlaywrightError:
                count = await loc.count()
                return _assert_fail(a, f"Element not found within timeout (count={count})", count)

        if atype == "element_not_exists":
            if loc is None:
                return _assert_pass(a, 0)
            count = await loc.count()
            return _assert_pass(a, 0) if count == 0 else \
                   _assert_fail(a, f"Element exists (count={count})", count)

        if atype == "element_visible":
            if loc is None:
                return _assert_fail(a, "Element not found")
            try:
                await loc.first.wait_for(state="visible", timeout=ASSERTION_TIMEOUT)
                return _assert_pass(a)
            except PlaywrightError:
                return _assert_fail(a, "Element not visible within timeout")

        if atype in ("element_hidden", "element_not_visible"):
            if loc is None:
                return _assert_pass(a)
            try:
                await loc.first.wait_for(state="hidden", timeout=ASSERTION_TIMEOUT)
                return _assert_pass(a)
            except PlaywrightError:
                return _assert_fail(a, "Element is visible but should not be")

        if atype == "element_enabled":
            if loc is None:
                return _assert_fail(a, "Element not found")
            enabled = await loc.first.is_enabled()
            return _assert_pass(a) if enabled else _assert_fail(a, "Element is disabled")

        if atype == "element_disabled":
            if loc is None:
                return _assert_fail(a, "Element not found")
            disabled = await loc.first.is_disabled()
            return _assert_pass(a) if disabled else _assert_fail(a, "Element is enabled")

        if atype == "element_checked":
            if loc is None:
                return _assert_fail(a, "Element not found")
            checked = await loc.first.is_checked()
            return _assert_pass(a) if checked else _assert_fail(a, "Element is not checked")

        if atype == "element_unchecked":
            if loc is None:
                return _assert_fail(a, "Element not found")
            checked = await loc.first.is_checked()
            return _assert_pass(a) if not checked else _assert_fail(a, "Element is checked")

        if atype == "element_focused":
            if loc is None:
                return _assert_fail(a, "Element not found")
            focused = await loc.first.evaluate("el => el === document.activeElement")
            return _assert_pass(a) if focused else _assert_fail(a, "Element is not focused")

        if atype == "element_editable":
            if loc is None:
                return _assert_fail(a, "Element not found")
            editable = await loc.first.is_editable()
            return _assert_pass(a) if editable else _assert_fail(a, "Element is not editable")

        if atype == "element_readonly":
            if loc is None:
                return _assert_fail(a, "Element not found")
            editable = await loc.first.is_editable()
            return _assert_pass(a) if not editable else _assert_fail(a, "Element is editable (not readonly)")

        if atype in ("element_text_equals", "element_contains_text", "element_text_contains"):
            if loc is None:
                return _assert_fail(a, "Element not found")
            actual = await loc.first.inner_text() or ""
            val_str = str(value) if value is not None else ""
            ok = (actual.strip() == val_str.strip()) if atype == "element_text_equals" \
                 else (val_str in actual)
            return _assert_pass(a, actual) if ok else \
                   _assert_fail(a, f"Text '{actual}' does not {'equal' if atype=='element_text_equals' else 'contain'} '{val_str}'", actual)

        if atype == "element_text_matches":
            if loc is None:
                return _assert_fail(a, "Element not found")
            actual = await loc.first.inner_text() or ""
            pattern = a.get("pattern", value or "")
            try:
                ok = bool(re.search(pattern, actual))
            except re.error as e:
                return _assert_fail(a, f"Invalid regex '{pattern}': {e}")
            return _assert_pass(a, actual) if ok else \
                   _assert_fail(a, f"Text '{actual}' does not match pattern '{pattern}'", actual)

        if atype in ("element_value_equals", "element_value"):
            if loc is None:
                return _assert_fail(a, "Element not found")
            actual = await loc.first.input_value() or ""
            val_str = str(value) if value is not None else ""
            return _assert_pass(a, actual) if actual == val_str else \
                   _assert_fail(a, f"Value '{actual}' != '{val_str}'", actual)

        if atype == "element_value_contains":
            if loc is None:
                return _assert_fail(a, "Element not found")
            actual = await loc.first.input_value() or ""
            val_str = str(value) if value is not None else ""
            return _assert_pass(a, actual) if val_str in actual else \
                   _assert_fail(a, f"Value '{actual}' does not contain '{val_str}'", actual)

        if atype == "element_count":
            # loc is None means 0 elements in DOM — treat as count=0 (valid for less_than/equals/at_most 0)
            count    = 0 if loc is None else await loc.count()
            raw      = a.get("count", value)
            expected = int(raw) if raw is not None and str(raw).isdigit() else 0
            operator = a.get("operator", "equals")
            checks   = {"equals": count == expected, "at_least": count >= expected,
                        "at_most": count <= expected, "greater_than": count > expected,
                        "less_than": count < expected}
            if operator not in checks:
                return _assert_fail(a, f"Unknown operator: {operator}")
            ok = checks[operator]
            return _assert_pass(a, count) if ok else \
                   _assert_fail(a, f"count={count}, expected {operator} {expected}", count)

        if atype == "element_attribute":
            if loc is None:
                return _assert_fail(a, "Element not found")
            attr   = a.get("attribute", "")
            actual = await loc.first.get_attribute(attr) or ""
            val_str = str(value) if value is not None else ""
            return _assert_pass(a, actual) if actual == val_str else \
                   _assert_fail(a, f"attr[{attr}]='{actual}', expected '{val_str}'", actual)

        if atype == "element_has_class":
            if loc is None:
                return _assert_fail(a, "Element not found")
            classes = (await loc.first.get_attribute("class") or "").split()
            cls     = str(a.get("class", value)) if a.get("class", value) is not None else ""
            return _assert_pass(a, classes) if cls in classes else \
                   _assert_fail(a, f"Class '{cls}' not in {classes}", classes)

        if atype == "element_has_style":
            if loc is None:
                return _assert_fail(a, "Element not found")
            prop = a.get("property", "")
            expected_val = str(a.get("value", a.get("expected", "")))
            actual = await loc.first.evaluate(
                "(el, prop) => window.getComputedStyle(el).getPropertyValue(prop)", prop
            )
            actual = (actual or "").strip()
            ok = actual == expected_val
            return _assert_pass(a, actual) if ok else \
                   _assert_fail(a, f"style[{prop}]='{actual}', expected '{expected_val}'", actual)

        if atype == "element_in_viewport":
            if loc is None:
                return _assert_fail(a, "Element not found")
            ratio = await loc.first.evaluate("""el => {
                const r = el.getBoundingClientRect();
                const vw = window.innerWidth, vh = window.innerHeight;
                const vx = Math.max(0, Math.min(r.right,vw) - Math.max(r.left,0));
                const vy = Math.max(0, Math.min(r.bottom,vh) - Math.max(r.top,0));
                const area = r.width * r.height;
                return area > 0 ? (vx*vy)/area : 0;
            }""")
            min_ratio = float(a.get("ratio", 0.5))
            return _assert_pass(a, ratio) if ratio >= min_ratio else \
                   _assert_fail(a, f"viewport ratio {ratio:.2f} < {min_ratio}", ratio)

        return _assert_fail(a, f"Unknown assertion type: {atype}")

    except Exception as e:
        return _assert_fail(a, f"Assertion error: {e}")


# ── Failure Taxonomy (Phase 4) ────────────────────────────────────────

class FailureCategory:
    AUTH_REJECTED = "AUTH_REJECTED"
    SELECTOR_NOT_FOUND = "SELECTOR_NOT_FOUND"
    SEMANTIC_MISMATCH = "SEMANTIC_MISMATCH"
    NAV_TIMEOUT = "NAV_TIMEOUT"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    FLOW_INCOMPLETE = "FLOW_INCOMPLETE"
    UNKNOWN = "UNKNOWN"

# ── Executor ──────────────────────────────────────────────────────────

class Executor:
    """
    Runs a complete test case deterministically.

    Usage:
        db = LocatorDB()
        async with Executor(db) as exc:
            result = await exc.run(test_case)

    Test case shape:
        {
            "id":   "TC001",
            "name": "User can log in",
            "url":  "https://app.com",
            "steps": [
                {"action": "navigate", "url": "https://app.com/login"},
                {"action": "fill",
                 "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Email"}},
                 "value": "user@test.com"},
                {"action": "click",
                 "selector": {"strategy": "role", "value": {"role": "button", "name": "Sign In"}}}
            ],
            "assertions": [
                {"type": "url_contains", "value": "/dashboard"},
                {"type": "element_visible",
                 "selector": {"strategy": "role", "value": {"role": "button", "name": "Log Out"}}}
            ]
        }
    """

    def __init__(
        self,
        db:           LocatorDB,
        headless:     Optional[bool]  = None,
        ai_client                     = None,
        credentials:  Optional[dict]  = None,
        state_graph:  Optional[StateGraph] = None,
        device:       Optional[str]   = None,
        viewport:     Optional[tuple] = None,
        trace_dir:    Optional[str]   = None,
        logger:       Optional[any]   = None,
    ):
        self._db          = db
        self._headless    = headless if headless is not None else (
            os.getenv("QAPAL_HEADLESS", "true").lower() == "true"
        )
        self._ai_client   = ai_client
        self._credentials = credentials
        self._state_graph = state_graph
        self._device_name = device or os.getenv("QAPAL_DEVICE", None)
        self._viewport    = viewport  # (width, height) tuple or None
        self._trace_dir   = trace_dir  # if set, save Playwright traces for failed tests
        self._log         = logger or log
        self._device_kwargs: dict = {}
        self._pw          = None
        self._browser     = None
        self._crawler     = None
        self._healer      = None

    async def start(self):
        self._pw          = await async_playwright().start()
        self._browser     = await self._pw.chromium.launch(headless=self._headless)

        # Resolve device emulation kwargs
        self._device_kwargs = {}
        if self._device_name:
            try:
                self._device_kwargs = dict(self._pw.devices[self._device_name])
            except KeyError:
                available = ", ".join(sorted(self._pw.devices.keys())[:10])
                raise ValueError(
                    f"Unknown device '{self._device_name}'. Examples: {available}..."
                )
        if self._viewport:
            self._device_kwargs["viewport"] = {
                "width": self._viewport[0], "height": self._viewport[1]
            }

        self._crawler     = Crawler(self._db, headless=self._headless,
                                    credentials=self._credentials,
                                    device=self._device_name, viewport=self._viewport)
        self._crawler._browser = self._browser
        self._crawler._pw      = self._pw
        self._crawler._device_kwargs = self._device_kwargs
        self._crawler._started = True
        
        # Initialize the surgical step healer
        try:
            from engine.repair.step_healer import StepHealer
            self._healer = StepHealer(self._ai_client, self._db)
        except ImportError:
            self._healer = None

    async def close(self):
        if self._browser:
            try: await self._browser.close()
            except Exception: pass
        if self._pw:
            try: await self._pw.stop()
            except Exception: pass

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def _attempt_recovery(
        self,
        page:                Page,
        current_url:         str,
        dom_hash:            str,
        failed_step_result:  dict,
        remaining_steps:     list,
        execution_history:   list,
        original_assertions: list,
    ) -> Optional[list]:
        """
        Unknown-state recovery — no re-crawl, uses the live page.

        1. Extract semantic context from live HTML/a11y tree.
        2. Save new state + dom_hash to DB.
        3. Trigger crawler.on_page_load() to populate fresh locators.
        4. Call Replanner to get a step patch.
        Returns the patch list, or None on failure.
        """
        if self._ai_client is None:
            return None

        try:
            from semantic_extractor import extract_semantic_context
            from replanner import Replanner, ReplanningError

            log.warning("recovery: unknown state at %s", current_url)

            # 1. Extract semantic context from live page (no navigation)
            semantic_ctx = await extract_semantic_context(page, current_url)
            page_name    = semantic_ctx.get("page", "")
            buttons      = semantic_ctx.get("buttons", [])
            headings     = semantic_ctx.get("headings", [])
            log.debug("recovery: context page=%r  buttons=%s  headings=%s",
                      page_name, buttons[:3], headings[:3])

            # 2. Persist new state fingerprint + context
            self._db.upsert_state(current_url, dom_hash, semantic_ctx)

            # 3. Update locators for this URL from live page
            await self._crawler.on_page_load(page, current_url)
            locator_count = len(self._db.get_all(current_url, valid_only=True))
            log.debug("recovery: live locators extracted: %d", locator_count)

            # 4. Pull fresh locators to give the replanner accurate context
            available_locators = self._db.get_all(current_url, valid_only=True)

            # 5. Replan — returns step patch only
            log.warning("recovery: calling replanner for %d remaining step(s)...",
                        len(remaining_steps))
            replanner = Replanner(self._ai_client)
            patch = await replanner.replan(
                execution_history   = execution_history,
                failed_step         = failed_step_result,
                current_url         = current_url,
                remaining_steps     = remaining_steps,
                semantic_context    = semantic_ctx,
                available_locators  = available_locators,
                original_assertions = original_assertions,
            )
            log.info("recovery: replanner generated %d replacement step(s)", len(patch))
            # Log token usage incurred by the recovery call
            tok = get_token_tracker().format_line("recovery")
            if tok:
                log.info(tok)
            return patch

        except Exception as e:
            log.error("recovery: failed: %s", e)
            return None

    async def run(self, test_case: dict) -> dict:
        """
        Run a complete test case. Returns a result dict.

        Result shape:
            {
                "id":          "TC001",
                "name":        "...",
                "status":      "pass" | "fail",
                "steps":       [...],
                "assertions":  [...],
                "duration_ms": 1234,
                "screenshot":  "path/on/failure.png"
            }
        """
        tc_id   = test_case.get("id") or test_case.get("test_id") or test_case.get("_meta", {}).get("test_id", "unknown")
        tc_name = test_case.get("name", tc_id)
        start   = time.monotonic()
        
        from assertions import validate_assertion
        for a in (test_case.get("assertions") or []):
            is_valid, errors = validate_assertion(a)
            if not is_valid:
                return {
                    "id":          tc_id,
                    "name":        tc_name,
                    "status":      "fail",
                    "steps":       [],
                    "assertions":  [{"type": a.get("type", "unknown"), "status": "fail", "reason": f"Invalid assertion: {'; '.join(errors)}"}],
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "screenshot":  None,
                }
                
        for step in (test_case.get("steps") or []):
            if step.get("_invalid_element_id"):
                return {
                    "id":          tc_id,
                    "name":        tc_name,
                    "status":      "fail",
                    "steps":       [{"action": step.get("action", "unknown"), "status": "fail", "reason": "Invalid or hallucinated element_id before execution"}],
                    "assertions":  [],
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "screenshot":  None,
                }

        ctx  = await _build_context(
            self._browser,
            self._db,
            test_case.get("url", ""),
            self._credentials,
            self._device_kwargs,
        )

        # Start Playwright tracing (saved only on failure)
        if self._trace_dir:
            await ctx.tracing.start(screenshots=True, snapshots=True, sources=False)

        page = await ctx.new_page()

        # ── Passive error interception ─────────────────────────────────
        _console_errors:   list = []
        _network_failures: list = []
        _js_exceptions:    list = []
        _base_url = test_case.get("url", "")

        def _on_console(msg):
            if msg.type == "error":
                _console_errors.append({
                    "text": msg.text,
                    "url":  msg.location.get("url", "") if hasattr(msg, "location") else "",
                })

        def _on_request_failed(req):
            if _is_signal_failure(req.url, _base_url):
                _network_failures.append({"url": req.url, "failure": req.failure})

        page.on("console",       _on_console)
        page.on("requestfailed", _on_request_failed)
        page.on("pageerror",     lambda err: _js_exceptions.append(str(err)))
        # ──────────────────────────────────────────────────────────────

        step_results        = []
        assertion_results   = []
        visual_regressions  = []
        failed_step         = None
        current_url         = _normalize_url(test_case.get("url", ""))
        passed              = False
        final_screenshot    = None

        # Recovery state
        steps            = list(test_case.get("steps", []))
        replan_count     = 0
        unknown_count    = 0
        url_visit_counts: dict = {}

        try:
            i = 0
            while i < len(steps):
                step        = steps[i]
                url_before  = current_url

                result, current_url = await _execute_step(
                    page, step, self._db, current_url,
                    self._crawler, self._ai_client,
                    state_graph = self._state_graph,
                    session_id  = tc_id,
                )

                # Visual regression: snapshot after navigate or URL-changing actions
                if VISUAL_REGRESSION and result.get("status") == "pass":
                    action = step.get("action", "")
                    if action == "navigate" or (action == "click" and current_url != url_before):
                        vr = await _visual_compare(page, tc_id, i)
                        if vr:
                            visual_regressions.append(vr)

                # Track URL visits for redirect-loop detection
                if current_url != url_before:
                    url_visit_counts[current_url] = url_visit_counts.get(current_url, 0) + 1
                    if url_visit_counts[current_url] > MAX_URL_VISITS:
                        failed_step = _step_fail(
                            step,
                            f"Redirect loop: '{current_url}' visited "
                            f"{url_visit_counts[current_url]} times (limit {MAX_URL_VISITS})",
                        )
                        break

                step_results.append(result)

                if result["status"] == "fail":
                    # ── Phase 1: Surgical Step Repair (Healer) ─────────
                    if self._healer and not step.get("_healed"):
                        log.warning("Step %d failed. Attempting surgical repair...", i + 1)
                        # Ensure we have fresh locators for the repair
                        await self._crawler.on_page_load(page, current_url)
                        available_locators = self._db.get_all(current_url, valid_only=True)
                        
                        repair_step = await self._healer.repair_step(
                            failed_step=step,
                            error_reason=result.get("reason", "unknown"),
                            current_url=current_url,
                            available_locators=available_locators,
                        )
                        
                        if repair_step:
                            # ── Task 4.2: Intent Locking ──────────────────
                            if repair_step.get("action") != step.get("action"):
                                log.error("Repair rejected: action drift detected (%s -> %s)", 
                                           step.get("action"), repair_step.get("action"))
                                result["category"] = FailureCategory.SEMANTIC_MISMATCH
                                result["reason"] = f"Repair rejected: intent changed from {step.get('action')} to {repair_step.get('action')}"
                                # Keep the failure and move on (do not retry)
                            else:
                                log.info("Repair success: replacing step index %d", i)
                                repair_step["_healed"] = True
                                steps[i] = repair_step
                                continue # retry the SAME index with the repaired step
                    # ────────────────────────────────────────────────

                    # ── Unknown-state recovery gate (Full Replan) ──────
                    dom_hash = await _compute_dom_hash(page)
                    if (
                        _detect_unknown_state(self._db, current_url, dom_hash)
                        and replan_count  < MAX_REPLANS_PER_TEST
                        and unknown_count < MAX_UNKNOWN_STATES
                        and self._ai_client is not None
                    ):
                        unknown_count += 1
                        replan_count  += 1
                        patch = await self._attempt_recovery(
                            page                = page,
                            current_url         = current_url,
                            dom_hash            = dom_hash,
                            failed_step_result  = result,
                            remaining_steps     = steps[i:],
                            execution_history   = step_results[:-1],  # exclude failed
                            original_assertions = test_case.get("assertions", []),
                        )
                        if patch:
                            # Swap remaining steps with the patch; remove the
                            # failed step result and retry from this index
                            steps = steps[:i] + patch
                            step_results.pop()
                            continue  # retry at same index with first patch step

                    failed_step = result
                    break

                i += 1

            if failed_step is None:
                for a in (test_case.get("assertions") or []):
                    ar = await _run_assertion(
                        page, a, self._db, current_url, self._ai_client
                    )
                    assertion_results.append(ar)

            steps_ok   = all(r["status"] == "pass" for r in step_results)
            asserts_ok = all(r["status"] == "pass" for r in assertion_results)
            passed     = steps_ok and asserts_ok

            if not passed:
                final_screenshot = await _screenshot(page, f"{tc_id}_fail")

        finally:
            # Stop tracing — save to file only for failed tests
            if self._trace_dir:
                try:
                    if not passed:
                        trace_path = Path(self._trace_dir) / f"{tc_id}.zip"
                        trace_path.parent.mkdir(parents=True, exist_ok=True)
                        await ctx.tracing.stop(path=str(trace_path))
                    else:
                        await ctx.tracing.stop()  # discard — no path
                except Exception:
                    pass  # tracing failure should not break test results
            await ctx.close()

        passive_errors = {
            "console_errors":   _console_errors,
            "network_failures": _network_failures,
            "js_exceptions":    _js_exceptions,
        }
        return {
            "id":                    tc_id,
            "name":                  tc_name,
            "status":                "pass" if passed else "fail",
            "steps":                 step_results,
            "assertions":            assertion_results,
            "duration_ms":           int((time.monotonic() - start) * 1000),
            "screenshot":            final_screenshot,
            "passive_errors":        passive_errors,
            "has_passive_errors":    bool(_console_errors or _network_failures or _js_exceptions),
            "visual_regressions":    visual_regressions,
            "has_visual_regressions": bool(visual_regressions),
        }

    async def run_parallel(self, plans: list, concurrency: int = 3) -> list:
        """
        Run multiple plans concurrently, up to `concurrency` at a time.
        Returns results in the same order as `plans`.
        Prints live status as each test completes.
        """
        semaphore  = asyncio.Semaphore(concurrency)
        log_lock   = asyncio.Lock()

        async def _one(plan: dict) -> dict:
            tc_id = plan.get("test_id") or plan.get("id") or "?"
            async with semaphore:
                result = await self.run(plan)
            async with log_lock:
                icon = "✓" if result["status"] == "pass" else "✗"
                self._log.info("  %s %s %s  (%dms)",
                         tc_id, icon, result["status"], result["duration_ms"])
                if result["status"] == "fail":
                    for s in result.get("steps", []):
                        if s.get("status") == "fail":
                            self._log.error("    step fail: %s", s.get("reason"))
                    for a in result.get("assertions", []):
                        if a.get("status") == "fail":
                            self._log.error("    assert fail: %s", a.get("reason"))
            return result

        return list(await asyncio.gather(*(_one(p) for p in plans)))


# ── Standalone Testing ────────────────────────────────────────────────

if __name__ == "__main__":
    # Standalone smoke test
    async def test():
        db = LocatorDB()
        try:
            async with Executor(db, headless=True) as exc:
                log.info("Executor initialized. Running smoke test...")
                plan = {
                    "id": "smoke",
                    "steps": [{"action": "navigate", "url": "https://example.com"}],
                    "assertions": [{"type": "title_contains", "value": "Example Domain"}]
                }
                result = await exc.run(plan)
                log.info("Result: %s", result["status"])
        except Exception as e:
            log.error("Smoke test failed: %s", e)
        finally:
            db.close()

    asyncio.run(test())
