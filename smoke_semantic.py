"""
smoke_semantic.py — Quick smoke test for semantic_extractor.py
==============================================================
No AI key needed. No DB. Just validates that semantic extraction
and DOM hashing work on a real live page.

Run:
    python smoke_semantic.py
    python smoke_semantic.py --url https://quotes.toscrape.com
    python smoke_semantic.py --url https://demo.playwright.dev/todomvc
"""

import asyncio
import argparse
import json

from playwright.async_api import async_playwright
from semantic_extractor import extract_semantic_context, compute_dom_hash


async def smoke(url: str, headless: bool = True):
    print(f"\n Smoke test: semantic extraction")
    print(f"   URL     : {url}")
    print(f"   headless: {headless}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx     = await browser.new_context()
        page    = await ctx.new_page()

        print(f"\n Loading page...")
        await page.goto(url, wait_until="networkidle", timeout=30_000)

        title = await page.title()
        print(f"   title   : {title}")

        print(f"\n Extracting semantic context...")
        semantic_ctx = await extract_semantic_context(page, url)

        html     = await page.content()
        dom_hash = compute_dom_hash(html)

        print(f"   dom_hash: {dom_hash}")
        print(f"\n Semantic context:")
        print(json.dumps(semantic_ctx, indent=2))

        await browser.close()

    # Validation checks
    print(f"\n Validation:")
    checks = [
        ("dom_hash is 16 chars",    len(dom_hash) == 16),
        ("page name non-empty",     bool(semantic_ctx.get("page"))),
        ("description non-empty",   bool(semantic_ctx.get("description"))),
        ("has buttons/links/headings/inputs", any([
            semantic_ctx.get("buttons"),
            semantic_ctx.get("links"),
            semantic_ctx.get("headings"),
            semantic_ctx.get("inputs"),
        ])),
        ("inputs have labels/testids", all(
            i.get("label") or i.get("testid")
            for i in semantic_ctx.get("inputs", [])
        ) if semantic_ctx.get("inputs") else True),
    ]

    all_pass = True
    for label, result in checks:
        icon = "✓" if result else "✗"
        print(f"   {icon} {label}")
        if not result:
            all_pass = False

    print(f"\n {'PASS' if all_pass else 'FAIL'}")
    return all_pass


def main():
    parser = argparse.ArgumentParser(description="Smoke test semantic_extractor.py")
    parser.add_argument("--url",      default="https://quotes.toscrape.com", help="URL to test")
    parser.add_argument("--headless", action="store_true", default=True,     help="Run headless")
    args = parser.parse_args()
    ok = asyncio.run(smoke(args.url, headless=args.headless))
    exit(0 if ok else 1)


if __name__ == "__main__":
    main()
