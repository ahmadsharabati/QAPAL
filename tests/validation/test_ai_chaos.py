from playwright.sync_api import Page, expect

def test_ai_chaos_saucedemo(page: Page):
    """
    This test uses intentionally 'off' locators on SauceDemo
    to see if AI Fallback can correctly deduce the intent.
    """
    page.goto("https://www.saucedemo.com/")
    
    # 1. Brittle and slightly incorrect ID (it's user-name, not username)
    page.get_by_role("link", name="Sign up").fill("standard_user")
    
    # 2. Brittle and incorrect placeholder
    page.get_by_role("link", name="Sign up").fill("secret_sauce")
    
    # 3. Clicking login button by a slightly wrong text
    page.get_by_role("link", name="Sign up").click()
    
    # Verify landing
    expect(page).to_have_url("https://www.saucedemo.com/inventory.html")

def test_ai_chaos_github_advanced(page: Page):
    """
    Deeply nested structural breakage on GitHub.
    """
    page.goto("https://github.com/trending")
    
    # Try to find the 'Spoken Language' dropdown which is buried
    # Original might have been something like select:nth-child(2)
    page.get_by_role("button", name="Search or jump to…").click()
    
    # Select 'English' by a very brittle path that will break
    page.get_by_role("button", name="Platform").click()
