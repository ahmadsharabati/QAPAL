from playwright.sync_api import Page, expect

def test_multi_page(page: Page, context):
    # Page 1: SauceDemo
    page.goto("https://www.saucedemo.com/")
    page.locator("#user-name").fill("standard_user")
    
    # Page 2: Wikipedia (New Tab)
    page2 = context.new_page()
    page2.goto("https://en.wikipedia.org/")
    page2.get_by_placeholder("Search Wikipedia").fill("Playwright")
    
    # Back to Page 1
    page.locator("#password").fill("secret_sauce")
    page.get_by_role("button", name="Login").click()
