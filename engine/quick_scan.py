import asyncio
from pathlib import Path
from typing import Dict, Any
from playwright.async_api import async_playwright


async def run_quick_scan(url: str, headless: bool = True) -> Dict[str, Any]:
    """
    Run the extension's Quick Scan rules against a URL using Playwright.

    The scanner bundle is injected via Playwright's add_init_script(), which
    uses Chrome DevTools Protocol (CDP) rather than a DOM <script> tag.
    This bypasses Content-Security-Policy restrictions entirely — sites with
    strict CSP (e.g. script-src 'self') that would block add_script_tag work
    correctly here.

    Injection order:
      1. context.add_init_script() installs the bundle on the context before
         any page navigation.  CDP fires it on every new document load via
         Page.addScriptToEvaluateOnNewDocument, before any page script runs.
      2. page.goto() loads the target URL; domcontentloaded waits for the DOM.
      3. A 1-second pause allows JS frameworks (React, Next.js, etc.) to hydrate.
      4. page.evaluate() calls window.runQapalScan(), which is guaranteed to
         exist — no polling loop needed.
    """
    bundle_path = (
        Path(__file__).parent.parent
        / "extension"
        / "dist"
        / "src"
        / "content"
        / "scanner.js"
    )

    if not bundle_path.exists():
        raise FileNotFoundError(
            f"Scanner bundle not found at {bundle_path}. "
            "Please run 'npm run build' in the extension directory first."
        )

    # Read once — passed as a string so Playwright doesn't re-read per page.
    script_content = bundle_path.read_text(encoding="utf-8")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()

        # Install the scanner on the context BEFORE any navigation.
        # CDP-level injection: immune to page Content-Security-Policy.
        await context.add_init_script(script=script_content)

        page = await context.new_page()

        try:
            # Navigate and wait for DOM to be fully parsed.
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Give JS frameworks a moment to hydrate the DOM.
            await page.wait_for_timeout(1000)

            # window.runQapalScan is always defined at this point —
            # add_init_script guarantees it ran before any page script.
            result = await page.evaluate("async () => await window.runQapalScan()")

            result["engine"] = "Playwright/init-script"
            return result

        finally:
            await browser.close()


if __name__ == "__main__":
    import sys
    import json

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = asyncio.run(run_quick_scan(target, headless=False))
    print(json.dumps(res, indent=2))
