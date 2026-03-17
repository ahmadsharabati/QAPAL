"""
test_stress.py — Enterprise stress tests on REAL production sites.

Tests QAPal against large, complex, never-before-tested production websites
to validate the framework works universally — not just on our usual test sites.

Sites chosen for maximum diversity:
  - Wikipedia.org         — Massive DOM, heavy text, multi-language, SSR
  - Reddit.com            — React SPA, infinite scroll, auth walls, dynamic IDs
  - Stack Overflow        — Enterprise Q&A, code blocks, voting UI, tags
  - Amazon.com            — Mega e-commerce, A/B testing, anti-bot, huge catalog
  - LinkedIn.com          — Enterprise SPA, auth redirect, complex navigation
  - YouTube.com           — Video SPA, lazy load, shadow DOM, web components
  - MDN Web Docs          — Developer docs, sidebar nav, code samples
  - Hacker News           — Minimal HTML, table layout, no framework
  - Airbnb.com            — Heavy SPA, map integration, date pickers
  - NYTimes.com           — Media site, paywall, complex article layout

All tests run headless with NO credentials.  Network required.
"""

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from locator_db import LocatorDB
from crawler import Crawler
from executor import Executor
from _tokens import get_token_tracker


# ── Mixin ─────────────────────────────────────────────────────────────

class _M:
    def setUp(self):
        self.db = LocatorDB(":memory:")
        get_token_tracker().reset()

    def tearDown(self):
        self.db.close()

    def _exec(self, plan: dict) -> dict:
        async def _r():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return asyncio.run(_r())

    def _crawl(self, url: str) -> list:
        async def _r():
            async with Crawler(self.db, headless=True) as c:
                return await c.bulk_crawl([url])
        return asyncio.run(_r())

    def _assert_clean(self, result, msg=""):
        """Result must be well-formed pass or fail — never crash."""
        self.assertIsInstance(result, dict, msg)
        self.assertIn("status", result, msg)
        self.assertIn(result["status"], ("pass", "fail"), msg)
        self.assertIn("duration_ms", result, msg)
        self.assertIn("steps", result, msg)
        self.assertIn("assertions", result, msg)


# ═══════════════════════════════════════════════════════════════════════
# 1. WIKIPEDIA — Massive server-rendered DOM, multi-language
# ═══════════════════════════════════════════════════════════════════════

class TestWikipedia(_M, unittest.TestCase):
    """Wikipedia: SSR, massive text DOM, multilingual, MediaWiki engine."""

    URL = "https://en.wikipedia.org/wiki/Main_Page"

    def test_crawl_wikipedia_homepage(self):
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].get("elements", 0), 20,
                           "Wikipedia homepage has hundreds of links/elements")

    def test_navigate_and_title(self):
        r = self._exec({
            "test_id": "wiki_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "title_contains", "value": "Wikipedia"}],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_search_input_exists(self):
        """Wikipedia's search box should be discoverable."""
        r = self._exec({
            "test_id": "wiki_search",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{
                "type": "element_exists",
                "selector": {"strategy": "css", "value": "#searchInput, input[name='search']"},
            }],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_fill_search_and_submit(self):
        """Type into Wikipedia search and press Enter."""
        r = self._exec({
            "test_id": "wiki_search_submit",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "fill",
                 "selector": {"strategy": "css", "value": "#searchInput, input[name='search']"},
                 "value": "Playwright testing"},
                {"action": "press",
                 "selector": {"strategy": "css", "value": "#searchInput, input[name='search']"},
                 "key": "Enter"},
                {"action": "wait", "duration": 2000},
            ],
            "assertions": [{"type": "url_contains", "value": "Playwright"}],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_navigate_to_article(self):
        """Navigate directly to a Wikipedia article."""
        r = self._exec({
            "test_id": "wiki_article",
            "steps": [{"action": "navigate",
                        "url": "https://en.wikipedia.org/wiki/Software_testing"}],
            "assertions": [
                {"type": "title_contains", "value": "Software testing"},
                {"type": "element_exists",
                 "selector": {"strategy": "css", "value": "#mw-content-text"}},
            ],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_multi_page_navigation(self):
        """Navigate across two Wikipedia articles."""
        r = self._exec({
            "test_id": "wiki_multi",
            "steps": [
                {"action": "navigate", "url": "https://en.wikipedia.org/wiki/Python_(programming_language)"},
                {"action": "navigate", "url": "https://en.wikipedia.org/wiki/Selenium_(software)"},
            ],
            "assertions": [{"type": "url_contains", "value": "Selenium"}],
        })
        self.assertEqual(r["status"], "pass")


# ═══════════════════════════════════════════════════════════════════════
# 2. REDDIT — React SPA, infinite scroll, dynamic IDs
# ═══════════════════════════════════════════════════════════════════════

class TestReddit(_M, unittest.TestCase):
    """Reddit: React SPA, infinite scroll, aggressive anti-bot, dynamic DOM."""

    URL = "https://www.reddit.com"

    def test_crawl_reddit(self):
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        r = results[0]
        # Reddit may block or serve limited content — just don't crash
        self.assertIn("elements", r)

    def test_navigate_and_url(self):
        r = self._exec({
            "test_id": "reddit_nav",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "reddit"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_subreddit_navigation(self):
        """Navigate to a specific subreddit."""
        r = self._exec({
            "test_id": "reddit_sub",
            "steps": [{"action": "navigate",
                        "url": "https://www.reddit.com/r/programming/"}],
            "assertions": [{"type": "url_contains", "value": "programming"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_scroll_infinite_feed(self):
        """Scroll down the infinite feed — should not crash."""
        r = self._exec({
            "test_id": "reddit_scroll",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "scroll", "direction": "down", "amount": 500},
                {"action": "scroll", "direction": "down", "amount": 500},
                {"action": "scroll", "direction": "down", "amount": 500},
            ],
            "assertions": [{"type": "url_contains", "value": "reddit"}],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 3. STACK OVERFLOW — Enterprise Q&A, code blocks, tags
# ═══════════════════════════════════════════════════════════════════════

class TestStackOverflow(_M, unittest.TestCase):
    """Stack Overflow: jQuery+Stacks, code blocks, voting UI, tags."""

    URL = "https://stackoverflow.com"

    def test_crawl_stackoverflow(self):
        """SO may show Cloudflare challenge — crawler must not crash."""
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        # Cloudflare may block, yielding very few elements
        self.assertIn("elements", results[0])

    def test_navigate_and_title(self):
        """SO title may be 'Just a moment...' if Cloudflare blocks."""
        r = self._exec({
            "test_id": "so_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "stackoverflow"}],
        })
        self._assert_clean(r)

    def test_navigate_to_question_page(self):
        """Navigate to the questions listing page."""
        r = self._exec({
            "test_id": "so_questions",
            "steps": [{"action": "navigate",
                        "url": "https://stackoverflow.com/questions"}],
            "assertions": [
                {"type": "url_contains", "value": "questions"},
                {"type": "element_exists",
                 "selector": {"strategy": "css", "value": "#questions, .question-summary, [data-searchsession]"}},
            ],
        })
        self._assert_clean(r)

    def test_navigate_to_tags(self):
        r = self._exec({
            "test_id": "so_tags",
            "steps": [{"action": "navigate",
                        "url": "https://stackoverflow.com/tags"}],
            "assertions": [{"type": "url_contains", "value": "tags"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_screenshot_on_so(self):
        r = self._exec({
            "test_id": "so_screenshot",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "screenshot"},
            ],
            "assertions": [],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 4. AMAZON — Mega e-commerce, A/B testing, anti-bot
# ═══════════════════════════════════════════════════════════════════════

class TestAmazon(_M, unittest.TestCase):
    """Amazon: Massive e-commerce, captcha risk, A/B testing, huge DOM."""

    URL = "https://www.amazon.com"

    def test_crawl_amazon(self):
        """Amazon may block bots — crawler must not crash regardless."""
        try:
            results = self._crawl(self.URL)
            self.assertEqual(len(results), 1)
        except Exception as e:
            self.fail(f"Crawler crashed on Amazon: {e}")

    def test_navigate_amazon(self):
        """Navigate to Amazon — may redirect to captcha, must not crash."""
        r = self._exec({
            "test_id": "amazon_nav",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "amazon"}],
        })
        self._assert_clean(r)

    def test_search_product(self):
        """Fill Amazon search box and submit."""
        r = self._exec({
            "test_id": "amazon_search",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "fill",
                 "selector": {"strategy": "css", "value": "#twotabsearchtextbox, input[name='field-keywords']"},
                 "value": "mechanical keyboard"},
                {"action": "press",
                 "selector": {"strategy": "css", "value": "#twotabsearchtextbox, input[name='field-keywords']"},
                 "key": "Enter"},
                {"action": "wait", "duration": 2000},
            ],
            "assertions": [{"type": "url_contains", "value": "keyboard"}],
        })
        # Amazon may captcha-block — both pass and fail are acceptable
        self._assert_clean(r, "Amazon search must return clean result")

    def test_navigate_product_category(self):
        r = self._exec({
            "test_id": "amazon_cat",
            "steps": [{"action": "navigate",
                        "url": "https://www.amazon.com/gp/bestsellers/"}],
            "assertions": [{"type": "url_contains", "value": "amazon"}],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 5. YOUTUBE — Video SPA, shadow DOM, web components, lazy load
# ═══════════════════════════════════════════════════════════════════════

class TestYouTube(_M, unittest.TestCase):
    """YouTube: Polymer/Lit web components, shadow DOM, heavy lazy loading."""

    URL = "https://www.youtube.com"

    def test_crawl_youtube(self):
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        # YouTube uses shadow DOM heavily — element count may vary
        self.assertIn("elements", results[0])

    def test_navigate_and_title(self):
        r = self._exec({
            "test_id": "yt_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "title_contains", "value": "YouTube"}],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_navigate_trending(self):
        """YouTube may redirect /feed/trending — must not crash."""
        r = self._exec({
            "test_id": "yt_trending",
            "steps": [{"action": "navigate",
                        "url": "https://www.youtube.com/feed/trending"}],
            "assertions": [{"type": "url_contains", "value": "youtube"}],
        })
        self._assert_clean(r)

    def test_scroll_video_feed(self):
        """Scroll YouTube's lazy-loading video feed."""
        r = self._exec({
            "test_id": "yt_scroll",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "scroll", "direction": "down", "amount": 800},
                {"action": "wait", "duration": 1000},
                {"action": "scroll", "direction": "down", "amount": 800},
            ],
            "assertions": [],
        })
        self._assert_clean(r)

    def test_js_assertion_on_youtube(self):
        """Run JavaScript assertion on YouTube's complex SPA."""
        r = self._exec({
            "test_id": "yt_js",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{
                "type": "javascript",
                "script": "document.querySelector('ytd-app') !== null || document.title.includes('YouTube')",
                "expected": True,
            }],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 6. MDN WEB DOCS — Developer documentation, sidebar nav, code samples
# ═══════════════════════════════════════════════════════════════════════

class TestMDN(_M, unittest.TestCase):
    """MDN: Yari-based docs, interactive examples, deep sidebar nav."""

    URL = "https://developer.mozilla.org/en-US/"

    def test_crawl_mdn(self):
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].get("elements", 0), 10)

    def test_navigate_and_title(self):
        r = self._exec({
            "test_id": "mdn_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "title_contains", "value": "MDN"}],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_navigate_to_js_reference(self):
        r = self._exec({
            "test_id": "mdn_js_ref",
            "steps": [{"action": "navigate",
                        "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference"}],
            "assertions": [
                {"type": "url_contains", "value": "JavaScript"},
                {"type": "title_contains", "value": "JavaScript"},
            ],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_deep_page_navigation(self):
        """Navigate 3 levels deep into MDN docs."""
        r = self._exec({
            "test_id": "mdn_deep",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "navigate",
                 "url": "https://developer.mozilla.org/en-US/docs/Web/API"},
                {"action": "navigate",
                 "url": "https://developer.mozilla.org/en-US/docs/Web/API/Document"},
            ],
            "assertions": [{"type": "url_contains", "value": "Document"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_go_back_across_mdn_pages(self):
        r = self._exec({
            "test_id": "mdn_back",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "navigate",
                 "url": "https://developer.mozilla.org/en-US/docs/Web/CSS"},
                {"action": "go_back"},
            ],
            "assertions": [{"type": "url_contains", "value": "developer.mozilla.org"}],
        })
        self.assertEqual(r["status"], "pass")


# ═══════════════════════════════════════════════════════════════════════
# 7. HACKER NEWS — Minimal HTML, table layout, no framework
# ═══════════════════════════════════════════════════════════════════════

class TestHackerNews(_M, unittest.TestCase):
    """Hacker News: Raw HTML tables, no JS framework, minimal CSS."""

    URL = "https://news.ycombinator.com"

    def test_crawl_hackernews(self):
        results = self._crawl(self.URL)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].get("elements", 0), 30,
                           "HN has 30+ story links")

    def test_navigate_and_title(self):
        r = self._exec({
            "test_id": "hn_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "title_contains", "value": "Hacker News"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_element_count_story_links(self):
        """HN front page should have ~30 story links."""
        r = self._exec({
            "test_id": "hn_count",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{
                "type": "element_count",
                "selector": {"strategy": "css", "value": ".titleline > a"},
                "count": 20,
                "operator": "at_least",
            }],
        })
        self.assertEqual(r["status"], "pass", r.get("assertions"))

    def test_navigate_to_show_hn(self):
        r = self._exec({
            "test_id": "hn_show",
            "steps": [{"action": "navigate",
                        "url": "https://news.ycombinator.com/show"}],
            "assertions": [{"type": "url_contains", "value": "show"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_click_more_link(self):
        """Click the 'More' link at bottom of HN."""
        r = self._exec({
            "test_id": "hn_more",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "click",
                 "selector": {"strategy": "css", "value": "a.morelink"}},
                {"action": "wait", "duration": 1000},
            ],
            "assertions": [{"type": "url_contains", "value": "p=2"}],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 8. AIRBNB — Heavy SPA, maps, date pickers, complex UI
# ═══════════════════════════════════════════════════════════════════════

class TestAirbnb(_M, unittest.TestCase):
    """Airbnb: Next.js SPA, Google Maps, complex form elements."""

    URL = "https://www.airbnb.com"

    def test_crawl_airbnb(self):
        try:
            results = self._crawl(self.URL)
            self.assertEqual(len(results), 1)
        except Exception as e:
            self.fail(f"Crawler crashed on Airbnb: {e}")

    def test_navigate_and_url(self):
        r = self._exec({
            "test_id": "airbnb_nav",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "airbnb"}],
        })
        self._assert_clean(r)

    def test_navigate_experiences(self):
        r = self._exec({
            "test_id": "airbnb_exp",
            "steps": [{"action": "navigate",
                        "url": "https://www.airbnb.com/s/experiences"}],
            "assertions": [{"type": "url_contains", "value": "airbnb"}],
        })
        self._assert_clean(r)

    def test_scroll_listings(self):
        r = self._exec({
            "test_id": "airbnb_scroll",
            "steps": [
                {"action": "navigate", "url": self.URL},
                {"action": "scroll", "direction": "down", "amount": 600},
                {"action": "scroll", "direction": "down", "amount": 600},
            ],
            "assertions": [],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 9. NYTIMES — Media, paywall, complex article layout
# ═══════════════════════════════════════════════════════════════════════

class TestNYTimes(_M, unittest.TestCase):
    """NYTimes: Paywall, ads, complex article layout, large images."""

    URL = "https://www.nytimes.com"

    def test_crawl_nytimes(self):
        try:
            results = self._crawl(self.URL)
            self.assertEqual(len(results), 1)
        except Exception as e:
            self.fail(f"Crawler crashed on NYTimes: {e}")

    def test_navigate_and_url(self):
        r = self._exec({
            "test_id": "nyt_nav",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "nytimes"}],
        })
        self._assert_clean(r)

    def test_title_contains_new_york(self):
        r = self._exec({
            "test_id": "nyt_title",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "title_contains", "value": "New York Times"}],
        })
        # May redirect to login/consent — both outcomes fine
        self._assert_clean(r)

    def test_navigate_to_section(self):
        r = self._exec({
            "test_id": "nyt_tech",
            "steps": [{"action": "navigate",
                        "url": "https://www.nytimes.com/section/technology"}],
            "assertions": [{"type": "url_contains", "value": "technology"}],
        })
        self._assert_clean(r)


# ═══════════════════════════════════════════════════════════════════════
# 10. LINKEDIN — Enterprise auth-wall SPA
# ═══════════════════════════════════════════════════════════════════════

class TestLinkedIn(_M, unittest.TestCase):
    """LinkedIn: Enterprise SPA, aggressive auth redirect, anti-bot."""

    URL = "https://www.linkedin.com"

    def test_navigate_linkedin(self):
        """LinkedIn redirects to login — executor must not crash."""
        r = self._exec({
            "test_id": "li_nav",
            "steps": [{"action": "navigate", "url": self.URL}],
            "assertions": [{"type": "url_contains", "value": "linkedin"}],
        })
        self._assert_clean(r)

    def test_crawl_linkedin(self):
        """LinkedIn may block — crawler must handle gracefully."""
        try:
            results = self._crawl(self.URL)
            self.assertEqual(len(results), 1)
        except Exception as e:
            self.fail(f"Crawler crashed on LinkedIn: {e}")


# ═══════════════════════════════════════════════════════════════════════
# 11. CROSS-SITE STRESS: parallel + sequential multi-site
# ═══════════════════════════════════════════════════════════════════════

class TestCrossSiteStress(_M, unittest.TestCase):
    """Cross-site robustness: concurrent, sequential, rapid switching."""

    def test_five_sites_sequential(self):
        """Navigate to 5 completely different sites in one session."""
        sites = [
            ("https://en.wikipedia.org", "wikipedia"),
            ("https://news.ycombinator.com", "ycombinator"),
            ("https://developer.mozilla.org", "mozilla"),
            ("https://stackoverflow.com", "stackoverflow"),
            ("https://www.youtube.com", "youtube"),
        ]
        for url, keyword in sites:
            with self.subTest(site=keyword):
                r = self._exec({
                    "test_id": f"seq_{keyword}",
                    "steps": [{"action": "navigate", "url": url}],
                    "assertions": [{"type": "url_contains", "value": keyword}],
                })
                self._assert_clean(r, f"Sequential nav to {keyword}")

    def test_three_sites_parallel(self):
        """Three executor instances running in parallel threads."""
        sites = [
            ("wiki", "https://en.wikipedia.org", "wikipedia"),
            ("hn", "https://news.ycombinator.com", "ycombinator"),
            ("mdn", "https://developer.mozilla.org", "mozilla"),
        ]
        results = {}
        errors = []

        def _run(name, url, keyword):
            try:
                db = LocatorDB(":memory:")
                async def _go():
                    async with Executor(db, headless=True) as exc:
                        return await exc.run({
                            "test_id": f"par_{name}",
                            "steps": [{"action": "navigate", "url": url}],
                            "assertions": [{"type": "url_contains", "value": keyword}],
                        })
                results[name] = asyncio.run(_go())
                db.close()
            except Exception as e:
                errors.append((name, e))

        threads = [threading.Thread(target=_run, args=s) for s in sites]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Parallel errors: {errors}")
        for name, _, _ in sites:
            self.assertIn(name, results)
            self.assertIn(results[name]["status"], ("pass", "fail"))

    def test_rapid_site_switching(self):
        """Navigate between 3 different sites within a single plan."""
        r = self._exec({
            "test_id": "rapid_switch",
            "steps": [
                {"action": "navigate", "url": "https://en.wikipedia.org"},
                {"action": "navigate", "url": "https://news.ycombinator.com"},
                {"action": "navigate", "url": "https://developer.mozilla.org"},
            ],
            "assertions": [{"type": "url_contains", "value": "mozilla"}],
        })
        self.assertEqual(r["status"], "pass")

    def test_go_back_across_sites(self):
        """go_back from MDN to HN after cross-site navigation."""
        r = self._exec({
            "test_id": "cross_back",
            "steps": [
                {"action": "navigate", "url": "https://news.ycombinator.com"},
                {"action": "navigate", "url": "https://developer.mozilla.org"},
                {"action": "go_back"},
            ],
            "assertions": [{"type": "url_contains", "value": "ycombinator"}],
        })
        self.assertEqual(r["status"], "pass")


# ═══════════════════════════════════════════════════════════════════════
# 12. EXTREME EDGE CASES on enterprise sites
# ═══════════════════════════════════════════════════════════════════════

class TestExtremeEdgeCases(_M, unittest.TestCase):
    """Edge cases that only surface on complex production sites."""

    def test_missing_element_on_wikipedia(self):
        """Click a nonexistent element on a huge DOM — must fail cleanly."""
        r = self._exec({
            "test_id": "wiki_missing",
            "steps": [
                {"action": "navigate", "url": "https://en.wikipedia.org"},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "qapal-nonexistent-xyz"}},
            ],
            "assertions": [],
        })
        self.assertEqual(r["status"], "fail")
        fail_step = next((s for s in r.get("steps", []) if s.get("status") == "fail"), None)
        self.assertIsNotNone(fail_step)

    def test_invalid_css_on_youtube(self):
        """Broken CSS selector on YouTube's shadow DOM — no crash."""
        r = self._exec({
            "test_id": "yt_bad_css",
            "steps": [
                {"action": "navigate", "url": "https://www.youtube.com"},
                {"action": "click",
                 "selector": {"strategy": "css", "value": "div[data>>broken"}},
            ],
            "assertions": [],
        })
        self.assertEqual(r["status"], "fail")

    def test_js_eval_on_stackoverflow(self):
        """Execute JS on Stack Overflow's jQuery-based page."""
        r = self._exec({
            "test_id": "so_eval",
            "steps": [
                {"action": "navigate", "url": "https://stackoverflow.com"},
                {"action": "evaluate", "script": "document.querySelectorAll('a').length"},
            ],
            "assertions": [{
                "type": "javascript",
                "script": "document.querySelectorAll('a').length > 10",
                "expected": True,
            }],
        })
        self._assert_clean(r)

    def test_screenshot_on_nytimes(self):
        """Screenshot a complex media page — must not crash."""
        r = self._exec({
            "test_id": "nyt_screenshot",
            "steps": [
                {"action": "navigate", "url": "https://www.nytimes.com"},
                {"action": "screenshot"},
            ],
            "assertions": [],
        })
        self._assert_clean(r)

    def test_element_in_viewport_on_hackernews(self):
        """Viewport assertion on HN's table-based layout."""
        r = self._exec({
            "test_id": "hn_viewport",
            "steps": [{"action": "navigate", "url": "https://news.ycombinator.com"}],
            "assertions": [{
                "type": "element_in_viewport",
                "selector": {"strategy": "css", "value": ".hnname a, #hnmain"},
                "ratio": 0.3,
            }],
        })
        self._assert_clean(r)

    def test_refresh_on_reddit(self):
        """Refresh Reddit's SPA — should reload cleanly."""
        r = self._exec({
            "test_id": "reddit_refresh",
            "steps": [
                {"action": "navigate", "url": "https://www.reddit.com"},
                {"action": "refresh"},
            ],
            "assertions": [{"type": "url_contains", "value": "reddit"}],
        })
        self._assert_clean(r)

    def test_hover_on_amazon(self):
        """Hover over a nav element on Amazon."""
        r = self._exec({
            "test_id": "amazon_hover",
            "steps": [
                {"action": "navigate", "url": "https://www.amazon.com"},
                {"action": "hover",
                 "selector": {"strategy": "css", "value": "#nav-link-accountList, #nav-hamburger-menu"}},
            ],
            "assertions": [],
        })
        self._assert_clean(r)

    def test_empty_plan_on_enterprise_db(self):
        """Empty plan after crawling an enterprise site — must pass."""
        self._crawl("https://news.ycombinator.com")
        r = self._exec({"test_id": "empty_enterprise", "steps": [], "assertions": []})
        self.assertEqual(r["status"], "pass")


# ── Runner ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
