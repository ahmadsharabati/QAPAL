from playwright.sync_api import Page, expect

def test_wikipedia_search_brittle(page: Page):
    page.goto("https://en.wikipedia.org/wiki/Main_Page")
    
    # 1. Highly structural and fragile search input locator
    page.locator("div#p-search form > div:nth-child(1) > input.cdx-text-input__input").fill("Playwright")
    page.locator("div#p-search form > div:nth-child(1) > input.cdx-text-input__input").press("Enter")
    
    # Wait for Playwright disambiguation or main page
    expect(page.locator("h1#firstHeading")).to_be_visible()
    
    # 2. Structural locator for a generic element on the page
    page.get_by_role("link", name="County Cavan").click()

    # Just ensure we navigated somewhere
    expect(page.get_by_role("link", name="Jump to content")).to_be_visible()
