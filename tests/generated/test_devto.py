"""
E2E tests for Dev.to using both brittle and semantic locators.
Covers article listing, tag navigation, search.
"""
from playwright.sync_api import Page, expect


def test_devto_search_flow(page: Page):
    page.goto("https://dev.to/", wait_until="domcontentloaded")

    # 1. Brittle structural locator for the search button
    page.locator("button.crayons-btn.crayons-btn--icon.focus\\:ring-action-accent-focus").click()

    # 2. Slightly brittle CSS-based input targeting (no testid)
    page.locator("input#nav-search").fill("playwright testing")
    page.locator("input#nav-search").press("Enter")

    # 3. Semantic: verify results heading
    expect(page.get_by_role("heading", name="Search Results")).to_be_visible(timeout=5000)


def test_devto_tag_navigation(page: Page):
    page.goto("https://dev.to/t/python", wait_until="domcontentloaded")

    # Semantic: ensure Python tag page loaded
    # The h1 on tag pages usually carries the tag name
    expect(page.get_by_role("heading", name="python", exact=False)).to_be_visible(timeout=10000)


def test_devto_top_article_click(page: Page):
    page.goto("https://dev.to/top/week", wait_until="domcontentloaded")

    # 1. Brittle: click 1st article via position
    first_article = page.locator("div.crayons-story h2 a").first
    first_article.click()

    # 2. Verify we navigated to an article
    expect(page.get_by_role("heading", level=1)).to_be_visible(timeout=10000)
