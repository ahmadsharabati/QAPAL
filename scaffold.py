"""
scaffold.py — Test File Scaffold Generator
============================================
Generates Playwright test file scaffolds from probe results.
No AI needed — pure template rendering with validated selectors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from parser import _qapal_to_python, _qapal_to_typescript
from ranker import format_grade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_name(url: str) -> str:
    """Convert a URL to a test function/name slug.
    https://myapp.com/auth/login → auth_login
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_").replace("-", "_")
    if not path:
        path = "home"
    # Remove non-alphanumeric chars
    slug = "".join(c if c.isalnum() or c == "_" else "_" for c in path)
    # Collapse multiple underscores
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")[:60]


def _element_label(elem) -> str:
    """Human-readable label for an element."""
    parts = []
    if elem.role:
        parts.append(elem.role.capitalize())
    if elem.name:
        parts.append(f'"{elem.name}"')
    elif elem.testid:
        parts.append(f'[testid={elem.testid}]')
    elif elem.aria_label:
        parts.append(f'[aria-label="{elem.aria_label}"]')
    return " ".join(parts) if parts else elem.tag or "element"


# ---------------------------------------------------------------------------
# Python scaffold
# ---------------------------------------------------------------------------

def generate_python_scaffold(
    url: str,
    elements: list,
    function_name: Optional[str] = None,
) -> str:
    """
    Generate a pytest-playwright test scaffold with validated selectors.

    Args:
        url: Page URL to test.
        elements: List of ElementInfo from probe_page().
        function_name: Optional test function name (default: derived from URL).

    Returns:
        Complete Python source string.
    """
    if function_name is None:
        slug = _url_to_name(url)
        function_name = f"test_{slug}"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f'"""',
        f"Auto-generated scaffold by QAPAL",
        f"URL: {url}",
        f"Generated: {ts}",
        f"Elements discovered: {len(elements)}",
        f'"""',
        "from playwright.sync_api import Page, expect",
        "",
        "",
        f"# === Validated elements on {url} ===",
        "#",
    ]

    # List all elements as comments with their best selector
    for elem in elements:
        if not elem.best_selector:
            continue
        label = _element_label(elem)
        expr = _qapal_to_python(
            elem.best_selector.get("strategy", "css"),
            elem.best_selector.get("value", ""),
        )
        grade = format_grade(elem.confidence)
        lines.append(f"# {label:<40s} \u2192 {expr:<55s} {grade}")

    lines.extend([
        "#",
        "",
        "",
        f"def {function_name}(page: Page):",
        f'    page.goto("{url}", wait_until="domcontentloaded")',
        "",
        "    # TODO: Write your test logic using the validated selectors above",
        "    pass",
        "",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TypeScript scaffold
# ---------------------------------------------------------------------------

def generate_typescript_scaffold(
    url: str,
    elements: list,
    test_name: Optional[str] = None,
) -> str:
    """
    Generate a Playwright TypeScript test scaffold with validated selectors.

    Args:
        url: Page URL to test.
        elements: List of ElementInfo from probe_page().
        test_name: Optional test name (default: derived from URL).

    Returns:
        Complete TypeScript source string.
    """
    if test_name is None:
        slug = _url_to_name(url).replace("_", " ")
        test_name = f"{slug} page"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"/**",
        f" * Auto-generated scaffold by QAPAL",
        f" * URL: {url}",
        f" * Generated: {ts}",
        f" * Elements discovered: {len(elements)}",
        f" */",
        "import { test, expect } from '@playwright/test';",
        "",
        "",
        f"// === Validated elements on {url} ===",
        "//",
    ]

    for elem in elements:
        if not elem.best_selector:
            continue
        label = _element_label(elem)
        expr = _qapal_to_typescript(
            elem.best_selector.get("strategy", "css"),
            elem.best_selector.get("value", ""),
        )
        grade = format_grade(elem.confidence)
        lines.append(f"// {label:<40s} \u2192 {expr:<55s} {grade}")

    lines.extend([
        "//",
        "",
        "",
        f"test('{test_name}', async ({{ page }}) => {{",
        f"  await page.goto('{url}');",
        "",
        "  // TODO: Add your test logic using the validated selectors above",
        "});",
        "",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def generate_file(
    url: str,
    elements: list,
    output_path: str,
    language: str = "python",
) -> str:
    """
    Generate a test scaffold and write it to a file.

    Args:
        url: Page URL.
        elements: List of ElementInfo.
        output_path: Directory or file path. If directory, filename is derived from URL.
        language: "python" or "typescript".

    Returns:
        Path to the generated file.
    """
    out = Path(output_path)

    if out.is_dir() or not out.suffix:
        # Generate filename from URL
        slug = _url_to_name(url)
        if language == "python":
            out = out / f"test_{slug}.py"
        else:
            out = out / f"{slug}.spec.ts"

    out.parent.mkdir(parents=True, exist_ok=True)

    if language == "python":
        content = generate_python_scaffold(url, elements)
    else:
        content = generate_typescript_scaffold(url, elements)

    out.write_text(content, encoding="utf-8")
    return str(out)
