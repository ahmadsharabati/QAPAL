"""
semantic_extractor.py — QAPal Semantic Context Extractor
=========================================================
Converts a live Playwright page into structured, AI-friendly semantic data.

Runs AFTER the crawler has populated the locator DB — completely separate
from crawling so you can reprocess semantic context without re-crawling.

Resolution order:
  1. Crawl4AI fit_markdown (optional) — noise-stripped page summary
  2. Accessibility snapshot (always available from Playwright) — fallback

New fields vs v1:
  - inputs:           list of form field descriptors {label, type, placeholder, testid}
  - error_containers: list of CSS selectors for known error/alert elements on the page
  - description:      now uses fit_markdown first paragraph when available

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
        "page":             "Dashboard",
        "description":      "Noise-stripped summary of the page",
        "buttons":          ["Create User", "Logout"],
        "links":            ["/settings", "/reports"],
        "tables":           ["Users Table"],
        "forms":            ["Search Users"],
        "headings":         ["Users", "Activity"],
        "inputs":           [{"label": "Email", "type": "email", "placeholder": "Enter email", "testid": "email"}],
        "error_containers": ["[data-test='alert-message']", ".error-message"]
    }
    """
    try:
        html  = await page.content()
        title = await page.title()
    except Exception:
        return _empty_context(url)

    # page.accessibility was removed in Playwright ≥1.47 — graceful fallback
    try:
        a11y = await page.accessibility.snapshot() or {}  # type: ignore[attr-defined]
    except Exception:
        a11y = {}

    a11y_result = _extract_from_a11y(a11y, url, title)

    # Live DOM extraction: form fields, error containers, and structure fallback
    inputs, error_containers, structure = await _extract_live_form_data(page)
    a11y_result["inputs"]           = inputs
    a11y_result["error_containers"] = error_containers

    # Use JS structure when a11y is unavailable (Playwright ≥1.47 removed page.accessibility)
    if not a11y_result["buttons"]:
        a11y_result["buttons"] = structure.get("buttons", [])
    if not a11y_result["links"]:
        a11y_result["links"] = structure.get("links", [])
    if not a11y_result["headings"]:
        a11y_result["headings"] = structure.get("headings", [])

    # Try Crawl4AI for better headings/description via fit_markdown
    crawl_result = await _extract_with_crawl4ai(html, url, title)
    if crawl_result:
        # Crawl4AI is better at headings (strips nav/footer noise) and description
        if crawl_result.get("headings"):
            a11y_result["headings"] = crawl_result["headings"]
        if crawl_result.get("description"):
            a11y_result["description"] = crawl_result["description"]
        if crawl_result.get("links"):
            a11y_result["links"] = crawl_result["links"]
        if crawl_result.get("tables"):
            a11y_result["tables"] = crawl_result["tables"]

    return a11y_result


def compute_dom_hash(html: str) -> str:
    """
    SHA-256 fingerprint of the page HTML.
    Used to detect DOM state changes (new modals, new sections, navigation).
    Only the first 16 hex chars — sufficient for state identification.
    """
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:16]


# ── Live DOM extraction (Playwright) ─────────────────────────────────

_PAGE_STRUCTURE_JS = """
() => {
    // Buttons (visible, non-submit)
    const buttons = [];
    document.querySelectorAll('button, [role="button"], input[type="submit"]').forEach(el => {
        const t = (el.textContent || el.getAttribute('value') || '').trim();
        if (t && buttons.length < 20) buttons.push(t);
    });

    // Links (internal, with visible text)
    const links = [];
    document.querySelectorAll('a[href]').forEach(el => {
        const href = el.getAttribute('href') || '';
        if (href && !href.startsWith('#') && links.length < 20) links.push(href);
    });

    // Headings
    const headings = [];
    document.querySelectorAll('h1,h2,h3,h4,[role="heading"]').forEach(el => {
        const t = el.textContent.trim();
        if (t && headings.length < 10) headings.push(t);
    });

    return { buttons, links, headings };
}
"""

_FORM_FIELD_JS = """
() => {
    const fields = [];
    const seen   = new Set();

    document.querySelectorAll('input, select, textarea').forEach(el => {
        // Skip hidden / submit / button / reset
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (['hidden','submit','button','reset','image'].includes(type)) return;

        // Derive label
        let label = '';
        if (el.id) {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parent = el.closest('label');
            if (parent) label = parent.textContent.trim();
        }
        if (!label && el.getAttribute('aria-label'))
            label = el.getAttribute('aria-label');
        if (!label && el.getAttribute('placeholder'))
            label = el.getAttribute('placeholder');
        if (!label && el.getAttribute('name'))
            label = el.getAttribute('name');

        const testid = el.getAttribute('data-test')
                    || el.getAttribute('data-testid')
                    || el.getAttribute('data-cy')
                    || el.getAttribute('data-qa')
                    || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const elType      = el.tagName === 'SELECT'   ? 'select'
                          : el.tagName === 'TEXTAREA' ? 'textarea'
                          : (type || 'text');

        const key = label + '|' + testid + '|' + elType;
        if (seen.has(key)) return;
        seen.add(key);

        fields.push({
            label:       label.replace(/\\s+/g, ' ').substring(0, 60),
            type:        elType,
            placeholder: placeholder.substring(0, 60),
            testid:      testid,
            required:    el.required || el.getAttribute('required') !== null,
        });
    });
    return fields;
}
"""

_ERROR_CONTAINER_JS = """
() => {
    const KEYWORDS = ['error', 'alert', 'danger', 'invalid', 'feedback',
                      'notification', 'warning', 'toast', 'message'];
    const results  = new Set();

    // 1. data-test attributes containing error keywords
    document.querySelectorAll('[data-test],[data-testid],[data-cy],[data-qa]').forEach(el => {
        const val = (el.getAttribute('data-test')
                  || el.getAttribute('data-testid')
                  || el.getAttribute('data-cy')
                  || el.getAttribute('data-qa') || '').toLowerCase();
        if (KEYWORDS.some(k => val.includes(k)))
            results.add('[data-test="' + (el.getAttribute('data-test') || '') + '"]');
    });

    // 2. role=alert elements
    document.querySelectorAll('[role="alert"],[role="status"],[aria-live]').forEach(el => {
        const testid = el.getAttribute('data-test') || el.getAttribute('data-testid') || '';
        if (testid) {
            results.add('[data-test="' + testid + '"]');
        } else if (el.getAttribute('role')) {
            results.add('[role="' + el.getAttribute('role') + '"]');
        }
    });

    // 3. Class-based (.error-message, .alert-danger, .invalid-feedback, etc.)
    //    Match keyword as a hyphen/underscore-delimited segment to avoid false
    //    positives like .alert-info, .alert-success, .message-sender.
    const ERROR_SEGMENTS = ['error', 'danger', 'invalid', 'warning', 'toast'];
    const VALID_COMPOUNDS = [
        'alert-danger', 'alert-error', 'alert-warning',
        'invalid-feedback', 'error-message', 'error-text',
        'form-error', 'field-error', 'validation-error',
    ];
    document.querySelectorAll('[class]').forEach(el => {
        const cls = el.className;
        if (typeof cls !== 'string') return;
        const parts = cls.split(/\\s+/);
        for (const p of parts) {
            if (!p) continue;
            const lower = p.toLowerCase();
            const segments = lower.split(/[-_]/);
            const matched = ERROR_SEGMENTS.some(k => segments.includes(k))
                         || VALID_COMPOUNDS.includes(lower);
            if (matched) {
                results.add('.' + p);
                break;
            }
        }
    });

    return Array.from(results).slice(0, 15);
}
"""


async def _extract_live_form_data(page) -> tuple:
    """
    Returns (inputs_list, error_containers_list, structure_dict) using live Playwright DOM.
    Gracefully returns empty values on any failure.
    """
    try:
        inputs = await page.evaluate(_FORM_FIELD_JS)
    except Exception:
        inputs = []

    try:
        error_containers = await page.evaluate(_ERROR_CONTAINER_JS)
    except Exception:
        error_containers = []

    try:
        structure = await page.evaluate(_PAGE_STRUCTURE_JS)
    except Exception:
        structure = {"buttons": [], "links": [], "headings": []}

    return inputs, error_containers, structure


# ── Crawl4AI extraction (optional) ───────────────────────────────────

async def _extract_with_crawl4ai(html: str, url: str, title: str) -> Optional[dict]:
    """
    Use Crawl4AI to extract noise-stripped content via fit_markdown.
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
            result = await crawler.arun(
                url    = url,
                html   = html,
                config = run_cfg,
            )

        if not result or not result.success:
            return None

        return _parse_crawl4ai_result(result, url, title)

    except Exception:
        return None


def _parse_crawl4ai_result(result, url: str, title: str) -> dict:
    """
    Parse a Crawl4AI CrawlResult into our semantic context schema.
    Prefers fit_markdown (noise-stripped) over raw markdown.
    """
    try:
        headings = []
        links    = []
        tables   = []
        description = ""

        # Prefer fit_markdown — it strips navbars, footers, ads
        text = (
            getattr(result, "fit_markdown", None)
            or getattr(result, "markdown", None)
            or getattr(result, "cleaned_html", None)
            or ""
        )

        # Extract headings and first paragraph for description
        first_para = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                if heading and len(headings) < 10:
                    headings.append(heading)
            elif stripped and not first_para and not stripped.startswith("|"):
                # First non-heading, non-table line = page summary
                first_para = stripped[:200]

        if first_para:
            description = first_para
        elif title:
            description = title

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

        return {
            "description": description,
            "headings":    headings,
            "links":       links,
            "tables":      tables,
        }

    except Exception:
        return {}


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
        "page":             page_name,
        "description":      f"Page at {url}" + (f" — {title}" if title else ""),
        "buttons":          buttons,
        "links":            links,
        "tables":           tables,
        "forms":            forms,
        "headings":         headings,
        "inputs":           [],   # filled by _extract_live_form_data
        "error_containers": [],   # filled by _extract_live_form_data
    }


# ── Helpers ───────────────────────────────────────────────────────────

def _page_name_from_url(url: str) -> str:
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    return segment.replace("-", " ").replace("_", " ").title() or "Page"


def _empty_context(url: str) -> dict:
    return {
        "page":             _page_name_from_url(url),
        "description":      f"Page at {url}",
        "buttons":          [],
        "links":            [],
        "tables":           [],
        "forms":            [],
        "headings":         [],
        "inputs":           [],
        "error_containers": [],
    }
