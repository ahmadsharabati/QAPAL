"""
E2E tests for Reddit — complex infinite-scroll SPA.
"""
from playwright.sync_api import Page, expect


def test_reddit_homepage_navigation(page: Page):
    page.goto("https://www.reddit.com/", wait_until="domcontentloaded")

    # 1. Brittle: click the 'Login' button via CSS class
    page.locator("a[href='/login/']").click()
    expect(page).to_have_url("https://www.reddit.com/login/")


def test_reddit_subreddit_navigation(page: Page):
    page.goto("https://www.reddit.com/r/python/", wait_until="domcontentloaded")

    # 1. Semantic: ensure the Python subreddit header is visible
    expect(page.get_by_role("heading", name="Python", exact=False)).to_be_visible(timeout=10000)

    # 2. Brittle: click the first post using a deep positional CSS selector
    page.locator("article:nth-child(1) a[slot='post-title']").first.click()


def test_reddit_search_flow(page: Page):
    page.goto("https://www.reddit.com/", wait_until="domcontentloaded")

    # 1. Semantic: find and focus the search input
    page.get_by_placeholder("Search Reddit").fill("playwright automation")
    page.get_by_placeholder("Search Reddit").press("Enter")

    # 2. Verify search results show
    expect(page).to_have_url("https://www.reddit.com/search/?q=playwright+automation", timeout=10000)
