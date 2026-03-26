"""
main.py — QAPal CLI
=====================
Coordinates crawl -> plan -> execute -> report.
Also provides vision-enabled exploration and UX auditing.

All config from environment variables. No config files.
Copy .env.example to .env and fill in values.

Commands:
  python main.py crawl    --urls https://app.com/login https://app.com/dashboard
  python main.py plan     --tests tests/tc001.json tests/tc002.json
  python main.py run      --tests tests/tc001.json
  python main.py explore  --url https://app.com --goal "Test the checkout flow"
  python main.py ux-audit --urls https://app.com/login https://app.com/dashboard
  python main.py status

Environment variables (see .env.example):
  QAPAL_AI_PROVIDER, ANTHROPIC_API_KEY / OPENAI_API_KEY / XAI_API_KEY
  QAPAL_DB_PATH, QAPAL_HEADLESS, QAPAL_SCREENSHOTS
  CRAWLER_STALE_MINUTES, QAPAL_CRAWL_CONCURRENCY
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import List, Optional

from locator_db import LocatorDB
from crawler import Crawler
from planner import Planner, PlanningError
from executor import Executor
from ai_client import AIClient
from semantic_extractor import extract_semantic_context, compute_dom_hash
from state_graph import StateGraph
from _log import get_logger, setup_logging
from _tokens import get_token_tracker

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = get_logger("main")


# ── Helpers ───────────────────────────────────────────────────────────

def _print_visual_regression_summary(results: list) -> None:
    """Log a warning block for any tests with visual regressions."""
    flagged = [r for r in results if r.get("has_visual_regressions")]
    if not flagged:
        return
    log.warning("\n  ⚠  VISUAL REGRESSIONS detected in %d test(s):", len(flagged))
    for r in flagged:
        for vr in r.get("visual_regressions", []):
            log.warning("     %s step %s: %s%% pixel diff", r["id"], vr["step_index"], vr["diff_pct"])
            log.warning("       baseline → %s", vr["baseline"])
            log.warning("       diff     → %s", vr["diff"])


def _print_passive_error_summary(results: list) -> None:
    """Log a warning block for any tests that recorded passive errors."""
    flagged = [r for r in results if r.get("has_passive_errors")]
    if not flagged:
        return
    log.warning("\n  ⚠  PASSIVE ERRORS detected in %d test(s):", len(flagged))
    for r in flagged:
        errs = r.get("passive_errors", {})
        nc = len(errs.get("console_errors",   []))
        nn = len(errs.get("network_failures", []))
        nj = len(errs.get("js_exceptions",    []))
        log.warning("     %s: %d console error(s), %d network failure(s), %d JS exception(s)",
                    r["id"], nc, nn, nj)
        for e in errs.get("console_errors",   [])[:3]:
            log.warning("       console: %s", e["text"][:120])
        for e in errs.get("network_failures", [])[:3]:
            log.warning("       network: %s  [%s]", e["url"][:100], e.get("failure", ""))
        for e in errs.get("js_exceptions",    [])[:3]:
            log.warning("       js:      %s", str(e)[:120])


_HTML_REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>QAPal Report — $title</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f5f5f5;color:#222}
 header{background:#1a1a2e;color:#fff;padding:1.2rem 2rem;display:flex;align-items:center;gap:1rem}
 header h1{margin:0;font-size:1.3rem}
 .badge{padding:.25rem .6rem;border-radius:999px;font-size:.8rem;font-weight:700}
 .pass{background:#22c55e;color:#fff} .fail{background:#ef4444;color:#fff}
 .warn{background:#f59e0b;color:#fff}
 main{padding:1.5rem 2rem}
 .summary{display:flex;gap:1.5rem;margin-bottom:1.5rem;flex-wrap:wrap}
 .stat{background:#fff;border-radius:.5rem;padding:1rem 1.5rem;box-shadow:0 1px 3px #0001;min-width:100px}
 .stat .n{font-size:2rem;font-weight:700;line-height:1}
 .stat .l{font-size:.8rem;color:#666;margin-top:.25rem}
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:.5rem;overflow:hidden;box-shadow:0 1px 3px #0001}
 th{background:#f0f0f0;text-align:left;padding:.6rem 1rem;font-size:.8rem;color:#555}
 td{padding:.6rem 1rem;border-top:1px solid #eee;font-size:.85rem;vertical-align:top}
 tr:hover td{background:#fafafa}
 .steps{margin:0;padding:0 0 0 1rem;list-style:none}
 .steps li{color:#555;font-size:.8rem;padding:.1rem 0}
 .steps li.fail{color:#ef4444;font-weight:600}
 details summary{cursor:pointer;color:#555;font-size:.8rem}
 .passive{color:#f59e0b;font-size:.75rem}
 .vr{color:#8b5cf6;font-size:.75rem}
 footer{text-align:center;padding:1rem;color:#aaa;font-size:.75rem}
</style>
</head>
<body>
<header>
 <h1>QAPal Test Report</h1>
 <span class="badge $header_badge">$passed / $total passed</span>
 <span style="margin-left:auto;font-size:.8rem;opacity:.7">$generated_at</span>
</header>
<main>
 <div class="summary">
  <div class="stat"><div class="n">$total</div><div class="l">Total</div></div>
  <div class="stat"><div class="n" style="color:#22c55e">$passed</div><div class="l">Passed</div></div>
  <div class="stat"><div class="n" style="color:#ef4444">$failed</div><div class="l">Failed</div></div>
  <div class="stat"><div class="n">$duration</div><div class="l">Duration</div></div>
 </div>
 <table>
  <thead><tr><th>Test</th><th>Status</th><th>Duration</th><th>Details</th></tr></thead>
  <tbody>$rows</tbody>
 </table>
</main>
<footer>Generated by QAPal &bull; $generated_at</footer>
</body>
</html>
"""


def _write_html_report(json_report_path: Path, results: list, summary: dict) -> Path:
    """Generate an HTML report alongside the JSON report. No extra dependencies."""
    from html import escape as _esc
    from string import Template

    rows_html = []
    for r in results:
        status   = r.get("status", "?")
        badge    = "pass" if status == "pass" else "fail"
        dur      = f"{r.get('duration_ms', 0)}ms"
        test_id  = _esc(str(r.get("id") or r.get("test_id", "?")))
        name     = _esc(str(r.get("name", test_id)))

        fail_steps = [s for s in r.get("steps", [])      if s.get("status") == "fail"]
        fail_asserts = [a for a in r.get("assertions", []) if a.get("status") == "fail"]
        passive  = r.get("passive_errors", {})
        vrs      = r.get("visual_regressions", [])

        detail_parts = []
        if fail_steps:
            items = "".join(f'<li class="fail">{_esc(str(s.get("reason","?"))[:120])}</li>' for s in fail_steps[:5])
            detail_parts.append(f'<ul class="steps">{items}</ul>')
        if fail_asserts:
            items = "".join(f'<li class="fail">{_esc(str(a.get("reason","?"))[:120])}</li>' for a in fail_asserts[:5])
            detail_parts.append(f'<ul class="steps">{items}</ul>')
        pe_count = len(passive.get("console_errors", [])) + len(passive.get("network_failures", []))
        if pe_count:
            detail_parts.append(f'<div class="passive">⚠ {pe_count} passive error(s)</div>')
        if vrs:
            detail_parts.append(f'<div class="vr">⚠ {len(vrs)} visual regression(s)</div>')

        details = "".join(detail_parts) or '<span style="color:#aaa">—</span>'

        rows_html.append(
            f"<tr>"
            f"<td><b>{test_id}</b><br><span style='color:#777;font-size:.8rem'>{name}</span></td>"
            f"<td><span class='badge {badge}'>{status}</span></td>"
            f"<td>{dur}</td>"
            f"<td>{details}</td>"
            f"</tr>"
        )

    passed   = summary.get("passed", 0)
    total    = summary.get("total", len(results))
    failed   = summary.get("failed", 0)
    duration = f"{summary.get('duration_ms', 0) // 1000}s"
    gen_at   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title    = json_report_path.stem

    html = Template(_HTML_REPORT_TEMPLATE).substitute(
        title         = title,
        header_badge  = "pass" if failed == 0 else "fail",
        passed        = passed,
        failed        = failed,
        total         = total,
        duration      = duration,
        generated_at  = gen_at,
        rows          = "\n  ".join(rows_html),
    )

    html_path = json_report_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    return html_path


def _load_json_files(patterns: List[str]) -> List[dict]:
    items = []
    for pattern in patterns:
        for path in sorted(glob(pattern)) or ([pattern] if Path(pattern).exists() else []):
            try:
                with open(path) as f:
                    data = json.load(f)
                    data["_source"] = path
                    items.append(data)
            except Exception as e:
                log.warning("could not load %s: %s", path, e)
    return items


def _get_ai_client() -> Optional[AIClient]:
    try:
        return AIClient.from_env()
    except EnvironmentError as e:
        log.warning("%s", e)
        return None


def _load_credentials(args) -> Optional[dict]:
    """Load credentials from a JSON file if --credentials-file was supplied."""
    path = getattr(args, "credentials_file", None)
    if not path:
        return None
    try:
        with open(path) as f:
            creds = json.load(f)
        required = {"url", "username", "password"}
        missing = required - creds.keys()
        if missing:
            log.error("credentials file missing keys: %s", ", ".join(sorted(missing)))
            return None
        return creds
    except FileNotFoundError:
        log.error("credentials file not found: %s", path)
        return None
    except json.JSONDecodeError as e:
        log.error("credentials file is not valid JSON: %s", e)
        return None


# ── Semantic pipeline helper ──────────────────────────────────────────

async def _extract_semantics(db: LocatorDB, urls: List[str], headless: bool) -> int:
    """
    Load each URL in a headless browser, extract semantic context from the
    live page, and save to the states table.  Returns the count of URLs processed.

    Deliberately separate from the crawler so you can reprocess semantic
    context at any time without re-crawling the entire site.
    """
    from playwright.async_api import async_playwright

    processed = 0
    async with async_playwright() as pw:
        pw.selectors.set_test_id_attribute("data-test")
        browser = await pw.chromium.launch(headless=headless)
        ctx     = await browser.new_context()
        page    = await ctx.new_page()
        for url in urls:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1500)  # let SPA frameworks render
                semantic_ctx = await extract_semantic_context(page, url)
                html         = await page.content()
                dom_hash_val = compute_dom_hash(html)
                db.upsert_state(url, dom_hash_val, semantic_ctx)
                processed += 1
            except Exception as e:
                log.warning("semantic extraction failed for %s: %s", url, e)
        await browser.close()
    return processed


# ── Commands ──────────────────────────────────────────────────────────

async def cmd_crawl(args):
    urls = args.urls
    if not urls:
        log.error("No URLs provided. Use --urls https://... https://...")
        return 1

    db = LocatorDB()
    sg = StateGraph(db)
    log.info("\n Crawling %d URL(s)  [db: %s]", len(urls), db._path)
    t0 = time.monotonic()

    headless_mode = True if args.headless else None
    credentials   = _load_credentials(args)
    spider        = getattr(args, "spider", False)
    depth         = getattr(args, "depth", 2)
    async with Crawler(db, headless=headless_mode, credentials=credentials, state_graph=sg) as crawler:
        if spider:
            results = await crawler.spider_crawl(urls, max_depth=depth, force=args.force)
        else:
            results = await crawler.bulk_crawl(urls, force=args.force)

    total_elements = sum(r.get("elements", 0) for r in results)
    total_new      = sum(r.get("new",      0) for r in results)
    crawled        = sum(1 for r in results if r.get("crawled"))
    duration       = int((time.monotonic() - t0) * 1000)

    log.info("\n Crawl complete")
    log.info("   pages crawled : %d/%d", crawled, len(urls))
    log.info("   elements      : %d (%d new)", total_elements, total_new)
    log.info("   duration      : %dms", duration)
    db.close()
    return 0


async def cmd_semantic(args):
    urls = args.urls
    if not urls:
        log.error("No URLs provided. Use --urls https://...")
        return 1

    db           = LocatorDB()
    headless     = args.headless if hasattr(args, "headless") else True
    log.info("\n Extracting semantic context for %d URL(s)  [db: %s]", len(urls), db._path)
    t0 = time.monotonic()

    processed = await _extract_semantics(db, urls, headless=headless)

    duration = int((time.monotonic() - t0) * 1000)
    log.info("\n Semantic pipeline complete")
    log.info("   URLs processed: %d/%d", processed, len(urls))
    log.info("   duration      : %dms", duration)
    db.close()
    return 0


async def cmd_plan(args):
    test_cases = _load_json_files(args.tests)
    if not test_cases:
        log.error("No test cases found.")
        return 1

    ai     = _get_ai_client()
    db     = LocatorDB()
    planner = Planner(db, ai)

    output_dir = Path(args.output or "plans")
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("\n Planning %d test(s)  [ai: %s]", len(test_cases), ai.provider if ai else "none")
    ok = failed = 0

    for tc in test_cases:
        tc_id = tc.get("id", "unknown")
        try:
            plan = planner.create_plan(tc, cache_key=tc_id)
            path = output_dir / f"{tc_id}_plan.json"
            with open(path, "w") as f:
                json.dump(plan, f, indent=2)
            log.info("  ✓ %s  (%d steps)  → %s", tc_id, len(plan.get("steps", [])), path)
            ok += 1
        except PlanningError as e:
            log.error("  ✗ %s  %s", tc_id, e)
            failed += 1

    log.info("\n   success: %d  failed: %d", ok, failed)
    tok = get_token_tracker().format_line("plan")
    if tok:
        log.info(tok)
    db.close()
    return 1 if failed else 0


async def cmd_run(args):
    # Load test cases or pre-generated plans
    if args.plans:
        plans = _load_json_files(args.plans)
    else:
        test_cases = _load_json_files(args.tests or [])
        if not test_cases:
            log.error("No test cases or plans provided.")
            return 1
        ai      = _get_ai_client()
        db      = LocatorDB()
        planner = Planner(db, ai)
        plans   = []
        try:
            for tc in test_cases:
                tc_id = tc.get("id", "unknown")
                try:
                    plans.append(planner.create_plan(tc, cache_key=tc_id))
                except PlanningError as e:
                    log.error("  Planning failed for %s: %s", tc_id, e)
                    plans.append({"id": tc_id, "_planning_error": str(e)})
        finally:
            db.close()

    valid_plans = [p for p in plans if not p.get("_planning_error")]
    if not valid_plans:
        log.error("No valid plans to run.")
        return 1

    ai          = _get_ai_client()
    db          = LocatorDB()
    try:
        sg          = StateGraph(db)
        credentials = _load_credentials(args)
        t0          = time.monotonic()
        results     = []

        parallel = getattr(args, "parallel", 1) or 1
        log.info("\n Running %d test(s)%s", len(valid_plans),
                 f"  [parallel={parallel}]" if parallel > 1 else "")

        headless_mode = True if args.headless else None
        async with Executor(db, headless=headless_mode, ai_client=ai,
                            credentials=credentials, state_graph=sg) as exc:
            if parallel > 1:
                results = await exc.run_parallel(valid_plans, concurrency=parallel)
            else:
                for plan in valid_plans:
                    tc_id = plan.get("id") or plan.get("test_id") or plan.get("_meta", {}).get("test_id", "?")
                    log.info("  %s ...", tc_id)
                    result = await exc.run(plan)
                    results.append(result)
                    icon = "✓" if result["status"] == "pass" else "✗"
                    log.info("  %s %s %s  (%dms)",
                             tc_id, icon, result["status"], result["duration_ms"])
                    if result["status"] == "fail":
                        for s in result.get("steps", []):
                            if s.get("status") == "fail":
                                log.error("    step fail: %s", s.get("reason"))
                        for a in result.get("assertions", []):
                            if a.get("status") == "fail":
                                log.error("    assert fail: %s", a.get("reason"))

        duration = int((time.monotonic() - t0) * 1000)
        passed   = sum(1 for r in results if r.get("status") == "pass")
        failed   = len(results) - passed

        log.info("\n   passed: %d  failed: %d  duration: %dms", passed, failed, duration)
        tok = get_token_tracker().format_line("run")
        if tok:
            log.info(tok)
        _print_passive_error_summary(results)

        # Save report
        output_dir = Path(args.output or "reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_path = output_dir / f"report_{ts}.json"
        summary_data = {"total": len(results), "passed": passed, "failed": failed, "duration_ms": duration}
        with open(report_path, "w") as f:
            json.dump({"summary": summary_data, "results": results, "generated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        html_path = _write_html_report(report_path, results, summary_data)
        log.info("   report  → %s", report_path)
        log.info("   html    → %s", html_path)

        return 1 if failed else 0
    finally:
        db.close()


async def cmd_status(args):
    db     = LocatorDB()
    stats  = db.stats()
    ai_ok  = False
    try:
        ai    = AIClient.from_env()
        ai_ok = True
        ai_str = f"{ai.provider} / {ai.model}"
    except EnvironmentError as e:
        ai_str = f"not configured ({e})"

    sg       = StateGraph(db)
    sg_stats = sg.stats()

    pages_with_shots = sum(1 for p in db.all_pages() if p.get("screenshot_path"))
    log.info("\n QAPal Status")
    log.info("   database        : %s", stats["db_path"])
    log.info("   total elements  : %d  (valid: %d)", stats["total_elements"], stats["valid_elements"])
    log.info("   pages crawled   : %d  (screenshots: %d)", stats["total_pages"], pages_with_shots)
    log.info("   semantic states : %d", stats.get("total_states", 0))
    log.info("   sessions        : %d", stats["total_sessions"])
    log.info("   low-conf        : %d", stats["low_confidence"])
    log.info("   with warnings   : %d", stats["with_warnings"])
    log.info("   graph edges     : %d  (pages: %d)",
             sg_stats["total_transitions"], sg_stats["unique_pages"])
    log.info("   AI client       : %s", ai_str)
    db.close()
    return 0


async def cmd_prd_run(args):
    from generator import TestGenerator

    ai = _get_ai_client()
    if not ai:
        log.error("QAPAL_AI_PROVIDER environment variable is required for prd-run.")
        return 1

    prd_files = args.prd
    urls = args.url
    db = LocatorDB()
    sg = StateGraph(db)
    t0 = time.monotonic()

    headless_mode   = True if args.headless else None
    credentials     = _load_credentials(args)
    update_baseline = getattr(args, "update_baseline", False)

    # --update-baseline: wipe stored baselines so the next run re-captures them
    if update_baseline:
        import shutil
        from executor import VISUAL_BASELINE_DIR
        if VISUAL_BASELINE_DIR.exists():
            shutil.rmtree(VISUAL_BASELINE_DIR)
            log.info("\n  [baseline] Cleared visual regression baselines → %s", VISUAL_BASELINE_DIR)

    # 1. Crawl — populates locator DB
    spider = getattr(args, "spider", False)
    depth  = getattr(args, "depth", 2)

    # Auto-spider when the nav graph is nearly empty (first run on a new site).
    # This ensures the AI has enough locator context to generate accurate plans.
    if not spider and sg.stats().get("unique_pages", 0) < 3:
        log.info("\n [auto] Nav graph is sparse — enabling --spider for first-run discovery.")
        spider = True

    log.info("\n [1/5] Crawling %d URL(s) to gather active locators%s...",
             len(urls), "  [spider mode]" if spider else "")
    async with Crawler(db, headless=headless_mode, credentials=credentials, state_graph=sg) as crawler:
        if spider:
            crawl_results = await crawler.spider_crawl(urls, max_depth=depth, force=args.force)
            urls = list({r["url"] for r in crawl_results if r.get("crawled")} | set(urls))
        else:
            await crawler.bulk_crawl(urls, force=args.force)

    # 2. Semantic pipeline — separate step so context can be reprocessed
    #    without re-crawling.  Uses live pages, no extra HTTP round-trips.
    headless_bool = headless_mode if headless_mode is not None else True
    log.info("\n [2/5] Extracting semantic context for %d URL(s)...", len(urls))
    processed = await _extract_semantics(db, urls, headless=headless_bool)
    log.info("   semantic contexts saved: %d/%d", processed, len(urls))

    num_tests       = getattr(args, "num_tests", None)
    negative_tests  = getattr(args, "negative_tests", False)
    use_compile     = getattr(args, "compile", False)

    compiled_model_path = None
    if use_compile:
        compiled_model_path = "compiled_model.json"
        log.info("\n [2.5/5] Compiling site model...")
        from site_compiler import SiteCompiler
        compiler = SiteCompiler(db, state_graph=sg)
        compiled_model = compiler.compile(output_path=compiled_model_path)
        log.info("   compiled %d locators → ~%d tokens",
                 compiled_model.locator_count, len(compiled_model.format_for_prompt()) // 4)
    elif Path("compiled_model.json").exists():
        # Auto-detect existing fresh compiled model
        from site_compiler import SiteCompiler
        existing = SiteCompiler.load("compiled_model.json")
        if existing and not existing.is_stale(max_age_minutes=120):
            compiled_model_path = "compiled_model.json"
            log.info("   [compile] Auto-detected compiled_model.json (%d locators)",
                     existing.locator_count)

    generator = TestGenerator(db, ai_client=ai, max_cases=args.max_cases, state_graph=sg,
                              max_locators=getattr(args, "max_locators", 400),
                              num_tests=num_tests, negative_tests=negative_tests,
                              compiled_model_path=compiled_model_path)
    output_dir = Path(args.output or "plans")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = []

    for prd_path_str in prd_files:
        prd_path = Path(prd_path_str)
        if not prd_path.exists():
            log.error("PRD file not found at %s", prd_path_str)
            continue

        log.info("\n Reading PRD: %s", prd_path)
        prd_content = prd_path.read_text('utf-8')

        # 3. Plan (Generate from PRD)
        get_token_tracker().reset()   # measure tokens for this plan generation alone
        log.info("\n [3/5] Generating execution plans from PRD: %s  [ai: %s]",
                 prd_path.name, ai.provider)
        try:
            plans = generator.generate_plans_from_prd(prd_content, urls, credentials=credentials)
        except Exception as e:
            log.error("Error generating plans for %s: %s", prd_path.name, e)
            continue

        # Derive a slug from the PRD filename to prefix plan IDs, preventing
        # plans from different PRDs overwriting each other (e.g. both start at TC001).
        # "bookshop_prd.md" → "bookshop", "toolbox.md" → "toolbox"
        prd_slug = re.sub(r"[_\-]prd$", "", prd_path.stem, flags=re.IGNORECASE)
        prd_slug = re.sub(r"[^a-zA-Z0-9]+", "-", prd_slug).strip("-").lower()

        valid_plans = []
        log.info("\n   Generated Plans:")
        for p in plans:
            tc_id = p.get("test_id", "unknown")
            if p.get("_planning_error"):
                log.error("  ✗ %s  %s", tc_id, p["_planning_error"])
            else:
                # Prefix test_id with slug if not already prefixed
                if prd_slug and not tc_id.startswith(prd_slug):
                    prefixed_id = f"{prd_slug}-{tc_id}"
                    p = {**p, "test_id": prefixed_id}
                    tc_id = prefixed_id
                path = output_dir / f"{tc_id}_plan.json"
                with open(path, "w") as f:
                    json.dump(p, f, indent=2)
                log.info("  ✓ %s  (%d steps)  → %s", tc_id, len(p.get("steps", [])), path)
                valid_plans.append(p)

        # Show token cost of this planning call
        tok = get_token_tracker().format_line("plan")
        if tok:
            log.info(tok)

        if not valid_plans:
            log.warning("\n No valid plans generated for %s.", prd_path.name)
            continue

        # 4. Execute
        parallel = getattr(args, "parallel", 1) or 1
        get_token_tracker().reset()   # measure AI tokens consumed by recovery during execution
        log.info("\n [4/5] Running %d generated plan(s) for %s%s...",
                 len(valid_plans), prd_path.name,
                 f"  [parallel={parallel}]" if parallel > 1 else "")

        async with Executor(db, headless=headless_mode, ai_client=ai,
                            credentials=credentials, state_graph=sg) as exc:
            if parallel > 1:
                results = await exc.run_parallel(valid_plans, concurrency=parallel)
                all_results.extend(results)
            else:
                for plan in valid_plans:
                    tc_id = plan.get("test_id", "?")
                    log.info("  %s ...", tc_id)
                    result = await exc.run(plan)
                    all_results.append(result)
                    icon = "✓" if result["status"] == "pass" else "✗"
                    log.info("  %s %s %s  (%dms)",
                             tc_id, icon, result["status"], result["duration_ms"])
                    if result["status"] == "fail":
                        for s in result.get("steps", []):
                            if s.get("status") == "fail":
                                log.error("    step fail: %s", s.get("reason"))
                        for a in result.get("assertions", []):
                            if a.get("status") == "fail":
                                log.error("    assert fail: %s", a.get("reason"))

        # Show AI tokens consumed by recovery (normally 0 — deterministic execution)
        tok = get_token_tracker().format_line("run")
        if tok:
            log.info(tok)

    # 5. Report
    duration = int((time.monotonic() - t0) * 1000)
    passed   = sum(1 for r in all_results if r.get("status") == "pass")
    failed   = len(all_results) - passed

    log.info("\n [5/5] Summary")
    log.info("   passed: %d  failed: %d  total duration: %dms", passed, failed, duration)
    _print_passive_error_summary(all_results)
    _print_visual_regression_summary(all_results)

    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path  = report_dir / f"prd_report_{ts}.json"
    summary_data = {"total": len(all_results), "passed": passed, "failed": failed, "duration_ms": duration}
    with open(report_path, "w") as f:
        json.dump({"summary": summary_data, "results": all_results, "generated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    html_path = _write_html_report(report_path, all_results, summary_data)
    log.info("   report  → %s", report_path)
    log.info("   html    → %s", html_path)

    db.close()
    return 1 if failed else 0


# ── Scan command (URL → auto PRD → plan → execute → report) ──────────

_SCAN_PRD_SYSTEM = """\
You are a senior QA engineer. Your job is to read raw crawl data from a website
(page inventory, form fields, navigation flows, button labels) and write a
professional Product Requirements Document (PRD) that describes the site's key
user flows for test automation.

OUTPUT FORMAT — plain Markdown, no fences:

# <Site Name> Test Plan

## Site Purpose
One sentence describing what this site does.

## User Flows

### TC1: <Goal-oriented name, e.g. "User can log in">
**Preconditions:** <what must be true before this test — e.g. "fresh browser, not logged in">
**Steps:**
1. Navigate to <url>
2. <Action on element>
3. ...
**Expected Outcomes:**
- URL changes to <path>
- <Visible text or element that confirms success>

... (one section per flow)

RULES:
- Write 6–10 test cases covering real business value (not just "page loads")
- Every test must be self-contained: include login steps before auth-required flows
- For multi-step flows (cart → checkout → payment), describe ALL steps in order
- Expected outcomes must be specific: URL pattern OR visible element, not generic
- If you see login/register forms: write auth tests first (they unlock other flows)
- If you see a cart/checkout flow: trace it fully — add item → cart → checkout → confirm
- If you see search: test search → results → item click
- Never invent pages or buttons not present in the crawl data
- For dropdown/submenu navigation: if a category or section link is reachable via a direct
  URL (visible in Navigation Flows), use navigate(url) rather than clicking a hidden dropdown
  item — this avoids hover-to-reveal race conditions in automated tests
- Never test navigation to links that likely go external (e.g. "Documentation", "Blog",
  social media icons, footer legal links) — these open new tabs and URL assertions fail
"""

_SCAN_PRD_PROMPT = """\
## Site URL
{url}

## Pages Discovered ({page_count} pages)
{page_inventory}

## Navigation Flows (observed click → URL transitions)
{nav_flows}

## Interactive Elements Per Page
{elements_per_page}
"""


def _build_scan_inventory(locator_db, crawl_results: list) -> tuple:
    """
    Build three text blocks for the PRD synthesis prompt:
      - page_inventory:    list of pages with element counts
      - nav_flows:         page transition descriptions
      - elements_per_page: forms/buttons/links per page
    Returns (page_inventory, nav_flows, elements_per_page).
    """
    from urllib.parse import urlparse

    crawled_urls = [r["url"] for r in crawl_results if r.get("crawled")]
    all_locs = locator_db.get_all_locators(valid_only=True)

    # Group locators by page
    by_url: dict = {}
    for loc in all_locs:
        u = loc.get("url", "")
        if u:
            by_url.setdefault(u, []).append(loc)

    # Page inventory
    inv_lines = []
    for u in crawled_urls[:15]:
        locs = by_url.get(u, [])
        roles = {l.get("identity", {}).get("role", "") for l in locs}
        has_form = bool(roles & {"textbox", "combobox", "searchbox", "checkbox"})
        has_nav  = bool(roles & {"link"})
        has_btn  = bool(roles & {"button"})
        tags = []
        if has_form: tags.append("form")
        if has_nav:  tags.append("nav")
        if has_btn:  tags.append("buttons")
        inv_lines.append(f"- {u}  [{', '.join(tags) or 'static'}]  ({len(locs)} elements)")
    page_inventory = "\n".join(inv_lines) or "(none)"

    # Elements per page
    elem_lines = []
    for u in crawled_urls[:10]:
        locs = by_url.get(u, [])
        if not locs:
            continue
        path = urlparse(u).path or "/"
        elem_lines.append(f"\n### {path}")
        forms = [l for l in locs if l.get("identity", {}).get("role") in
                 ("textbox", "combobox", "searchbox", "checkbox")]
        btns  = [l for l in locs if l.get("identity", {}).get("role") == "button"]
        links = [l for l in locs if l.get("identity", {}).get("role") == "link"
                 and l.get("identity", {}).get("container") != "nav"]
        nav_links = [l for l in locs if l.get("identity", {}).get("role") == "link"
                     and l.get("identity", {}).get("container") == "nav"]
        if forms:
            names = [l.get("identity", {}).get("name", "?") for l in forms[:8]]
            elem_lines.append(f"  Inputs: {', '.join(n for n in names if n)}")
        if btns:
            names = [l.get("identity", {}).get("name", "?") for l in btns[:6]]
            elem_lines.append(f"  Buttons: {', '.join(n for n in names if n)}")
        if links:
            names = [l.get("identity", {}).get("name", "?") for l in links[:6]]
            elem_lines.append(f"  Links: {', '.join(n for n in names if n)}")
        if nav_links:
            def _href(loc):
                chain = loc.get("locators", {}).get("chain", [])
                return next((c.get("value", "") for c in chain if c.get("strategy") == "href"), "")
            nav_parts = []
            for l in nav_links[:8]:
                name = l.get("identity", {}).get("name", "?")
                href = _href(l)
                if href:
                    nav_parts.append(f"{name} ({href})")
                else:
                    nav_parts.append(name)
            elem_lines.append(f"  Nav links: {', '.join(nav_parts)}")
    elements_per_page = "\n".join(elem_lines) or "(no element data)"

    # Nav flows
    nav_lines = []
    for u in crawled_urls[:10]:
        locs = by_url.get(u, [])
        nav_locs = [l for l in locs if l.get("identity", {}).get("role") == "link"]
        for loc in nav_locs[:5]:
            name = loc.get("identity", {}).get("name", "")
            chain = loc.get("locators", {}).get("chain", [])
            href = next((c.get("value", "") for c in chain
                         if c.get("strategy") == "href"), "")
            if name and href and href.startswith("http"):
                nav_lines.append(
                    f"- {urlparse(u).path or '/'} → click \"{name}\" → {urlparse(href).path or '/'}"
                )
    nav_flows = "\n".join(nav_lines[:30]) or "(will be populated after first test run)"

    return page_inventory, nav_flows, elements_per_page


async def _scan_synthesize_prd(locator_db, url: str, crawl_results: list, ai_client) -> str:
    """Use AI to write a flow-aware PRD from crawl data. Falls back to element list."""
    page_inventory, nav_flows, elements_per_page = _build_scan_inventory(
        locator_db, crawl_results
    )
    page_count = sum(1 for r in crawl_results if r.get("crawled"))

    prompt = _SCAN_PRD_PROMPT.format(
        url=url,
        page_count=page_count,
        page_inventory=page_inventory,
        nav_flows=nav_flows,
        elements_per_page=elements_per_page,
    )

    try:
        model_override = ai_client.small_model if hasattr(ai_client, "small_model") else None
        prd = await asyncio.to_thread(
            ai_client.complete,
            prompt,
            _SCAN_PRD_SYSTEM,
            4096,
            0,
            model_override,
        )
        log.info("   AI-synthesized PRD: %d chars, %d pages analysed", len(prd), page_count)
        return prd
    except Exception as exc:
        log.warning("   PRD synthesis failed (%s), falling back to element list", exc)
        # Simple fallback
        crawled_urls = [r["url"] for r in crawl_results if r.get("crawled")]
        lines = [
            f"# Smoke Test for {url}", "",
            "## Test Scope",
            f"Verify the basic functionality of {url}.", "",
            "## Test Cases", "",
            "### TC1: Page loads and core elements are visible",
            f"- Navigate to {url}",
            "- Verify the page loads without errors",
            "- Verify the page title is non-empty",
        ]
        all_locs = locator_db.get_all_locators(valid_only=True)
        nav_links = [l for l in all_locs if l.get("identity", {}).get("role") == "link"
                     and l.get("identity", {}).get("container") == "nav"]
        if nav_links:
            lines += ["", "### TC2: Navigation links are functional",
                       "- Click primary navigation links and verify pages load"]
            for link in nav_links[:3]:
                name = link.get("identity", {}).get("name", "")
                if name:
                    lines.append(f"- Navigation link: \"{name}\"")
        if len(crawled_urls) > 1:
            lines += ["", "## Discovered Pages"]
            for page_url in crawled_urls[:10]:
                page_locs = locator_db.get_all(page_url, valid_only=True)
                lines.append(f"- {page_url} ({len(page_locs)} elements)")
        return "\n".join(lines)


_SCAN_PRD_EVOLVE_SYSTEM = """\
You are a senior QA engineer maintaining a living test plan for a website.
You are given the CURRENT PRD and the LATEST crawl data.

Compare them and update the PRD:
- If new pages or features were discovered in the crawl → ADD new test cases
- If existing pages changed (elements added/removed/renamed) → UPDATE affected test cases
- If a page no longer exists in the crawl data → mark its test case as DEPRECATED (add "[DEPRECATED]" prefix)
- If nothing changed → return the PRD EXACTLY as-is, character for character

Do NOT remove test cases — only add, update, or mark deprecated.
Keep the same Markdown format and TC numbering. New test cases get the next available TC number.
Output the complete updated PRD as plain Markdown, no fences.
"""

_SCAN_PRD_EVOLVE_PROMPT = """\
## CURRENT PRD
{old_prd}

## LATEST CRAWL DATA

### Site URL
{url}

### Pages Discovered ({page_count} pages)
{page_inventory}

### Navigation Flows
{nav_flows}

### Interactive Elements Per Page
{elements_per_page}
"""


async def _scan_evolve_prd(locator_db, url: str, crawl_results: list, ai_client, old_prd: str) -> str:
    """Evolve an existing PRD with new crawl data. Returns updated PRD."""
    page_inventory, nav_flows, elements_per_page = _build_scan_inventory(
        locator_db, crawl_results
    )
    page_count = sum(1 for r in crawl_results if r.get("crawled"))

    prompt = _SCAN_PRD_EVOLVE_PROMPT.format(
        old_prd=old_prd,
        url=url,
        page_count=page_count,
        page_inventory=page_inventory,
        nav_flows=nav_flows,
        elements_per_page=elements_per_page,
    )

    try:
        model_override = ai_client.small_model if hasattr(ai_client, "small_model") else None
        prd = await asyncio.to_thread(
            ai_client.complete,
            prompt,
            _SCAN_PRD_EVOLVE_SYSTEM,
            4096,
            0,
            model_override,
        )
        log.info("   AI-evolved PRD: %d chars", len(prd))
        return prd
    except Exception as exc:
        log.warning("   PRD evolution failed (%s), keeping existing PRD", exc)
        return old_prd


async def cmd_scan(args):
    """Scan a site: crawl, evolve PRD, generate/evolve test plans, output diff.

    Does NOT execute tests. Use `qapal run` for deterministic execution.

    Stateful — each domain gets persistent state at .qapal/<domain>/:
      - db.json          locator database (crawl data)
      - prd.md           synthesized PRD (evolves with site)
      - plans/           proposed test plans (new + adjusted + unchanged)

    Workflow:  scan → review → promote to test suite → run in CI
    """
    from generator import TestGenerator
    from urllib.parse import urlparse

    ai = _get_ai_client()
    if not ai:
        log.error("QAPAL_AI_PROVIDER environment variable is required for scan.")
        return 1

    url = args.url
    headless_mode   = True if args.headless else None
    credentials     = _load_credentials(args)
    device, viewport = _get_device_args(args)
    depth           = getattr(args, "depth", 2)
    num_tests       = getattr(args, "num_tests", None)
    max_locators    = getattr(args, "max_locators", 400)
    fresh           = getattr(args, "fresh", False)

    # Persistent state per domain — stored in .qapal/<domain>/
    slug = urlparse(url).netloc.replace(".", "-").replace(":", "-")
    domain_dir = Path(".qapal") / slug
    domain_dir.mkdir(parents=True, exist_ok=True)
    db_path   = domain_dir / "db.json"
    prd_path  = domain_dir / "prd.md"
    plans_dir = domain_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    is_first_run = not db_path.exists()

    if fresh:
        import shutil
        if domain_dir.exists():
            shutil.rmtree(domain_dir)
            domain_dir.mkdir(parents=True, exist_ok=True)
            plans_dir.mkdir(parents=True, exist_ok=True)
        log.info("   --fresh: cleared all state for %s", slug)
        is_first_run = True

    db = LocatorDB(path=str(db_path))
    sg = StateGraph(db)
    t0 = time.monotonic()

    prev_stats = db.stats()
    prev_locators = prev_stats.get("valid_elements", 0)
    prev_pages = prev_stats.get("total_pages", 0)

    log.info("\n QAPal Scan: %s", url)
    log.info("   AI: %s / %s", ai.provider, ai.model)
    log.info("   State: %s (%s)", domain_dir,
             "new" if is_first_run else f"cached: {prev_locators} locators, {prev_pages} pages")

    try:
        # ── Phase 1: Crawl ──────────────────────────────────────────
        force_crawl = fresh or is_first_run
        log.info("\n [1/4] Crawling site (depth=%d%s)...",
                 depth, ", full" if force_crawl else ", incremental")
        async with Crawler(db, headless=headless_mode, credentials=credentials,
                           state_graph=sg, device=device, viewport=viewport) as crawler:
            crawl_results = await crawler.spider_crawl(
                [url], max_depth=depth, force=force_crawl,
            )

        crawled_count = sum(1 for r in crawl_results if r.get("crawled"))
        skipped_count = sum(1 for r in crawl_results if not r.get("crawled"))
        crawled_urls = list({r["url"] for r in crawl_results if r.get("crawled")} | {url})
        log.info("   Crawled %d pages%s",
                 crawled_count,
                 f" (skipped {skipped_count} cached)" if skipped_count else "")

        # Semantic extraction (only for newly crawled pages)
        if crawled_urls:
            headless_bool = headless_mode if headless_mode is not None else True
            log.info("   Extracting semantic context for %d URL(s)...", len(crawled_urls))
            processed = await _extract_semantics(db, crawled_urls, headless=headless_bool)
            log.info("   Semantic contexts saved: %d/%d", processed, len(crawled_urls))

        # ── Phase 2: Evolve PRD ─────────────────────────────────────
        log.info("\n [2/4] Evolving PRD...")
        get_token_tracker().reset()

        # Build crawl_results for ALL known pages (cached + fresh)
        all_crawl_results = crawl_results[:]
        for page in db.all_pages():
            page_url = page.get("url", "")
            if page_url and not any(r["url"] == page_url for r in all_crawl_results):
                all_crawl_results.append({"url": page_url, "crawled": True})

        old_prd = prd_path.read_text(encoding="utf-8") if prd_path.exists() else None

        if old_prd and crawled_count == 0:
            # No new pages — PRD stays as-is
            prd_content = old_prd
            log.info("   PRD unchanged (no new crawl data)")
        elif old_prd and crawled_count > 0:
            # New pages discovered — evolve the PRD
            prd_content = await _scan_evolve_prd(db, url, all_crawl_results, ai, old_prd)
            if prd_content.strip() == old_prd.strip():
                log.info("   PRD unchanged (AI found no new flows)")
            else:
                log.info("   PRD updated with new crawl data")
        else:
            # First run — synthesize from scratch
            prd_content = await _scan_synthesize_prd(db, url, all_crawl_results, ai)
            log.info("   PRD generated (%d chars)", len(prd_content))

        prd_path.write_text(prd_content, encoding="utf-8")
        log.info("   PRD → %s", prd_path)

        tok = get_token_tracker().format_line("prd")
        if tok:
            log.info(tok)

        # ── Phase 3: Evolve test plans ──────────────────────────────
        log.info("\n [3/4] Generating test plans  [ai: %s]", ai.provider)
        get_token_tracker().reset()

        # Load existing plans from previous scans
        existing_plans = []
        for plan_file in sorted(plans_dir.glob("*.json")):
            try:
                with open(plan_file) as f:
                    existing_plans.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue

        generator = TestGenerator(db, ai_client=ai, state_graph=sg,
                                  max_locators=max_locators, num_tests=num_tests)

        if existing_plans:
            # Incremental: pass existing plans so AI can adjust + add new ones
            plans = generator.generate_plans_from_prd(
                _build_incremental_prd(prd_content, existing_plans),
                [url], credentials=credentials,
            )
        else:
            plans = generator.generate_plans_from_prd(prd_content, [url], credentials=credentials)

        valid_plans = [p for p in plans if not p.get("_planning_error")]
        if not valid_plans:
            log.error("No valid plans generated.")
            return 1

        tok = get_token_tracker().format_line("plan")
        if tok:
            log.info(tok)

        # ── Phase 4: Diff & save ────────────────────────────────────
        log.info("\n [4/4] Plan diff")

        # Build maps for comparison
        old_by_id = {p.get("test_id", ""): p for p in existing_plans}
        new_by_id = {p.get("test_id", ""): p for p in valid_plans}

        added    = []
        modified = []
        unchanged = []

        for tc_id, plan in new_by_id.items():
            if tc_id not in old_by_id:
                added.append(plan)
            else:
                # Compare steps + assertions (ignore _meta)
                old_core = {"steps": old_by_id[tc_id].get("steps"), "assertions": old_by_id[tc_id].get("assertions")}
                new_core = {"steps": plan.get("steps"), "assertions": plan.get("assertions")}
                if json.dumps(old_core, sort_keys=True) != json.dumps(new_core, sort_keys=True):
                    modified.append(plan)
                else:
                    unchanged.append(plan)

        deprecated = [old_by_id[tid] for tid in old_by_id if tid not in new_by_id]

        # Save all plans to domain plans dir
        for p in valid_plans:
            tc_id = p.get("test_id", "unknown")
            path = plans_dir / f"{tc_id}.json"
            with open(path, "w") as f:
                json.dump(p, f, indent=2)

        # Print diff summary
        if added:
            log.info("   ✚ NEW (%d):", len(added))
            for p in added:
                log.info("     + %s: %s  (%d steps)",
                         p.get("test_id"), p.get("name", ""), len(p.get("steps", [])))

        if modified:
            log.info("   ✎ MODIFIED (%d):", len(modified))
            for p in modified:
                log.info("     ~ %s: %s", p.get("test_id"), p.get("name", ""))

        if unchanged:
            log.info("   ═ UNCHANGED (%d):", len(unchanged))
            for p in unchanged:
                log.info("     = %s", p.get("test_id"))

        if deprecated:
            log.info("   ✖ DEPRECATED (%d):", len(deprecated))
            for p in deprecated:
                log.info("     - %s: %s", p.get("test_id"), p.get("name", ""))
                # Remove deprecated plan files
                dep_path = plans_dir / f"{p.get('test_id', 'unknown')}.json"
                if dep_path.exists():
                    dep_path.unlink()

        # ── Summary ─────────────────────────────────────────────────
        duration = int((time.monotonic() - t0) * 1000)
        new_stats = db.stats()

        log.info("\n ─── Scan Summary ───")
        log.info("   URL:        %s", url)
        log.info("   Pages:      %d crawled, %d total known", crawled_count, new_stats.get("total_pages", 0))
        log.info("   Locators:   %d total", new_stats.get("valid_elements", 0))
        log.info("   Plans:      %d total  (+%d new, ~%d modified, -%d deprecated)",
                 len(valid_plans), len(added), len(modified), len(deprecated))
        log.info("   Duration:   %dms", duration)
        log.info("")
        log.info("   State:  %s", domain_dir)
        log.info("   PRD:    %s", prd_path)
        log.info("   Plans:  %s/*.json", plans_dir)
        log.info("")
        log.info("   Next steps:")
        log.info("     Review:  cat %s", prd_path)
        log.info("     Run:     python main.py run --plan '%s/*.json'", plans_dir)
        log.info("     Promote: cp %s/*.json tests/", plans_dir)

        return 0

    finally:
        db.close()


def _build_incremental_prd(prd_content: str, existing_plans: list) -> str:
    """Append existing test summaries to PRD so the AI knows what's already covered."""
    lines = [prd_content, "", "---", "",
             "## EXISTING TESTS (already generated — adjust if needed, add new ones for gaps)", ""]
    for p in existing_plans:
        tc_id = p.get("test_id", "?")
        name = p.get("name", "")
        steps = p.get("steps", [])
        step_summary = " → ".join(
            s.get("action", "?") + (" " + s.get("url", "") if s.get("action") == "navigate" else "")
            for s in steps[:5]
        )
        assertions = p.get("assertions", [])
        assert_summary = ", ".join(a.get("type", "?") for a in assertions[:3])
        lines.append(f"- **{tc_id}**: {name}")
        lines.append(f"  Steps: {step_summary}")
        if assert_summary:
            lines.append(f"  Asserts: {assert_summary}")
    return "\n".join(lines)


# ── Compile command ───────────────────────────────────────────────────

async def cmd_compile(args):
    """Compile the locator DB into a compact compiled_model.json."""
    from site_compiler import SiteCompiler

    db   = LocatorDB()
    sg   = StateGraph(db)
    out  = getattr(args, "output", "compiled_model.json") or "compiled_model.json"

    stats = db.stats()
    log.info("\n [compile] Compiling locator DB (%d elements → %s)", stats["total_elements"], out)
    t0 = time.monotonic()

    compiler = SiteCompiler(db, state_graph=sg)
    model    = compiler.compile(output_path=out)

    duration = int((time.monotonic() - t0) * 1000)
    log.info("   locators   : %d", model.locator_count)
    log.info("   prompt size: ~%d tokens", len(model.format_for_prompt()) // 4)
    log.info("   output     : %s", out)
    log.info("   duration   : %dms", duration)
    log.info("\n Preview:\n%s", model.format_for_prompt())

    db.close()
    return 0


# ── Codegen command ───────────────────────────────────────────────────

async def cmd_codegen(args):
    """
    Convert QAPAL JSON plan(s) into standalone pytest-playwright .py files.

    Examples:
      python main.py codegen --plan plans/TC001_login.json
      python main.py codegen --plans plans/TC*.json --output tests/generated/
    """
    from codegen import codegen_plan_file, codegen_plans
    import glob as _glob

    output_dir = args.output or "tests/generated"

    # Collect target plan files
    plan_files: List[str] = []
    if getattr(args, "plan", None):
        plan_files = [args.plan]
    elif getattr(args, "plans", None):
        for pattern in args.plans:
            plan_files.extend(_glob.glob(pattern))
        plan_files = sorted(set(plan_files))

    if not plan_files:
        log.error("No plan files specified. Use --plan FILE or --plans GLOB")
        return 1

    log.info("\n [codegen] Converting %d plan(s) → %s/", len(plan_files), output_dir)
    t0 = time.monotonic()

    if len(plan_files) == 1:
        out = codegen_plan_file(plan_files[0], output_dir)
        log.info("   ✓ %s", out)
    else:
        outs = codegen_plans(plan_files, output_dir)
        for o in outs:
            log.info("   ✓ %s", o)

    duration = int((time.monotonic() - t0) * 1000)
    log.info("   %d file(s) written in %dms", len(plan_files), duration)
    log.info("\n Run with:  pytest %s/ -v", output_dir)
    return 0


# ── Graph-crawl command ───────────────────────────────────────────────

async def cmd_graph_crawl(args):
    """
    Navigate the site naturally — clicking every unique link on each page —
    and record transitions into the State Graph while crawling each new page
    for locators.  No regex deduplication: the graph's own edge deduplication
    ensures each unique navigation is recorded exactly once.
    """
    from playwright.async_api import async_playwright
    from state_graph import StateGraph
    from locator_db import _normalize_url

    urls        = args.urls
    max_pages   = args.max_pages
    depth       = args.depth
    headless    = bool(args.headless)
    credentials = _load_credentials(args)

    db = LocatorDB()
    sg = StateGraph(db)
    t0 = time.monotonic()

    allowed_domains = {__import__("urllib.parse", fromlist=["urlparse"]).urlparse(u).netloc for u in urls}

    visited   = set()   # normalized URLs already crawled
    queue     = [(u, 0) for u in urls]
    pages_done = 0

    log.info("\n Graph-crawl  [db: %s]", db._path)
    log.info("   start URLs : %d  |  max pages: %d  |  depth: %d", len(urls), max_pages, depth)

    async with async_playwright() as pw:
        pw.selectors.set_test_id_attribute("data-test")
        browser = await pw.chromium.launch(headless=headless)

        async def _visit(url: str, current_depth: int):
            nonlocal pages_done
            from crawler import _build_context, crawl_page, wait_for_stable
            norm = _normalize_url(url)
            if norm in visited or pages_done >= max_pages:
                return []
            visited.add(norm)

            ctx  = await _build_context(browser, db, url, credentials)
            page = await ctx.new_page()
            discovered = []
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await wait_for_stable(page)

                # Crawl this page for locators
                result = await crawl_page(page, norm, db)
                pages_done += 1
                status = f"{result['elements']} elements | {result['new']} new"
                log.info("  [graph-crawl] (%d/%d) %s — %s", pages_done, max_pages, norm, status)

                if current_depth >= depth:
                    return []

                # Extract all same-domain links and record transitions
                hrefs = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({href: e.href, text: (e.textContent||'').trim()}))"
                    ".filter(o => o.href && !o.href.startsWith('javascript') && !o.href.startsWith('mailto'))"
                )
                from urllib.parse import urlparse as _up
                for item in hrefs:
                    href = item.get("href", "")
                    label = item.get("text", "") or href
                    if not href or _up(href).netloc not in allowed_domains:
                        continue
                    dest_norm = _normalize_url(href)
                    if dest_norm == norm:
                        continue
                    # Record the navigation edge in the graph
                    sg.record_transition(
                        from_url       = norm,
                        to_url         = dest_norm,
                        trigger_action = "click",
                        trigger_label  = label,
                        session_id     = "graph-crawl",
                    )
                    if dest_norm not in visited:
                        discovered.append(href)
            except Exception as e:
                log.warning("  [graph-crawl] failed %s: %s", norm, e)
            finally:
                await ctx.close()
            return discovered

        # BFS level by level
        for lvl in range(depth + 1):
            if not queue or pages_done >= max_pages:
                break
            current = [(u, d) for u, d in queue if d == lvl]
            queue   = [(u, d) for u, d in queue if d != lvl]
            for url, d in current:
                if pages_done >= max_pages:
                    break
                new_hrefs = await _visit(url, d)
                for href in new_hrefs:
                    norm = _normalize_url(href)
                    if norm not in visited:
                        queue.append((href, d + 1))

        await browser.close()

    sg_stats = sg.stats()
    duration = int((time.monotonic() - t0) * 1000)
    log.info("\n Graph-crawl complete")
    log.info("   pages crawled : %d", pages_done)
    log.info("   graph edges   : %d  (pages: %d)",
             sg_stats["total_transitions"], sg_stats["unique_pages"])
    log.info("   duration      : %dms", duration)
    db.close()
    return 0


# ── Graph command ─────────────────────────────────────────────────────

async def cmd_graph(args):
    """Display the state graph of recorded page transitions."""
    from urllib.parse import urlparse

    db      = LocatorDB()
    sg      = StateGraph(db)
    stats   = sg.stats()

    log.info("\n State Graph  [db: %s]", db._path)
    log.info("   graph edges  : %d", stats["total_transitions"])
    log.info("   unique pages : %d", stats["unique_pages"])

    if stats["total_transitions"] == 0:
        log.info("\n   (no transitions recorded yet — run tests first)")
        db.close()
        return 0

    # ── --stats only ─────────────────────────────────────────────────
    if getattr(args, "stats", False):
        if stats["most_traversed"]:
            log.info("\n   Most traversed:")
            for frm, to, count in stats["most_traversed"]:
                log.info("     %s → %s  (%dx)", frm, to, count)
        db.close()
        return 0

    # ── --path FROM TO ────────────────────────────────────────────────
    if getattr(args, "path", None):
        from_url, to_url = args.path
        path = sg.get_path(from_url, to_url)
        if path is None:
            log.info("\n   No path found from %s to %s", from_url, to_url)
        else:
            hops = len(path)
            log.info("\n   Shortest path (%d hop%s):", hops, "s" if hops != 1 else "")
            for i, edge in enumerate(path):
                t  = edge["trigger"]
                frm = edge["from_url"]
                log.info("     %d. %s", i + 1, frm)
                log.info('        %s "%s"', t["action"], t["label"])
            log.info("     %d. %s", hops + 1, to_url)
        db.close()
        return 0

    # ── full graph dump ───────────────────────────────────────────────
    from_filter = getattr(args, "from_url", None)
    to_filter   = getattr(args, "to_url",   None)

    transitions = sg.all_transitions()
    if from_filter:
        transitions = [t for t in transitions if t["from_url"] == from_filter]
    if to_filter:
        transitions = [t for t in transitions if t["to_url"]   == to_filter]

    transitions = sorted(transitions, key=lambda t: t["traversal_count"], reverse=True)

    log.info("\n Edges (%d shown):", len(transitions))
    for t in transitions:
        tr  = t["trigger"]
        frm = t["from_url"]
        to  = t["to_url"]
        act = tr.get("action", "?")
        lbl = tr.get("label", "")
        cnt = t["traversal_count"]
        log.info("   %s", frm)
        log.info('     --[%s "%s"]--> %s  (%dx)', act, lbl, to, cnt)

    db.close()
    return 0


# ── Explore command ───────────────────────────────────────────────────

async def cmd_explore(args):
    """Autonomously explore an app using vision-guided navigation."""
    from explorer import Explorer
    from vision_client import VisionClient
    from ux_report import generate_exploration_report

    url  = args.url
    goal = getattr(args, "goal", "") or "Explore the application and find UX issues"
    db   = LocatorDB()
    sg   = StateGraph(db)

    ai = _get_ai_client()
    try:
        vision = VisionClient.from_env()
    except EnvironmentError as e:
        log.warning("Vision client not available (%s) — using text-only exploration", e)
        vision = None

    headless    = bool(args.headless)
    credentials = _load_credentials(args)
    max_steps   = getattr(args, "max_steps", 30)

    log.info("\n [explore] Starting autonomous exploration")
    log.info("   URL:       %s", url)
    log.info("   Goal:      %s", goal)
    log.info("   Max steps: %d", max_steps)
    log.info("   Vision:    %s", "enabled" if vision else "disabled (text-only)")

    t0 = time.monotonic()
    async with Explorer(
        db, vision_client=vision, ai_client=ai,
        headless=headless, credentials=credentials, state_graph=sg,
    ) as explorer:
        trace = await explorer.explore(url, goal=goal, max_steps=max_steps)

    duration = int((time.monotonic() - t0) * 1000)

    log.info("\n [explore] Exploration complete")
    log.info("   Steps taken:   %d", len(trace.steps))
    log.info("   Pages visited: %d", trace.pages_visited)
    log.info("   Vision calls:  %d", trace.vision_calls)
    log.info("   UX findings:   %d", len(trace.ux_findings))
    log.info("   Duration:      %dms", duration)
    tok = get_token_tracker().format_line("explore")
    if tok:
        log.info(tok)

    # Severity breakdown
    sev_counts = {"high": 0, "medium": 0, "low": 0}
    for f in trace.ux_findings:
        sev_counts[f.get("severity", "low")] = sev_counts.get(f.get("severity", "low"), 0) + 1
    log.info("   Severity:      %d high, %d medium, %d low",
             sev_counts["high"], sev_counts["medium"], sev_counts["low"])

    # Log top findings
    high_findings = [f for f in trace.ux_findings if f.get("severity") == "high"]
    if high_findings:
        log.info("\n   High-severity findings:")
        for f in high_findings[:5]:
            log.info("     - [%s] %s", f.get("category", "?"), f.get("description", "")[:100])

    # Generate report
    html_path, json_path = generate_exploration_report(trace, output_dir="reports")
    log.info("\n   report → %s", html_path)
    log.info("   json   → %s", json_path)
    log.info("   trace  → reports/exploration/%s/trace.json", trace.session_id)

    db.close()
    return 0


# ── UX Audit command ─────────────────────────────────────────────────

async def cmd_ux_audit(args):
    """Run UX heuristic evaluation on one or more URLs."""
    from playwright.async_api import async_playwright
    from ux_evaluator import UXEvaluator, UXAuditResult
    from ux_report import generate_ux_report
    from vision_client import VisionClient

    urls = args.urls
    if not urls:
        log.error("No URLs provided. Use --urls https://...")
        return 1

    db = LocatorDB()
    try:
        vision = VisionClient.from_env()
    except EnvironmentError:
        vision = None

    headless     = bool(args.headless)
    credentials  = _load_credentials(args)
    static_only  = getattr(args, "static", False)

    evaluator = UXEvaluator(db, vision_client=vision)

    log.info("\n [ux-audit] Auditing %d URL(s)", len(urls))
    log.info("   Mode:   %s", "static (DB only)" if static_only else "live (browser + vision)")
    log.info("   Vision: %s", "enabled" if vision and not static_only else "disabled")

    t0           = time.monotonic()
    all_findings = []
    vision_calls = 0

    if static_only:
        # Static mode: only check what's in the DB
        for url in urls:
            findings = evaluator.audit_static(url)
            all_findings.extend(findings)
            log.info("   %s: %d finding(s)", url, len(findings))
    else:
        # Live mode: open browser, run DOM + vision checks
        async with async_playwright() as pw:
            pw.selectors.set_test_id_attribute("data-test")
            browser = await pw.chromium.launch(headless=headless)

            for url in urls:
                ctx  = await browser.new_context()
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(1500)  # let SPA render

                    # Capture screenshot for vision analysis
                    screenshot_bytes = await page.screenshot(full_page=False)

                    findings = await evaluator.audit_url(
                        page, url=url, screenshot_bytes=screenshot_bytes,
                    )
                    all_findings.extend(findings)

                    vision_count = sum(1 for f in findings if f.source == "vision")
                    vision_calls += min(1, vision_count)  # 1 vision call per page
                    rule_count   = sum(1 for f in findings if f.source == "rule")
                    log.info("   %s: %d finding(s) (%d rule, %d vision)",
                             url, len(findings), rule_count, vision_count)

                except Exception as e:
                    log.error("   %s: ERROR — %s", url, e)
                finally:
                    await ctx.close()

            await browser.close()

    duration = int((time.monotonic() - t0) * 1000)
    score    = UXEvaluator.compute_score(all_findings)

    audit = UXAuditResult(
        urls          = urls,
        findings      = all_findings,
        score         = score,
        audited_at    = datetime.now(timezone.utc).isoformat(),
        duration_ms   = duration,
        vision_calls  = vision_calls,
        pages_audited = len(urls),
    )

    sev = audit.severity_counts
    log.info("\n [ux-audit] Audit complete")
    log.info("   Score:    %d/100 (Grade: %s)", score, audit.grade)
    log.info("   Findings: %d total (%d high, %d medium, %d low)",
             len(all_findings), sev["high"], sev["medium"], sev["low"])
    log.info("   Duration: %dms", duration)
    tok = get_token_tracker().format_line("ux-audit")
    if tok:
        log.info(tok)

    # Generate reports
    html_path, json_path = generate_ux_report(audit, output_dir="reports")
    log.info("\n   report → %s", html_path)
    log.info("   json   → %s", json_path)

    db.close()
    return 0


# ── Entry point ───────────────────────────────────────────────────────

def main():
    setup_logging()
    parser = argparse.ArgumentParser(
        description="QAPal — deterministic AI UI testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    def _add_device_args(p):
        """Add --device and --viewport flags to a subparser."""
        p.add_argument("--device", default=None,
                       help="Playwright device preset (e.g. 'iPhone 12', 'Pixel 5', 'iPad Pro')")
        p.add_argument("--viewport", nargs=2, type=int, metavar=("W", "H"),
                       help="Custom viewport width and height (overrides device default)")

    # crawl
    p = sub.add_parser("crawl", help="Crawl pages and populate the locator DB")
    p.add_argument("--urls",  "-u", nargs="+", required=True, help="URLs to crawl")
    p.add_argument("--force", "-f", action="store_true",      help="Force re-crawl even if fresh")
    p.add_argument("--headless", "-H", action="store_true",   help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials (url, username, password, selectors)")
    p.add_argument("--spider", action="store_true",
                   help="Follow links and crawl the whole site from the starting URLs")
    p.add_argument("--depth", type=int, default=2, metavar="N",
                   help="Max link-follow depth for --spider (default: 2)")

    # plan
    p = sub.add_parser("plan", help="Generate execution plans from test cases")
    p.add_argument("--tests",  "-t", nargs="+", required=True, help="Test case JSON files")
    p.add_argument("--output", "-o", help="Output directory for plans (default: plans/)")

    # run
    p = sub.add_parser("run", help="Execute tests (plan+run or run pre-generated plans)")
    p.add_argument("--tests",  "-t", nargs="+", help="Test case JSON files")
    p.add_argument("--plans",  "-p", nargs="+", help="Pre-generated plan JSON files")
    p.add_argument("--output", "-o", help="Output directory for reports (default: reports/)")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials (url, username, password, selectors)")
    p.add_argument("--parallel", "-j", type=int, default=1, metavar="N",
                   help="Run N tests concurrently (default: 1 = sequential)")

    # prd-run
    p = sub.add_parser("prd-run", help="Generate test plans from a PRD and run them immediately")
    p.add_argument("--prd", nargs="+", required=True, help="Path to the PRD markdown file(s)")
    p.add_argument("--url", nargs="+", required=True, help="Base URLs to crawl for locators")
    p.add_argument("--force", "-f", action="store_true", help="Force re-crawl even if fresh")
    p.add_argument("--output", "-o", help="Output directory for plans (default: plans/)")
    p.add_argument("--max-cases", action="store_true", help="Generate the maximum amount of meaningful test cases")
    p.add_argument("--num-tests", dest="num_tests", type=int, default=None, metavar="N",
                   help="Generate exactly N test cases (overrides --max-cases; default: 5)")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials (url, username, password, selectors)")
    p.add_argument("--spider", action="store_true",
                   help="Follow links and crawl the whole site from the starting URLs")
    p.add_argument("--depth", type=int, default=2, metavar="N",
                   help="Max link-follow depth for --spider (default: 2)")
    p.add_argument("--update-baseline", dest="update_baseline", action="store_true",
                   help="Delete stored visual regression baselines before running (forces re-baseline)")
    p.add_argument("--negative-tests", dest="negative_tests", action="store_true",
                   help="Also generate negative and boundary test cases")
    p.add_argument("--max-locators", dest="max_locators", type=int, default=400, metavar="N",
                   help="Max locators sent to AI (default: 400; reduce for small-context local models)")
    p.add_argument("--compile", action="store_true",
                   help="Compile locator DB to compact model before planning (saves tokens)")
    p.add_argument("--parallel", "-j", type=int, default=1, metavar="N",
                   help="Run N tests concurrently (default: 1 = sequential)")

    # scan
    p = sub.add_parser("scan", help="Scan a site: crawl → evolve PRD → generate plans → diff (no execution)")
    p.add_argument("--url", "-u", required=True, help="Site URL to scan")
    p.add_argument("--fresh", action="store_true",
                   help="Clear all cached state and start from scratch")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--depth", type=int, default=2, metavar="N",
                   help="Max spider crawl depth (default: 2)")
    p.add_argument("--num-tests", dest="num_tests", type=int, default=None, metavar="N",
                   help="Number of test cases to generate (default: AI decides, 6-10)")
    p.add_argument("--max-locators", dest="max_locators", type=int, default=400, metavar="N",
                   help="Max locators sent to AI (default: 400)")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials (url, username, password, selectors)")
    _add_device_args(p)

    # compile
    p = sub.add_parser("codegen", help="Convert QAPAL JSON plan(s) to standalone pytest-playwright .py files")
    _cg = p.add_mutually_exclusive_group(required=True)
    _cg.add_argument("--plan",  metavar="FILE",  help="Single plan JSON file to convert")
    _cg.add_argument("--plans", metavar="GLOB", nargs="+", help="One or more plan JSON files / globs")
    p.add_argument("--output", "-o", metavar="DIR", default="tests/generated",
                   help="Output directory (default: tests/generated/)")

    p = sub.add_parser("compile", help="Compile locator DB into a compact compiled_model.json")
    p.add_argument("--output", "-o", default="compiled_model.json",
                   help="Output path (default: compiled_model.json)")

    # semantic
    p = sub.add_parser("semantic", help="Extract semantic context for URLs (run after crawl, before plan)")
    p.add_argument("--urls",     "-u", nargs="+", required=True, help="URLs to process")
    p.add_argument("--headless", "-H", action="store_true",       help="Run browser in headless mode")

    # graph-crawl
    p = sub.add_parser("graph-crawl", help="Navigate the site, record transitions into the State Graph, crawl each page for locators")
    p.add_argument("--urls", "-u", nargs="+", required=True, help="Entry-point URLs")
    p.add_argument("--depth", type=int, default=2, metavar="N", help="Max navigation depth (default: 2)")
    p.add_argument("--max-pages", dest="max_pages", type=int, default=40, metavar="N", help="Max pages to crawl (default: 40)")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials")

    # explore
    p = sub.add_parser("explore", help="Autonomously explore an app with vision-guided navigation")
    p.add_argument("--url", "-u", required=True, help="Starting URL to explore")
    p.add_argument("--goal", "-g", default="Explore the application and find UX issues",
                   help="Natural language goal for the exploration")
    p.add_argument("--max-steps", dest="max_steps", type=int, default=30, metavar="N",
                   help="Maximum exploration steps (default: 30)")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials")

    # ux-audit
    p = sub.add_parser("ux-audit", help="Run UX heuristic evaluation on one or more URLs")
    p.add_argument("--urls", "-u", nargs="+", required=True, help="URLs to audit")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials")
    p.add_argument("--static", action="store_true",
                   help="Static audit only — use locator DB, no browser or vision")

    # status
    sub.add_parser("status", help="Show DB and AI client status")

    # graph
    p = sub.add_parser("graph", help="Show the recorded page-transition graph")
    p.add_argument("--from-url",  dest="from_url", metavar="URL",
                   help="Filter edges originating from this URL")
    p.add_argument("--to-url",    dest="to_url",   metavar="URL",
                   help="Filter edges leading to this URL")
    p.add_argument("--path",      nargs=2, metavar=("FROM", "TO"),
                   help="Compute shortest navigation path between two URLs")
    p.add_argument("--stats",     action="store_true",
                   help="Show summary statistics only")

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return 1

    try:
        if args.cmd == "crawl":
            return asyncio.run(cmd_crawl(args))
        elif args.cmd == "semantic":
            return asyncio.run(cmd_semantic(args))
        elif args.cmd == "plan":
            return asyncio.run(cmd_plan(args))
        elif args.cmd == "run":
            return asyncio.run(cmd_run(args))
        elif args.cmd == "prd-run":
            return asyncio.run(cmd_prd_run(args))
        elif args.cmd == "scan":
            return asyncio.run(cmd_scan(args))
        elif args.cmd == "status":
            return asyncio.run(cmd_status(args))
        elif args.cmd == "graph":
            return asyncio.run(cmd_graph(args))
        elif args.cmd == "graph-crawl":
            return asyncio.run(cmd_graph_crawl(args))
        elif args.cmd == "codegen":
            return asyncio.run(cmd_codegen(args))
        elif args.cmd == "compile":
            return asyncio.run(cmd_compile(args))
        elif args.cmd == "explore":
            return asyncio.run(cmd_explore(args))
        elif args.cmd == "ux-audit":
            return asyncio.run(cmd_ux_audit(args))
    except KeyboardInterrupt:
        log.warning("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())