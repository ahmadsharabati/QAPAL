import asyncio
import os
from pathlib import Path
from typing import Dict, Any
from playwright.async_api import async_playwright

async def run_quick_scan(url: str, headless: bool = True) -> Dict[str, Any]:
    """
    Run the extension's Quick Scan rules against a URL using Playwright.
    
    This bridges the Typescript-based rules into the Python CLI by injecting
    the bundled scanner.js into the page context.
    """
    bundle_path = Path(__file__).parent.parent / "extension" / "dist" / "src" / "content" / "scanner.js"
    
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"Scanner bundle not found at {bundle_path}. "
            "Please run 'npm run build' in the extension directory."
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            # Navigate and wait for content
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000) # Give a moment for hydration
            
            # Inject the scanner bundle as an ES module
            # This ensures (window as any).runQapalScan is attached.
            await page.add_script_tag(path=str(bundle_path), type="module")
            
            # Wait for the script to be parsed and window.runQapalScan to be available
            # Since it's a module, we might need a small delay or a check loop
            max_retries = 10
            for i in range(max_retries):
                is_ready = await page.evaluate("typeof window.runQapalScan === 'function'")
                if is_ready:
                    break
                await asyncio.sleep(0.2)
            else:
                raise RuntimeError("Timed out waiting for window.runQapalScan to be ready.")
            
            # Execute the scan
            result = await page.evaluate("async () => await window.runQapalScan()")
            
            # Add some server-side metadata
            result["engine"] = "Playwright/ESM-Bridge"
            return result
            
        finally:
            await browser.close()

if __name__ == "__main__":
    # Quick manual test
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = asyncio.run(run_quick_scan(target, headless=False))
    import json
    print(json.dumps(res, indent=2))
