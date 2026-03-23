"""
quick_scan.py — Browser-native quick scan engine.

Injects the extension's compiled scanner bundle into a real Chromium page
via Playwright's add_init_script() (CDP-level, CSP-immune) and returns
the structured issue list.

Browser strategy (in priority order):
  1. BrowserPool (preferred when running inside the backend server)
     — reuses a warm browser process, ~60-120MB per context vs ~400MB spawn
  2. Fresh browser (fallback for CLI / standalone use, or if pool not started)
     — spins up a new Chromium process, cleans up on exit

Usage inside the backend:
    from engine.quick_scan import run_quick_scan
    result = await run_quick_scan(url)          # uses pool automatically

Usage from CLI / tests (pool not running):
    result = await run_quick_scan(url, headless=True)  # spawns fresh browser
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from playwright.async_api import BrowserContext, async_playwright

logger = logging.getLogger(__name__)

_BUNDLE_PATH = (
    Path(__file__).parent.parent
    / "extension"
    / "dist"
    / "src"
    / "content"
    / "scanner.js"
)


def _load_bundle() -> str:
    """Read and cache the scanner bundle from disk."""
    if not _BUNDLE_PATH.exists():
        raise FileNotFoundError(
            f"Scanner bundle not found at {_BUNDLE_PATH}. "
            "Run 'npm run build' in the extension/ directory first."
        )
    return _BUNDLE_PATH.read_text(encoding="utf-8")


# Cache the bundle content so we only read from disk once per process
_bundle_cache: Optional[str] = None


def _get_bundle() -> str:
    global _bundle_cache
    if _bundle_cache is None:
        _bundle_cache = _load_bundle()
    return _bundle_cache


@asynccontextmanager
async def _get_context(headless: bool) -> AsyncGenerator[BrowserContext, None]:
    """
    Yield a BrowserContext either from the global pool (if running inside
    the backend and the pool is healthy) or from a fresh browser process.
    """
    # Try the pool first
    try:
        from backend.services.browser_pool import browser_pool
        if browser_pool.is_healthy:
            async with browser_pool.acquire() as ctx:
                yield ctx
            return
    except ImportError:
        pass  # running outside the backend package (e.g. CLI tests)
    except Exception as exc:
        logger.debug("Pool not available, falling back to fresh browser: %s", exc)

    # Fallback: spawn a dedicated browser for this scan
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"],
        )
        try:
            ctx = await browser.new_context()
            try:
                yield ctx
            finally:
                await ctx.close()
        finally:
            await browser.close()


async def run_quick_scan(url: str, headless: bool = True) -> Dict[str, Any]:
    """
    Run the extension's Quick Scan rules against *url* and return a dict:

        {
            "issues":     [...],        # list of issue objects
            "pageUrl":    "https://...",
            "pageTitle":  "...",
            "duration_ms": 1234,
            "checksRun":  26,
            "engine":     "Playwright/init-script"
        }

    The scanner bundle is injected via context.add_init_script() — CDP-level
    injection that bypasses Content-Security-Policy restrictions on any site.
    """
    script = _get_bundle()

    async with _get_context(headless) as context:
        # Install scanner on the context BEFORE any navigation.
        # CDP fires it on every new document load via
        # Page.addScriptToEvaluateOnNewDocument — completely immune to CSP.
        await context.add_init_script(script=script)

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            # Give JS frameworks (React, Next.js, Vue) time to hydrate the DOM.
            await page.wait_for_timeout(1_000)

            # window.runQapalScan is guaranteed to exist — add_init_script ran
            # before any page script so there is no race condition.
            result: Dict[str, Any] = await page.evaluate(
                "async () => await window.runQapalScan()"
            )
            result["engine"] = "Playwright/init-script"
            return result

        finally:
            await page.close()


# ── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = asyncio.run(run_quick_scan(target, headless=False))
    print(json.dumps(res, indent=2))
