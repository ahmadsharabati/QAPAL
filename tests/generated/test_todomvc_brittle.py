from playwright.sync_api import Page, expect

def test_todomvc_brittle(page: Page):
    page.goto("https://demo.playwright.dev/todomvc/")
    
    # Fragile CSS selector
    page.get_by_role("textbox", name="What needs to be done?").fill("Buy milk")
    page.get_by_role("textbox", name="What needs to be done?").press("Enter")
    
    # Generic element match instead of semantic
    expect(page.locator("label").nth(0)).to_have_text("Buy milk")
