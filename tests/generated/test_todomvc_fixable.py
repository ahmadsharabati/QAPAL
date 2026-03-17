"""Test file with fixable weak selectors."""
from playwright.sync_api import Page, expect


def test_todomvc_input(page: Page):
    page.goto("https://demo.playwright.dev/todomvc/")

    # Weak: CSS class selector for the input (should be role or placeholder)
    page.get_by_role("textbox", name="What needs to be done?").fill("Buy milk")

    # Weak: CSS for footer links
    page.get_by_role("link", name="Remo H. Jansen").click()
