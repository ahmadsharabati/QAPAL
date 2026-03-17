from playwright.sync_api import Page, expect
import time

def test_github_search_brittle(page: Page):
    page.goto("https://github.com/")
    
    # 1. Very brittle: relying on deep structural paths for the search button (which opens the modal)
    # The actual search input is hidden until you click this
    page.get_by_role("button", name="Search or jump to…").click()
    
    # 2. Brittle: typing into the modal input
    page.locator("#hero_user_email").fill("playwright-python")
    page.locator("#query-builder-test").press("Enter")
    
    # Wait for results page
    expect(page).to_have_url("https://github.com/search?q=playwright-python&type=repositories")
    
    # 3. Brittle: Clicking the specific span inside the first a-tag of the first search result
    page.locator("div.Box-sc-g0xbh4-0.bBwPjs.search-title > a > div > span").first.click()
    
    # Wait for the playwright-python repo page
    expect(page.get_by_text("playwright-python", exact=False).first).to_be_visible()
