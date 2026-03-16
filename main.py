"""
main.py — QAPal CLI
=====================
Coordinates crawl -> plan -> execute -> report.

All config from environment variables. No config files.
Copy .env.example to .env and fill in values.

Commands:
  python main.py crawl  --urls https://app.com/login https://app.com/dashboard
  python main.py plan   --tests tests/tc001.json tests/tc002.json
  python main.py run    --tests tests/tc001.json
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Helpers ───────────────────────────────────────────────────────────

def _print_visual_regression_summary(results: list) -> None:
    """Print a warning block for any tests with visual regressions."""
    flagged = [r for r in results if r.get("has_visual_regressions")]
    if not flagged:
        return
    print(f"\n  ⚠  VISUAL REGRESSIONS detected in {len(flagged)} test(s):")
    for r in flagged:
        for vr in r.get("visual_regressions", []):
            print(f"     {r['id']} step {vr['step_index']}: {vr['diff_pct']}% pixel diff")
            print(f"       baseline → {vr['baseline']}")
            print(f"       diff     → {vr['diff']}")


def _print_passive_error_summary(results: list) -> None:
    """Print a warning block for any tests that recorded passive errors."""
    flagged = [r for r in results if r.get("has_passive_errors")]
    if not flagged:
        return
    print(f"\n  ⚠  PASSIVE ERRORS detected in {len(flagged)} test(s):")
    for r in flagged:
        errs = r.get("passive_errors", {})
        nc = len(errs.get("console_errors",   []))
        nn = len(errs.get("network_failures", []))
        nj = len(errs.get("js_exceptions",    []))
        print(f"     {r['id']}: {nc} console error(s), {nn} network failure(s), {nj} JS exception(s)")
        for e in errs.get("console_errors",   [])[:3]:
            print(f"       console: {e['text'][:120]}")
        for e in errs.get("network_failures", [])[:3]:
            print(f"       network: {e['url'][:100]}  [{e.get('failure','')}]")
        for e in errs.get("js_exceptions",    [])[:3]:
            print(f"       js:      {str(e)[:120]}")


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
    from string import Template

    rows_html = []
    for r in results:
        status   = r.get("status", "?")
        badge    = "pass" if status == "pass" else "fail"
        dur      = f"{r.get('duration_ms', 0)}ms"
        test_id  = r.get("id") or r.get("test_id", "?")
        name     = r.get("name", test_id)

        fail_steps = [s for s in r.get("steps", [])      if s.get("status") == "fail"]
        fail_asserts = [a for a in r.get("assertions", []) if a.get("status") == "fail"]
        passive  = r.get("passive_errors", {})
        vrs      = r.get("visual_regressions", [])

        detail_parts = []
        if fail_steps:
            items = "".join(f'<li class="fail">{s.get("reason","?")[:120]}</li>' for s in fail_steps[:5])
            detail_parts.append(f'<ul class="steps">{items}</ul>')
        if fail_asserts:
            items = "".join(f'<li class="fail">{a.get("reason","?")[:120]}</li>' for a in fail_asserts[:5])
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
                print(f"  Warning: could not load {path}: {e}")
    return items


def _get_ai_client() -> Optional[AIClient]:
    try:
        return AIClient.from_env()
    except EnvironmentError as e:
        print(f"  Warning: {e}")
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
            print(f"  Error: credentials file missing keys: {', '.join(sorted(missing))}")
            return None
        return creds
    except FileNotFoundError:
        print(f"  Error: credentials file not found: {path}")
        return None
    except json.JSONDecodeError as e:
        print(f"  Error: credentials file is not valid JSON: {e}")
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
                print(f"  Warning: semantic extraction failed for {url}: {e}")
        await browser.close()
    return processed


# ── Commands ──────────────────────────────────────────────────────────

async def cmd_crawl(args):
    urls = args.urls
    if not urls:
        print("No URLs provided. Use --urls https://... https://...")
        return 1

    db = LocatorDB()
    sg = StateGraph(db)
    print(f"\n Crawling {len(urls)} URL(s)  [db: {db._path}]")
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

    print(f"\n Crawl complete")
    print(f"   pages crawled : {crawled}/{len(urls)}")
    print(f"   elements      : {total_elements} ({total_new} new)")
    print(f"   duration      : {duration}ms")
    db.close()
    return 0


async def cmd_semantic(args):
    urls = args.urls
    if not urls:
        print("No URLs provided. Use --urls https://...")
        return 1

    db           = LocatorDB()
    headless     = args.headless if hasattr(args, "headless") else True
    print(f"\n Extracting semantic context for {len(urls)} URL(s)  [db: {db._path}]")
    t0 = time.monotonic()

    processed = await _extract_semantics(db, urls, headless=headless)

    duration = int((time.monotonic() - t0) * 1000)
    print(f"\n Semantic pipeline complete")
    print(f"   URLs processed: {processed}/{len(urls)}")
    print(f"   duration      : {duration}ms")
    db.close()
    return 0


async def cmd_plan(args):
    test_cases = _load_json_files(args.tests)
    if not test_cases:
        print("No test cases found.")
        return 1

    ai     = _get_ai_client()
    db     = LocatorDB()
    planner = Planner(db, ai)

    output_dir = Path(args.output or "plans")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n Planning {len(test_cases)} test(s)  [ai: {ai.provider if ai else 'none'}]")
    ok = failed = 0

    for tc in test_cases:
        tc_id = tc.get("id", "unknown")
        try:
            plan = planner.create_plan(tc, cache_key=tc_id)
            path = output_dir / f"{tc_id}_plan.json"
            with open(path, "w") as f:
                json.dump(plan, f, indent=2)
            print(f"  ✓ {tc_id}  ({len(plan.get('steps',[]))} steps)  → {path}")
            ok += 1
        except PlanningError as e:
            print(f"  ✗ {tc_id}  {e}")
            failed += 1

    print(f"\n   success: {ok}  failed: {failed}")
    db.close()
    return 1 if failed else 0


async def cmd_run(args):
    # Load test cases or pre-generated plans
    if args.plans:
        plans = _load_json_files(args.plans)
    else:
        test_cases = _load_json_files(args.tests or [])
        if not test_cases:
            print("No test cases or plans provided.")
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
                    print(f"  Planning failed for {tc_id}: {e}")
                    plans.append({"id": tc_id, "_planning_error": str(e)})
        finally:
            db.close()

    valid_plans = [p for p in plans if not p.get("_planning_error")]
    if not valid_plans:
        print("No valid plans to run.")
        return 1

    ai          = _get_ai_client()
    db          = LocatorDB()
    try:
        sg          = StateGraph(db)
        credentials = _load_credentials(args)
        t0          = time.monotonic()
        results     = []

        parallel = getattr(args, "parallel", 1) or 1
        print(f"\n Running {len(valid_plans)} test(s)"
              + (f"  [parallel={parallel}]" if parallel > 1 else ""))

        headless_mode = True if args.headless else None
        async with Executor(db, headless=headless_mode, ai_client=ai,
                            credentials=credentials, state_graph=sg) as exc:
            if parallel > 1:
                results = await exc.run_parallel(valid_plans, concurrency=parallel)
            else:
                for plan in valid_plans:
                    tc_id = plan.get("id") or plan.get("test_id") or plan.get("_meta", {}).get("test_id", "?")
                    print(f"  {tc_id} ...", end=" ", flush=True)
                    result = await exc.run(plan)
                    results.append(result)
                    icon = "✓" if result["status"] == "pass" else "✗"
                    print(f"{icon} {result['status']}  ({result['duration_ms']}ms)")
                    if result["status"] == "fail":
                        for s in result.get("steps", []):
                            if s.get("status") == "fail":
                                print(f"    step fail: {s.get('reason')}")
                        for a in result.get("assertions", []):
                            if a.get("status") == "fail":
                                print(f"    assert fail: {a.get('reason')}")

        duration = int((time.monotonic() - t0) * 1000)
        passed   = sum(1 for r in results if r.get("status") == "pass")
        failed   = len(results) - passed

        print(f"\n   passed: {passed}  failed: {failed}  duration: {duration}ms")
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
        print(f"   report  → {report_path}")
        print(f"   html    → {html_path}")

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
    print(f"\n QAPal Status")
    print(f"   database        : {stats['db_path']}")
    print(f"   total elements  : {stats['total_elements']}  (valid: {stats['valid_elements']})")
    print(f"   pages crawled   : {stats['total_pages']}  (screenshots: {pages_with_shots})")
    print(f"   semantic states : {stats.get('total_states', 0)}")
    print(f"   sessions        : {stats['total_sessions']}")
    print(f"   low-conf        : {stats['low_confidence']}")
    print(f"   with warnings   : {stats['with_warnings']}")
    print(f"   graph edges     : {sg_stats['total_transitions']}  (pages: {sg_stats['unique_pages']})")
    print(f"   AI client       : {ai_str}")
    db.close()
    return 0


async def cmd_prd_run(args):
    from generator import TestGenerator

    ai = _get_ai_client()
    if not ai:
        print("Error: QAPAL_AI_PROVIDER environment variable is required for prd-run.")
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
            print(f"\n  [baseline] Cleared visual regression baselines → {VISUAL_BASELINE_DIR}")

    # 1. Crawl — populates locator DB
    spider = getattr(args, "spider", False)
    depth  = getattr(args, "depth", 2)

    # Auto-spider when the nav graph is nearly empty (first run on a new site).
    # This ensures the AI has enough locator context to generate accurate plans.
    if not spider and sg.stats().get("unique_pages", 0) < 3:
        print(f"\n [auto] Nav graph is sparse — enabling --spider for first-run discovery.")
        spider = True

    print(f"\n [1/5] Crawling {len(urls)} URL(s) to gather active locators{'  [spider mode]' if spider else ''}...")
    async with Crawler(db, headless=headless_mode, credentials=credentials, state_graph=sg) as crawler:
        if spider:
            crawl_results = await crawler.spider_crawl(urls, max_depth=depth, force=args.force)
            urls = list({r["url"] for r in crawl_results if r.get("crawled")} | set(urls))
        else:
            await crawler.bulk_crawl(urls, force=args.force)

    # 2. Semantic pipeline — separate step so context can be reprocessed
    #    without re-crawling.  Uses live pages, no extra HTTP round-trips.
    headless_bool = headless_mode if headless_mode is not None else True
    print(f"\n [2/5] Extracting semantic context for {len(urls)} URL(s)...")
    processed = await _extract_semantics(db, urls, headless=headless_bool)
    print(f"   semantic contexts saved: {processed}/{len(urls)}")

    num_tests       = getattr(args, "num_tests", None)
    negative_tests  = getattr(args, "negative_tests", False)
    use_compile     = getattr(args, "compile", False)

    compiled_model_path = None
    if use_compile:
        compiled_model_path = "compiled_model.json"
        print(f"\n [2.5/5] Compiling site model...")
        from site_compiler import SiteCompiler
        compiler = SiteCompiler(db, state_graph=sg)
        compiled_model = compiler.compile(output_path=compiled_model_path)
        print(f"   compiled {compiled_model.locator_count} locators → ~{len(compiled_model.format_for_prompt()) // 4} tokens")
    elif Path("compiled_model.json").exists():
        # Auto-detect existing fresh compiled model
        from site_compiler import SiteCompiler
        existing = SiteCompiler.load("compiled_model.json")
        if existing and not existing.is_stale(max_age_minutes=120):
            compiled_model_path = "compiled_model.json"
            print(f"   [compile] Auto-detected compiled_model.json ({existing.locator_count} locators)")

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
            print(f"Error: PRD file not found at {prd_path_str}")
            continue

        print(f"\n Reading PRD: {prd_path}")
        prd_content = prd_path.read_text('utf-8')

        # 3. Plan (Generate from PRD)
        print(f"\n [3/5] Generating execution plans from PRD: {prd_path.name}  [ai: {ai.provider}]")
        try:
            plans = generator.generate_plans_from_prd(prd_content, urls, credentials=credentials)
        except Exception as e:
            print(f"Error generating plans for {prd_path.name}: {e}")
            continue

        # Derive a slug from the PRD filename to prefix plan IDs, preventing
        # plans from different PRDs overwriting each other (e.g. both start at TC001).
        # "bookshop_prd.md" → "bookshop", "toolbox.md" → "toolbox"
        prd_slug = re.sub(r"[_\-]prd$", "", prd_path.stem, flags=re.IGNORECASE)
        prd_slug = re.sub(r"[^a-zA-Z0-9]+", "-", prd_slug).strip("-").lower()

        valid_plans = []
        print("\n   Generated Plans:")
        for p in plans:
            tc_id = p.get("test_id", "unknown")
            if p.get("_planning_error"):
                print(f"  ✗ {tc_id}  {p['_planning_error']}")
            else:
                # Prefix test_id with slug if not already prefixed
                if prd_slug and not tc_id.startswith(prd_slug):
                    prefixed_id = f"{prd_slug}-{tc_id}"
                    p = {**p, "test_id": prefixed_id}
                    tc_id = prefixed_id
                path = output_dir / f"{tc_id}_plan.json"
                with open(path, "w") as f:
                    json.dump(p, f, indent=2)
                print(f"  ✓ {tc_id}  ({len(p.get('steps',[]))} steps)  → {path}")
                valid_plans.append(p)

        if not valid_plans:
            print(f"\n No valid plans generated for {prd_path.name}.")
            continue

        # 4. Execute
        parallel = getattr(args, "parallel", 1) or 1
        print(f"\n [4/5] Running {len(valid_plans)} generated plan(s) for {prd_path.name}"
              + (f"  [parallel={parallel}]" if parallel > 1 else "") + "...")

        async with Executor(db, headless=headless_mode, ai_client=ai,
                            credentials=credentials, state_graph=sg) as exc:
            if parallel > 1:
                results = await exc.run_parallel(valid_plans, concurrency=parallel)
                all_results.extend(results)
            else:
                for plan in valid_plans:
                    tc_id = plan.get("test_id", "?")
                    print(f"  {tc_id} ...", end=" ", flush=True)
                    result = await exc.run(plan)
                    all_results.append(result)
                    icon = "✓" if result["status"] == "pass" else "✗"
                    print(f"{icon} {result['status']}  ({result['duration_ms']}ms)")
                    if result["status"] == "fail":
                        for s in result.get("steps", []):
                            if s.get("status") == "fail":
                                print(f"    step fail: {s.get('reason')}")
                        for a in result.get("assertions", []):
                            if a.get("status") == "fail":
                                print(f"    assert fail: {a.get('reason')}")

    # 5. Report
    duration = int((time.monotonic() - t0) * 1000)
    passed   = sum(1 for r in all_results if r.get("status") == "pass")
    failed   = len(all_results) - passed

    print(f"\n [5/5] Summary")
    print(f"   passed: {passed}  failed: {failed}  total duration: {duration}ms")
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
    print(f"   report  → {report_path}")
    print(f"   html    → {html_path}")

    db.close()
    return 1 if failed else 0


# ── Compile command ───────────────────────────────────────────────────

async def cmd_compile(args):
    """Compile the locator DB into a compact compiled_model.json."""
    from site_compiler import SiteCompiler

    db   = LocatorDB()
    sg   = StateGraph(db)
    out  = getattr(args, "output", "compiled_model.json") or "compiled_model.json"

    stats = db.stats()
    print(f"\n [compile] Compiling locator DB ({stats['total_elements']} elements → {out})")
    t0 = time.monotonic()

    compiler = SiteCompiler(db, state_graph=sg)
    model    = compiler.compile(output_path=out)

    duration = int((time.monotonic() - t0) * 1000)
    print(f"   locators   : {model.locator_count}")
    print(f"   prompt size: ~{len(model.format_for_prompt()) // 4} tokens")
    print(f"   output     : {out}")
    print(f"   duration   : {duration}ms")
    print(f"\n Preview:\n{model.format_for_prompt()}")

    db.close()
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

    print(f"\n Graph-crawl  [db: {db._path}]")
    print(f"   start URLs : {len(urls)}  |  max pages: {max_pages}  |  depth: {depth}")

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
                print(f"  [graph-crawl] ({pages_done}/{max_pages}) {norm} — {status}")

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
                print(f"  [graph-crawl] failed {norm}: {e}")
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
    print(f"\n Graph-crawl complete")
    print(f"   pages crawled : {pages_done}")
    print(f"   graph edges   : {sg_stats['total_transitions']}  (pages: {sg_stats['unique_pages']})")
    print(f"   duration      : {duration}ms")
    db.close()
    return 0


# ── Graph command ─────────────────────────────────────────────────────

async def cmd_graph(args):
    """Display the state graph of recorded page transitions."""
    from urllib.parse import urlparse

    db      = LocatorDB()
    sg      = StateGraph(db)
    stats   = sg.stats()

    print(f"\n State Graph  [db: {db._path}]")
    print(f"   graph edges  : {stats['total_transitions']}")
    print(f"   unique pages : {stats['unique_pages']}")

    if stats["total_transitions"] == 0:
        print("\n   (no transitions recorded yet — run tests first)")
        db.close()
        return 0

    # ── --stats only ─────────────────────────────────────────────────
    if getattr(args, "stats", False):
        if stats["most_traversed"]:
            print("\n   Most traversed:")
            for frm, to, count in stats["most_traversed"]:
                print(f"     {frm} → {to}  ({count}x)")
        db.close()
        return 0

    # ── --path FROM TO ────────────────────────────────────────────────
    if getattr(args, "path", None):
        from_url, to_url = args.path
        path = sg.get_path(from_url, to_url)
        if path is None:
            print(f"\n   No path found from {from_url} to {to_url}")
        else:
            hops = len(path)
            print(f"\n   Shortest path ({hops} hop{'s' if hops != 1 else ''}):")
            for i, edge in enumerate(path):
                t  = edge["trigger"]
                frm = edge["from_url"]
                print(f"     {i + 1}. {frm}")
                print(f"        {t['action']} \"{t['label']}\"")
            print(f"     {hops + 1}. {to_url}")
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

    print(f"\n Edges ({len(transitions)} shown):")
    for t in transitions:
        tr  = t["trigger"]
        frm = t["from_url"]
        to  = t["to_url"]
        act = tr.get("action", "?")
        lbl = tr.get("label", "")
        cnt = t["traversal_count"]
        print(f"   {frm}")
        print(f"     --[{act} \"{lbl}\"]--> {to}  ({cnt}x)")

    db.close()
    return 0


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QAPal — deterministic AI UI testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

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

    # compile
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
        elif args.cmd == "status":
            return asyncio.run(cmd_status(args))
        elif args.cmd == "graph":
            return asyncio.run(cmd_graph(args))
        elif args.cmd == "graph-crawl":
            return asyncio.run(cmd_graph_crawl(args))
        elif args.cmd == "compile":
            return asyncio.run(cmd_compile(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())