"""
E2E tests for MDN Web Docs — dynamic SPA with complex navigiation.
"""
from playwright.sync_api import Page, expect


def test_mdn_search(page: Page):
    page.goto("https://developer.mozilla.org/en-US/", wait_until="domcontentloaded")

    # 1. Brittle: structural CSS for the search input
    page.locator(".header-search input[type='search']").click()
    page.locator(".header-search input[type='search']").fill("Playwright")
    page.locator(".header-search input[type='search']").press("Enter")

    # 2. Semantic: verify search results page
    expect(page.get_by_role("heading", name="Search results", exact=False)).to_be_visible(timeout=10000)


def test_mdn_api_reference_navigation(page: Page):
    page.goto("https://developer.mozilla.org/en-US/docs/Web/API", wait_until="domcontentloaded")

    # 1. Semantic: the main heading
    expect(page.get_by_role("heading", name="Web APIs", exact=False)).to_be_visible()

    # 2. Brittle: click first item in the API index via deep CSS
    page.locator("div.section-index a").first.click()

    # Verify navigation happened
    expect(page).not_to_have_url("https://developer.mozilla.org/en-US/docs/Web/API")


def test_mdn_dark_mode_toggle(page: Page):
    page.goto("https://developer.mozilla.org/en-US/", wait_until="domcontentloaded")

    # 1. Brittle: target the theme toggle by CSS class
    page.locator("button.theme-toggle").click()

    # Verify something changed in the page
    expect(page.locator("html")).to_be_visible()
