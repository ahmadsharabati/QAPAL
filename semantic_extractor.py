"""
semantic_extractor.py — QAPal Semantic Context Extractor
=========================================================
Converts a live Playwright page into structured, AI-friendly semantic data.

Runs AFTER the crawler has populated the locator DB — completely separate
from crawling so you can reprocess semantic context without re-crawling.

Resolution order:
  1. Crawl4AI (optional, install: pip install crawl4ai) — richest output
  2. Accessibility snapshot (always available from Playwright) — fallback

Usage:
    ctx      = await extract_semantic_context(page, url)
    dom_hash = compute_dom_hash(html)
    db.upsert_state(url, dom_hash, ctx)
"""

import hashlib
from typing import Optional


# ── Public API ────────────────────────────────────────────────────────

async def extract_semantic_context(page, url: str) -> dict:
    """
    Extract semantic context from a live Playwright page.

    Uses the live page HTML/a11y tree — no extra HTTP requests, no navigation.

    Returns:
    {
        "page":        "Dashboard",
        "description": "Main dashboard showing user management and statistics",
        "buttons":     ["Create User", "Logout"],
        "links":       ["/settings", "/reports"],
        "tables":      ["Users Table"],
        "forms":       ["Search Users"],
        "headings":    ["Users", "Activity"]
    }
    """
    try:
        html  = await page.content()
        a11y  = await page.accessibility.snapshot() or {}
        title = await page.title()
    except Exception:
        return _empty_context(url)

    # Try Crawl4AI first — richer link/table extraction
    result = await _extract_with_crawl4ai(html, url, title)
    if result:
        return result

    # Fallback: parse Playwright accessibility snapshot directly
    return _extract_from_a11y(a11y, url, title)


def compute_dom_hash(html: str) -> str:
    """
    SHA-256 fingerprint of the page HTML.
    Used to detect DOM state changes (new modals, new sections, navigation).
    Only the first 16 hex chars — sufficient for state identification.
    """
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:16]


# ── Crawl4AI extraction (optional) ───────────────────────────────────

async def _extract_with_crawl4ai(html: str, url: str, title: str) -> Optional[dict]:
    """
    Use Crawl4AI to extract structured content from raw HTML.
    Returns None if crawl4ai is not installed or extraction fails.
    """
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import CrawlerRunConfig, BrowserConfig
    except ImportError:
        return None

    try:
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        run_cfg     = CrawlerRunConfig(
            word_count_threshold=0,
            only_text=False,
            verbose=False,
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            # Pass raw HTML directly to avoid an extra HTTP request
            result = await crawler.arun(
                url        = url,
                html       = html,
                config     = run_cfg,
            )

        if not result or not result.success:
            return None

        return _parse_crawl4ai_result(result, url, title)

    except Exception:
        return None


def _parse_crawl4ai_result(result, url: str, title: str) -> dict:
    """Parse a Crawl4AI CrawlResult into our semantic context schema."""
    try:
        buttons  = []
        links    = []
        tables   = []
        forms    = []
        headings = []

        # Headings from markdown
        text = getattr(result, "markdown", None) or getattr(result, "cleaned_html", None) or ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                if heading and len(headings) < 10:
                    headings.append(heading)

        # Links from structured result
        raw_links = getattr(result, "links", None) or {}
        for link in (raw_links.get("internal", []) + raw_links.get("external", []))[:20]:
            href = link.get("href") or link.get("url", "")
            if href:
                links.append(href)

        # Tables
        raw_tables = getattr(result, "tables", None) or []
        for t in raw_tables[:5]:
            caption = (
                t.get("caption")
                or (t.get("headers", [""])[0] if t.get("headers") else "")
                or "Table"
            )
            tables.append(caption)

        page_name = title or _page_name_from_url(url)

        return {
            "page":        page_name,
            "description": f"Page at {url}" + (f" — {title}" if title else ""),
            "buttons":     buttons,
            "links":       links,
            "tables":      tables,
            "forms":       forms,
            "headings":    headings,
        }
    except Exception:
        return _empty_context(url)


# ── Accessibility snapshot fallback ──────────────────────────────────

def _extract_from_a11y(a11y: dict, url: str, title: str) -> dict:
    """
    Derive semantic context from a Playwright accessibility.snapshot() dict.
    No external dependencies — always works.
    """
    buttons  = []
    links    = []
    headings = []
    forms    = []
    tables   = []

    def _walk(node: dict):
        if not isinstance(node, dict):
            return
        role = node.get("role", "")
        name = node.get("name", "")
        if role == "button" and name and len(buttons) < 20:
            buttons.append(name)
        elif role == "link" and name and len(links) < 20:
            links.append(name)
        elif role in ("heading", "columnheader") and name and len(headings) < 10:
            headings.append(name)
        elif role == "form" and name and len(forms) < 10:
            forms.append(name)
        elif role in ("table", "grid") and name and len(tables) < 10:
            tables.append(name)
        for child in node.get("children", []):
            _walk(child)

    _walk(a11y)

    page_name = title or _page_name_from_url(url)
    return {
        "page":        page_name,
        "description": f"Page at {url}" + (f" — {title}" if title else ""),
        "buttons":     buttons,
        "links":       links,
        "tables":      tables,
        "forms":       forms,
        "headings":    headings,
    }


# ── Helpers ───────────────────────────────────────────────────────────

def _page_name_from_url(url: str) -> str:
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    return segment.replace("-", " ").replace("_", " ").title() or "Page"


def _empty_context(url: str) -> dict:
    return {
        "page":        _page_name_from_url(url),
        "description": f"Page at {url}",
        "buttons":     [],
        "links":       [],
        "tables":      [],
        "forms":       [],
        "headings":    [],
    }
