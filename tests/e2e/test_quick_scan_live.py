"""
tests/e2e/test_quick_scan_live.py

Live Quick Scan tests — require outbound internet access and a built
extension bundle (extension/dist/src/content/scanner.js).

Marked @pytest.mark.live so they are excluded from the default CI run.
Run explicitly with:
    pytest tests/e2e/test_quick_scan_live.py -m live -v

What these tests validate:
  1. The CSP-immune init_script injection works on every site category
  2. Scanner returns well-structured results (not empty, not erroring)
  3. Known high-issue sites produce a meaningful issue count
  4. The engine field confirms the correct injection path
  5. CSP-strict sites (previously failing with add_script_tag) now work
"""

import asyncio
import pytest

from engine.quick_scan import run_quick_scan


# ── Test matrix ───────────────────────────────────────────────────────────────
# (url, framework_label, min_issues, must_have_ruleids)
# min_issues = minimum number of issues expected on a good scan
# must_have_ruleids = rule IDs we expect to find (empty = don't assert)

GAUNTLET = [
    pytest.param(
        "https://news.ycombinator.com",
        "Static/High-issue",
        20,  # HN is notoriously accessibility-poor
        ["a11y/contrast", "seo/og-tags"],
        id="hacker-news",
    ),
    pytest.param(
        "https://www.wikipedia.org",
        "Static/Large-DOM",
        1,
        [],
        id="wikipedia",
    ),
    pytest.param(
        "https://demo.playwright.dev/todomvc/#/",
        "React/SPA",
        0,   # TodoMVC is clean — 0+ is fine
        [],
        id="todomvc-react",
    ),
    pytest.param(
        "https://the-internet.herokuapp.com/",
        "Legacy/Edge-cases",
        5,   # legacy site, many issues expected
        [],
        id="the-internet",
    ),
    pytest.param(
        "https://magento.softwaretestingboard.com/",
        "Magento/E-commerce",
        5,
        [],
        id="magento-luma",
    ),
    pytest.param(
        "https://demo.playwright.dev/cart/",
        "React/CSP-strict",
        0,   # CSP site — was previously failing, now fixed with init_script
        [],
        id="playwright-demo-csp",
    ),
]


@pytest.mark.live
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.parametrize("url,framework,min_issues,must_have_rules", GAUNTLET)
def test_quick_scan_returns_structured_results(url, framework, min_issues, must_have_rules):
    """
    Quick scan must return a well-formed result on every site category.
    Validates schema, engine tag, and minimum issue count.
    """
    result = asyncio.run(run_quick_scan(url, headless=True))

    # Engine must confirm init_script path (not the old add_script_tag path)
    assert result.get("engine") == "Playwright/init-script", (
        f"Wrong engine on {framework} ({url}): {result.get('engine')}"
    )

    # Schema check
    assert "issues" in result, f"Missing 'issues' key in result for {url}"
    assert "pageUrl" in result
    assert "duration_ms" in result
    assert isinstance(result["issues"], list)
    assert result["duration_ms"] >= 0

    # Issue count floor
    actual = len(result["issues"])
    assert actual >= min_issues, (
        f"{framework} ({url}): expected ≥{min_issues} issues, got {actual}"
    )

    # Spot-check expected rule IDs
    found_rules = {i.get("ruleId") for i in result["issues"]}
    for rule in must_have_rules:
        assert rule in found_rules, (
            f"Expected rule {rule!r} on {url} but got: {found_rules}"
        )


@pytest.mark.live
@pytest.mark.network
@pytest.mark.parametrize("url,framework,_min,_rules", GAUNTLET)
def test_quick_scan_issue_schema(url, framework, _min, _rules):
    """Every issue object must conform to the schema the extension and action expect."""
    result = asyncio.run(run_quick_scan(url, headless=True))

    for issue in result.get("issues", []):
        assert "ruleId" in issue, f"Issue missing ruleId on {url}: {issue}"
        assert "severity" in issue, f"Issue missing severity on {url}: {issue}"
        assert "title" in issue, f"Issue missing title on {url}: {issue}"
        assert "category" in issue, f"Issue missing category on {url}: {issue}"
        assert issue["severity"] in ("critical", "major", "medium", "minor"), (
            f"Unknown severity {issue['severity']!r} on {url}"
        )
        assert issue["category"] in (
            "accessibility", "seo", "forms", "links", "performance"
        ), f"Unknown category {issue['category']!r} on {url}"


@pytest.mark.live
@pytest.mark.network
def test_quick_scan_csp_site_not_blocked():
    """
    Regression test: demo.playwright.dev/cart/ has a strict CSP that blocked
    add_script_tag. Must return >0 issues with init_script injection.
    """
    result = asyncio.run(
        run_quick_scan("https://demo.playwright.dev/cart/", headless=True)
    )
    assert result["engine"] == "Playwright/init-script"
    # Should find at least some issues (contrast, SEO tags, etc.)
    assert len(result["issues"]) > 0, (
        "CSP-strict site returned 0 issues — injection may have failed silently"
    )


@pytest.mark.live
@pytest.mark.network
def test_quick_scan_invalid_url_raises():
    """A completely unreachable URL must raise, not return empty results."""
    with pytest.raises(Exception):
        asyncio.run(
            run_quick_scan("https://this-domain-does-not-exist.qapal.invalid/", headless=True)
        )


@pytest.mark.live
@pytest.mark.network
def test_quick_scan_duration_is_reasonable():
    """Scans should complete within 30 seconds on a normal connection."""
    import time
    start = time.monotonic()
    result = asyncio.run(run_quick_scan("https://www.wikipedia.org", headless=True))
    elapsed = time.monotonic() - start
    assert elapsed < 30, f"Scan took {elapsed:.1f}s — too slow"
    # Also check the reported duration aligns roughly
    assert result["duration_ms"] < 30_000
