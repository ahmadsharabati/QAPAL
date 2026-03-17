from playwright.sync_api import Page, expect

def test_todomvc_broken(page: Page):
    page.goto("https://demo.playwright.dev/todomvc/")
    
    # Broken selector - original class is .new-todo
    page.locator(".new-todo-broken-class").fill("Buy milk")
    page.locator(".new-todo-broken-class").press("Enter")
    
    # Verify it got added
    expect(page.get_by_test_id("todo-title")).to_have_text("Buy milk")
