"""
Worker — MVP lifecycle stub.

Simulates the scan lifecycle and produces a placeholder report.
Exercises job state transitions (queued → running → complete/failed)
and report plumbing WITHOUT running the real engine yet.

Phase 4 will swap this stub with real engine execution.
"""

import time
import logging
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models import Job

logger = logging.getLogger("qapal.worker")


def _placeholder_report(url: str, duration_ms: int) -> dict:
    """
    Generate a placeholder report that matches the expected shape.
    The extension UI can render this shape immediately.
    """
    return {
        "summary": f"Scan completed for {url} (simulated)",
        "score": 85,
        "issues": [
            {
                "id": "sim-001",
                "severity": "medium",
                "rule": "PLACEHOLDER",
                "message": "This is a simulated issue for lifecycle validation",
                "page": url,
                "element": None,
            }
        ],
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 1,
        "pages_crawled": 1,
        "actions_taken": 0,
        "duration_ms": duration_ms,
        "engine_version": "stub-1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scan_stub(job_id: str) -> None:
    """
    Background task: simulate the scan lifecycle.

    This is a lifecycle stub — it validates that:
    1. Jobs transition correctly (queued → running → complete)
    2. Progress updates flow to the database
    3. Reports are attached in the expected shape
    4. The polling API can observe each stage

    No real crawling, no Playwright, no network access.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error("Job %s not found", job_id)
            return

        if job.state != "queued":
            logger.warning("Job %s is in state '%s', expected 'queued'", job_id, job.state)
            return

        # ── Running ──────────────────────────────────────────────────
        job.transition("running")
        job.progress = 10
        job.message = "Starting scan..."
        db.commit()
        logger.info("Job %s → running", job_id)

        start = time.monotonic()

        # Simulate progress checkpoints
        checkpoints = [
            (25, "Analyzing site structure..."),
            (50, "Discovering interactive elements..."),
            (75, "Validating actions..."),
            (90, "Generating report..."),
        ]

        for progress, message in checkpoints:
            time.sleep(0.1)  # simulate work (fast for tests)
            job.progress = progress
            job.message = message
            db.commit()

        # ── Complete ─────────────────────────────────────────────────
        duration_ms = int((time.monotonic() - start) * 1000)
        report = _placeholder_report(job.url, duration_ms)

        job.progress = 100
        job.message = "Scan complete"
        job.report = report
        job.transition("complete")
        db.commit()
        logger.info("Job %s → complete (%.0fms)", job_id, duration_ms)

    except Exception as e:
        logger.exception("Job %s failed: %s", job_id, e)
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job and job.state == "running":
                job.transition("failed")
                job.error = str(e)
                job.message = "Scan failed"
                db.commit()
        except Exception:
            logger.exception("Failed to mark job %s as failed", job_id)
    finally:
        db.close()
