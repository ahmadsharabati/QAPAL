#!/usr/bin/env python3
"""
cli.py — QAPAL Locator Intelligence Engine CLI
================================================
New entry point for the refactored QAPAL.

Commands:
  qapal analyze <files> --url <url>   — scan tests, report weak selectors
  qapal fix <files> --url <url>       — replace broken selectors with validated ones
  qapal generate --url <url>          — scaffold test files with probed selectors
  qapal probe "<selector>" --url <url>— validate a single selector
  qapal heal --test-results <json>    — CI mode: detect failures, fix, retry, PR
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qapal",
        description="Locator intelligence engine for Playwright — analyze, fix, and heal test selectors.",
    )

    # Global flags
    parser.add_argument("--headless", action="store_true", default=None,
                        help="Run browser in headless mode (default: from env or True)")
    parser.add_argument("--headed", action="store_true",
                        help="Run browser in headed mode (visible)")
    parser.add_argument("--device", type=str, default=None,
                        help="Playwright device preset (e.g. 'iPhone 12')")
    parser.add_argument("--credentials-file", type=str, default=None,
                        help="JSON file with login credentials")
    parser.add_argument("--timeout", type=int, default=10000,
                        help="Action timeout in ms (default: 10000)")
    parser.add_argument("--db-path", type=str, default=None,
                        help="Path to locator DB (default: locators.json)")

    sub = parser.add_subparsers(dest="command")

    # ── analyze ──
    p_analyze = sub.add_parser("analyze", help="Scan test files and report selector health")
    p_analyze.add_argument("files", nargs="+", help="Test file paths or glob patterns")
    p_analyze.add_argument("--url", required=True, help="Base URL to probe against")
    p_analyze.add_argument("--format", choices=["table", "json", "github"], default="table",
                           help="Output format")

    # ── fix ──
    p_fix = sub.add_parser("fix", help="Replace weak/broken selectors with validated alternatives")
    p_fix.add_argument("files", nargs="+", help="Test file paths or glob patterns")
    p_fix.add_argument("--url", required=True, help="Base URL to probe against")
    p_fix.add_argument("--dry-run", action="store_true", help="Show diff without applying")
    p_fix.add_argument("--apply", action="store_true", help="Apply fixes to files")
    p_fix.add_argument("--pr", action="store_true", help="Create a PR with fixes")
    p_fix.add_argument("--branch", type=str, default="qapal/fix-selectors",
                        help="Git branch name for PR")
    p_fix.add_argument("--min-confidence", type=float, default=0.5,
                        help="Minimum confidence to apply fix (default: 0.5)")
    p_fix.add_argument("--ai-fallback", action="store_true", help="Enable LLM fuzzy matching for broken CSS selectors")

    # ── generate ──
    p_gen = sub.add_parser("generate", help="Scaffold test files with validated selectors")
    p_gen.add_argument("--url", required=True, help="Page URL to generate test for")
    p_gen.add_argument("--output", "-o", default="tests/generated/",
                        help="Output directory or file path")
    p_gen.add_argument("--language", "-l", choices=["python", "typescript"], default="python",
                        help="Output language")

    # ── probe ──
    p_probe = sub.add_parser("probe", help="Validate a single selector against a live page")
    p_probe.add_argument("selector", type=str,
                         help='Playwright expression (e.g. "page.getByTestId(\'email\')")')
    p_probe.add_argument("--url", required=True, help="Page URL to probe against")

    # ── heal ──
    p_heal = sub.add_parser("heal", help="CI mode: detect locator failures, fix, retry, PR")
    p_heal.add_argument("--test-results", required=True,
                        help="Path to test results JSON (pytest-json-report format)")
    p_heal.add_argument("--url", required=True, help="Base URL of the application")
    p_heal.add_argument("--pr", action="store_true", help="Create a PR with fixes")
    p_heal.add_argument("--branch", type=str, default="qapal/heal-selectors")
    p_heal.add_argument("--ai-fallback", action="store_true", help="Enable LLM fuzzy matching for broken CSS selectors")

    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand_files(patterns: List[str]) -> List[str]:
    """Expand glob patterns to file paths."""
    files = []
    for pattern in patterns:
        expanded = glob.glob(pattern, recursive=True)
        if expanded:
            files.extend(expanded)
        elif Path(pattern).exists():
            files.append(pattern)
    return sorted(set(files))


def _get_headless(args) -> bool:
    if args.headed:
        return False
    if args.headless:
        return True
    return os.getenv("QAPAL_HEADLESS", "true").lower() == "true"


def _load_credentials(args) -> dict | None:
    if not args.credentials_file:
        return None
    path = Path(args.credentials_file)
    if not path.exists():
        print(f"Error: credentials file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _get_db(args):
    from locator_db import LocatorDB
    db_path = args.db_path or os.getenv("QAPAL_DB_PATH", "locators.json")
    return LocatorDB(db_path)


# ---------------------------------------------------------------------------
# Command: analyze
# ---------------------------------------------------------------------------

async def cmd_analyze(args):
    """Scan test files, probe each selector, report health."""
    from parser import parse_file, selector_to_qapal
    from probe import ProbeEngine
    from ranker import grade, format_grade

    files = _expand_files(args.files)
    if not files:
        print("No test files found.", file=sys.stderr)
        return 1

    # Parse all selectors
    all_selectors = []
    for f in files:
        all_selectors.extend(parse_file(f))

    if not all_selectors:
        print("No Playwright selectors found in the given files.")
        return 0

    print(f"Found {len(all_selectors)} selectors across {len(files)} file(s).")
    print(f"Probing against {args.url}...\n")

    db = _get_db(args)
    
    ai_client = None
    if getattr(args, "ai_fallback", False):
        try:
            from ai_client import AIClient
            ai_client = AIClient.from_env()
        except Exception as e:
            print(f"Warning: Failed to initialize AIClient: {e}. AI fallback disabled.", file=sys.stderr)

    try:
        async with ProbeEngine(
            db,
            headless=_get_headless(args),
            credentials=_load_credentials(args),
            device=args.device,
            ai_client=ai_client,
        ) as engine:
            results = []
            for parsed in all_selectors:
                qapal_sel = selector_to_qapal(parsed)
                target_url = parsed.context_url or args.url
                result = await engine.probe(target_url, qapal_sel)
                results.append((parsed, result))

        # Output
        if args.format == "json":
            _output_json(results)
        elif args.format == "github":
            _output_github(results)
        else:
            _output_table(results)

        # Summary
        total = len(results)
        broken = sum(1 for _, r in results if not r.found)
        weak = sum(1 for _, r in results if r.found and r.confidence < 0.5)
        strong = sum(1 for _, r in results if r.found and r.confidence >= 0.7)

        print(f"\n--- Summary ---")
        print(f"Total: {total}  |  Strong: {strong}  |  Weak: {weak}  |  Broken: {broken}")

        return 1 if broken > 0 else 0

    finally:
        db.close()


def _output_table(results):
    from ranker import format_grade as _fmt_grade
    print(f"{'File':<30s} {'Line':>5s}  {'Type':<12s} {'Value':<30s} {'Found':>5s} {'Grade':>10s}")
    print("-" * 97)
    for parsed, result in results:
        val = str(parsed.value)[:28]
        found = "YES" if result.found else "NO"
        g = _fmt_grade(result.confidence) if result.found else "[F — 0.00]"
        fname = Path(parsed.file_path).name[-28:]
        print(f"{fname:<30s} {parsed.line_number:>5d}  {parsed.selector_type:<12s} {val:<30s} {found:>5s} {g:>10s}")


def _output_json(results):
    data = []
    for parsed, result in results:
        data.append({
            "file": parsed.file_path,
            "line": parsed.line_number,
            "selector_type": parsed.selector_type,
            "value": parsed.value,
            "expression": parsed.full_expression,
            "found": result.found,
            "count": result.count,
            "visible": result.visible,
            "confidence": result.confidence,
            "strategy_used": result.strategy_used,
        })
    print(json.dumps(data, indent=2))


def _output_github(results):
    """Output GitHub Actions annotation format."""
    for parsed, result in results:
        if not result.found:
            level = "error"
            msg = f"Broken selector: {parsed.full_expression} — element not found"
        elif result.confidence < 0.5:
            level = "warning"
            msg = f"Weak selector: {parsed.full_expression} (confidence: {result.confidence:.2f})"
        else:
            continue  # Don't annotate healthy selectors
        print(f"::{level} file={parsed.file_path},line={parsed.line_number}::{msg}")


# ---------------------------------------------------------------------------
# Command: fix
# ---------------------------------------------------------------------------

async def cmd_fix(args):
    """Replace broken/weak selectors with validated alternatives."""
    from parser import parse_file, selector_to_qapal
    from probe import ProbeEngine
    from patcher import generate_patch, apply_patches, preview_patches, format_patch_summary, create_pr
    from ranker import format_grade

    files = _expand_files(args.files)
    if not files:
        print("No test files found.", file=sys.stderr)
        return 1

    all_selectors = []
    for f in files:
        all_selectors.extend(parse_file(f))

    if not all_selectors:
        print("No Playwright selectors found.")
        return 0

    print(f"Found {len(all_selectors)} selectors. Probing against {args.url}...\n")

    db = _get_db(args)
    patches = []
    
    ai_client = None
    if getattr(args, "ai_fallback", False):
        try:
            from ai_client import AIClient
            ai_client = AIClient.from_env()
        except Exception as e:
            print(f"Warning: Failed to initialize AIClient: {e}. AI fallback disabled.", file=sys.stderr)

    try:
        async with ProbeEngine(
            db,
            headless=_get_headless(args),
            credentials=_load_credentials(args),
            device=args.device,
            ai_client=ai_client,
        ) as engine:

            # Cache for discovered elements per URL
            url_to_elements = {}

            for parsed in all_selectors:
                target_url = parsed.context_url or args.url
                
                # Discover elements if not already cached for this URL
                if target_url not in url_to_elements:
                    url_to_elements[target_url] = await engine.probe_url(target_url)
                
                elements = url_to_elements[target_url]
                qapal_sel = selector_to_qapal(parsed)
                result = await engine.probe(target_url, qapal_sel)

                # Only fix broken or weak selectors
                if result.found and result.confidence >= args.min_confidence:
                    continue

                # Extract live element attributes if the selector resolves
                element_attrs = None
                if result.found and result.count >= 1:
                    try:
                        element_attrs = await _extract_element_attrs(engine, target_url, qapal_sel)
                    except Exception:
                        pass  # Best-effort

                # Find the best alternative from discovered elements
                best_alt = await _find_best_alternative(parsed, elements, result, element_attrs, ai_client=ai_client)
                if best_alt is None:
                    continue

                new_selector, new_confidence = best_alt
                if new_confidence <= result.confidence:
                    continue  # Alternative isn't better

                patch = generate_patch(parsed, new_selector, new_confidence)
                patches.append(patch)

        if not patches:
            print("All selectors are healthy. Nothing to fix.")
            return 0

        print(format_patch_summary(patches))

        if args.dry_run:
            print("\n--- Diff Preview (--dry-run) ---\n")
            print(preview_patches(patches))
        elif args.pr:
            print(f"\nCreating PR on branch '{args.branch}'...")
            pr_url = create_pr(patches, branch_name=args.branch)
            if pr_url:
                print(f"PR created: {pr_url}")
            else:
                print("Failed to create PR.", file=sys.stderr)
                return 1
        elif args.apply:
            succeeded, failed = apply_patches(patches)
            print(f"\nApplied: {succeeded}  Failed: {failed}")
        else:
            print("\nUse --dry-run, --apply, or --pr to take action.")

        return 0

    finally:
        db.close()


async def _extract_element_attrs(engine, url, qapal_sel):
    """
    Resolve a selector on the live page and extract the element's semantic attributes.
    Returns dict with keys: role, name, testid, aria_label, text, placeholder.
    """
    from probe import _build_locator
    page = engine._page
    if page is None:
        return None

    locator = _build_locator(page, qapal_sel)
    try:
        # Use .first to handle non-unique selectors gracefully
        attrs = await locator.first.evaluate("""el => ({
            role: el.getAttribute('role') || el.tagName.toLowerCase(),
            name: el.getAttribute('aria-label') || (el.textContent || '').trim().substring(0, 100) || '',
            testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy') || null,
            aria_label: el.getAttribute('aria-label') || null,
            text: (el.textContent || '').trim().substring(0, 100) || '',
            placeholder: el.getAttribute('placeholder') || null,
        })""")
        return attrs
    except Exception:
        return None


async def _find_best_alternative(parsed, elements, probe_result, element_attrs=None, ai_client=None):
    """
    Find the best alternative selector from discovered elements.

    element_attrs: optional dict with keys like 'role', 'name', 'testid',
                   'aria_label', 'text' — extracted from the live DOM element
                   that the current selector resolves to. This enables matching
                   CSS/ID selectors to semantic alternatives.
    """
    # Try to match by name or testid from the parsed selector
    target_value = parsed.value
    target_name = ""
    if isinstance(target_value, dict):
        target_name = target_value.get("name", "")
    elif isinstance(target_value, str):
        target_name = target_value

    best = None
    best_score = 0.0

    for elem in elements:
        matched = False

        # Match by testid
        if parsed.selector_type == "testid" and elem.testid and elem.testid == target_name:
            matched = True

        # Match by name
        if not matched and target_name and elem.name and target_name.lower() in elem.name.lower():
            matched = True

        # Match by role + name
        if not matched and isinstance(target_value, dict):
            target_role = target_value.get("role", "")
            if target_role == elem.role and target_name and elem.name and target_name.lower() in elem.name.lower():
                matched = True

        # Match by live element attributes (for CSS/ID selectors that resolve on page)
        if not matched and element_attrs:
            attrs = element_attrs
            # Match by accessible name
            if attrs.get("name") and elem.name and attrs["name"].strip() == elem.name.strip():
                matched = True
            # Match by aria-label
            elif attrs.get("aria_label") and elem.name and attrs["aria_label"] == elem.name:
                matched = True
            # Match by placeholder → element name (Playwright uses placeholder as accessible name)
            elif attrs.get("placeholder") and elem.name and attrs["placeholder"] in elem.name:
                matched = True
            # Match by role + text content
            elif attrs.get("role") and attrs.get("role") == elem.role:
                if attrs.get("name") and elem.name and attrs["name"].lower() in elem.name.lower():
                    matched = True

        if matched and elem.best_selector and elem.confidence > best_score:
            # Don't suggest the same strategy — that's not an upgrade
            existing_strategy = parsed.selector_type
            new_strategy = elem.best_selector.get("strategy", "")
            if new_strategy != existing_strategy or not probe_result.found:
                best = (elem.best_selector, elem.confidence)
                best_score = elem.confidence

    # AI Rediscovery Fallback if no deterministic match is found
    if best is None and ai_client is not None:
        try:
            prompt = f"""We are trying to replace a brittle/broken UI Playwright selector.
Original brittle selector: type="{parsed.selector_type}", value="{parsed.value}"

Here is the list of ALL actionable semantic elements on the current page:
"""
            # Truncate elements list slightly if it's massive to save tokens
            for idx, elem in enumerate(elements[:150]):
                prompt += f"  - [{idx}] {elem.best_selector.get('strategy')}: {elem.best_selector.get('value')} (role={elem.role}, name={elem.name})\n"
            
            prompt += """
Based on the structure, context, or visual cues of the original brittle selector, output a strict JSON object mapping to the BEST semantic alternative from the above list. 
IMPORTANT: Only pick an element from the given list.

Example Output:
{"strategy": "role", "value": {"role": "link", "name": "Log in"}}

Return ONLY proper JSON. Do not return markdown blocks or any conversational text.
"""
            # Use small/fast model for fuzzy matching to keep performance snappy
            import json
            response_text = await ai_client.acomplete(prompt, max_tokens=100)
            response_text = response_text.strip()
            if "```" in response_text:
                response_text = response_text.split("```")[1].lstrip("json").strip().split("```")[0]
            
            ai_suggestion = json.loads(response_text)
            
            # Validate suggestion exists in our list
            for elem in elements:
                if elem.best_selector.get("strategy") == ai_suggestion.get("strategy") and \
                   str(elem.best_selector.get("value")) == str(ai_suggestion.get("value")):
                    # Penalize AI score slightly so exact deterministic matches always win
                    best = (elem.best_selector, elem.confidence * 0.9)
                    break 

        except Exception as e:
            print(f"AI Fallback heuristic failed: {e}", file=sys.stderr)

    return best


# ---------------------------------------------------------------------------
# Command: generate
# ---------------------------------------------------------------------------

async def cmd_generate(args):
    """Scaffold a test file with validated selectors."""
    from probe import ProbeEngine
    from scaffold import generate_file

    db = _get_db(args)

    try:
        async with ProbeEngine(
            db,
            headless=_get_headless(args),
            credentials=_load_credentials(args),
            device=args.device,
        ) as engine:
            print(f"Probing {args.url}...")
            elements = await engine.probe_url(args.url)

        if not elements:
            print("No interactive elements found on the page.", file=sys.stderr)
            return 1

        output_path = generate_file(
            url=args.url,
            elements=elements,
            output_path=args.output,
            language=args.language,
        )

        actionable = [e for e in elements if e.actionable]
        print(f"Discovered {len(actionable)} interactive elements.")
        print(f"Scaffold written to: {output_path}")
        return 0

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: probe
# ---------------------------------------------------------------------------

async def cmd_probe(args):
    """Validate a single selector against a live page."""
    from parser import parse_file, selector_to_qapal, ParsedSelector
    from probe import ProbeEngine
    from ranker import format_grade, grade

    # Parse the selector expression
    # User provides something like: page.getByTestId('email')
    # We need to wrap it in a fake file to parse
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
        f.write(f"  {args.selector}.click();\n")
        tmp_path = f.name

    try:
        selectors = parse_file(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not selectors:
        print(f"Could not parse selector: {args.selector}", file=sys.stderr)
        return 1

    parsed = selectors[0]
    qapal_sel = selector_to_qapal(parsed)

    print(f"Selector: {parsed.full_expression}")
    print(f"Type:     {parsed.selector_type}")
    print(f"Value:    {parsed.value}")
    print(f"Probing {args.url}...\n")

    db = _get_db(args)

    try:
        async with ProbeEngine(
            db,
            headless=_get_headless(args),
            credentials=_load_credentials(args),
            device=args.device,
        ) as engine:
            result = await engine.probe(args.url, qapal_sel)

            print(f"Found:       {'YES' if result.found else 'NO'}")
            print(f"Count:       {result.count}")
            print(f"Visible:     {result.visible}")
            print(f"Enabled:     {result.enabled}")
            print(f"In viewport: {result.in_viewport}")
            print(f"Confidence:  {format_grade(result.confidence)}")
            print(f"Strategy:    {result.strategy_used}")

            if result.found:
                # Show alternatives
                elements = await engine.probe_url(args.url)
                # Find matching element
                for elem in elements:
                    if (elem.testid == parsed.value or
                        (isinstance(parsed.value, dict) and
                         parsed.value.get("name") and
                         parsed.value["name"] == elem.name)):
                        candidates = await engine.generate_candidates(args.url, elem)
                        if candidates:
                            print(f"\nAlternative selectors (ranked):")
                            for c in candidates[:5]:
                                from parser import qapal_to_expression
                                expr = qapal_to_expression(
                                    {"strategy": c.strategy, "value": c.value}, "typescript"
                                )
                                print(f"  {format_grade(c.score)}  {expr}")
                        break

        return 0 if result.found else 1

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: heal
# ---------------------------------------------------------------------------

async def cmd_heal(args):
    """CI healing: read test failures, fix selectors, retry."""
    from parser import parse_file, selector_to_qapal
    from probe import ProbeEngine
    from patcher import generate_patch, apply_patches, create_pr, format_patch_summary

    results_path = Path(args.test_results)
    if not results_path.exists():
        print(f"Test results file not found: {results_path}", file=sys.stderr)
        return 1

    # Parse test results (support pytest-json-report format)
    data = json.loads(results_path.read_text())
    failures = _extract_failures(data)

    if not failures:
        print("No locator failures detected in test results.")
        return 0

    print(f"Detected {len(failures)} potential locator failure(s).")

    db = _get_db(args)
    patches = []

    ai_client = None
    if getattr(args, "ai_fallback", False):
        try:
            from ai_client import AIClient
            ai_client = AIClient.from_env()
        except Exception as e:
            print(f"Warning: Failed to initialize AIClient: {e}. AI fallback disabled.", file=sys.stderr)

    try:
        async with ProbeEngine(
            db,
            headless=_get_headless(args),
            credentials=_load_credentials(args),
            device=args.device,
            ai_client=ai_client,
        ) as engine:

            elements = await engine.probe_url(args.url)

            for failure in failures:
                file_path = failure.get("file")
                line = failure.get("line")
                if not file_path or not Path(file_path).exists():
                    continue

                file_selectors = parse_file(file_path)
                # Find the selector closest to the failure line
                closest = None
                for sel in file_selectors:
                    if closest is None or abs(sel.line_number - line) < abs(closest.line_number - line):
                        closest = sel

                if closest is None:
                    continue

                qapal_sel = selector_to_qapal(closest)
                result = await engine.probe(args.url, qapal_sel)

                if result.found and result.confidence >= 0.5:
                    continue  # Selector works fine, failure was something else

                best_alt = await _find_best_alternative(closest, elements, result, ai_client=ai_client)
                if best_alt:
                    new_selector, new_confidence = best_alt
                    patch = generate_patch(closest, new_selector, new_confidence,
                                          reason="Auto-healed by QAPAL CI")
                    patches.append(patch)

        if not patches:
            print("No selector fixes found for the failures.")
            return 0

        print(format_patch_summary(patches))

        succeeded, failed = apply_patches(patches)
        print(f"\nApplied: {succeeded}  Failed: {failed}")

        if args.pr and succeeded > 0:
            pr_url = create_pr(patches, branch_name=args.branch)
            if pr_url:
                print(f"PR created: {pr_url}")
            else:
                print("Failed to create PR.", file=sys.stderr)

        return 0

    finally:
        db.close()


def _extract_failures(data: dict) -> List[dict]:
    """Extract failure locations from pytest-json-report format."""
    failures = []

    # pytest-json-report format
    tests = data.get("tests", [])
    for test in tests:
        if test.get("outcome") != "failed":
            continue
        call = test.get("call", {})
        longrepr = call.get("longrepr", "")

        # Try to extract file:line from traceback
        # Pattern: "file.py:123: in test_func"
        import re
        match = re.search(r'([^\s]+\.(?:py|ts)):(\d+)', str(longrepr))
        if match:
            failures.append({
                "file": match.group(1),
                "line": int(match.group(2)),
                "message": str(longrepr)[:200],
            })

    # Fallback: simple JSON format {failures: [{file, line, message}]}
    if not failures:
        for f in data.get("failures", []):
            if isinstance(f, dict) and "file" in f:
                failures.append(f)

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "analyze":  cmd_analyze,
        "fix":      cmd_fix,
        "generate": cmd_generate,
        "probe":    cmd_probe,
        "heal":     cmd_heal,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = asyncio.run(handler(args))
    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
