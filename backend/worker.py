"""
Worker — Deep Scan pipeline.

Replaces the lifecycle stub with real QAPAL engine integration:
  crawl → auto-PRD → plan → execute → report

Called by BackgroundTasks.add_task(run_deep_scan, job_id).
Each job is fully isolated with its own temp LocatorDB.
"""

import sys
import os
import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Ensure the QAPAL engine root is importable
_ENGINE_ROOT = str(Path(__file__).resolve().parent.parent)
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

from backend.database import SessionLocal
from backend.models import Job
from backend.config import settings

_base_logger = logging.getLogger("qapal.worker")


def _job_logger(job_id: str) -> logging.LoggerAdapter:
    """Create a logger that auto-tags every message with the job ID."""
    return logging.LoggerAdapter(_base_logger, {"job_id": job_id})


# ── Job DB helpers ───────────────────────────────────────────────────────


def _update_job(job_id: str, log: Optional[logging.LoggerAdapter] = None, **fields) -> None:
    """Open a fresh DB session, update specified fields, commit, close."""
    _log = log or _base_logger
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
        for key, value in fields.items():
            if key == "state":
                job.transition(value)
            else:
                setattr(job, key, value)
        session.commit()
    except Exception:
        _log.exception("Failed to update job %s", job_id)
        session.rollback()
    finally:
        session.close()


def _get_job_info(job_id: str) -> tuple:
    """Return (url, options) for a job."""
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise RuntimeError(f"Job {job_id} not found")
        return job.url, job.options or {}
    finally:
        session.close()


# ── Auto-PRD generation ─────────────────────────────────────────────────


def _build_auto_prd(locator_db, url: str, crawl_results: list) -> str:
    """
    Synthesize a smoke-test PRD from crawl results.

    Inspects discovered locators to build test cases for:
    - Page loads and title
    - Navigation links (if found)
    - Forms (if found)
    - Buttons/interactive elements (fallback)
    """
    crawled_urls = [r["url"] for r in crawl_results if r.get("crawled")]

    lines = [
        f"# Smoke Test for {url}",
        "",
        "## Test Scope",
        f"Verify the basic functionality of {url}.",
        "",
        "## Test Cases",
        "",
        "### TC1: Page loads and core elements are visible",
        f"- Navigate to {url}",
        "- Verify the page loads without errors",
        "- Verify the page title is non-empty",
        "- Verify the main content area is visible",
    ]

    # Discover page structure from locators
    all_locs = locator_db.get_all_locators(valid_only=True)
    forms = [l for l in all_locs if l.get("role") in ("textbox", "combobox", "searchbox")]
    nav_links = [l for l in all_locs if l.get("role") == "link" and l.get("container") == "nav"]
    buttons = [l for l in all_locs if l.get("role") == "button"]

    if nav_links:
        lines.append("")
        lines.append("### TC2: Navigation links are functional")
        lines.append("- Click primary navigation links and verify pages load")
        for link in nav_links[:3]:
            name = link.get("name", "")
            if name:
                lines.append(f"- Navigation link: \"{name}\"")

    if forms:
        lines.append("")
        tc_num = "TC3" if nav_links else "TC2"
        lines.append(f"### {tc_num}: Forms are interactable")
        lines.append("- Locate form inputs on the page")
        lines.append("- Verify form elements are visible and enabled")
        for inp in forms[:5]:
            name = inp.get("name", "")
            if name:
                lines.append(f"- Form field: \"{name}\"")

    if not nav_links and not forms and buttons:
        lines.append("")
        lines.append("### TC2: Interactive elements are present")
        lines.append(f"- Verify at least {min(len(buttons), 3)} buttons are visible")
        for btn in buttons[:3]:
            name = btn.get("name", "")
            if name:
                lines.append(f"- Button: \"{name}\"")

    # Discovered pages context
    if len(crawled_urls) > 1:
        lines.append("")
        lines.append("## Discovered Pages")
        for page_url in crawled_urls[:10]:
            page_locs = locator_db.get_all(page_url, valid_only=True)
            lines.append(f"- {page_url} ({len(page_locs)} elements)")

    return "\n".join(lines)


# ── Issue extraction ─────────────────────────────────────────────────────


def _extract_issues(exec_results: list) -> list:
    """
    Map executor results to the Issue[] schema.

    Maps:
    - Failed navigation steps → critical / NAVIGATION_FAILURE
    - Failed interaction steps → high / INTERACTION_FAILURE
    - Failed URL assertions → critical / URL_ASSERTION_FAILED
    - Failed element assertions → high / ELEMENT_ASSERTION_FAILED
    - Console errors → medium / CONSOLE_ERROR
    - JS exceptions → high / JS_EXCEPTION
    - Network failures → medium / NETWORK_FAILURE
    """
    issues = []
    counter = 0

    for result in exec_results:
        tc_id = result.get("id", "?")
        test_url = ""

        # Step failures
        for step in result.get("steps", []):
            if step.get("action") == "navigate":
                test_url = step.get("url", test_url)

            if step.get("status") != "fail":
                continue

            counter += 1
            action = step.get("action", "unknown")
            is_nav = action == "navigate"
            cat = step.get("category", "UNKNOWN")
            
            # ── Task 4.1 & 4.5: Mapping ───────────────────────────────
            rule_map = {
                "AUTH_REJECTED": ("AUTH_FAILED", "critical"),
                "SELECTOR_NOT_FOUND": ("ELEMENT_MISSING", "high"),
                "SEMANTIC_MISMATCH": ("INTENT_DRIFT", "high"),
                "NAV_TIMEOUT": ("PAGE_LOAD_ERROR", "critical"),
                "FLOW_INCOMPLETE": ("PLANNING_GAP", "medium"),
            }
            rule, severity = rule_map.get(cat, ("INTERACTION_FAILURE", "high"))
            
            msg = f"[{tc_id}] {cat}: {step.get('reason', 'unknown')}"
            if cat == "AUTH_REJECTED":
                msg = f"[{tc_id}] Login Rejected: Cannot proceed with authenticated flow."
            elif cat == "SEMANTIC_MISMATCH":
                msg = f"[{tc_id}] Intent Drift: AI found similar element but rejected repair to avoid false pass."
            elif cat == "SELECTOR_NOT_FOUND" and not is_nav:
                msg = f"[{tc_id}] Broken Flow: User cannot find/interact with {step.get('selector', '?')}."

            issues.append({
                "id": f"issue-{counter:03d}",
                "severity": severity,
                "rule": rule,
                "message": msg,
                "page": test_url or "unknown",
                "element": step.get("selector_used") if isinstance(step.get("selector_used"), str) else None,
            })

        # Assertion failures
        for assertion in result.get("assertions", []):
            if assertion.get("status") != "fail":
                continue

            counter += 1
            cat = assertion.get("category", "ASSERTION_FAILED")
            atype = assertion.get("type", "unknown")
            
            expected = assertion.get("expected", assertion.get("value", "?"))
            actual = assertion.get("actual", "?")
            
            msg = f"[{tc_id}] {atype} failed: expected={expected}, actual={actual}"
            if cat == "ASSERTION_FAILED":
                 msg = f"[{tc_id}] Business Logic Error: Expected state not reached (expected {expected})."

            issues.append({
                "id": f"issue-{counter:03d}",
                "severity": "critical" if atype.startswith("url_") else "high",
                "rule": "ASSERTION_ERROR",
                "message": msg,
                "page": test_url or "unknown",
                "element": None,
            })

        # Passive errors
        passive = result.get("passive_errors", {})

        for err in passive.get("console_errors", [])[:10]:  # cap at 10
            counter += 1
            issues.append({
                "id": f"issue-{counter:03d}",
                "severity": "medium",
                "rule": "CONSOLE_ERROR",
                "message": f"[{tc_id}] Console error: {str(err.get('text', ''))[:200]}",
                "page": err.get("url", test_url) or "unknown",
                "element": None,
            })

        for err in passive.get("js_exceptions", [])[:5]:
            counter += 1
            issues.append({
                "id": f"issue-{counter:03d}",
                "severity": "high",
                "rule": "JS_EXCEPTION",
                "message": f"[{tc_id}] JS exception: {str(err)[:200]}",
                "page": test_url or "unknown",
                "element": None,
            })

        for err in passive.get("network_failures", [])[:10]:
            counter += 1
            issues.append({
                "id": f"issue-{counter:03d}",
                "severity": "medium",
                "rule": "NETWORK_FAILURE",
                "message": f"[{tc_id}] Network failure: {err.get('url', '')} ({err.get('failure', '')})",
                "page": test_url or "unknown",
                "element": None,
            })

    return issues


def _generate_playwright_test(exec_results: list, credentials: Optional[dict] = None) -> str:
    """Generate a standalone Playwright TypeScript test from execution results."""
    creds_user = (credentials or {}).get("username", "")
    creds_pass = (credentials or {}).get("password", "")

    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        "/**",
        " * QAPAL Reproduction Script",
        " * Run with: QAPAL_TEST_USER=xxx QAPAL_TEST_PASS=yyy npx playwright test",
        " */",
        f"const TEST_USER = process.env.QAPAL_TEST_USER || '{creds_user or 'admin@example.com'}';",
        f"const TEST_PASS = process.env.QAPAL_TEST_PASS || '{creds_pass or 'password123'}';",
        "",
        "test.describe('QAPAL Reproduced User Flows', () => {",
    ]

    for result in exec_results:
        tc_id = result.get("id", "test")
        tc_name = result.get("name", tc_id)
        lines.append(f"  test('{tc_name}', async ({{ page }}) => {{")

        for step in result.get("steps", []):
            action = step.get("action")
            sel = step.get("selector_used") or step.get("selector")
            
            if action == "navigate":
                lines.append(f"    await page.goto('{step.get('url')}');")
            elif action == "click":
                if isinstance(sel, dict) and sel.get("strategy") == "role":
                    role = sel["value"]["role"]
                    name = sel["value"].get("name", "")
                    lines.append(f"    await page.getByRole('{role}', {{ name: '{name}', exact: false }}).click();")
                elif isinstance(sel, dict) and sel.get("strategy") == "text":
                    lines.append(f"    await page.getByText('{sel['value']}', {{ exact: false }}).click();")
                else:
                    lines.append(f"    // Click fallback for {action}")
            elif action == "fill":
                val = step.get("value", "")
                # ── Auth Injection (Task 4.6) ─────────────────────────
                fill_val = f"'{val}'"
                if creds_user and val == creds_user: fill_val = "TEST_USER"
                if creds_pass and val == creds_pass: fill_val = "TEST_PASS"

                if isinstance(sel, dict) and sel.get("strategy") == "role":
                    role = sel["value"]["role"]
                    name = sel["value"].get("name", "")
                    lines.append(f"    await page.getByRole('{role}', {{ name: '{name}' }}).fill({fill_val});")
                else:
                    lines.append(f"    // Fill fallback for {action}")
        
        for assertion in result.get("assertions", []):
            atype = assertion.get("type", "")
            if atype == "url_contains":
                lines.append(f"    await expect(page).toHaveURL(/.*{assertion.get('value')}.*/);")
            elif atype == "element_visible":
                sel = assertion.get("selector")
                if isinstance(sel, dict) and sel.get("strategy") == "role":
                    lines.append(f"    await expect(page.getByRole('{sel['value']['role']}', {{ name: '{sel['value'].get('name', '')}' }})).toBeVisible();")

        lines.append("  });")
        lines.append("")

    lines.append("});")
    return "\n".join(lines)


def _calculate_score(issues: list) -> int:
    """Deterministic score from issue severities. 0-100, higher is better."""
    weights = {"critical": 25, "high": 10, "medium": 3, "low": 1}
    deductions = sum(weights.get(i["severity"], 0) for i in issues)
    return max(0, 100 - deductions)


# ── Report assembly ──────────────────────────────────────────────────────


def _build_report(
    url: str,
    crawl_results: list,
    exec_results: list,
    duration_ms: int,
    timeout_stage: Optional[str] = None,
    credentials:   Optional[dict] = None,
) -> dict:
    """
    Assemble the final Report dict matching the extension's Report interface.
    """
    issues = _extract_issues(exec_results)
    score = _calculate_score(issues)
    repro_test = _generate_playwright_test(exec_results, credentials=credentials)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for issue in issues:
        sev = issue["severity"]
        if sev in counts:
            counts[sev] += 1

    passed = sum(1 for r in exec_results if r.get("status") == "pass")
    failed = sum(1 for r in exec_results if r.get("status") == "fail")
    pages = sum(1 for r in crawl_results if r.get("crawled"))
    actions = sum(len(r.get("steps", [])) for r in exec_results)

    summary = f"Deep Scan: {passed} passed, {failed} failed across {pages} pages"
    if timeout_stage:
        summary += f" (timed out during {timeout_stage}, partial results)"

    return {
        "summary": summary,
        "score": score,
        "issues": issues,
        "reproduce_test": repro_test,
        "critical_count": counts["critical"],
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "pages_crawled": pages,
        "actions_taken": actions,
        "duration_ms": duration_ms,
        "engine_version": "deep-1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Main pipeline ────────────────────────────────────────────────────────


async def run_deep_scan(job_id: str) -> None:
    """
    Main Deep Scan pipeline. Called by BackgroundTasks.add_task().

    Pipeline: crawl → auto-PRD → plan (1 AI call) → execute (0 AI calls) → report.
    Each job gets an isolated temp LocatorDB. Cleaned up on exit.
    """
    from locator_db import LocatorDB
    from crawler import Crawler
    from generator import TestGenerator
    from executor import Executor
    from state_graph import StateGraph
    from ai_client import AIClient

    log = _job_logger(job_id)
    db_path = f"/tmp/qapal_job_{job_id}.json"
    trace_dir = Path(settings.SCAN_TRACE_DIR) / job_id
    locator_db = None
    crawl_results: list = []
    exec_results: list = []
    url: str = "unknown"
    stage = "init"
    start_time = time.monotonic()

    try:
        # ── 1. Init (10%) ────────────────────────────────────────────
        _update_job(job_id, log=log, state="running", progress=10, message="Starting scan...")
        url, options = _get_job_info(job_id)
        max_pages = options.get("max_pages", settings.SCAN_MAX_PAGES_DEFAULT)

        locator_db = LocatorDB(path=db_path)
        sg = StateGraph(locator_db)
        ai_client = AIClient.from_env()

        # ── 2. Crawl (15→30%) ────────────────────────────────────────
        stage = "crawl"
        _update_job(job_id, log=log, progress=15, message="Crawling site structure...")
        log.info("Crawling %s (max_pages=%d)", url, max_pages)

        # Merge job options with default settings for auth
        credentials = options.get("credentials") or {}
        if (credentials.get("username") or credentials.get("password")) and "url" not in credentials:
            credentials["url"] = url # Default login page to base URL if not specified

        async with Crawler(
            locator_db, headless=True, state_graph=sg, 
            credentials=credentials
        ) as crawler:
            crawl_results = await asyncio.wait_for(
                crawler.spider_crawl(
                    start_urls=[url],
                    max_depth=settings.SCAN_MAX_DEPTH,
                    max_pages=max_pages,
                    force=True,
                ),
                timeout=settings.SCAN_TIMEOUT_SECONDS * 0.4,
            )

        crawled_count = sum(1 for r in crawl_results if r.get("crawled"))
        _update_job(job_id, log=log, progress=30, message=f"Crawled {crawled_count} pages")
        log.info("Crawled %d pages", crawled_count)

        # ── 3. Auto-PRD (35%) ────────────────────────────────────────
        stage = "plan"
        _update_job(job_id, log=log, progress=35, message="Analyzing site structure...")
        prd_content = _build_auto_prd(locator_db, url, crawl_results)
        log.info("Auto-PRD generated (%d chars)", len(prd_content))

        # ── 4. Plan (40→55%) — sync method, run in thread ────────────
        _update_job(job_id, log=log, progress=40, message="Generating test plans...")

        generator = TestGenerator(
            locator_db,
            ai_client,
            state_graph=sg,
            num_tests=settings.SCAN_NUM_TESTS,
            logger=log,
        )
        plans = await asyncio.to_thread(
            generator.generate_plans_from_prd, prd_content, [url]
        )

        valid_plans = [p for p in plans if not p.get("_planning_error")]
        if not valid_plans:
            raise RuntimeError("AI planner produced no valid test plans")

        _update_job(
            job_id, log=log, progress=55,
            message=f"Generated {len(valid_plans)} test plans",
        )
        log.info("%d valid plans generated", len(valid_plans))

        # ── 5. Execute (60→85%) ──────────────────────────────────────
        stage = "execute"
        _update_job(job_id, log=log, progress=60, message="Executing tests...")

        async with Executor(
            locator_db,
            headless=True,
            ai_client=ai_client,
            credentials=credentials,
            state_graph=sg,
            trace_dir=str(trace_dir),
            logger=log,
        ) as executor:
            exec_results = await asyncio.wait_for(
                executor.run_parallel(
                    valid_plans,
                    concurrency=settings.SCAN_EXEC_CONCURRENCY,
                ),
                timeout=settings.SCAN_TIMEOUT_SECONDS * 0.5,
            )

        _update_job(job_id, log=log, progress=85, message="Tests complete, building report...")
        log.info("Executed %d tests", len(exec_results))

        # ── 6. Report (90→95%) ────────────────────────────────────────
        stage = "report"
        duration_ms = int((time.monotonic() - start_time) * 1000)
        report = _build_report(url, crawl_results, exec_results, duration_ms, credentials=credentials)

        # ── 7. Narration (95→100%) ────────────────────────────────────
        _update_job(job_id, log=log, progress=95, message="Generating summary...")
        try:
            from backend.services.narration import generate_narration
            narration = await generate_narration(
                url=url,
                score=report["score"],
                issues=report["issues"],
                pages_crawled=report["pages_crawled"],
                actions_taken=report["actions_taken"],
            )
            report["narration"] = narration
            log.info("Narration: %s", narration[:80])
        except Exception as narr_err:
            log.warning("Narration failed: %s", narr_err)
            report["narration"] = None

        # Check for trace files from failed tests
        trace_files = list(trace_dir.glob("*.zip")) if trace_dir.exists() else []
        trace_path_str = str(trace_dir) if trace_files else None

        _update_job(
            job_id, log=log,
            state="complete",
            progress=100,
            message="Scan complete",
            report=report,
            trace_path=trace_path_str,
        )
        log.info(
            "Complete (score=%d, issues=%d, traces=%d, %.1fs)",
            report["score"], len(report["issues"]), len(trace_files), duration_ms / 1000,
        )

    except asyncio.TimeoutError:
        # Save partial results — partial data is more useful than "failed"
        duration_ms = int((time.monotonic() - start_time) * 1000)
        log.warning("Timed out during %s stage after %.1fs", stage, duration_ms / 1000)

        report = _build_report(
            url, crawl_results, exec_results, duration_ms,
            timeout_stage=stage,
            credentials=credentials,
        )

        # Template narration for timeouts (skip AI to save time)
        try:
            from backend.services.narration import _template_narration
            report["narration"] = _template_narration(
                url, report["score"], report["issues"],
                report["pages_crawled"], timed_out=True,
            )
        except Exception:
            report["narration"] = None

        trace_files = list(trace_dir.glob("*.zip")) if trace_dir.exists() else []

        _update_job(
            job_id, log=log,
            state="complete",
            progress=100,
            message=f"Scan complete (timed out during {stage})",
            report=report,
            failure_stage=stage,
            trace_path=str(trace_dir) if trace_files else None,
        )

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        log.exception("Failed during %s stage: %s", stage, e)

        # Build partial report from whatever we collected
        partial_report = None
        if crawl_results or exec_results:
            partial_report = _build_report(
                url, crawl_results, exec_results, duration_ms,
                credentials=credentials,
            )
            partial_report["summary"] += f" (failed during {stage})"

        trace_files = list(trace_dir.glob("*.zip")) if trace_dir.exists() else []

        _update_job(
            job_id, log=log,
            state="failed",
            error=str(e)[:500],
            message=f"Scan failed during {stage}",
            failure_stage=stage,
            report=partial_report,
            trace_path=str(trace_dir) if trace_files else None,
        )

    finally:
        # Cleanup temp locator DB (traces are kept for diagnostics)
        if locator_db:
            try:
                locator_db.close()
            except Exception:
                pass
        try:
            # Path(db_path).unlink(missing_ok=True) # DISABLED FOR DEBUGGING
            pass
        except Exception:
            pass
