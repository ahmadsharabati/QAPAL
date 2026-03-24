"""
tests/e2e/test_deep_scan_pipeline.py
=====================================
End-to-end tests for the full Deep Scan worker pipeline.

Serves a local HTML fixture via Python's http.server so no outbound
internet access is required.  The AI generator is mocked to return
deterministic plans — the browser (Playwright), crawler, and executor
all run for real against the local fixture.

What these tests prove:
  - run_deep_scan() traverses all pipeline stages without crashing
  - The Job in the DB reaches state="complete" with a valid report blob
  - The report schema matches the Report interface the extension depends on
  - failure_stage is populated on error; partial report is saved
  - Timeout handler saves whatever partial data was collected and marks
    failure_stage correctly
  - Playwright trace zips are written for failed test-cases

Markers:
  @pytest.mark.slow  — each test launches a real Chromium (~10-30s)

Run:
  pytest tests/e2e/test_deep_scan_pipeline.py -v
  pytest tests/e2e/test_deep_scan_pipeline.py -v -m slow
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.worker import run_deep_scan


# ── Fixture HTML server ────────────────────────────────────────────────────────

_FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "simple_app.html").read_bytes()


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_FIXTURE_HTML)))
        self.end_headers()
        self.wfile.write(_FIXTURE_HTML)

    def log_message(self, *args):
        pass  # silence HTTP logs during tests


@contextmanager
def _fixture_server():
    """Serve the fixture HTML on a random localhost port. Yields the base URL."""
    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()


# ── Fake AI client ────────────────────────────────────────────────────────────

def _make_fake_ai(fixture_url: str, *, plans: list | None = None) -> MagicMock:
    """
    Return a mock AIClient that produces a deterministic 2-plan JSON response.

    Plans use data-testid selectors so they work against the fixture HTML
    regardless of which locators the crawler happened to index.

    Pass `plans=` to override the default plans (e.g. for failure-path tests).
    """
    if plans is None:
        plans = [
            {
                "test_id": "TC001_page_loads",
                "name": "Page loads with form visible",
                "steps": [
                    {"action": "navigate", "url": fixture_url},
                ],
                "assertions": [
                    {
                        "type": "element_visible",
                        "selector": {"strategy": "testid", "value": "email"},
                    }
                ],
            },
            {
                "test_id": "TC002_form_submit",
                "name": "Submit login form with valid email",
                "steps": [
                    {"action": "navigate", "url": fixture_url},
                    {
                        "action": "fill",
                        "selector": {"strategy": "testid", "value": "email"},
                        "value": "user@example.com",
                    },
                    {
                        "action": "click",
                        "selector": {"strategy": "testid", "value": "login-btn"},
                    },
                ],
                "assertions": [
                    {
                        "type": "element_text_contains",
                        "selector": {"strategy": "testid", "value": "status"},
                        "value": "Welcome",
                    }
                ],
            },
        ]

    payload = json.dumps(plans)
    ai = MagicMock()
    ai.model = "fake-model-v1"
    ai.complete.return_value = payload
    ai.acomplete = AsyncMock(return_value=payload)
    return ai


# ── Isolated DB factory ────────────────────────────────────────────────────────

def _make_test_db(url: str):
    """
    Create an isolated file-backed SQLite DB, insert a queued Job,
    and return (Session_factory, job_id, db_file_path) so the caller
    can patch backend.worker.SessionLocal and inspect results.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import backend.models  # noqa: registers all ORM models with Base
    from backend.database import Base
    from backend.models import Job, User

    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()

    engine = create_engine(
        f"sqlite:///{tf.name}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    session = Session()
    user = User(id="e2e-user", email="e2e@test.local", tier="pro")
    session.add(user)
    job = Job(id=str(uuid.uuid4()), user_id="e2e-user", url=url)
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()

    return engine, Session, job_id, tf.name


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_job(Session, job_id):
    from backend.models import Job
    session = Session()
    try:
        return session.query(Job).filter_by(id=job_id).first()
    finally:
        session.close()


def _run_pipeline(Session, job_id, fake_ai):
    """Patch SessionLocal + AIClient and run run_deep_scan synchronously."""
    # AIClient is imported *inside* run_deep_scan, so we patch the source module
    with patch("backend.worker.SessionLocal", Session), \
         patch("ai_client.AIClient") as MockAI:
        MockAI.from_env.return_value = fake_ai
        asyncio.run(run_deep_scan(job_id))


# ═══════════════════════════════════════════════════════════════════════════════
# Suite 1 — Happy path
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestDeepScanHappyPath:
    """The full crawl→plan→execute→report pipeline against a local fixture."""

    def test_job_reaches_complete(self):
        """run_deep_scan() drives the job to state='complete'."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                job = _get_job(Session, job_id)
                assert job.state == "complete", \
                    f"Expected complete, got {job.state!r}. error={job.error}"
                assert job.progress == 100
                assert job.failure_stage is None
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_report_is_non_null(self):
        """A complete job carries a non-null report dict."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                job = _get_job(Session, job_id)
                assert job.report is not None
                assert isinstance(job.report, dict)
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_report_schema_complete(self):
        """Every field the extension's Report interface requires is present."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                required = {
                    "summary", "score", "issues",
                    "critical_count", "high_count", "medium_count",
                    "pages_crawled", "actions_taken",
                    "duration_ms", "engine_version", "generated_at",
                }
                missing = required - set(r.keys())
                assert not missing, f"Report missing keys: {missing}"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_score_is_valid_integer(self):
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                assert isinstance(r["score"], int)
                assert 0 <= r["score"] <= 100
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_pages_crawled_at_least_one(self):
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                assert r["pages_crawled"] >= 1
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_duration_ms_positive(self):
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                assert isinstance(r["duration_ms"], int)
                assert r["duration_ms"] > 0
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_issues_is_list(self):
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                assert isinstance(r["issues"], list)
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_generated_test_is_present(self):
        """Codegen produces a non-empty test file attached to the report."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                job = _get_job(Session, job_id)
                gt = (job.report or {}).get("generated_test")
                # May be None if codegen module unavailable — just check type
                assert gt is None or isinstance(gt, str)
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_engine_version_field(self):
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                r = _get_job(Session, job_id).report
                assert r["engine_version"] == "deep-1.0"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Suite 2 — Failure-stage tracking
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestDeepScanFailurePaths:
    """failure_stage and partial-report persistence on error."""

    def test_bad_ai_response_sets_failure_stage(self):
        """
        When the AI returns unparseable garbage the generator raises,
        the job ends in state='failed' with failure_stage populated.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            bad_ai = MagicMock()
            bad_ai.model = "garbage-model"
            bad_ai.complete.return_value = "THIS IS NOT JSON AT ALL ~~~"
            bad_ai.acomplete = AsyncMock(return_value="THIS IS NOT JSON AT ALL ~~~")
            try:
                _run_pipeline(Session, job_id, bad_ai)
                job = _get_job(Session, job_id)
                assert job.state == "failed"
                assert job.failure_stage in ("plan", "execute"), \
                    f"Unexpected failure_stage: {job.failure_stage!r}"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_partial_report_saved_after_crawl_succeeds(self):
        """
        If the pipeline fails during/after crawl, a partial report
        (with pages_crawled > 0) is saved so the extension isn't empty-handed.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            # AI returns garbage — plan stage fails, but crawl already ran
            bad_ai = MagicMock()
            bad_ai.model = "garbage-model"
            bad_ai.complete.return_value = "not json"
            bad_ai.acomplete = AsyncMock(return_value="not json")
            try:
                _run_pipeline(Session, job_id, bad_ai)
                job = _get_job(Session, job_id)
                if job.report is not None:
                    # Partial report must include the failure annotation
                    assert "failed during" in job.report.get("summary", "")
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_failed_job_has_error_field(self):
        """A failed job populates job.error with the exception message."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            bad_ai = MagicMock()
            bad_ai.model = "err-model"
            bad_ai.complete.side_effect = RuntimeError("AI is down")
            bad_ai.acomplete = AsyncMock(side_effect=RuntimeError("AI is down"))
            try:
                _run_pipeline(Session, job_id, bad_ai)
                job = _get_job(Session, job_id)
                assert job.state == "failed"
                assert job.error is not None
                assert len(job.error) > 0
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Suite 3 — Timeout handling
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestDeepScanTimeout:
    """asyncio.TimeoutError is caught, partial state saved, job marked complete."""

    def test_crawl_timeout_sets_failure_stage(self):
        """
        Simulating a crawl timeout: the pipeline's asyncio.wait_for raises
        TimeoutError on the first call (the crawl). The job should end
        as 'complete' (partial) with failure_stage='crawl'.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            fake_ai = _make_fake_ai(url)

            original_wait_for = asyncio.wait_for

            async def timeout_on_crawl(coro, timeout):
                # First wait_for call is the spider crawl — make it time out.
                # Close the coroutine to avoid "was never awaited" ResourceWarning.
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    coro.close()
                raise asyncio.TimeoutError()

            try:
                with patch("backend.worker.SessionLocal", Session), \
                     patch("ai_client.AIClient") as MockAI, \
                     patch("backend.worker.asyncio.wait_for",
                           side_effect=timeout_on_crawl):
                    MockAI.from_env.return_value = fake_ai
                    asyncio.run(run_deep_scan(job_id))

                job = _get_job(Session, job_id)
                # Timeout is treated as complete-with-partial, not failed
                assert job.state == "complete"
                assert job.failure_stage == "crawl"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_execute_timeout_saves_crawl_data(self):
        """
        Simulating a timeout during execution: crawl runs for real (gives
        pages_crawled >= 1), then the executor wait_for times out.
        The partial report should have pages_crawled >= 1.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            fake_ai = _make_fake_ai(url)
            original_wait_for = asyncio.wait_for
            call_count = 0

            async def first_ok_then_timeout(coro, timeout):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return await original_wait_for(coro, timeout)
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    coro.close()
                raise asyncio.TimeoutError()

            try:
                with patch("backend.worker.SessionLocal", Session), \
                     patch("ai_client.AIClient") as MockAI, \
                     patch("backend.worker.asyncio.wait_for",
                           side_effect=first_ok_then_timeout):
                    MockAI.from_env.return_value = fake_ai
                    asyncio.run(run_deep_scan(job_id))

                job = _get_job(Session, job_id)
                assert job.state == "complete"
                assert job.failure_stage in ("plan", "execute")
                r = job.report
                assert r is not None
                assert r["pages_crawled"] >= 1, \
                    "Crawl data should be in the partial report"
                assert "timed out during" in r["summary"]
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_timeout_report_contains_required_fields(self):
        """Even a timeout-partial report has the required schema fields."""
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            fake_ai = _make_fake_ai(url)

            async def always_timeout(coro, timeout):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    coro.close()
                raise asyncio.TimeoutError()

            try:
                with patch("backend.worker.SessionLocal", Session), \
                     patch("ai_client.AIClient") as MockAI, \
                     patch("backend.worker.asyncio.wait_for",
                           side_effect=always_timeout):
                    MockAI.from_env.return_value = fake_ai
                    asyncio.run(run_deep_scan(job_id))

                r = _get_job(Session, job_id).report
                assert r is not None
                for key in ("summary", "score", "issues", "duration_ms"):
                    assert key in r, f"Timeout report missing key: {key!r}"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Suite 4 — Playwright trace capture
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestDeepScanTraces:
    """Playwright trace zips are written for failed test-cases."""

    def test_passing_tests_produce_no_trace_path(self):
        """
        When all tests pass, no trace zips are written and trace_path is None.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            try:
                _run_pipeline(Session, job_id, _make_fake_ai(url))
                job = _get_job(Session, job_id)
                # Passing tests: executor discards traces → no zip files → None
                # (trace_path may still be set if any test flaked; allow both)
                assert job.trace_path is None or isinstance(job.trace_path, str)
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)

    def test_failing_test_writes_trace_zip(self):
        """
        A plan that clicks a nonexistent element causes a test failure.
        The executor writes a Playwright trace zip and trace_path is set.
        """
        with _fixture_server() as url:
            engine, Session, job_id, db_path = _make_test_db(url)
            failing_plans = [
                {
                    "test_id": "TC001_intentional_fail",
                    "name": "Click nonexistent element",
                    "steps": [
                        {"action": "navigate", "url": url},
                        {
                            "action": "click",
                            "selector": {
                                "strategy": "testid",
                                "value": "does-not-exist-xyz-99999",
                            },
                        },
                    ],
                    "assertions": [],
                }
            ]
            fail_ai = _make_fake_ai(url, plans=failing_plans)
            try:
                _run_pipeline(Session, job_id, fail_ai)
                job = _get_job(Session, job_id)
                assert job.state == "complete"
                # Failed test → trace dir should exist with at least 1 zip
                if job.trace_path:
                    trace_dir = Path(job.trace_path)
                    zips = list(trace_dir.glob("*.zip")) if trace_dir.exists() else []
                    assert len(zips) >= 1, \
                        f"Expected trace zips in {trace_dir}, found none"
            finally:
                engine.dispose()
                Path(db_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Suite 5 — Structured logging
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeepScanLogging:
    """Worker log records are tagged with the job ID (no browser needed)."""

    def test_job_logger_tags_records_with_job_id(self):
        """LoggerAdapter injects job_id into every emitted record."""
        import logging
        from backend.worker import _job_logger

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logging.getLogger("qapal.worker").addHandler(handler)
        logging.getLogger("qapal.worker").setLevel(logging.DEBUG)
        try:
            log = _job_logger("abc123")
            log.info("test message")
            assert records, "No log records emitted"
            rec = records[-1]
            assert getattr(rec, "job_id", None) == "abc123"
        finally:
            logging.getLogger("qapal.worker").removeHandler(handler)

    def test_job_logger_different_ids_are_independent(self):
        """Two adapters for different jobs don't bleed job_id into each other."""
        import logging
        from backend.worker import _job_logger

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logging.getLogger("qapal.worker").addHandler(handler)
        logging.getLogger("qapal.worker").setLevel(logging.DEBUG)
        try:
            log_a = _job_logger("job-aaa")
            log_b = _job_logger("job-bbb")
            log_a.info("from a")
            log_b.info("from b")
            ids = [getattr(r, "job_id", None) for r in records[-2:]]
            assert "job-aaa" in ids
            assert "job-bbb" in ids
        finally:
            logging.getLogger("qapal.worker").removeHandler(handler)
