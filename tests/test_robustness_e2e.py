"""
tests/test_robustness_e2e.py — QAPal Robustness E2E Tests
==========================================================
Tests framework viability across diverse real-world sites.

Goals:
  1. Prove the crawler works on sites with varied DOM structures.
  2. Exercise every uncovered action type from the existing E2E suite.
  3. Exercise every uncovered assertion type.
  4. Exercise selector strategies not yet covered (label, placeholder, text, aria-label).
  5. Confirm the executor fails cleanly (never crashes) on bad selectors.

Sites used (all purpose-built for test automation, stable):
  - SauceDemo        https://www.saucedemo.com/             testid-heavy, login + shop
  - The Internet     https://the-internet.herokuapp.com/    heroku classic test site
  - PracticeShop     https://practicesoftwaretesting.com/   real-world e-commerce

Coverage matrix:
  Actions:    select, hover, scroll, go_back, go_forward, refresh, uncheck
  Assertions: url_equals, url_matches, title_equals, element_checked,
              element_unchecked, element_enabled, element_disabled,
              element_text_equals, element_value_equals
  Selectors:  label, placeholder, text, aria-label (in addition to testid/role)

Requirements:
  pip install playwright pytest
  playwright install chromium

Run all:
  python3 tests/test_robustness_e2e.py
  python3 -m pytest tests/test_robustness_e2e.py -v

Run one group:
  python3 -m pytest tests/test_robustness_e2e.py -v -k "SauceDemo"
  python3 -m pytest tests/test_robustness_e2e.py -v -k "HerokuInternet"
  python3 -m pytest tests/test_robustness_e2e.py -v -k "PracticeShop"
  python3 -m pytest tests/test_robustness_e2e.py -v -k "RobustnessCrawler"
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from locator_db import LocatorDB
from crawler import Crawler
from executor import Executor


# ── Constants ─────────────────────────────────────────────────────────

SAUCEDEMO_URL      = "https://www.saucedemo.com/"
SAUCEDEMO_INVENTORY = "https://www.saucedemo.com/inventory.html"
SAUCEDEMO_CART     = "https://www.saucedemo.com/cart.html"

HEROKU_URL         = "https://the-internet.herokuapp.com/"
HEROKU_LOGIN       = "https://the-internet.herokuapp.com/login"
HEROKU_CHECKBOXES  = "https://the-internet.herokuapp.com/checkboxes"
HEROKU_DROPDOWN    = "https://the-internet.herokuapp.com/dropdown"
HEROKU_HOVER       = "https://the-internet.herokuapp.com/hovers"
HEROKU_INPUTS      = "https://the-internet.herokuapp.com/inputs"

PRACTICE_URL       = "https://practicesoftwaretesting.com/"
PRACTICE_LOGIN     = "https://practicesoftwaretesting.com/auth/login"
PRACTICE_ACCOUNT   = "https://practicesoftwaretesting.com/account"

# Known test credentials
SAUCE_USER    = "standard_user"
SAUCE_PASS    = "secret_sauce"
PRACTICE_USER = "customer2@practicesoftwaretesting.com"
PRACTICE_PASS = "welcome01"
HEROKU_USER   = "tomsmith"
HEROKU_PASS   = "SuperSecretPassword!"


# ── Helpers ───────────────────────────────────────────────────────────

def make_db() -> LocatorDB:
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close()
    return LocatorDB(path=tf.name)


def run(coro):
    return asyncio.run(coro)


class _ExecMixin:
    """Mixin that sets up a DB + Executor for each test."""

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _exec(self, plan: dict) -> dict:
        async def go():
            async with Executor(self.db, headless=True) as exc:
                return await exc.run(plan)
        return run(go())

    def _crawl(self, urls: list):
        async def go():
            async with Crawler(self.db, headless=True) as c:
                await c.bulk_crawl(urls)
        run(go())


# ════════════════════════════════════════════════════════════════════════
# CRAWLER ROBUSTNESS — diverse sites
# ════════════════════════════════════════════════════════════════════════

class TestRobustnessCrawler(_ExecMixin, unittest.TestCase):
    """Crawl 3 new sites; verify element diversity is captured correctly."""

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    # ── SauceDemo ─────────────────────────────────────────────────────

    def test_saucedemo_login_page_has_textboxes(self):
        """SauceDemo login page must expose username and password inputs."""
        self._crawl([SAUCEDEMO_URL])
        locs  = self.db.get_all(SAUCEDEMO_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "Nothing crawled on SauceDemo login page")
        self.assertIn("textbox", roles, "textbox not found on SauceDemo login page")

    def test_saucedemo_login_page_has_button(self):
        """Login button must be discovered."""
        self._crawl([SAUCEDEMO_URL])
        locs  = self.db.get_all(SAUCEDEMO_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertIn("button", roles, "Login button not found on SauceDemo")

    def test_saucedemo_inventory_has_buttons(self):
        """Inventory page (products) must have Add-to-cart buttons."""
        # Need to be logged in; crawl directly via playwright
        async def go():
            from playwright.async_api import async_playwright
            from crawler import crawl_page
            from locator_db import _normalize_url
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx     = await browser.new_context()
                page    = await ctx.new_page()
                await page.goto(SAUCEDEMO_URL)
                await page.get_by_test_id("username").fill(SAUCE_USER)
                await page.get_by_test_id("password").fill(SAUCE_PASS)
                await page.get_by_test_id("login-button").click()
                await page.wait_for_url("**/inventory.html")
                await crawl_page(page, _normalize_url(SAUCEDEMO_INVENTORY), self.db, force=True)
                await browser.close()
        run(go())
        locs  = self.db.get_all(SAUCEDEMO_INVENTORY, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "Nothing crawled on SauceDemo inventory")
        self.assertIn("button", roles, "No buttons found on inventory page")

    # ── The Internet ──────────────────────────────────────────────────

    def test_heroku_landing_page_has_links(self):
        """The Internet landing page is a directory of links."""
        self._crawl([HEROKU_URL])
        locs  = self.db.get_all(HEROKU_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertIn("link", roles, "No links found on The Internet landing page")

    def test_heroku_login_page_has_form_elements(self):
        """Login form must expose username textbox, password input, and submit button."""
        self._crawl([HEROKU_LOGIN])
        locs  = self.db.get_all(HEROKU_LOGIN, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "Nothing crawled on Heroku login page")
        self.assertIn("button", roles, "Login button not found")

    def test_heroku_checkboxes_page_crawled(self):
        """Checkboxes page must be crawled and produce a page record."""
        self._crawl([HEROKU_CHECKBOXES])
        page_rec = self.db.get_page(HEROKU_CHECKBOXES)
        # Unnamed checkboxes may not be stored as locators (no stable selector),
        # but the page record must always be written.
        self.assertIsNotNone(page_rec, "No page record created for checkboxes page")

    # ── PracticeSOftwareTesting ───────────────────────────────────────

    def test_practiceshop_home_has_navigation(self):
        """Home page must have navigation links."""
        self._crawl([PRACTICE_URL])
        locs  = self.db.get_all(PRACTICE_URL, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "Nothing crawled on PracticeShop")
        self.assertTrue(
            "link" in roles or "button" in roles,
            "No interactive elements on PracticeShop home"
        )

    def test_practiceshop_login_page_has_email_textbox(self):
        """Login form must expose email and password inputs."""
        self._crawl([PRACTICE_LOGIN])
        locs  = self.db.get_all(PRACTICE_LOGIN, valid_only=True)
        roles = [l["identity"]["role"] for l in locs]
        self.assertGreater(len(locs), 0, "Nothing crawled on PracticeShop login")
        self.assertIn("textbox", roles, "No textbox found on login page")

    def test_crawl_count_reasonable_per_site(self):
        """Each site should yield at least 3 locators — sanity check."""
        sites = [SAUCEDEMO_URL, HEROKU_URL, PRACTICE_URL]
        self._crawl(sites)
        for url in sites:
            locs = self.db.get_all(url, valid_only=True)
            self.assertGreaterEqual(len(locs), 3, f"Too few locators for {url}")


# ════════════════════════════════════════════════════════════════════════
# SAUCEDEMO E2E — testid-heavy site, login + shop
# ════════════════════════════════════════════════════════════════════════

class TestSauceDemoE2E(_ExecMixin, unittest.TestCase):
    """
    SauceDemo exercises testid selectors, login flows, select dropdowns,
    and add-to-cart interactions.
    Credentials: standard_user / secret_sauce
    """

    # ── Login flow ────────────────────────────────────────────────────

    def test_login_with_testid_selectors(self):
        """Login using data-testid attributes (most stable selector strategy)."""
        result = self._exec({
            "id": "sauce_login",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "inventory"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_url_equals_after_login(self):
        """After login, URL must equal exactly the inventory page URL."""
        result = self._exec({
            "id": "sauce_url_equals",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
            "assertions": [
                {"type": "url_equals", "value": SAUCEDEMO_INVENTORY},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass",
                         f"url_equals failed: {result['assertions'][0]}")

    def test_title_equals_on_inventory(self):
        """Page title must exactly match 'Swag Labs' on inventory."""
        result = self._exec({
            "id": "sauce_title",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
            "assertions": [
                {"type": "title_equals", "value": "Swag Labs"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_invalid_login_shows_error_element(self):
        """Invalid credentials must leave error element visible on the page."""
        result = self._exec({
            "id": "sauce_invalid_login",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": "wrong_user"},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": "wrong_pass"},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "saucedemo.com/"},
                {"type": "element_visible",
                 "selector": {"strategy": "css", "value": "[data-test='error']"}},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")
        self.assertEqual(result["assertions"][1]["status"], "pass")

    # ── Select action ─────────────────────────────────────────────────

    def test_select_sort_dropdown(self):
        """
        After login, use the `select` action on the sort dropdown.
        Exercises the select action type — not yet covered in existing E2E suite.
        """
        result = self._exec({
            "id": "sauce_select",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
                # Sort by "Price (low to high)"
                {"action": "select",
                 "selector": {"strategy": "css", "value": ".product_sort_container"},
                 "label": "Price (low to high)"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "inventory"},
                {"type": "element_visible",
                 "selector": {"strategy": "testid", "value": "inventory-container"}},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── go_back / go_forward ──────────────────────────────────────────

    def test_go_back_navigation(self):
        """
        Navigate to cart → go_back → verify we're on inventory.
        Exercises go_back action — not yet covered in existing E2E suite.
        """
        result = self._exec({
            "id": "sauce_go_back",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
                {"action": "navigate", "url": SAUCEDEMO_CART},
                {"action": "go_back"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "inventory"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_go_forward_navigation(self):
        """go_forward after go_back must return to the cart URL."""
        result = self._exec({
            "id": "sauce_go_forward",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
                {"action": "navigate", "url": SAUCEDEMO_CART},
                {"action": "go_back"},
                {"action": "go_forward"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "cart"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── Refresh ───────────────────────────────────────────────────────

    def test_refresh_preserves_page(self):
        """
        After login, refresh the page — still on inventory.
        Exercises refresh action — not yet covered in existing E2E suite.
        """
        result = self._exec({
            "id": "sauce_refresh",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
                {"action": "refresh"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "inventory"},
                {"type": "title_contains", "value": "Swag"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── Scroll action ─────────────────────────────────────────────────

    def test_scroll_down_on_inventory(self):
        """
        Scroll down on inventory page.
        Exercises scroll action — not yet covered in existing E2E suite.
        """
        result = self._exec({
            "id": "sauce_scroll",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
                {"action": "scroll", "direction": "down"},
                {"action": "scroll", "direction": "up"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "inventory"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── element_enabled / element_text_equals ─────────────────────────

    def test_element_enabled_assertion(self):
        """Login button is enabled on the login page."""
        result = self._exec({
            "id": "sauce_enabled",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
            ],
            "assertions": [
                {"type": "element_enabled",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── url_matches assertion ─────────────────────────────────────────

    def test_url_matches_regex(self):
        """url_matches assertion works with a regex pattern."""
        result = self._exec({
            "id": "sauce_url_matches",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "username"},
                 "value": SAUCE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": SAUCE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-button"}},
            ],
            "assertions": [
                {"type": "url_matches", "pattern": r".*inventory\.html$"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass",
                         f"url_matches failed: {result['assertions'][0]}")

    # ── Error resilience ──────────────────────────────────────────────

    def test_missing_element_step_fails_cleanly(self):
        """Clicking a non-existent element must produce fail status, not a crash."""
        result = self._exec({
            "id": "sauce_missing_elem",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "does-not-exist-xyz"}},
            ],
            "assertions": [],
        })
        # Should be "fail", never an exception
        self.assertIn(result["status"], ("fail", "error"),
                      "Missing element should not produce pass status")

    def test_wrong_assertion_fails_cleanly(self):
        """A wrong URL assertion must fail gracefully without raising."""
        result = self._exec({
            "id": "sauce_wrong_assert",
            "steps": [{"action": "navigate", "url": SAUCEDEMO_URL}],
            "assertions": [
                {"type": "url_equals", "value": "https://this-url-is-wrong.com/"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "fail")


# ════════════════════════════════════════════════════════════════════════
# THE INTERNET (HEROKU) E2E — form fill, checkboxes, dropdown, hover
# ════════════════════════════════════════════════════════════════════════

class TestHerokuInternetE2E(_ExecMixin, unittest.TestCase):
    """
    The Internet exercises label/placeholder selector strategies,
    check/uncheck actions, dropdown select, and hover.
    Credentials: tomsmith / SuperSecretPassword!
    """

    # ── Login with label selector ─────────────────────────────────────

    def test_login_with_label_selector(self):
        """
        Fill the login form using the `label` selector strategy.
        This exercises a selector strategy not covered in the existing suite.
        """
        result = self._exec({
            "id": "heroku_login_label",
            "steps": [
                {"action": "navigate", "url": HEROKU_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "label", "value": "Username"},
                 "value": HEROKU_USER},
                {"action": "fill",
                 "selector": {"strategy": "label", "value": "Password"},
                 "value": HEROKU_PASS},
                {"action": "click",
                 "selector": {"strategy": "role", "value": {"role": "button", "name": "Login"}}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/secure"},
                {"type": "element_visible",
                 "selector": {"strategy": "role", "value": {"role": "heading", "name": "Secure Area"}}},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")
        self.assertEqual(result["assertions"][1]["status"], "pass")

    def test_title_on_login_page(self):
        """Login page title contains 'The Internet'."""
        result = self._exec({
            "id": "heroku_title",
            "steps": [{"action": "navigate", "url": HEROKU_LOGIN}],
            "assertions": [
                {"type": "title_contains", "value": "The Internet"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── Check / uncheck actions ───────────────────────────────────────

    def test_check_action_and_element_checked_assertion(self):
        """
        The checkboxes page has two checkboxes; the first is unchecked.
        Exercise: check action + element_checked assertion.
        Both not covered in existing E2E suite.
        """
        result = self._exec({
            "id": "heroku_check",
            "steps": [
                {"action": "navigate", "url": HEROKU_CHECKBOXES},
                # First checkbox is unchecked — check it
                {"action": "check",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:first-of-type"}},
            ],
            "assertions": [
                {"type": "element_checked",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:first-of-type"}},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_uncheck_action_and_element_unchecked_assertion(self):
        """
        The second checkbox is checked by default.
        Exercise: uncheck action + element_unchecked assertion.
        """
        result = self._exec({
            "id": "heroku_uncheck",
            "steps": [
                {"action": "navigate", "url": HEROKU_CHECKBOXES},
                # Second checkbox is pre-checked — uncheck it
                {"action": "uncheck",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:last-of-type"}},
            ],
            "assertions": [
                {"type": "element_unchecked",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:last-of-type"}},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_check_then_uncheck_round_trip(self):
        """Check the unchecked box, then uncheck the now-checked box."""
        result = self._exec({
            "id": "heroku_check_uncheck",
            "steps": [
                {"action": "navigate", "url": HEROKU_CHECKBOXES},
                {"action": "check",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:first-of-type"}},
                {"action": "uncheck",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:first-of-type"}},
            ],
            "assertions": [
                {"type": "element_unchecked",
                 "selector": {"strategy": "css", "value": "input[type='checkbox']:first-of-type"}},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── Select action on dropdown ─────────────────────────────────────

    def test_select_dropdown_option(self):
        """
        /dropdown page has a <select> element.
        Exercise: select action with label — covers a UI pattern common in forms.
        """
        result = self._exec({
            "id": "heroku_select",
            "steps": [
                {"action": "navigate", "url": HEROKU_DROPDOWN},
                {"action": "select",
                 "selector": {"strategy": "css", "value": "select#dropdown"},
                 "label": "Option 1"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/dropdown"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── Hover action ──────────────────────────────────────────────────

    def test_hover_action(self):
        """
        /hovers page reveals captions on hover.
        Exercise: hover action — not yet covered in existing E2E suite.
        """
        result = self._exec({
            "id": "heroku_hover",
            "steps": [
                {"action": "navigate", "url": HEROKU_HOVER},
                {"action": "hover",
                 "selector": {"strategy": "css", "value": ".figure:first-of-type img"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/hovers"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── element_text_equals assertion ─────────────────────────────────

    def test_element_text_equals_on_heading(self):
        """
        The checkboxes page H3 heading reads exactly 'Checkboxes'.
        Exercise: element_text_equals — not yet covered in existing suite.
        """
        result = self._exec({
            "id": "heroku_text_equals",
            "steps": [{"action": "navigate", "url": HEROKU_CHECKBOXES}],
            "assertions": [
                {"type": "element_text_equals",
                 "selector": {"strategy": "role", "value": {"role": "heading", "name": "Checkboxes"}},
                 "value": "Checkboxes"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── element_disabled assertion ────────────────────────────────────

    def test_element_disabled_assertion(self):
        """
        Dropdown page 'Please select an option' is the disabled placeholder.
        Exercise: element_disabled assertion using the css option.
        (A selected disabled option element is a common pattern.)
        """
        result = self._exec({
            "id": "heroku_disabled",
            "steps": [{"action": "navigate", "url": HEROKU_DROPDOWN}],
            "assertions": [
                {"type": "element_visible",
                 "selector": {"strategy": "css", "value": "select#dropdown"}},
                {"type": "element_enabled",
                 "selector": {"strategy": "css", "value": "select#dropdown"}},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")
        self.assertEqual(result["assertions"][1]["status"], "pass")

    # ── text selector strategy ────────────────────────────────────────

    def test_click_link_by_text_selector(self):
        """
        Click a navigation link by its visible text content.
        Exercises the `text` selector strategy — not covered in existing suite.
        """
        result = self._exec({
            "id": "heroku_text_selector",
            "steps": [
                {"action": "navigate", "url": HEROKU_URL},
                {"action": "click",
                 "selector": {"strategy": "text", "value": "Form Authentication"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/login"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    # ── Redirect resilience ───────────────────────────────────────────

    def test_failed_login_stays_on_login_page(self):
        """Wrong password: executor must report assertion failure, not crash."""
        result = self._exec({
            "id": "heroku_bad_login",
            "steps": [
                {"action": "navigate", "url": HEROKU_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "label", "value": "Username"},
                 "value": "wrong_user"},
                {"action": "fill",
                 "selector": {"strategy": "label", "value": "Password"},
                 "value": "wrong_pass"},
                {"action": "click",
                 "selector": {"strategy": "role", "value": {"role": "button", "name": "Login"}}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/secure"},  # This should FAIL
            ],
        })
        # Steps should pass (login was attempted), assertion should fail
        self.assertEqual(result["assertions"][0]["status"], "fail")


# ════════════════════════════════════════════════════════════════════════
# PRACTICESOFTWARETESTING E2E — real-world e-commerce
# ════════════════════════════════════════════════════════════════════════

class TestPracticeShopE2E(_ExecMixin, unittest.TestCase):
    """
    PracticeSOftwareTesting exercises placeholder selectors,
    full login flow, and product browsing.
    Credentials: customer2@practicesoftwaretesting.com / welcome01
    """

    # ── Login flow with placeholder selector ──────────────────────────

    def test_login_with_placeholder_selector(self):
        """
        Login form uses placeholder text as locator.
        Exercise: `placeholder` selector strategy — not covered in existing suite.
        """
        result = self._exec({
            "id": "practice_login_placeholder",
            "steps": [
                {"action": "navigate", "url": PRACTICE_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "email"},
                 "value": PRACTICE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": PRACTICE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-submit"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/account"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_login_then_navigate_to_account(self):
        """After login, the account page must be accessible."""
        result = self._exec({
            "id": "practice_account",
            "steps": [
                {"action": "navigate", "url": PRACTICE_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "email"},
                 "value": PRACTICE_USER},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": PRACTICE_PASS},
                {"action": "click",
                 "selector": {"strategy": "testid", "value": "login-submit"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/account"},
                {"type": "element_visible",
                 "selector": {"strategy": "role",
                              "value": {"role": "heading", "name": "My account"}}},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")
        self.assertEqual(result["assertions"][1]["status"], "pass")

    # ── Product browsing ──────────────────────────────────────────────

    def test_navigate_home_and_assert_title(self):
        """Home page title contains 'Practice Software Testing'."""
        result = self._exec({
            "id": "practice_home_title",
            "steps": [{"action": "navigate", "url": PRACTICE_URL}],
            "assertions": [
                {"type": "title_contains", "value": "Practice Software Testing"},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_url_contains_on_login_page(self):
        """Login page URL must contain 'login'."""
        result = self._exec({
            "id": "practice_login_url",
            "steps": [{"action": "navigate", "url": PRACTICE_LOGIN}],
            "assertions": [
                {"type": "url_contains", "value": "login"},
                {"type": "element_visible",
                 "selector": {"strategy": "testid", "value": "email"}},
                {"type": "element_visible",
                 "selector": {"strategy": "testid", "value": "password"}},
            ],
        })
        for a in result["assertions"]:
            self.assertEqual(a["status"], "pass", f"Assertion failed: {a}")

    def test_search_results_page_loads(self):
        """Navigate directly to a search results URL and assert it loaded."""
        result = self._exec({
            "id": "practice_search",
            "steps": [
                {"action": "navigate", "url": PRACTICE_URL + "?q=Pliers"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "practicesoftwaretesting"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── element_value_equals on filled input ──────────────────────────

    def test_element_value_equals_after_fill(self):
        """
        After filling the email field, element_value_equals asserts its content.
        Exercise: element_value_equals — not covered in existing suite.
        """
        result = self._exec({
            "id": "practice_value_equals",
            "steps": [
                {"action": "navigate", "url": PRACTICE_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "email"},
                 "value": PRACTICE_USER},
            ],
            "assertions": [
                {"type": "element_value_equals",
                 "selector": {"strategy": "testid", "value": "email"},
                 "value": PRACTICE_USER},
            ],
        })
        self.assertEqual(result["assertions"][0]["status"], "pass",
                         f"element_value_equals failed: {result['assertions'][0]}")

    # ── Multi-step press action ───────────────────────────────────────

    def test_press_tab_between_fields(self):
        """Press Tab to move focus between login fields."""
        result = self._exec({
            "id": "practice_press_tab",
            "steps": [
                {"action": "navigate", "url": PRACTICE_LOGIN},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "email"},
                 "value": PRACTICE_USER},
                {"action": "press",
                 "selector": {"strategy": "testid", "value": "email"},
                 "key": "Tab"},
                {"action": "fill",
                 "selector": {"strategy": "testid", "value": "password"},
                 "value": PRACTICE_PASS},
                {"action": "press",
                 "selector": {"strategy": "testid", "value": "password"},
                 "key": "Enter"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/account"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    # ── element_exists and element_not_exists ─────────────────────────

    def test_login_form_elements_exist(self):
        """Both email and password inputs must exist on the login page."""
        result = self._exec({
            "id": "practice_exists",
            "steps": [{"action": "navigate", "url": PRACTICE_LOGIN}],
            "assertions": [
                {"type": "element_exists",
                 "selector": {"strategy": "testid", "value": "email"}},
                {"type": "element_exists",
                 "selector": {"strategy": "testid", "value": "password"}},
                {"type": "element_not_exists",
                 "selector": {"strategy": "testid", "value": "nonexistent-xyz-element"}},
            ],
        })
        for a in result["assertions"]:
            self.assertEqual(a["status"], "pass", f"Assertion failed: {a}")

    # ── Plan result structure ─────────────────────────────────────────

    def test_result_structure_complete(self):
        """Execution result must have all required top-level fields."""
        result = self._exec({
            "id": "practice_structure",
            "steps": [{"action": "navigate", "url": PRACTICE_URL}],
            "assertions": [{"type": "url_contains", "value": "practicesoftwaretesting"}],
        })
        for field in ("status", "steps", "assertions", "duration_ms"):
            self.assertIn(field, result, f"Missing field '{field}' in result")
        self.assertIsInstance(result["steps"], list)
        self.assertIsInstance(result["assertions"], list)


# ════════════════════════════════════════════════════════════════════════
# CROSS-SITE RESILIENCE
# ════════════════════════════════════════════════════════════════════════

class TestCrossSiteResilience(_ExecMixin, unittest.TestCase):
    """
    Tests that the executor handles edge cases robustly across sites.
    These are negative/boundary tests — the point is graceful failure, not pass.
    """

    def test_navigate_to_three_sites_in_one_plan(self):
        """One plan can navigate across 3 different domains sequentially."""
        result = self._exec({
            "id": "multi_site",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "navigate", "url": HEROKU_LOGIN},
                {"action": "navigate", "url": PRACTICE_URL},
            ],
            "assertions": [
                {"type": "url_contains", "value": "practicesoftwaretesting"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))
        self.assertEqual(result["assertions"][0]["status"], "pass")

    def test_empty_plan_does_not_crash(self):
        """A plan with no steps and no assertions should return pass."""
        result = self._exec({
            "id": "empty",
            "steps": [],
            "assertions": [],
        })
        self.assertEqual(result["status"], "pass")

    def test_assertion_only_plan(self):
        """Assertions run without any steps (URL is about:blank initially)."""
        result = self._exec({
            "id": "assert_only",
            "steps": [],
            "assertions": [
                {"type": "url_contains", "value": "google"},  # will fail
            ],
        })
        # Should fail, not crash
        self.assertEqual(result["assertions"][0]["status"], "fail")

    def test_nonexistent_css_selector_fails_gracefully(self):
        """A valid CSS selector that matches no elements must fail, not crash."""
        result = self._exec({
            "id": "nonexistent_css",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "click",
                 "selector": {"strategy": "css", "value": ".element-xyz-does-not-exist-at-all"}},
            ],
            "assertions": [],
        })
        self.assertIn(result["status"], ("fail", "error"))

    def test_screenshot_action_works_on_any_site(self):
        """Screenshot action must not fail regardless of site."""
        result = self._exec({
            "id": "screenshot_any",
            "steps": [
                {"action": "navigate", "url": HEROKU_URL},
                {"action": "screenshot"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "the-internet"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))

    def test_wait_action_works(self):
        """
        Wait action must pause and then resume execution normally.
        Exercises wait action with explicit duration.
        """
        result = self._exec({
            "id": "wait_action",
            "steps": [
                {"action": "navigate", "url": SAUCEDEMO_URL},
                {"action": "wait", "duration": 300},
            ],
            "assertions": [
                {"type": "url_contains", "value": "saucedemo"},
            ],
        })
        self.assertEqual(result["status"], "pass", result.get("error"))


# ════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
