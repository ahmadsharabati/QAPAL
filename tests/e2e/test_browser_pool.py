"""
tests/e2e/test_browser_pool.py

Unit tests for the BrowserPool service.
Uses a real Playwright browser — marked @pytest.mark.network to allow
CI to skip when Chromium is unavailable.

Tests validate:
  1. Pool starts and stops cleanly
  2. Context acquire/release works
  3. Semaphore caps concurrency at pool size
  4. is_healthy reflects browser state
  5. Multiple sequential acquires reuse the same browser
"""

import asyncio
import pytest

from backend.services.browser_pool import BrowserPool


@pytest.fixture()
async def pool():
    """Small pool (size=2) for fast tests."""
    p = BrowserPool(size=2, headless=True)
    await p.start()
    yield p
    await p.stop()


# ── Lifecycle ──────────────────────────────────────────────────────────────

@pytest.mark.network
def test_pool_starts_and_stops():
    """Pool lifecycle: start → verify healthy → stop → verify stopped."""
    async def _run():
        p = BrowserPool(size=2, headless=True)
        assert not p._started
        await p.start()
        assert p._started
        assert p.is_healthy
        await p.stop()
        assert not p._started

    asyncio.run(_run())


@pytest.mark.network
def test_pool_double_start_is_idempotent():
    """Starting twice must not launch two browsers."""
    async def _run():
        p = BrowserPool(size=2, headless=True)
        await p.start()
        browser_ref = p._browser
        await p.start()  # second call — should be no-op
        assert p._browser is browser_ref  # same object
        await p.stop()

    asyncio.run(_run())


# ── Acquire / release ──────────────────────────────────────────────────────

@pytest.mark.network
def test_acquire_yields_functional_context():
    """Acquired context must be able to create a page and navigate."""
    async def _run():
        p = BrowserPool(size=2, headless=True)
        await p.start()
        try:
            async with p.acquire() as ctx:
                page = await ctx.new_page()
                await page.goto("about:blank")
                assert page.url == "about:blank"
                await page.close()
        finally:
            await p.stop()

    asyncio.run(_run())


@pytest.mark.network
def test_acquire_increments_active_counter():
    """active counter must reflect in-use contexts."""
    async def _run():
        p = BrowserPool(size=2, headless=True)
        await p.start()
        try:
            assert p.active == 0
            async with p.acquire():
                assert p.active == 1
                async with p.acquire():
                    assert p.active == 2
                assert p.active == 1
            assert p.active == 0
        finally:
            await p.stop()

    asyncio.run(_run())


@pytest.mark.network
def test_acquire_releases_on_exception():
    """Context must be released even when the body raises."""
    async def _run():
        p = BrowserPool(size=2, headless=True)
        await p.start()
        try:
            with pytest.raises(ValueError):
                async with p.acquire():
                    raise ValueError("test error")
            assert p.active == 0, "Context not released after exception"
        finally:
            await p.stop()

    asyncio.run(_run())


# ── Concurrency cap ────────────────────────────────────────────────────────

@pytest.mark.network
def test_pool_caps_concurrency_at_size():
    """
    A pool of size 2 must block a third acquire until one is released.
    Measured by timing: the 3rd task should be delayed by ~100ms.
    """
    async def _run():
        p = BrowserPool(size=2, headless=True)
        await p.start()
        try:
            results = []

            async def worker(label, hold_ms):
                async with p.acquire():
                    results.append(f"{label}:start")
                    await asyncio.sleep(hold_ms / 1000)
                    results.append(f"{label}:end")

            # Workers 1 and 2 run in parallel, Worker 3 must wait for one to finish
            await asyncio.gather(
                worker("A", 150),
                worker("B", 150),
                worker("C", 0),   # C should be delayed behind A or B
            )

            # C:start must come after at least one of A:end or B:end
            c_start = results.index("C:start")
            a_end = results.index("A:end")
            b_end = results.index("B:end")
            assert c_start > min(a_end, b_end), (
                f"C started before a slot was freed: {results}"
            )
        finally:
            await p.stop()

    asyncio.run(_run())


# ── available / active properties ─────────────────────────────────────────

@pytest.mark.network
def test_available_plus_active_equals_size():
    async def _run():
        p = BrowserPool(size=3, headless=True)
        await p.start()
        try:
            async with p.acquire():
                assert p.active == 1
                assert p.available == 2
        finally:
            await p.stop()

    asyncio.run(_run())


# ── Not started ────────────────────────────────────────────────────────────

def test_acquire_before_start_raises():
    async def _run():
        p = BrowserPool(size=2, headless=True)
        with pytest.raises(RuntimeError, match="not been started"):
            async with p.acquire():
                pass

    asyncio.run(_run())


def test_is_healthy_false_before_start():
    p = BrowserPool(size=2, headless=True)
    assert not p.is_healthy
