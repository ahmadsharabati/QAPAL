"""
E2E tests for Hacker News using a mix of brittle and semantic locators.
Tests navigation, search-equivalent, and content interaction.
"""
from playwright.sync_api import Page, expect


def test_hn_navigation_and_search(page: Page):
    page.goto("https://news.ycombinator.com/", wait_until="domcontentloaded")

    # 1. Brittle: using numeric ID-based upvote button (changes per item/day)
    page.locator("#up_47416736").click()

    # 2. Semantic: click the 'new' nav link
    page.get_by_role("link", name="new").click()
    expect(page).to_have_url("https://news.ycombinator.com/newest")


def test_hn_login_form(page: Page):
    page.goto("https://news.ycombinator.com/login", wait_until="domcontentloaded")

    # 1. Brittle: rely on form input by position
    page.locator("form input:nth-child(1)").fill("testuser")
    page.locator("form input:nth-child(3)").fill("testpass")

    # 2. Check the submit button is a real button
    expect(page.get_by_role("button", name="login")).to_be_visible()


def test_hn_ask_section(page: Page):
    page.goto("https://news.ycombinator.com/", wait_until="domcontentloaded")

    # Fully semantic navigation
    page.get_by_role("link", name="ask").click()
    expect(page).to_have_url("https://news.ycombinator.com/ask")

    # Also check footer links are present
    expect(page.get_by_role("link", name="Guidelines")).to_be_visible()
    expect(page.get_by_role("link", name="FAQ")).to_be_visible()
