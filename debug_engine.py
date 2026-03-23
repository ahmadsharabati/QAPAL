import asyncio
import os
import sys
from pathlib import Path

# Fix sys.path
root = Path(__file__).resolve().parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from crawler import Crawler
from locator_db import LocatorDB
from generator import TestGenerator

async def test():
    db_path = "debug_example.json"
    db = LocatorDB(db_path)
    try:
        url = "https://demo.playwright.dev/todomvc/#/"
        print(f"--- 1. Crawling {url} ---")
        async with Crawler(db, headless=True) as crawler:
            results = await crawler.spider_crawl([url], max_pages=1, force=True)
            print(f"Crawl results: {results}")
        
        print("\n--- 2. Checking DB content ---")
        locs = db.get_all_locators(valid_only=True)
        print(f"Locators in DB: {len(locs)}")
        for i, l in enumerate(locs):
            print(f"  [{i}] {l.get('identity', {}).get('role')}: {l.get('identity', {}).get('name')}")

        if not locs:
             print("ERROR: No locators found in DB after crawl!")
             return

        print("\n--- 3. Planning ---")
        gen = TestGenerator(db)
        prd = "User wants to click the more information link on example.com"
        plans = gen.generate_plans_from_prd(prd, [url])
        print(f"Plans generated: {len(plans)}")

    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        db.close()
        # Path(db_path).unlink(missing_ok=True)

if __name__ == "__main__":
    asyncio.run(test())
