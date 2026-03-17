from playwright.sync_api import Page, expect

def test_saucedemo_brittle(page: Page):
    page.goto("https://www.saucedemo.com/")
    
    # Brittle locators
    # ID is fine, but data-test is preferred. QAPAL should upgrade if possible, but let's use brittle CSS
    page.get_by_test_id("username").first.fill("standard_user")
    page.get_by_test_id("password").fill("secret_sauce")
    
    # submit button CSS
    page.locator(".submit-button.btn_action").click()
    
    # Verify login worked by checking inventory page
    expect(page).to_have_url("https://www.saucedemo.com/inventory.html")
    
    # Add first item to cart via brittle class
    page.locator(".btn_inventory").first.click()
    
    # Verify cart badge
    expect(page.locator(".shopping_cart_badge")).to_have_text("1")
