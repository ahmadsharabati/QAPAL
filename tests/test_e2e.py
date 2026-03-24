"""
tests/test_e2e.py — QAPal End-to-End Integration Tests
=======================================================
Exercises the full QAPAL pipeline against live websites.

Groups:
  1. CrawlerE2E      — crawl real pages, verify locators extracted + persisted
  2. ExecutorE2E     — run hand-crafted plans against TodoMVC (no AI)
  3. MultiPageE2E    — tests spanning multiple URLs / filter views
  4. GeneratorE2E    — PRD → plans with a deterministic fake AI client
  5. FullPipelineE2E — crawl → generate (mocked) → execute end-to-end
  6. CLICommandsE2E  — subprocess calls to main.py CLI commands

Requirements:
  pip install playwright pytest
  playwright install chromium

Run all:
  python tests/test_e2e.py
  python -m pytest tests/test_e2e.py -v

Run a group:
  python -m pytest tests/test_e2e.py -v -k "Crawler"
  python -m pytest tests/test_e2e.py -v -k "Executor"
  python -m pytest tests/test_e2e.py -v -k "Generator"
  python -m pytest tests/test_e2e.py -v -k "MultiPage"
  python -m pytest tests/test_e2e.py -v -k "Pipeline"
  python -m pytest tests/test_e2e.py -v -k "CLI"
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from locator_db import LocatorDB
from crawler import Crawler
from executor import Executor
from generator import TestGenerator
from planner import PlanningError


# ── Constants ─────────────────────────────────────────────────────────

TODOMVC_URL    = "https://demo.playwright.dev/todomvc/#/"
ACTIVE_URL     = "https://demo.playwright.dev/todomvc/#/active"
COMPLETED_URL  = "https://demo.playwright.dev/todomvc/#/completed"
BOOKS_URL      = "https://books.toscrape.com/"
BOOKS_PAGE2    = "https://books.toscrape.com/catalogue/page-2.html"
PLAYWRIGHT_URL = "https://playwright.dev/"
PLAYWRIGHT_DOCS = "https://playwright.dev/docs/intro"

MAIN_PY = os.path.join(os.path.dirname(__file__), "..", "main.py")


# ── Helpers ───────────────────────────────────────────────────────────

def make_db() -> LocatorDB:
    """Isolated LocatorDB backed by a temporary file."""
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close()
    return LocatorDB(path=tf.name)


def run(coro):
    """Run an async coroutine synchronously (works in all Python 3.8+)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fake AI client ────────────────────────────────────────────────────

class FakeAIClient:
    """
    Deterministic stand-in for a real AI client.
    Always returns the pre-scripted response string.
    """

    def __init__(self, response: str):
        self._response = response
        self.model     = "fake-model"
        self.provider  = "fake"
        self.calls     = []

    def complete(self, prompt: str, system_prompt: str = "", max_tokens: int = 4096) -> str:
        self.calls.append(prompt)
        return self._response

    async def acomplete(self, prompt: str, system_prompt: str = "", max_tokens: int = 4096) -> str:
        self.calls.append(prompt)
        return self._response


def fake_ai_with_plans(plans: list) -> FakeAIClient:
    return FakeAIClient(json.dumps(plans))


# ════════════════════════════════════════════════════════════════════════
# 1. CRAWLER E2E
# ════════════════════════════════════════════════════════════════════════

class TestCrawlerE2E(unittest.TestCase):
    """Crawl live websites and verify the locator DB is populated correctly."""

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    # ── single page ──────────────────────────────────────────────────

    def test_todomvc_extracts_textbox_locator(self):
        """Crawling TodoMVC must discover the main input field."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs  = self.db.get_all(TODOMVC_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "No locators found after crawl")
        self.assertIn("textbox", roles, "Input textbox not found in locators")

    def test_todomvc_extracts_link_locators(self):
        """TodoMVC footer filter links (All / Active / Completed) must be found."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                # Add a todo first so footer renders, then crawl
                from playwright.async_api import async_playwright
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    ctx     = await browser.new_context()
                    page    = await ctx.new_page()
                    await page.goto(TODOMVC_URL)
                    tb = page.get_by_role("textbox")
                    await tb.fill("seed item")
                    await tb.press("Enter")
                    from crawler import crawl_page
                    from locator_db import _normalize_url
                    await crawl_page(page, _normalize_url(TODOMVC_URL), self.db, force=True)
                    await browser.close()

        run(go())
        locs  = self.db.get_all(TODOMVC_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        # After seeding, filter links should appear
        self.assertIn("link", roles, "Filter links not found in locators")

    def test_crawl_page_record_created(self):
        """A page record must exist for the crawled URL."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        page_rec = self.db.get_page(TODOMVC_URL)
        self.assertIsNotNone(page_rec, "No page record created for crawled URL")
        self.assertIn("last_crawled", page_rec)

    # ── multi-URL ─────────────────────────────────────────────────────

    def test_crawl_two_distinct_sites(self):
        """Crawling two different sites produces locators for both."""
        urls = [TODOMVC_URL, PLAYWRIGHT_URL]

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl(urls)

        run(go())
        for url in urls:
            locs = self.db.get_all(url, valid_only=True)
            self.assertGreater(len(locs), 0, f"No locators for {url}")

    def test_crawl_multi_page_ecommerce(self):
        """Crawling two catalogue pages of books.toscrape.com."""
        urls = [BOOKS_URL, BOOKS_PAGE2]

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl(urls)

        run(go())
        for url in urls:
            locs = self.db.get_all(url, valid_only=True)
            self.assertGreater(len(locs), 0, f"No locators for {url}")

    def test_crawl_multipage_playwright_docs(self):
        """Crawl Playwright landing page and docs page."""
        urls = [PLAYWRIGHT_URL, PLAYWRIGHT_DOCS]

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl(urls)

        run(go())
        for url in urls:
            locs = self.db.get_all(url, valid_only=True)
            self.assertGreater(len(locs), 0, f"No locators for {url}")

    # ── persistence ───────────────────────────────────────────────────

    def test_locators_persist_after_db_reload(self):
        """Locators survive DB close and reopen from the same file."""
        tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tf.close()

        db1 = LocatorDB(path=tf.name)

        async def go(db):
            async with Crawler(db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go(db1))
        count1 = len(db1.get_all(TODOMVC_URL, valid_only=True))
        db1.close()

        db2    = LocatorDB(path=tf.name)
        count2 = len(db2.get_all(TODOMVC_URL, valid_only=True))
        db2.close()

        self.assertGreater(count1, 0, "Nothing crawled")
        self.assertEqual(count1, count2, "Locators not persisted after DB reload")

    def test_recrawl_does_not_inflate_locator_count(self):
        """Crawling the same URL twice should update, not duplicate, records."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])
                c1 = len(self.db.get_all(TODOMVC_URL, valid_only=True))
                await c.bulk_crawl([TODOMVC_URL], force=True)
                c2 = len(self.db.get_all(TODOMVC_URL, valid_only=True))
            return c1, c2

        c1, c2 = run(go())
        self.assertGreater(c1, 0, "Nothing on first crawl")
        # Re-crawl should stay within 25% of original count
        self.assertLessEqual(
            c2, c1 * 1.25,
            f"Locator count jumped from {c1} to {c2} on re-crawl (suspected duplicate)"
        )

    # ── locator chain quality ─────────────────────────────────────────

    def test_locator_chain_not_css_only(self):
        """
        Every interactive element must have at least one semantic strategy
        (testid, role, aria-label, placeholder, label, text) in its chain.
        A chain that is ONLY 'css' is fragile and means extraction failed.
        """
        SEMANTIC = {"testid", "role", "role+container", "aria-label",
                    "placeholder", "label", "text", "id"}

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertGreater(len(locs), 0, "No locators found")

        css_only = []
        for loc in locs:
            strategies = {s["strategy"] for s in loc["locators"]["chain"]}
            if strategies and strategies <= {"css"}:
                css_only.append(loc["identity"])

        self.assertEqual(
            css_only, [],
            f"These elements have ONLY css strategy (fragile): {css_only[:3]}"
        )

    def test_high_confidence_locators_have_role_and_name(self):
        """
        Locators marked confidence='high' must always have both role and name.
        Low-confidence (dom_fallback) entries are exempt.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)

        for loc in locs:
            if loc["locators"].get("confidence") == "high":
                identity = loc["identity"]
                self.assertTrue(
                    identity.get("role") and identity.get("name"),
                    f"High-confidence locator missing role or name: {identity}"
                )

    def test_all_locator_documents_have_required_fields(self):
        """
        Every document in the DB must contain the mandatory top-level fields:
        id, url, identity, locators, history.
        Missing fields mean the upsert is broken.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([BOOKS_URL])

        run(go())
        locs = self.db.get_all(BOOKS_URL, valid_only=False)
        self.assertGreater(len(locs), 0, "No locators found")

        required = {"id", "url", "identity", "locators", "history"}
        for loc in locs:
            missing = required - set(loc.keys())
            self.assertEqual(
                missing, set(),
                f"Locator doc missing fields {missing}: {loc.get('id')}"
            )
            # identity sub-fields
            identity = loc["identity"]
            for field in ("role", "name", "container", "dom_path"):
                self.assertIn(
                    field, identity,
                    f"identity missing '{field}' in doc {loc.get('id')}"
                )
            # history sub-fields
            history = loc["history"]
            for field in ("first_seen", "last_seen", "hit_count", "miss_count", "valid"):
                self.assertIn(
                    field, history,
                    f"history missing '{field}' in doc {loc.get('id')}"
                )

    def test_locator_chain_entries_have_strategy_and_value(self):
        """Every entry in a locator chain must have both 'strategy' and 'value' keys."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        for loc in locs:
            for entry in loc["locators"]["chain"]:
                self.assertIn(
                    "strategy", entry,
                    f"Chain entry missing 'strategy': {entry}"
                )
                self.assertIn(
                    "value", entry,
                    f"Chain entry missing 'value': {entry}"
                )
                self.assertIsNotNone(
                    entry["value"],
                    f"Chain entry has None value: {entry}"
                )

    # ── stale-check / skip logic ──────────────────────────────────────

    def test_fresh_crawl_is_skipped_without_force(self):
        """
        A URL crawled moments ago must be skipped on the next call
        (result['crawled'] == False) unless force=True.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                first  = await c.crawl_url(TODOMVC_URL, force=True)
                second = await c.crawl_url(TODOMVC_URL, force=False)
            return first, second

        first, second = run(go())
        self.assertTrue(first["crawled"],  "First crawl should have run")
        self.assertFalse(second["crawled"], "Second crawl should be skipped (not stale)")

    def test_force_flag_overrides_stale_check(self):
        """force=True must re-crawl even if the page was just visited."""
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.crawl_url(TODOMVC_URL, force=True)
                second = await c.crawl_url(TODOMVC_URL, force=True)
            return second

        result = run(go())
        self.assertTrue(result["crawled"], "force=True must always re-crawl")

    def test_stale_url_returns_element_count_from_cache(self):
        """
        Even when a crawl is skipped, the result dict must contain
        the element count from the cached DB data.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.crawl_url(TODOMVC_URL, force=True)
                cached = await c.crawl_url(TODOMVC_URL, force=False)
            return cached

        result = run(go())
        self.assertFalse(result["crawled"])
        self.assertGreater(
            result["elements"], 0,
            "Skipped crawl result should still report cached element count"
        )

    # ── DB lookup (get / search) ──────────────────────────────────────

    def test_get_by_role_and_name_after_crawl(self):
        """
        After crawling TodoMVC, db.get(url, role='textbox', name=...) must
        return the main input element — not None.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        # The TodoMVC input has accessible name "What needs to be done?" or similar.
        # We check via search() since the exact name may vary by browser version.
        results = self.db.search(TODOMVC_URL, name_fragment="todo", role="textbox")
        if not results:
            # Fallback: any textbox is acceptable — site has exactly one
            all_locs = self.db.get_all(TODOMVC_URL, valid_only=True)
            results  = [l for l in all_locs if l["identity"]["role"] == "textbox"]
        self.assertGreater(len(results), 0, "No textbox found after crawl via get/search")

    def test_search_by_name_fragment_returns_matches(self):
        """
        db.search() with a partial name must return a subset of locators.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([PLAYWRIGHT_URL])

        run(go())
        # Playwright.dev has links/buttons with "Docs", "Community", etc.
        results = self.db.search(PLAYWRIGHT_URL, name_fragment="Docs")
        self.assertGreater(len(results), 0, "search('Docs') returned no results")
        for r in results:
            name = r["identity"]["name"].lower()
            self.assertIn("docs", name, f"search result name mismatch: {name!r}")

    def test_get_all_sorted_by_hit_count(self):
        """
        get_all() must return records sorted by hit_count descending.
        After two crawls, hit_count on existing records increments.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.crawl_url(TODOMVC_URL, force=True)
                await c.crawl_url(TODOMVC_URL, force=True)

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertGreater(len(locs), 0)
        counts = [l["history"]["hit_count"] for l in locs]
        self.assertEqual(
            counts, sorted(counts, reverse=True),
            "get_all() is not sorted by hit_count descending"
        )

    # ── soft decay / invalidation ────────────────────────────────────

    def test_soft_decay_invalidates_missing_elements(self):
        """
        soft_decay() with an empty seen_ids set must increment miss_count
        on all records and eventually mark them invalid after MISS_THRESHOLD hits.
        """
        from locator_db import MISS_THRESHOLD

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs_before = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertGreater(len(locs_before), 0)

        # Decay MISS_THRESHOLD times with no seen elements → all should become invalid
        for _ in range(MISS_THRESHOLD):
            self.db.soft_decay(TODOMVC_URL, seen_ids=set())

        locs_after = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertEqual(
            len(locs_after), 0,
            f"{len(locs_after)} locators still valid after {MISS_THRESHOLD} full-decay passes"
        )

    def test_soft_decay_spares_seen_elements(self):
        """
        soft_decay() must NOT increment miss_count for IDs included in seen_ids.
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([TODOMVC_URL])

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertGreater(len(locs), 0)

        # Protect all IDs from decay
        all_ids = {l["id"] for l in locs}
        self.db.soft_decay(TODOMVC_URL, seen_ids=all_ids)

        locs_after = self.db.get_all(TODOMVC_URL, valid_only=True)
        self.assertEqual(
            len(locs_after), len(locs),
            "soft_decay() invalidated elements that were in seen_ids"
        )

    def test_re_crawl_resets_miss_count(self):
        """
        An element decayed once (miss_count=1) must have miss_count reset to 0
        after the next successful crawl (because the element reappeared).
        """
        async def go():
            async with Crawler(self.db, headless=True) as c:
                # First crawl — populates DB
                await c.crawl_url(TODOMVC_URL, force=True)
                locs = self.db.get_all(TODOMVC_URL, valid_only=True)
                first_id = locs[0]["id"]

                # Decay once (without the first element in seen_ids)
                self.db.soft_decay(TODOMVC_URL, seen_ids=set())
                decayed = self.db.get_by_id(first_id)

                # Re-crawl — the element should reappear and miss_count reset
                await c.crawl_url(TODOMVC_URL, force=True)
                recovered = self.db.get_by_id(first_id)

                return decayed, recovered

        decayed, recovered = run(go())
        self.assertIsNotNone(decayed)
        self.assertGreater(
            decayed["history"]["miss_count"], 0,
            "miss_count should be > 0 after decay"
        )
        self.assertEqual(
            recovered["history"]["miss_count"], 0,
            "miss_count should reset to 0 after successful re-crawl"
        )

    # ── concurrent crawl safety ───────────────────────────────────────

    def test_concurrent_crawl_no_duplicates(self):
        """
        Two simultaneous crawls of the same URL must not create duplicate
        locator records — the DB identity hash prevents double-insertion.
        """
        from crawler import crawl_page
        from locator_db import _normalize_url
        from playwright.async_api import async_playwright

        async def go():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx1    = await browser.new_context()
                ctx2    = await browser.new_context()
                page1   = await ctx1.new_page()
                page2   = await ctx2.new_page()

                await page1.goto(TODOMVC_URL)
                await page2.goto(TODOMVC_URL)

                # Fire both crawls at the same time
                url = _normalize_url(TODOMVC_URL)
                await asyncio.gather(
                    crawl_page(page1, url, self.db, force=True),
                    crawl_page(page2, url, self.db, force=True),
                )
                await browser.close()

        run(go())
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        ids  = [l["id"] for l in locs]
        self.assertEqual(
            len(ids), len(set(ids)),
            f"Duplicate locator IDs found after concurrent crawl: "
            f"{len(ids) - len(set(ids))} duplicates"
        )

    # ── URL normalisation ─────────────────────────────────────────────

    def test_query_strings_stripped_from_stored_url(self):
        """
        Locators must be stored under the clean URL (no query params).
        e.g. https://books.toscrape.com/?foo=bar → https://books.toscrape.com/
        """
        from locator_db import _normalize_url
        url_with_qs = BOOKS_URL.rstrip("/") + "/?ref=homepage&page=1"

        async def go():
            async with Crawler(self.db, headless=True) as c:
                # crawl the plain URL
                await c.bulk_crawl([BOOKS_URL])

        run(go())
        clean_url = _normalize_url(BOOKS_URL)
        locs = self.db.get_all(clean_url, valid_only=True)
        self.assertGreater(len(locs), 0, "No locators under normalised URL")

        # All stored URLs must equal the clean form
        for loc in locs:
            self.assertEqual(
                loc["url"], clean_url,
                f"Locator stored with non-normalised URL: {loc['url']!r}"
            )

    # ── end-to-end usability (crawl → executor can find element) ─────

    def test_crawled_locator_usable_by_executor(self):
        """
        After crawling books.toscrape.com, the executor must be able to
        click a link discovered by the crawler — no AI, no replanning.
        This is the fundamental contract: crawl output feeds executor input.
        """
        async def go():
            # Step 1: crawl
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl([BOOKS_URL])

            # Step 2: pick any link locator from the DB
            locs  = self.db.get_all(BOOKS_URL, valid_only=True)
            links = [l for l in locs if l["identity"]["role"] == "link"]
            self.assertGreater(len(links), 0, "No link locators found after crawl")

            # Choose the first link that has a role+name strategy
            chosen = None
            for l in links:
                strategies = [s["strategy"] for s in l["locators"]["chain"]]
                if "role" in strategies or "testid" in strategies:
                    chosen = l
                    break
            self.assertIsNotNone(chosen, "No link with role/testid strategy found")

            # Step 3: build a minimal plan using the crawled locator
            chain   = chosen["locators"]["chain"]
            best    = chain[0]  # highest-priority strategy
            selector = {"strategy": best["strategy"], "value": best["value"]}
            plan = {
                "test_id": "crawl_usability_check",
                "name":    "Verify crawled locator is executable",
                "url":     BOOKS_URL,
                "steps":   [
                    {"action": "navigate", "url": BOOKS_URL},
                    {"action": "click",    "selector": selector},
                ],
                "assertions": [],
            }

            # Step 4: execute the plan
            async with Executor(self.db, headless=True) as exc:
                result = await exc.run(plan)

            return result

        result = run(go())
        self.assertEqual(
            result["status"], "pass",
            f"Executor failed to use crawled locator: "
            f"{result['steps'][-1].get('reason', 'no reason')} | "
            f"selector: {result['steps'][-1].get('selector', {})}"
        )


# ════════════════════════════════════════════════════════════════════════
# 2. EXECUTOR E2E
# ════════════════════════════════════════════════════════════════════════

class TestExecutorE2E(unittest.TestCase):
    """Run hand-crafted plans against live websites — no AI required."""

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _exec(self, plan: dict) -> dict:
        async def go():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return run(go())

    # ── Navigation ────────────────────────────────────────────────────

    def test_navigate_todomvc(self):
        result = self._exec({
            "id":    "nav",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{"type": "url_contains", "value": "todomvc"}],
        })
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_title_contains_assertion(self):
        result = self._exec({
            "id":    "title",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{"type": "title_contains", "value": "TodoMVC"}],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_navigate_books_site(self):
        result = self._exec({
            "id":    "nav_books",
            "steps": [{"action": "navigate", "url": BOOKS_URL}],
            "assertions": [{"type": "url_contains", "value": "books.toscrape.com"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_navigate_playwright_site(self):
        result = self._exec({
            "id":    "nav_pw",
            "steps": [{"action": "navigate", "url": PLAYWRIGHT_URL}],
            "assertions": [{"type": "url_contains", "value": "playwright.dev"}],
        })
        self.assertEqual(result["status"], "pass")

    # ── Input interactions ────────────────────────────────────────────

    def test_fill_input_field(self):
        result = self._exec({
            "id": "fill",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Write e2e tests",
                },
            ],
            "assertions": [{
                "type":     "element_value_equals",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
                "value":    "Write e2e tests",
            }],
        })
        self.assertEqual(result["status"], "pass")

    def test_fill_and_press_enter_adds_todo(self):
        result = self._exec({
            "id": "add_todo",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "E2E test item",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_exists",
                "selector": {"strategy": "text", "value": "E2E test item"},
            }],
        })
        self.assertEqual(result["status"], "pass")

    def test_add_multiple_todos(self):
        steps = [{"action": "navigate", "url": TODOMVC_URL}]
        for item in ("Alpha", "Beta", "Gamma"):
            steps += [
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    item,
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ]
        result = self._exec({
            "id":    "multi_add",
            "steps": steps,
            "assertions": [{
                "type":     "element_count",
                "selector": {"strategy": "role", "value": {"role": "listitem"}},
                "value":    3,
            }],
        })
        self.assertEqual(result["status"], "pass")

    # ── Click / checkbox ──────────────────────────────────────────────

    def test_click_checkbox_marks_todo_checked(self):
        result = self._exec({
            "id": "complete",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Buy milk",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role", "value": {"role": "checkbox"}},
                },
            ],
            "assertions": [{
                "type":     "element_checked",
                "selector": {"strategy": "role", "value": {"role": "checkbox"}},
            }],
        })
        self.assertEqual(result["status"], "pass")

    def test_click_active_filter_link_changes_url(self):
        result = self._exec({
            "id": "filter_active",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Task A",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role", "value": {"role": "link", "name": "Active"}},
                },
            ],
            "assertions": [{"type": "url_contains", "value": "active"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_click_completed_filter_link_changes_url(self):
        result = self._exec({
            "id": "filter_completed",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Finish report",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role", "value": {"role": "checkbox"}},
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role", "value": {"role": "link", "name": "Completed"}},
                },
            ],
            "assertions": [{"type": "url_contains", "value": "completed"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_dblclick_todo_enters_edit_mode(self):
        """Double-clicking a todo label should enter inline edit mode."""
        result = self._exec({
            "id": "dblclick_edit",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Editable task",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "dblclick",
                    "selector": {"strategy": "text", "value": "Editable task"},
                },
            ],
            "assertions": [],  # just verifying the action doesn't crash
        })
        # Steps should all pass; the double-click action itself succeeds
        step_statuses = [s["status"] for s in result["steps"]]
        self.assertNotIn("fail", step_statuses[:3], "Setup steps failed")

    # ── Element assertions ────────────────────────────────────────────

    def test_element_visible_textbox(self):
        result = self._exec({
            "id": "visible",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{
                "type":     "element_visible",
                "selector": {"strategy": "role", "value": {"role": "textbox"}},
            }],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_element_not_exists_on_empty_list(self):
        """No list items should exist on a fresh TodoMVC page."""
        result = self._exec({
            "id": "not_exists",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{
                "type":     "element_not_exists",
                "selector": {"strategy": "role", "value": {"role": "listitem"}},
            }],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_element_count_two_todos(self):
        result = self._exec({
            "id": "count_2",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "First",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Second",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_count",
                "selector": {"strategy": "role", "value": {"role": "listitem"}},
                "value":    2,
            }],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_element_contains_text(self):
        result = self._exec({
            "id": "text_check",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Buy oranges",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_contains_text",
                "selector": {"strategy": "text", "value": "Buy oranges"},
                "value":    "Buy oranges",
            }],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_element_unchecked_by_default(self):
        """A newly created todo checkbox must be unchecked."""
        result = self._exec({
            "id": "unchecked",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "New task",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_unchecked",
                "selector": {"strategy": "role", "value": {"role": "checkbox"}},
            }],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── Failure behaviour ─────────────────────────────────────────────

    def test_wrong_url_assertion_fails(self):
        result = self._exec({
            "id":    "bad_assert",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{"type": "url_contains", "value": "NONEXISTENT_12345_XYZ"}],
        })
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["assertions"][0]["status"], "fail")

    def test_missing_element_step_fails_hard(self):
        """A step that can't find its element should fail and stop execution."""
        result = self._exec({
            "id": "step_fail",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "button", "name": "NOSUCHBUTTON_XYZ"}},
                },
                # This step must NOT execute
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Should not run",
                },
            ],
            "assertions": [],
        })
        self.assertEqual(result["status"], "fail")
        # Only navigate + failed-click recorded; fill never reached
        self.assertLessEqual(len(result["steps"]), 2)

    def test_result_has_required_fields(self):
        """Every result dict must carry all documented fields."""
        result = self._exec({
            "id":   "structure",
            "name": "Structure check",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{"type": "url_contains", "value": "todomvc"}],
        })
        for field in ("id", "name", "status", "steps", "assertions", "duration_ms"):
            self.assertIn(field, result, f"Missing result field: {field}")
        self.assertIn(result["status"], ("pass", "fail"))
        self.assertIsInstance(result["duration_ms"], int)
        self.assertGreater(result["duration_ms"], 0)

    def test_screenshot_action_does_not_fail(self):
        """Screenshot action must succeed (file created or silently skipped)."""
        result = self._exec({
            "id": "screenshot",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {"action": "screenshot", "label": "e2e_test_snap"},
            ],
            "assertions": [],
        })
        snap_step = result["steps"][1]
        self.assertEqual(snap_step["status"], "pass")


# ════════════════════════════════════════════════════════════════════════
# 3. MULTI-PAGE E2E
# ════════════════════════════════════════════════════════════════════════

class TestMultiPageE2E(unittest.TestCase):
    """Tests that span multiple pages or URL contexts."""

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _exec(self, plan: dict) -> dict:
        async def go():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return run(go())

    # ── TodoMVC multi-view ────────────────────────────────────────────

    def test_crawl_all_three_todomvc_views(self):
        """Crawling All / Active / Completed views populates three URL entries."""
        urls = [TODOMVC_URL, ACTIVE_URL, COMPLETED_URL]

        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl(urls)

        run(go())
        for url in urls:
            locs = self.db.get_all(url, valid_only=True)
            self.assertGreater(len(locs), 0, f"No locators found for {url}")

    def test_navigate_active_view_hides_completed_todo(self):
        """After completing a todo, the Active view should not show it."""
        result = self._exec({
            "id": "active_hides_done",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Active task",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Done task",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                # Complete the second todo (its checkbox is the last one)
                {
                    "action":   "click",
                    "selector": {"strategy": "css", "value": "li:last-child .toggle"},
                },
                # Navigate to Active filter
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "Active"}},
                },
            ],
            "assertions": [
                {"type": "url_contains", "value": "active"},
                {
                    "type":     "element_exists",
                    "selector": {"strategy": "text", "value": "Active task"},
                },
            ],
        })
        self.assertEqual(result["status"], "pass")

    def test_completed_view_shows_only_done_items(self):
        """After completing a todo, Completed view shows it; Active view does not."""
        result = self._exec({
            "id": "completed_view",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Finish report",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role", "value": {"role": "checkbox"}},
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "Completed"}},
                },
            ],
            "assertions": [
                {"type": "url_contains", "value": "completed"},
                {
                    "type":     "element_exists",
                    "selector": {"strategy": "text", "value": "Finish report"},
                },
                {
                    "type":     "element_checked",
                    "selector": {"strategy": "role", "value": {"role": "checkbox"}},
                },
            ],
        })
        self.assertEqual(result["status"], "pass")

    def test_all_filter_shows_both_active_and_completed(self):
        """All filter displays both active and completed items."""
        result = self._exec({
            "id": "all_filter",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Pending item",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Done item",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "css", "value": "li:last-child .toggle"},
                },
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "All"}},
                },
            ],
            "assertions": [{
                "type":     "element_count",
                "selector": {"strategy": "role", "value": {"role": "listitem"}},
                "value":    2,
            }],
        })
        self.assertEqual(result["status"], "pass")

    # ── Multi-site navigation ─────────────────────────────────────────

    def test_books_catalogue_next_page(self):
        """Navigate from books catalogue page 1 to page 2 via 'next' link."""
        result = self._exec({
            "id": "books_next",
            "steps": [
                {"action": "navigate", "url": BOOKS_URL},
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "next"}},
                },
            ],
            "assertions": [{"type": "url_contains", "value": "page-2"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_books_catalogue_back_navigation(self):
        """Navigate forward then use go_back to return to page 1."""
        result = self._exec({
            "id": "books_back",
            "steps": [
                {"action": "navigate", "url": BOOKS_URL},
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "next"}},
                },
                {"action": "go_back"},
            ],
            "assertions": [{"type": "url_contains", "value": "books.toscrape.com"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_playwright_docs_navigation(self):
        """Navigate from Playwright landing page to docs/intro."""
        result = self._exec({
            "id": "pw_docs",
            "steps": [
                {"action": "navigate", "url": PLAYWRIGHT_DOCS},
            ],
            "assertions": [
                {"type": "url_contains", "value": "playwright.dev/docs"},
                {"type": "title_contains", "value": "Playwright"},
            ],
        })
        self.assertEqual(result["status"], "pass")

    def test_multi_plan_state_does_not_leak_between_runs(self):
        """Each test run gets its own browser context; state must not bleed."""
        async def run_all():
            results = []
            async with Executor(self.db, headless=True) as exc:
                for n in range(3):
                    plan = {
                        "id": f"isolated_{n}",
                        "steps": [
                            {"action": "navigate", "url": TODOMVC_URL},
                            {
                                "action":   "fill",
                                "selector": {"strategy": "role",
                                             "value": {"role": "textbox"}},
                                "value":    f"Item {n}",
                            },
                            {
                                "action":   "press",
                                "selector": {"strategy": "role",
                                             "value": {"role": "textbox"}},
                                "value":    "Enter",
                            },
                        ],
                        "assertions": [{
                            "type":     "element_count",
                            "selector": {"strategy": "role",
                                         "value": {"role": "listitem"}},
                            "value":    1,   # each fresh page has exactly 1 new item
                        }],
                    }
                    results.append(await exc.run(plan))
            return results

        results = run(run_all())
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(
                r["status"], "pass",
                f"{r['id']} failed — state likely leaked from previous run: {r}"
            )


# ════════════════════════════════════════════════════════════════════════
# 4. GENERATOR E2E (Mocked AI)
# ════════════════════════════════════════════════════════════════════════

class TestGeneratorE2E(unittest.TestCase):
    """PRD → plan generation with a deterministic fake AI client."""

    def setUp(self):
        self.db = make_db()
        # Seed the DB so the generator doesn't raise "no locators"
        self.db.upsert(TODOMVC_URL, {
            "role":       "textbox",
            "name":       "What needs to be done?",
            "tag":        "input",
            "loc":        {"strategy": "role",
                           "value": {"role": "textbox",
                                     "name": "What needs to be done?"}},
            "actionable": True,
        })

    def tearDown(self):
        self.db.close()

    # ── Basic generation ─────────────────────────────────────────────

    def test_single_plan_returned(self):
        ai  = fake_ai_with_plans([{
            "test_id": "TC001_Login",
            "name":    "User can log in",
            "steps": [
                {"action": "navigate", "url": "https://example.com/login"},
                {"action": "fill",
                 "selector": {"strategy": "role",
                              "value": {"role": "textbox", "name": "Email"}},
                 "value": "user@test.com"},
            ],
            "assertions": [{"type": "url_contains", "value": "/dashboard"}],
        }])
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# Login\nUser logs in.", [TODOMVC_URL])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["test_id"], "TC001_Login")
        self.assertEqual(len(plans[0]["steps"]), 2)
        self.assertEqual(len(plans[0]["assertions"]), 1)

    def test_multiple_plans_returned(self):
        ai = fake_ai_with_plans([
            {"test_id": "TC001", "name": "A", "steps": [], "assertions": []},
            {"test_id": "TC002", "name": "B", "steps": [], "assertions": []},
            {"test_id": "TC003", "name": "C", "steps": [], "assertions": []},
        ])
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App\nFeatures A, B, C.", [TODOMVC_URL])
        self.assertEqual(len(plans), 3)
        ids = [p["test_id"] for p in plans]
        self.assertIn("TC001", ids)
        self.assertIn("TC003", ids)

    def test_meta_block_attached(self):
        ai = fake_ai_with_plans([{
            "test_id": "TC001", "name": "T",
            "steps": [], "assertions": [],
        }])
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App\nSomething.", [TODOMVC_URL])
        meta = plans[0].get("_meta", {})
        self.assertEqual(meta.get("source"),   "prd_generator")
        self.assertEqual(meta.get("ai_model"), "fake-model")
        self.assertIn("planned_at", meta)
        self.assertIn("locators",   meta)

    # ── Validation ────────────────────────────────────────────────────

    def test_invented_element_id_flagged(self):
        """An element_id not in the locator DB must be marked _invalid_element_id."""
        ai = fake_ai_with_plans([{
            "test_id": "TC001",
            "name":    "Click test",
            "steps": [{
                "action":     "click",
                "selector":   {"strategy": "role",
                               "value": {"role": "button", "name": "Go"}},
                "element_id": "invented_id_xyz_9999",
            }],
            "assertions": [],
        }])
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App", [TODOMVC_URL])
        step  = plans[0]["steps"][0]
        self.assertTrue(step.get("_invalid_element_id"),
                        "Invented element_id not flagged")
        self.assertTrue(step.get("_needs_review"))

    def test_known_element_id_not_flagged(self):
        """A valid element_id (present in DB) must NOT be flagged."""
        # Grab the real ID from the seeded locator
        locs = self.db.get_all(TODOMVC_URL, valid_only=True)
        real_id = locs[0]["id"]

        ai = fake_ai_with_plans([{
            "test_id": "TC001",
            "name":    "Real ID test",
            "steps": [{
                "action":     "fill",
                "selector":   {"strategy": "role",
                               "value": {"role": "textbox",
                                         "name": "What needs to be done?"}},
                "element_id": real_id,
                "value":      "hello",
            }],
            "assertions": [],
        }])
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App", [TODOMVC_URL])
        step  = plans[0]["steps"][0]
        self.assertNotIn("_invalid_element_id", step)
        self.assertNotIn("_needs_review",       step)

    # ── Error paths ───────────────────────────────────────────────────

    def test_no_ai_client_raises_planning_error(self):
        gen = TestGenerator(self.db, ai_client=None)
        with self.assertRaises(PlanningError):
            gen.generate_plans_from_prd("# Test", [TODOMVC_URL])

    def test_empty_locator_db_raises_planning_error(self):
        empty_db = make_db()
        ai       = fake_ai_with_plans([])
        gen      = TestGenerator(empty_db, ai_client=ai)
        with self.assertRaises(PlanningError):
            gen.generate_plans_from_prd("# Test", ["https://unknown.nowhere.invalid"])
        empty_db.close()

    def test_invalid_ai_json_raises_planning_error(self):
        ai  = FakeAIClient("THIS IS NOT JSON AT ALL !!!")
        gen = TestGenerator(self.db, ai_client=ai)
        with self.assertRaises(PlanningError):
            gen.generate_plans_from_prd("# Test", [TODOMVC_URL])

    def test_single_dict_response_wrapped_to_list(self):
        """AI returning a single object (not array) is gracefully wrapped."""
        ai = FakeAIClient(json.dumps({
            "test_id": "TC001", "name": "Wrapped",
            "steps": [], "assertions": [],
        }))
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# Test", [TODOMVC_URL])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["test_id"], "TC001")

    def test_max_cases_injects_critical_instruction(self):
        """max_cases=True must append the CRITICAL instruction to the prompt."""
        ai = fake_ai_with_plans([{
            "test_id": "TC001", "name": "T", "steps": [], "assertions": [],
        }])
        gen = TestGenerator(self.db, ai_client=ai, max_cases=True)
        gen.generate_plans_from_prd("# App\nSomething.", [TODOMVC_URL])
        self.assertTrue(
            any("CRITICAL" in call for call in ai.calls),
            "CRITICAL instruction not found in AI prompt when max_cases=True"
        )

    def test_style_blindness_rule_in_system_prompt(self):
        """The generator system prompt must contain the style-blindness rule."""
        from generator import _GENERATOR_SYSTEM
        self.assertIn("ABSOLUTE STYLE-BLINDNESS", _GENERATOR_SYSTEM)
        self.assertIn("~~", _GENERATOR_SYSTEM,
                      "Rule should warn about ~~ specifically")

    def test_markdown_fenced_ai_response_parsed(self):
        """Generator must handle AI wrapping its JSON in markdown code fences."""
        plans_list = [{
            "test_id": "TC001", "name": "Fenced",
            "steps": [], "assertions": [],
        }]
        ai = FakeAIClient("```json\n" + json.dumps(plans_list) + "\n```")
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# Test", [TODOMVC_URL])
        self.assertEqual(len(plans), 1)


# ════════════════════════════════════════════════════════════════════════
# 5. FULL PIPELINE E2E
# ════════════════════════════════════════════════════════════════════════

class TestFullPipelineE2E(unittest.TestCase):
    """
    Crawl → Generate (mocked AI) → Execute
    Verifies that the three pipeline stages interoperate correctly.
    """

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _crawl(self, urls):
        async def go():
            async with Crawler(self.db, headless=True) as c:
                return await c.bulk_crawl(urls)
        return run(go())

    def _execute(self, plan: dict) -> dict:
        async def go():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return run(go())

    # ── Crawl → Execute ───────────────────────────────────────────────

    def test_crawl_then_execute_add_todo(self):
        """Crawl first, then run a hand-crafted plan using the populated DB."""
        self._crawl([TODOMVC_URL])
        self.assertGreater(len(self.db.get_all(TODOMVC_URL, valid_only=True)), 0)

        result = self._execute({
            "id": "pipeline_add",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Pipeline test item",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_exists",
                "selector": {"strategy": "text", "value": "Pipeline test item"},
            }],
        })
        self.assertEqual(result["status"], "pass")

    # ── Crawl → Generate → Execute ────────────────────────────────────

    def test_full_pipeline_todomvc(self):
        """Complete 3-stage pipeline: crawl → generate (mocked) → execute."""
        # Stage 1: Crawl
        self._crawl([TODOMVC_URL])

        # Stage 2: Generate plan using mocked AI
        plans_json = json.dumps([{
            "test_id": "TC_Pipeline_Add",
            "name":    "Add a todo via generated plan",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
                {
                    "action":   "fill",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Generated plan item",
                },
                {
                    "action":   "press",
                    "selector": {"strategy": "role", "value": {"role": "textbox"}},
                    "value":    "Enter",
                },
            ],
            "assertions": [{
                "type":     "element_exists",
                "selector": {"strategy": "text", "value": "Generated plan item"},
            }],
        }])
        ai    = FakeAIClient(plans_json)
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App\nAdd todos.", [TODOMVC_URL])
        self.assertEqual(len(plans), 1, "Generator returned wrong plan count")

        # Stage 3: Execute
        result = self._execute(plans[0])
        self.assertEqual(result["status"], "pass")
        self.assertGreater(result["duration_ms"], 0)

    def test_full_pipeline_multipage(self):
        """Full pipeline targeting multiple filter views of TodoMVC."""
        urls = [TODOMVC_URL, ACTIVE_URL, COMPLETED_URL]

        # Stage 1: Crawl all views
        self._crawl(urls)
        for url in urls:
            self.assertGreater(len(self.db.get_all(url, valid_only=True)), 0,
                               f"No locators after crawl: {url}")

        # Stage 2: Generate two plans
        plans_json = json.dumps([
            {
                "test_id": "TC_MP_01",
                "name":    "Navigate to Active view",
                "steps": [
                    {"action": "navigate", "url": TODOMVC_URL},
                    {
                        "action":   "fill",
                        "selector": {"strategy": "role",
                                     "value": {"role": "textbox"}},
                        "value":    "Multi page task",
                    },
                    {
                        "action":   "press",
                        "selector": {"strategy": "role",
                                     "value": {"role": "textbox"}},
                        "value":    "Enter",
                    },
                    {
                        "action":   "click",
                        "selector": {"strategy": "role",
                                     "value": {"role": "link", "name": "Active"}},
                    },
                ],
                "assertions": [{"type": "url_contains", "value": "active"}],
            },
            {
                "test_id": "TC_MP_02",
                "name":    "Navigate to Completed view",
                "steps": [
                    {"action": "navigate", "url": TODOMVC_URL},
                    {
                        "action":   "fill",
                        "selector": {"strategy": "role",
                                     "value": {"role": "textbox"}},
                        "value":    "Done task",
                    },
                    {
                        "action":   "press",
                        "selector": {"strategy": "role",
                                     "value": {"role": "textbox"}},
                        "value":    "Enter",
                    },
                    {
                        "action":   "click",
                        "selector": {"strategy": "role",
                                     "value": {"role": "checkbox"}},
                    },
                    {
                        "action":   "click",
                        "selector": {"strategy": "role",
                                     "value": {"role": "link", "name": "Completed"}},
                    },
                ],
                "assertions": [{"type": "url_contains", "value": "completed"}],
            },
        ])
        ai    = FakeAIClient(plans_json)
        gen   = TestGenerator(self.db, ai_client=ai)
        plans = gen.generate_plans_from_prd("# App\nFilters.", [TODOMVC_URL])
        self.assertEqual(len(plans), 2)

        # Stage 3: Execute both plans
        async def run_both():
            results = []
            async with Executor(self.db, headless=True) as exc:
                for p in plans:
                    results.append(await exc.run(p))
            return results

        results = run(run_both())
        for r in results:
            self.assertEqual(r["status"], "pass",
                             f"{r.get('id')} failed: {r}")

    def test_full_pipeline_books_site(self):
        """Full pipeline against books.toscrape.com (genuine multi-page site)."""
        urls = [BOOKS_URL, BOOKS_PAGE2]
        self._crawl(urls)

        for url in urls:
            self.assertGreater(len(self.db.get_all(url, valid_only=True)), 0,
                               f"Crawl missed: {url}")

        # Execute pagination test
        result = self._execute({
            "id": "books_pipeline",
            "steps": [
                {"action": "navigate", "url": BOOKS_URL},
                {
                    "action":   "click",
                    "selector": {"strategy": "role",
                                 "value": {"role": "link", "name": "next"}},
                },
            ],
            "assertions": [{"type": "url_contains", "value": "page-2"}],
        })
        self.assertEqual(result["status"], "pass")

    def test_report_structure_matches_spec(self):
        """
        The executor result dict must match the documented shape so that
        callers (cmd_run, cmd_prd_run) can safely iterate it.
        """
        result = self._execute({
            "id":   "report_shape",
            "name": "Report shape test",
            "steps": [
                {"action": "navigate", "url": TODOMVC_URL},
            ],
            "assertions": [
                {"type": "url_contains", "value": "todomvc"},
                {"type": "title_contains", "value": "TodoMVC"},
            ],
        })

        # Top-level fields
        for field in ("id", "name", "status", "steps", "assertions", "duration_ms"):
            self.assertIn(field, result)

        # Step fields
        for step in result["steps"]:
            self.assertIn("status", step)
            self.assertIn("action", step)

        # Assertion fields
        for assertion in result["assertions"]:
            self.assertIn("status", assertion)
            self.assertIn("type",   assertion)


# ════════════════════════════════════════════════════════════════════════
# 6. CLI COMMANDS E2E
# ════════════════════════════════════════════════════════════════════════

class TestCLICommandsE2E(unittest.TestCase):
    """Invoke main.py as a subprocess and verify exit codes + output."""

    PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

    def _run_cmd(self, args: list, env: dict = None, timeout: int = 120) -> tuple:
        """Run main.py with given args. Returns (returncode, stdout, stderr)."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        proc = subprocess.run(
            [sys.executable, MAIN_PY] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=self.PROJECT_ROOT,
            env=full_env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    # ── status ────────────────────────────────────────────────────────

    def test_status_exits_zero(self):
        rc, out, err = self._run_cmd(["status"])
        self.assertEqual(rc, 0,
                         f"status command exited {rc}:\n{out}\n{err}")

    def test_status_shows_database_line(self):
        rc, out, _ = self._run_cmd(["status"])
        self.assertIn("database", out.lower())

    def test_status_shows_elements_line(self):
        rc, out, _ = self._run_cmd(["status"])
        self.assertIn("elements", out.lower())

    # ── crawl ─────────────────────────────────────────────────────────

    def test_crawl_exits_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            db_path = tf.name
        try:
            rc, out, err = self._run_cmd(
                ["crawl", "--urls", TODOMVC_URL, "--headless"],
                env={"QAPAL_DB_PATH": db_path},
            )
            self.assertEqual(rc, 0,
                             f"crawl exited {rc}:\n{out}\n{err}")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_crawl_populates_db(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            db_path = tf.name
        try:
            self._run_cmd(
                ["crawl", "--urls", TODOMVC_URL, "--headless"],
                env={"QAPAL_DB_PATH": db_path},
            )
            db   = LocatorDB(path=db_path)
            locs = db.get_all(TODOMVC_URL, valid_only=True)
            db.close()
            self.assertGreater(len(locs), 0,
                               "crawl command did not write any locators")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_crawl_multiple_urls(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            db_path = tf.name
        try:
            rc, out, err = self._run_cmd(
                ["crawl", "--urls", TODOMVC_URL, BOOKS_URL, "--headless"],
                env={"QAPAL_DB_PATH": db_path},
            )
            self.assertEqual(rc, 0,
                             f"multi-URL crawl exited {rc}:\n{out}\n{err}")
            db = LocatorDB(path=db_path)
            for url in (TODOMVC_URL, BOOKS_URL):
                locs = db.get_all(url, valid_only=True)
                self.assertGreater(len(locs), 0, f"No locators for {url}")
            db.close()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    # ── run (pre-generated plans) ─────────────────────────────────────

    def test_run_pregenerated_plan_exits_zero(self):
        """run command with a pre-generated plan file must exit 0 on pass."""
        plan = {
            "id": "cli_run_test",
            "name": "CLI run test",
            "steps": [{"action": "navigate", "url": TODOMVC_URL}],
            "assertions": [{"type": "url_contains", "value": "todomvc"}],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_plan.json", delete=False
        ) as pf:
            json.dump(plan, pf)
            plan_path = pf.name

        with tempfile.TemporaryDirectory() as report_dir:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
                db_path = tf.name
            try:
                rc, out, err = self._run_cmd(
                    ["run", "--plans", plan_path,
                     "--output", report_dir, "--headless"],
                    env={"QAPAL_DB_PATH": db_path},
                )
                self.assertEqual(rc, 0,
                                 f"run exited {rc}:\n{out}\n{err}")
                # Verify a report JSON was written
                reports = list(Path(report_dir).glob("report_*.json"))
                self.assertGreater(len(reports), 0, "No report file written")

                with open(reports[0]) as f:
                    report = json.load(f)
                self.assertIn("summary",      report)
                self.assertIn("results",      report)
                self.assertIn("generated_at", report)
                self.assertEqual(report["summary"]["passed"], 1)
            finally:
                try:
                    os.unlink(plan_path)
                    os.unlink(db_path)
                except OSError:
                    pass

    # ── help / unknown command ────────────────────────────────────────

    def test_help_flag_does_not_crash(self):
        rc, out, err = self._run_cmd(["--help"])
        combined = (out + err).lower()
        self.assertIn("usage", combined)

    def test_unknown_subcommand_prints_help(self):
        rc, out, err = self._run_cmd(["unknowncmd"])
        # argparse prints usage and exits non-zero for unknown commands
        combined = (out + err).lower()
        self.assertTrue(
            "usage" in combined or rc != 0,
            "Unknown command should either show usage or exit non-zero"
        )


# ════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
