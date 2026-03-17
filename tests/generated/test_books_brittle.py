from playwright.sync_api import Page, expect

def test_books_toscrape_brittle(page: Page):
    page.goto("https://books.toscrape.com/")
    
    # Brittle locator for a specific book category
    page.get_by_role("link", name="Historical Fiction").click()
    
    # Verify category page
    expect(page.locator(".page-header > h1")).to_have_text("Historical Fiction")
    
    # Click the first book using a highly structural fragile locator
    page.locator("section > div:nth-child(2) > ol > li:nth-child(1) > article > h3 > a").click()
    
    # Wait for book description
    expect(page.locator("#content_inner > article > p")).to_be_visible()
