"""
AI narration service — generates human-readable summaries of scan reports.

Makes a single, cheap AI call (short prompt, short response) to narrate
findings in plain English. Designed for the extension popup UI.

Falls back gracefully: if AI is unavailable, returns a template-based summary.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("qapal.narration")


def _build_narration_prompt(
    url: str,
    score: int,
    issues: list,
    pages_crawled: int,
    actions_taken: int,
    timed_out: bool = False,
) -> str:
    """Build the prompt for AI narration."""

    # Group issues by severity
    by_severity: dict[str, list] = {"critical": [], "high": [], "medium": [], "low": []}
    for issue in issues[:20]:  # cap to keep prompt small
        sev = issue.get("severity", "medium")
        by_severity.setdefault(sev, []).append(issue.get("message", "Unknown issue"))

    issue_summary = []
    for sev in ("critical", "high", "medium", "low"):
        items = by_severity.get(sev, [])
        if items:
            issue_summary.append(f"  {sev.upper()} ({len(items)}):")
            for msg in items[:5]:
                issue_summary.append(f"    - {msg[:150]}")
            if len(items) > 5:
                issue_summary.append(f"    ... and {len(items) - 5} more")

    issues_text = "\n".join(issue_summary) if issue_summary else "  No issues found."

    timeout_note = " (scan timed out, results are partial)" if timed_out else ""

    return f"""You are a QA analyst summarizing automated test results for a website owner.

SCAN RESULTS for {url}{timeout_note}:
  Score: {score}/100
  Pages crawled: {pages_crawled}
  Test actions executed: {actions_taken}

ISSUES FOUND:
{issues_text}

Write a 2-3 sentence summary for a non-technical audience. Be specific about what works
and what needs attention. If the score is high (80+), lead with the positive. If low,
lead with the most critical problems. Don't mention the score number directly.
Keep it under 60 words. No markdown, no bullet points — plain sentences only."""


def _template_narration(
    url: str,
    score: int,
    issues: list,
    pages_crawled: int,
    timed_out: bool = False,
) -> str:
    """Fallback template narration when AI is unavailable."""
    critical = sum(1 for i in issues if i.get("severity") == "critical")
    high = sum(1 for i in issues if i.get("severity") == "high")
    medium = sum(1 for i in issues if i.get("severity") == "medium")
    total = len(issues)

    if timed_out:
        return (
            f"The scan of {url} timed out before completing all checks. "
            f"Partial results across {pages_crawled} page(s) found {total} issue(s). "
            f"Consider running the scan again with fewer pages."
        )

    if score >= 90 and total == 0:
        return (
            f"Great news — {url} passed all automated checks across "
            f"{pages_crawled} page(s) with no issues detected."
        )

    if score >= 80:
        if critical or high:
            return (
                f"Overall, {url} is in good shape across {pages_crawled} page(s), "
                f"but {critical + high} issue(s) need attention: "
                f"{critical} critical and {high} high-priority."
            )
        return (
            f"{url} looks solid across {pages_crawled} page(s). "
            f"Found {total} minor issue(s) worth reviewing when time permits."
        )

    if score >= 50:
        return (
            f"{url} has some issues across {pages_crawled} page(s) that need attention. "
            f"Found {critical} critical, {high} high, and {medium} medium-priority issues."
        )

    # score < 50
    return (
        f"{url} has significant problems across {pages_crawled} page(s). "
        f"Found {critical} critical and {high} high-priority issues that should be fixed urgently."
    )


async def generate_narration(
    url: str,
    score: int,
    issues: list,
    pages_crawled: int,
    actions_taken: int,
    timed_out: bool = False,
) -> str:
    """
    Generate a human-readable narration of scan results.

    Tries AI first, falls back to template if AI is unavailable.
    """
    # Try AI narration
    try:
        provider = os.getenv("QAPAL_AI_PROVIDER", "").strip()
        if not provider:
            return _template_narration(url, score, issues, pages_crawled, timed_out)

        from ai_client import AIClient
        client = AIClient.from_env()

        prompt = _build_narration_prompt(
            url, score, issues, pages_crawled, actions_taken, timed_out,
        )

        response = await client.acomplete([{"role": "user", "content": prompt}])

        narration = response.strip()
        if len(narration) < 10:
            raise ValueError("Narration too short")

        # Enforce length limit
        if len(narration) > 300:
            narration = narration[:297] + "..."

        logger.info("AI narration generated (%d chars)", len(narration))
        return narration

    except Exception as e:
        logger.warning("AI narration failed (%s), using template", e)
        return _template_narration(url, score, issues, pages_crawled, timed_out)
