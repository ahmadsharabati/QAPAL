"""
browser_pool.py — Persistent Playwright browser pool.

Problem solved:
  The naive approach (async_playwright() per scan) spawns and kills a full
  Chromium process for every job.  At 10 concurrent scans that's 10 processes
  × ~500MB RAM = 5GB just for browsers.  Cold-start time is also ~1–2 seconds
  per scan, which degrades the UX for Quick Scan and is pure waste for Deep Scan.

Solution:
  Keep ONE Chromium browser process alive for the lifetime of the backend.
  Per-scan, acquire a fresh BrowserContext (isolated cookies/storage/network)
  from the shared Browser.  Release it when the scan finishes.  A Semaphore
  caps concurrency so we never exceed BROWSER_POOL_SIZE simultaneous scans.

  RAM profile (contexts instead of browsers):
    idle:           ~80MB  (1 browser process)
    per context:    ~60–120MB additional (vs ~400MB for a full browser spawn)
    4 concurrent:   ~400–600MB total  (vs ~2GB spawning 4 browsers)

Usage (in worker.py or quick_scan.py):

    from backend.services.browser_pool import browser_pool

    # During scan:
    async with browser_pool.acquire() as context:
        page = await context.new_page()
        await page.goto(url)
        ...

Lifecycle (in app.py):

    @app.on_event("startup")
    async def startup():
        await browser_pool.start()

    @app.on_event("shutdown")
    async def shutdown():
        await browser_pool.stop()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)

from backend.config import settings

logger = logging.getLogger(__name__)


class BrowserPool:
    """
    Singleton-style pool of Playwright browser contexts.

    One browser process is kept alive; contexts are created and destroyed
    per scan.  A semaphore enforces the concurrency cap.
    """

    def __init__(self, size: int, headless: bool = True) -> None:
        self._size = size
        self._headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active: int = 0
        self._lock = asyncio.Lock()
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the shared browser process.  Call once at app startup."""
        if self._started:
            return
        logger.info("BrowserPool: launching Chromium (pool_size=%d)", self._size)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-dev-shm-usage",   # required inside Docker/CI
                "--no-sandbox",              # required inside Docker/CI
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        self._semaphore = asyncio.Semaphore(self._size)
        self._started = True
        logger.info("BrowserPool: ready (pool_size=%d)", self._size)

    async def stop(self) -> None:
        """Gracefully close the browser and Playwright.  Call at shutdown."""
        if not self._started:
            return
        logger.info("BrowserPool: shutting down")
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._started = False
        logger.info("BrowserPool: stopped")

    # ── Acquire ────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def acquire(self, **context_kwargs) -> AsyncGenerator[BrowserContext, None]:
        """
        Acquire an isolated BrowserContext from the pool.

        Blocks (up to the caller's timeout) if the pool is at capacity.
        The context is always closed when the `async with` block exits,
        even on exception.

        Optional context_kwargs are forwarded to browser.new_context() —
        e.g. viewport, locale, user_agent, extra_http_headers.

        Example:
            async with browser_pool.acquire(locale="en-US") as ctx:
                page = await ctx.new_page()
                await page.goto("https://example.com")
        """
        if not self._started:
            raise RuntimeError(
                "BrowserPool has not been started. "
                "Call `await browser_pool.start()` at application startup."
            )

        async with self._semaphore:
            async with self._lock:
                self._active += 1
                active_now = self._active

            logger.debug("BrowserPool: context acquired (active=%d/%d)", active_now, self._size)
            context: Optional[BrowserContext] = None
            try:
                context = await self._browser.new_context(**context_kwargs)
                yield context
            finally:
                if context:
                    try:
                        await context.close()
                    except Exception as exc:
                        logger.warning("BrowserPool: error closing context: %s", exc)
                async with self._lock:
                    self._active -= 1
                logger.debug("BrowserPool: context released (active=%d/%d)", self._active, self._size)

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def active(self) -> int:
        """Number of contexts currently in use."""
        return self._active

    @property
    def available(self) -> int:
        """Number of slots available for new scans."""
        return max(0, self._size - self._active)

    @property
    def is_healthy(self) -> bool:
        """True if the browser process is alive and accepting new contexts."""
        return (
            self._started
            and self._browser is not None
            and self._browser.is_connected()
        )

    async def recover(self) -> None:
        """
        Attempt to restart the browser if it has crashed.

        Called by the health check endpoint when is_healthy returns False.
        This is a best-effort recovery — if it fails, the health endpoint
        should report degraded status so the load balancer can route away.
        """
        logger.warning("BrowserPool: attempting crash recovery")
        try:
            await self.stop()
        except Exception:
            pass
        try:
            await self.start()
            logger.info("BrowserPool: crash recovery succeeded")
        except Exception as exc:
            logger.error("BrowserPool: crash recovery failed: %s", exc)
            raise


# ── Global singleton ────────────────────────────────────────────────────────
# Imported by app.py (lifecycle), worker.py (deep scan), quick_scan.py.
# Not started until app.py calls browser_pool.start() at startup.

browser_pool = BrowserPool(
    size=settings.BROWSER_POOL_SIZE,
    headless=settings.BROWSER_HEADLESS,
)
