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
    print(f"\n Crawling {len(urls)} URL(s)  [db: {db._path}]")
    t0 = time.monotonic()

    headless_mode = True if args.headless else None
    credentials   = _load_credentials(args)
    spider        = getattr(args, "spider", False)
    depth         = getattr(args, "depth", 2)
    async with Crawler(db, headless=headless_mode, credentials=credentials) as crawler:
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
    sg          = StateGraph(db)
    credentials = _load_credentials(args)
    t0          = time.monotonic()
    results     = []

    print(f"\n Running {len(valid_plans)} test(s)")

    headless_mode = True if args.headless else None
    async with Executor(db, headless=headless_mode, ai_client=ai,
                        credentials=credentials, state_graph=sg) as exc:
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

    # Save report
    output_dir = Path(args.output or "reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"report_{ts}.json"
    with open(report_path, "w") as f:
        json.dump({
            "summary":      {"total": len(results), "passed": passed, "failed": failed, "duration_ms": duration},
            "results":      results,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    print(f"   report  → {report_path}")

    db.close()
    return 1 if failed else 0


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

    print(f"\n QAPal Status")
    print(f"   database        : {stats['db_path']}")
    print(f"   total elements  : {stats['total_elements']}  (valid: {stats['valid_elements']})")
    print(f"   pages crawled   : {stats['total_pages']}")
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

    headless_mode = True if args.headless else None
    credentials   = _load_credentials(args)

    # 1. Crawl — populates locator DB
    spider = getattr(args, "spider", False)
    depth  = getattr(args, "depth", 2)
    print(f"\n [1/5] Crawling {len(urls)} URL(s) to gather active locators{'  [spider mode]' if spider else ''}...")
    async with Crawler(db, headless=headless_mode, credentials=credentials) as crawler:
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

    generator = TestGenerator(db, ai_client=ai, max_cases=args.max_cases, state_graph=sg, max_locators=400)
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

        valid_plans = []
        print("\n   Generated Plans:")
        for p in plans:
            tc_id = p.get("test_id", "unknown")
            if p.get("_planning_error"):
                print(f"  ✗ {tc_id}  {p['_planning_error']}")
            else:
                path = output_dir / f"{tc_id}_plan.json"
                with open(path, "w") as f:
                    json.dump(p, f, indent=2)
                print(f"  ✓ {tc_id}  ({len(p.get('steps',[]))} steps)  → {path}")
                valid_plans.append(p)

        if not valid_plans:
            print(f"\n No valid plans generated for {prd_path.name}.")
            continue

        # 4. Execute
        print(f"\n [4/5] Running {len(valid_plans)} generated plan(s) for {prd_path.name}...")

        async with Executor(db, headless=headless_mode, ai_client=ai,
                            credentials=credentials, state_graph=sg) as exc:
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

    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"prd_report_{ts}.json"
    with open(report_path, "w") as f:
        json.dump({
            "summary":      {"total": len(all_results), "passed": passed, "failed": failed, "duration_ms": duration},
            "results":      all_results,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)
    print(f"   report  → {report_path}")

    db.close()
    return 1 if failed else 0


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
    headless    = True if args.headless else True
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
                    "els => els.map(e => ({href: e.href, text: (e.textContent||'').trim().slice(0,60)}))"
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

    # prd-run
    p = sub.add_parser("prd-run", help="Generate test plans from a PRD and run them immediately")
    p.add_argument("--prd", nargs="+", required=True, help="Path to the PRD markdown file(s)")
    p.add_argument("--url", nargs="+", required=True, help="Base URLs to crawl for locators")
    p.add_argument("--force", "-f", action="store_true", help="Force re-crawl even if fresh")
    p.add_argument("--output", "-o", help="Output directory for plans (default: plans/)")
    p.add_argument("--max-cases", action="store_true", help="Generate the maximum amount of meaningful test cases")
    p.add_argument("--headless", "-H", action="store_true", help="Run browser in headless mode")
    p.add_argument("--credentials-file", dest="credentials_file", metavar="FILE",
                   help="JSON file with login credentials (url, username, password, selectors)")
    p.add_argument("--spider", action="store_true",
                   help="Follow links and crawl the whole site from the starting URLs")
    p.add_argument("--depth", type=int, default=2, metavar="N",
                   help="Max link-follow depth for --spider (default: 2)")

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
        import asyncio
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
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())