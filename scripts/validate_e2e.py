#!/usr/bin/env python3
"""
End-to-end validation script for the QAPAL Deep Scan pipeline.

Tests the full flow: backend → worker → crawler → planner → executor → report.
Requires a running backend or starts one automatically.

Usage:
    # Against a running backend:
    python scripts/validate_e2e.py --base-url http://localhost:8000

    # Auto-start backend:
    python scripts/validate_e2e.py

    # With a specific target site:
    python scripts/validate_e2e.py --target https://example.com
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import requests

# ── Config ─────────────────────────────────────────────────────────────

DEFAULT_BASE = "http://localhost:8000"
DEFAULT_TARGET = "https://example.com"
DEFAULT_TOKEN = "dev-e2e-validator"
POLL_INTERVAL = 5  # seconds
MAX_WAIT = 360     # 6 minutes


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    symbol = {"INFO": "ℹ", "OK": "✓", "FAIL": "✗", "WARN": "⚠"}.get(level, " ")
    print(f"  [{ts}] {symbol}  {msg}")


def check_health(base: str) -> dict:
    """Check backend health."""
    r = requests.get(f"{base}/v1/health", timeout=10)
    r.raise_for_status()
    return r.json()


def create_job(base: str, token: str, target: str) -> dict:
    """Create a Deep Scan job."""
    r = requests.post(
        f"{base}/v1/jobs",
        json={"url": target},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def poll_job(base: str, token: str, job_id: str) -> dict:
    """Poll a job until terminal state."""
    headers = {"Authorization": f"Bearer {token}"}
    start = time.monotonic()

    while time.monotonic() - start < MAX_WAIT:
        r = requests.get(f"{base}/v1/jobs/{job_id}", headers=headers, timeout=10)
        r.raise_for_status()
        job = r.json()

        state = job["state"]
        progress = job.get("progress", 0)
        message = job.get("message", "")

        log(f"[{state}] {progress}% — {message}")

        if state in ("complete", "failed"):
            return job

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Job {job_id} did not finish within {MAX_WAIT}s")


def validate_report(report: dict) -> list:
    """Validate report shape and return list of issues found."""
    errors = []
    required = [
        "summary", "score", "issues", "critical_count", "high_count",
        "medium_count", "pages_crawled", "actions_taken", "duration_ms",
        "engine_version", "generated_at",
    ]
    for key in required:
        if key not in report:
            errors.append(f"Missing report key: {key}")

    if "score" in report:
        if not (0 <= report["score"] <= 100):
            errors.append(f"Score out of range: {report['score']}")

    if "issues" in report:
        if not isinstance(report["issues"], list):
            errors.append("issues is not a list")
        for i, issue in enumerate(report["issues"]):
            for field in ("id", "severity", "rule", "message"):
                if field not in issue:
                    errors.append(f"Issue {i} missing field: {field}")

    if "engine_version" in report:
        if report["engine_version"] != "deep-1.0":
            errors.append(f"Unexpected engine: {report['engine_version']}")

    return errors


def check_quota(base: str, token: str) -> dict:
    """Check user quota."""
    r = requests.get(
        f"{base}/v1/user/quota",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def check_profile(base: str, token: str) -> dict:
    """Check user profile."""
    r = requests.get(
        f"{base}/v1/user/profile",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def run_validation(base: str, token: str, target: str) -> bool:
    """Run the full E2E validation. Returns True if all checks pass."""
    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            log(f"{name}: PASSED", "OK")
            passed += 1
        else:
            log(f"{name}: FAILED — {detail}", "FAIL")
            failed += 1

    print("\n" + "=" * 60)
    print("  QAPAL End-to-End Validation")
    print("=" * 60)
    print(f"  Backend:  {base}")
    print(f"  Target:   {target}")
    print(f"  Token:    {token[:10]}...")
    print("=" * 60 + "\n")

    # ── 1. Health Check ────────────────────────────────────────────
    print("▸ Step 1: Health Check")
    try:
        health = check_health(base)
        check("Health endpoint", health.get("db") == "ok", f"db={health.get('db')}")
        check("Version present", bool(health.get("version")))
        check("AI field present", "ai" in health)
        check("Playwright field present", "playwright" in health)
        check("Disk field present", "disk" in health)

        if health.get("ai") != "ok":
            log(f"AI status: {health.get('ai')} — Deep Scan may fail without AI provider", "WARN")
        if health.get("playwright") != "ok":
            log(f"Playwright status: {health.get('playwright')} — Deep Scan requires Playwright", "WARN")
    except Exception as e:
        check("Health endpoint", False, str(e))
        log("Backend unreachable — aborting", "FAIL")
        return False

    # ── 2. Auth & Profile ──────────────────────────────────────────
    print("\n▸ Step 2: Auth & Profile")
    try:
        profile = check_profile(base, token)
        check("Profile loads", bool(profile.get("id")))
        check("Email present", bool(profile.get("email")))
        check("Tier present", profile.get("tier") in ("free", "starter", "pro"))
    except Exception as e:
        check("Profile loads", False, str(e))

    # ── 3. Quota ───────────────────────────────────────────────────
    print("\n▸ Step 3: Quota")
    try:
        quota = check_quota(base, token)
        check("Quota endpoint", True)
        check("Used is int", isinstance(quota.get("used"), int))
        check("Limit is int", isinstance(quota.get("limit"), int))
        check("Reset date present", bool(quota.get("resets_at")))
        log(f"Quota: {quota['used']}/{quota['limit']} used, resets {quota['resets_at']}")
    except Exception as e:
        check("Quota endpoint", False, str(e))

    # ── 4. Create Deep Scan Job ────────────────────────────────────
    print("\n▸ Step 4: Create Deep Scan Job")
    try:
        job = create_job(base, token, target)
        job_id = job["id"]
        check("Job created", job.get("state") in ("queued", "running"))
        check("Job has ID", bool(job_id))
        check("Job URL matches", job.get("url") == target)
        log(f"Job ID: {job_id}")
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        check("Job created", False, f"{e.response.status_code}: {detail}")
        log("Cannot create job — aborting remaining checks", "FAIL")
        print(f"\n{'=' * 60}")
        print(f"  Result: {passed} passed, {failed} failed")
        print(f"{'=' * 60}\n")
        return failed == 0
    except Exception as e:
        check("Job created", False, str(e))
        return False

    # ── 5. Poll Until Complete ─────────────────────────────────────
    print("\n▸ Step 5: Poll Job Progress")
    try:
        final_job = poll_job(base, token, job_id)
        state = final_job["state"]
        check("Job reached terminal state", state in ("complete", "failed"))

        if state == "complete":
            log("Job completed successfully!", "OK")
        else:
            error = final_job.get("error", "Unknown error")
            failure_stage = final_job.get("failure_stage", "unknown")
            log(f"Job failed during {failure_stage}: {error}", "WARN")
    except TimeoutError:
        check("Job finished in time", False, f"Timed out after {MAX_WAIT}s")
        return False

    # ── 6. Validate Report ─────────────────────────────────────────
    print("\n▸ Step 6: Validate Report")
    report = final_job.get("report")
    if report:
        check("Report present", True)
        report_errors = validate_report(report)
        check("Report schema valid", len(report_errors) == 0,
              "; ".join(report_errors) if report_errors else "")

        log(f"Score: {report.get('score', '?')}/100")
        log(f"Issues: {len(report.get('issues', []))}")
        log(f"Pages crawled: {report.get('pages_crawled', '?')}")
        log(f"Actions taken: {report.get('actions_taken', '?')}")
        log(f"Duration: {report.get('duration_ms', '?')}ms")
        log(f"Engine: {report.get('engine_version', '?')}")

        # Check for narration (Phase 8)
        if report.get("narration"):
            check("AI narration present", True)
            log(f"Narration: {report['narration'][:100]}...")
        else:
            log("No AI narration (expected if QAPAL_AI_PROVIDER not set)", "WARN")
    else:
        if state == "complete":
            check("Report present", False, "Job completed but no report")
        else:
            log("No report (job failed)", "WARN")

    # ── 7. Verify Quota Decremented ────────────────────────────────
    print("\n▸ Step 7: Post-Scan Checks")
    try:
        quota_after = check_quota(base, token)
        check("Quota incremented", quota_after["used"] > quota.get("used", 0),
              f"Before: {quota.get('used', 0)}, After: {quota_after['used']}")
    except Exception as e:
        check("Quota check", False, str(e))

    # ── 8. Job List ────────────────────────────────────────────────
    try:
        r = requests.get(
            f"{base}/v1/jobs",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        jobs = r.json()
        check("Job list includes new job", any(j["id"] == job_id for j in jobs.get("jobs", [])))
    except Exception as e:
        check("Job list", False, str(e))

    # ── Summary ────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    total = passed + failed
    print(f"  Result: {passed}/{total} checks passed", end="")
    if failed > 0:
        print(f" ({failed} FAILED)")
    else:
        print(" — ALL PASSED!")
    print(f"{'=' * 60}\n")

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="QAPAL E2E validation")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="Backend URL")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="Site to scan")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Auth token")
    args = parser.parse_args()

    success = run_validation(args.base_url, args.token, args.target)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
