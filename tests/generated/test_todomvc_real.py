"""Test file with real selectors for analyze/fix testing."""
from playwright.sync_api import Page, expect


def test_todomvc_add_item(page: Page):
    page.goto("https://demo.playwright.dev/todomvc/")

    # Good selector (role)
    page.get_by_placeholder("What needs to be done?").fill("Buy milk")
    page.get_by_placeholder("What needs to be done?").press("Enter")

    # Weak selector (CSS class — fragile)
    page.locator(".todo-list li").first.click()

    # Good selector (role + name)
    page.get_by_role("link", name="TodoMVC").click()

    # Broken selector (doesn't exist on page)
    page.get_by_test_id("nonexistent-element").click()
