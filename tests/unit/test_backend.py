"""
Backend API tests — uses FastAPI TestClient (no server needed).

Tests cover: health, auth, quota, job lifecycle, SSRF, ownership, state machine,
rate limiting, concurrent scan limits, narration, security headers.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.config import settings
import backend.models  # noqa: ensure all ORM models register with Base.metadata before create_all

# ── Test DB (in-memory SQLite with shared connection) ────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    """Create fresh tables for every test."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


def _stub_deep_scan(job_id: str, user_id: str = ""):
    """Sync stub that replaces the async _run_and_deregister for API tests."""
    from backend.models import Job
    from backend.services.rate_limit import deregister_active_scan
    from datetime import datetime, timezone

    try:
        db = _TestSession()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return
            job.transition("running")
            job.progress = 50
            job.message = "Running (stub)..."
            db.commit()

            job.progress = 100
            job.message = "Scan complete"
            job.report = {
                "summary": f"Stub scan for {job.url}",
                "score": 85,
                "issues": [],
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "pages_crawled": 1,
                "actions_taken": 0,
                "duration_ms": 100,
                "engine_version": "test-stub",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "narration": "Test site passed all checks with no issues.",
            }
            job.transition("complete")
            db.commit()
        finally:
            db.close()
    finally:
        # Deregister from active scans (mirrors _run_and_deregister)
        deregister_active_scan(user_id, job_id)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset rate limit state between tests."""
    from backend.services.rate_limit import _global_limiter, _scan_limiter, _active_scans
    _global_limiter._requests.clear()
    _scan_limiter._requests.clear()
    _active_scans.clear()
    yield


@pytest.fixture()
def client():
    """FastAPI TestClient with in-memory DB and stubbed worker."""
    from backend.app import create_app

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    # Replace the async worker with a sync stub for API tests
    with patch("backend.routers.jobs._run_and_deregister", _stub_deep_scan):
        yield TestClient(app, raise_server_exceptions=False)


# Dev auth headers
AUTH = {"Authorization": "Bearer dev-testuser"}
AUTH_USER2 = {"Authorization": "Bearer dev-user2"}


# ============================================================================
# Health
# ============================================================================

class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["db"] == "ok"
        assert data["version"] == settings.APP_VERSION

    def test_health_no_auth_required(self, client):
        """Health endpoint should work without auth."""
        r = client.get("/v1/health")
        assert r.status_code == 200

    def test_health_includes_all_fields(self, client):
        """Health response includes ai, playwright, disk fields."""
        r = client.get("/v1/health")
        data = r.json()
        for field in ("status", "db", "ai", "playwright", "disk", "version"):
            assert field in data, f"Missing field: {field}"

    def test_health_degraded_without_ai(self, client):
        """Missing AI provider env var → status=degraded, ai=missing_provider."""
        with patch.dict("os.environ", {"QAPAL_AI_PROVIDER": ""}, clear=False):
            r = client.get("/v1/health")
            data = r.json()
            assert data["ai"] == "missing_provider"
            # If DB is fine but AI is missing, overall should be degraded
            if data["db"] == "ok":
                assert data["status"] == "degraded"


# ============================================================================
# Auth
# ============================================================================

class TestAuth:
    def test_no_auth_returns_401(self, client):
        """Endpoints that require auth return 401 without a token."""
        r = client.get("/v1/user/profile")
        assert r.status_code in (401, 403)

    def test_dev_token_creates_user(self, client):
        """Dev stub auth creates a user on first request."""
        r = client.get("/v1/user/profile", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "testuser@dev.local"
        assert data["tier"] == "free"

    def test_dev_token_upserts(self, client):
        """Second call with same token returns same user."""
        client.get("/v1/user/profile", headers=AUTH)
        r = client.get("/v1/user/profile", headers=AUTH)
        assert r.status_code == 200


# ============================================================================
# Quota
# ============================================================================

class TestQuota:
    def test_quota_starts_full(self, client):
        r = client.get("/v1/user/quota", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["used"] == 0
        assert data["limit"] == settings.FREE_TIER_LIMIT

    def test_quota_decrements_on_job_create(self, client):
        client.post("/v1/jobs", json={"url": "https://example.com"}, headers=AUTH)
        r = client.get("/v1/user/quota", headers=AUTH)
        assert r.json()["used"] == 1

    def test_quota_exceeded_returns_403(self, client):
        """Creating more jobs than the free limit returns 403."""
        for i in range(settings.FREE_TIER_LIMIT):
            r = client.post(
                "/v1/jobs",
                json={"url": f"https://example.com/{i}"},
                headers=AUTH,
            )
            assert r.status_code == 201, f"Job {i} failed: {r.text}"

        # This one should be blocked
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com/blocked"},
            headers=AUTH,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        msg = detail["message"] if isinstance(detail, dict) else detail
        assert "quota" in msg.lower()

    def test_quota_exceeded_includes_upgrade_hint(self, client):
        """Quota error message suggests upgrading."""
        for i in range(settings.FREE_TIER_LIMIT):
            client.post(
                "/v1/jobs",
                json={"url": f"https://example.com/{i}"},
                headers=AUTH,
            )
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com/over"},
            headers=AUTH,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        msg = detail["message"] if isinstance(detail, dict) else detail
        assert "upgrade" in msg.lower()
        # Structured error should also include an error code
        if isinstance(detail, dict):
            assert detail.get("error") == "QUOTA_EXCEEDED"


# ============================================================================
# Jobs — CRUD
# ============================================================================

class TestJobLifecycle:
    def test_create_job(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["url"] == "https://example.com"
        assert data["id"]
        # After background task, job should be complete
        assert data["state"] in ("queued", "complete")

    def test_get_job(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["id"] == job_id

    def test_list_jobs(self, client):
        for i in range(3):
            client.post(
                "/v1/jobs",
                json={"url": f"https://example.com/{i}"},
                headers=AUTH,
            )

        r = client.get("/v1/jobs", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["jobs"]) == 3

    def test_delete_job(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 204

        # Job should now be hidden
        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 404

    def test_deleted_job_hidden_from_list(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        client.delete(f"/v1/jobs/{job_id}", headers=AUTH)

        r = client.get("/v1/jobs", headers=AUTH)
        assert r.json()["total"] == 0

    def test_job_response_shape(self, client):
        """Verify the normalized response shape has all expected fields."""
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        data = r.json()
        expected_keys = {
            "id", "state", "progress", "message", "url",
            "report", "error", "failure_stage", "trace_path",
            "created_at", "started_at", "completed_at",
        }
        assert expected_keys.issubset(set(data.keys()))


# ============================================================================
# Jobs — State Machine
# ============================================================================

class TestJobStateMachine:
    def test_allowed_transitions(self, client):
        """Verify the state machine allows correct transitions."""
        from backend.models import valid_transition
        assert valid_transition("queued", "running") is True
        assert valid_transition("running", "complete") is True
        assert valid_transition("running", "failed") is True
        assert valid_transition("running", "deleted") is False
        assert valid_transition("complete", "deleted") is True
        assert valid_transition("failed", "deleted") is True
        assert valid_transition("deleted", "queued") is False
        assert valid_transition("deleted", "running") is False


# ============================================================================
# Jobs — Ownership
# ============================================================================

class TestJobOwnership:
    def test_cannot_read_other_users_job(self, client):
        """User 2 cannot access User 1's job."""
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH_USER2)
        assert r.status_code == 404

    def test_cannot_delete_other_users_job(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.delete(f"/v1/jobs/{job_id}", headers=AUTH_USER2)
        assert r.status_code == 404

    def test_list_shows_only_own_jobs(self, client):
        """Each user only sees their own jobs."""
        client.post("/v1/jobs", json={"url": "https://a.com"}, headers=AUTH)
        client.post("/v1/jobs", json={"url": "https://b.com"}, headers=AUTH_USER2)

        r = client.get("/v1/jobs", headers=AUTH)
        assert r.json()["total"] == 1
        assert r.json()["jobs"][0]["url"] == "https://a.com"


# ============================================================================
# Jobs — SSRF Protection
# ============================================================================

class TestSSRFProtection:
    @pytest.mark.parametrize("url", [
        "http://localhost:8080",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/internal",
        "http://172.16.0.1/secret",
        "http://192.168.1.1/router",
        "http://169.254.169.254/metadata",
    ])
    def test_ssrf_urls_blocked(self, client, url):
        r = client.post(
            "/v1/jobs",
            json={"url": url},
            headers=AUTH,
        )
        assert r.status_code == 422  # Pydantic validation error

    def test_valid_url_accepted(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        assert r.status_code == 201

    def test_non_http_url_rejected(self, client):
        r = client.post(
            "/v1/jobs",
            json={"url": "ftp://files.example.com"},
            headers=AUTH,
        )
        assert r.status_code == 422


# ============================================================================
# Rate Limiting
# ============================================================================

class TestRateLimiting:
    def test_rate_limit_headers_present(self, client):
        """All responses include rate limit headers."""
        r = client.get("/v1/user/profile", headers=AUTH)
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers

    def test_health_exempt_from_rate_limit(self, client):
        """Health endpoint is exempt from rate limiting."""
        for _ in range(100):
            r = client.get("/v1/health")
            assert r.status_code == 200

    def test_scan_rate_limit_unit(self):
        """Scan rate limiter blocks rapid-fire creates."""
        from backend.services.rate_limit import check_scan_rate_limit

        # First 10 should pass
        for _ in range(10):
            allowed, _ = check_scan_rate_limit("test-user")
            assert allowed is True

        # 11th should be blocked
        allowed, headers = check_scan_rate_limit("test-user")
        assert allowed is False
        assert "Retry-After" in headers


# ============================================================================
# Concurrent Scan Limits
# ============================================================================

class TestConcurrentScans:
    def test_concurrent_limit_free_tier(self):
        """Free tier users can only run 1 concurrent scan."""
        from backend.services.rate_limit import (
            can_start_scan, register_active_scan, deregister_active_scan,
        )

        assert can_start_scan("u1", "free") is True
        register_active_scan("u1", "job-1")
        assert can_start_scan("u1", "free") is False

        deregister_active_scan("u1", "job-1")
        assert can_start_scan("u1", "free") is True

    def test_concurrent_limit_pro_tier(self):
        """Pro tier allows up to 5 concurrent scans."""
        from backend.services.rate_limit import can_start_scan, register_active_scan

        for i in range(5):
            assert can_start_scan("u1", "pro") is True
            register_active_scan("u1", f"job-{i}")

        assert can_start_scan("u1", "pro") is False

    def test_concurrent_limit_isolated_per_user(self):
        """One user's scans don't affect another user's limits."""
        from backend.services.rate_limit import can_start_scan, register_active_scan

        register_active_scan("u1", "job-1")
        assert can_start_scan("u2", "free") is True


# ============================================================================
# Security Headers
# ============================================================================

class TestSecurityHeaders:
    def test_x_frame_options(self, client):
        r = client.get("/v1/health")
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options(self, client):
        r = client.get("/v1/health")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy(self, client):
        r = client.get("/v1/health")
        assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, client):
        r = client.get("/v1/health")
        assert "camera=()" in r.headers.get("Permissions-Policy", "")


# ============================================================================
# Worker Stub
# ============================================================================

class TestWorkerIntegration:
    def test_worker_completes_job(self, client):
        """
        TestClient runs background tasks synchronously,
        so the job should be complete after creation.
        """
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        data = r.json()
        assert data["state"] == "complete"
        assert data["progress"] == 100

    def test_report_shape(self, client):
        """Completed job has the expected report shape."""
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        data = r.json()

        assert data["state"] == "complete"
        report = data["report"]
        assert "summary" in report
        assert "score" in report
        assert "issues" in report
        assert isinstance(report["issues"], list)
        assert "duration_ms" in report

    def test_report_includes_narration(self, client):
        """Completed job report includes narration field."""
        r = client.post(
            "/v1/jobs",
            json={"url": "https://example.com"},
            headers=AUTH,
        )
        job_id = r.json()["id"]

        r = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        report = r.json()["report"]
        assert "narration" in report


# ============================================================================
# Startup / Table Creation
# ============================================================================

class TestStartup:
    def test_tables_exist_after_boot(self, client):
        """Health check works, which means tables were created on startup."""
        r = client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["db"] == "ok"


# ============================================================================
# Worker Helpers — Pure Unit Tests (no Playwright, no AI, no network)
# ============================================================================

class TestWorkerHelpers:
    """Test the pure functions in backend.worker."""

    def test_extract_issues_step_failure(self):
        from backend.worker import _extract_issues

        exec_results = [
            {
                "id": "TC001",
                "status": "fail",
                "steps": [
                    {"action": "navigate", "url": "https://x.com", "status": "pass"},
                    # Phase 4: no category → falls back to INTERACTION_FAILURE/high
                    {"action": "click", "status": "fail", "reason": "Element not found"},
                ],
                "assertions": [],
                "passive_errors": {},
            }
        ]
        issues = _extract_issues(exec_results)
        assert len(issues) == 1
        assert issues[0]["severity"] == "high"
        assert issues[0]["rule"] == "INTERACTION_FAILURE"
        # Phase 4 message format: "[TC001] UNKNOWN: <reason>"
        assert "Element not found" in issues[0]["message"]

    def test_extract_issues_navigation_failure(self):
        """Phase 4: navigation timeouts use category=NAV_TIMEOUT → PAGE_LOAD_ERROR/critical."""
        from backend.worker import _extract_issues

        exec_results = [
            {
                "id": "TC002",
                "status": "fail",
                "steps": [
                    {
                        "action": "navigate",
                        "url": "https://x.com",
                        "status": "fail",
                        "reason": "Timeout",
                        "category": "NAV_TIMEOUT",  # Phase 4 executor sets this
                    },
                ],
                "assertions": [],
                "passive_errors": {},
            }
        ]
        issues = _extract_issues(exec_results)
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        assert issues[0]["rule"] == "PAGE_LOAD_ERROR"

    def test_extract_issues_assertion_failure(self):
        from backend.worker import _extract_issues

        exec_results = [
            {
                "id": "TC003",
                "status": "fail",
                "steps": [{"action": "navigate", "url": "https://x.com", "status": "pass"}],
                "assertions": [
                    {"type": "url_equals", "status": "fail", "value": "https://x.com/dash", "actual": "https://x.com/login"},
                ],
                "passive_errors": {},
            }
        ]
        issues = _extract_issues(exec_results)
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        # Phase 4: unified rule name for all assertion failures
        assert issues[0]["rule"] == "ASSERTION_ERROR"

    def test_extract_issues_passive_errors(self):
        from backend.worker import _extract_issues

        exec_results = [
            {
                "id": "TC004",
                "status": "pass",
                "steps": [],
                "assertions": [],
                "passive_errors": {
                    "console_errors": [{"text": "Uncaught ref error", "url": "https://x.com"}],
                    "js_exceptions": ["TypeError: x is not a function"],
                    "network_failures": [{"url": "https://x.com/api/data", "failure": "net::ERR_FAILED"}],
                },
            }
        ]
        issues = _extract_issues(exec_results)
        assert len(issues) == 3
        severities = {i["rule"]: i["severity"] for i in issues}
        assert severities["CONSOLE_ERROR"] == "medium"
        assert severities["JS_EXCEPTION"] == "high"
        assert severities["NETWORK_FAILURE"] == "medium"

    def test_extract_issues_empty_results(self):
        from backend.worker import _extract_issues

        issues = _extract_issues([])
        assert issues == []

    def test_calculate_score_no_issues(self):
        from backend.worker import _calculate_score
        assert _calculate_score([]) == 100

    def test_calculate_score_one_critical(self):
        from backend.worker import _calculate_score
        issues = [{"severity": "critical"}]
        assert _calculate_score(issues) == 75  # 100 - 25

    def test_calculate_score_mixed(self):
        from backend.worker import _calculate_score
        issues = [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        # 100 - 25 - 10 - 3 - 1 = 61
        assert _calculate_score(issues) == 61

    def test_calculate_score_floors_at_zero(self):
        from backend.worker import _calculate_score
        issues = [{"severity": "critical"}] * 5  # 5 * 25 = 125
        assert _calculate_score(issues) == 0

    def test_build_report_schema(self):
        from backend.worker import _build_report

        report = _build_report(
            url="https://example.com",
            crawl_results=[{"url": "https://example.com", "crawled": True}],
            exec_results=[
                {
                    "id": "TC001",
                    "status": "pass",
                    "steps": [{"action": "navigate", "url": "https://example.com", "status": "pass"}],
                    "assertions": [],
                    "passive_errors": {},
                }
            ],
            duration_ms=1500,
        )

        # Verify all required Report keys exist
        required_keys = [
            "summary", "score", "issues", "critical_count", "high_count",
            "medium_count", "pages_crawled", "actions_taken", "duration_ms",
            "engine_version", "generated_at",
        ]
        for key in required_keys:
            assert key in report, f"Missing key: {key}"

        assert report["engine_version"] == "deep-1.0"
        assert report["pages_crawled"] == 1
        assert report["duration_ms"] == 1500
        assert isinstance(report["issues"], list)
        assert report["score"] == 100  # no failures

    def test_build_report_with_failures(self):
        from backend.worker import _build_report

        report = _build_report(
            url="https://example.com",
            crawl_results=[{"url": "https://example.com", "crawled": True}],
            exec_results=[
                {
                    "id": "TC001",
                    "status": "fail",
                    "steps": [
                        # Phase 4: NAV_TIMEOUT category → PAGE_LOAD_ERROR/critical
                        {
                            "action": "navigate",
                            "url": "https://example.com",
                            "status": "fail",
                            "reason": "Timeout",
                            "category": "NAV_TIMEOUT",
                        },
                    ],
                    "assertions": [],
                    "passive_errors": {},
                }
            ],
            duration_ms=5000,
        )

        assert len(report["issues"]) == 1
        assert report["critical_count"] == 1
        assert report["score"] < 100

    def test_build_report_timed_out(self):
        from backend.worker import _build_report

        report = _build_report(
            url="https://example.com",
            crawl_results=[],
            exec_results=[],
            duration_ms=300000,
            timeout_stage="crawl",
        )

        assert "timed out" in report["summary"].lower()
        assert "crawl" in report["summary"].lower()
        assert report["score"] == 100  # no issues found (timed out before execution)

    def test_update_job(self):
        """_update_job writes fields to the DB correctly."""
        from backend.worker import _update_job
        from backend.models import Job

        # Create a job directly
        db = _TestSession()
        job = Job(user_id="u1", url="https://example.com", state="queued", progress=0)
        db.add(job)
        db.commit()
        job_id = job.id
        db.close()

        # Patch SessionLocal so _update_job uses our test DB
        with patch("backend.worker.SessionLocal", _TestSession):
            _update_job(job_id, progress=50, message="Halfway")

        # Verify
        db = _TestSession()
        job = db.query(Job).filter(Job.id == job_id).first()
        assert job.progress == 50
        assert job.message == "Halfway"
        db.close()

    def test_build_auto_prd(self):
        """Auto-PRD contains expected sections from mock locators."""
        from unittest.mock import MagicMock
        from backend.worker import _build_auto_prd

        mock_db = MagicMock()
        mock_db.get_all_locators.return_value = [
            {"role": "link", "name": "Home", "container": "nav"},
            {"role": "link", "name": "About", "container": "nav"},
            {"role": "textbox", "name": "Email"},
            {"role": "button", "name": "Submit"},
        ]
        mock_db.get_all.return_value = [{"id": 1}, {"id": 2}]

        crawl_results = [
            {"url": "https://example.com", "crawled": True},
            {"url": "https://example.com/about", "crawled": True},
        ]

        prd = _build_auto_prd(mock_db, "https://example.com", crawl_results)

        assert "Smoke Test" in prd
        assert "example.com" in prd
        assert "TC1" in prd
        assert "Navigation" in prd or "navigation" in prd
        assert "Form" in prd or "form" in prd
        assert "Home" in prd
        assert "Email" in prd

    def test_job_logger_includes_job_id(self):
        """LoggerAdapter auto-tags records with job_id."""
        from backend.worker import _job_logger
        import logging

        log = _job_logger("test-abc-123")
        with patch.object(log.logger, "handle") as mock_handle:
            log.info("Test message")
            assert mock_handle.called
            record = mock_handle.call_args[0][0]
            assert record.job_id == "test-abc-123"

    def test_build_report_with_timeout_stage(self):
        """Report summary includes the stage that timed out."""
        from backend.worker import _build_report

        report = _build_report(
            url="https://example.com",
            crawl_results=[{"url": "https://example.com", "crawled": True}],
            exec_results=[],
            duration_ms=120000,
            timeout_stage="execute",
        )
        assert "execute" in report["summary"]
        assert "timed out" in report["summary"].lower()

    def test_build_report_partial_on_error(self):
        """Report built from crawl-only data (no exec results) is valid."""
        from backend.worker import _build_report

        report = _build_report(
            url="https://example.com",
            crawl_results=[
                {"url": "https://example.com", "crawled": True},
                {"url": "https://example.com/about", "crawled": True},
            ],
            exec_results=[],
            duration_ms=5000,
        )
        assert report["pages_crawled"] == 2
        assert report["score"] == 100  # no exec = no issues
        assert report["issues"] == []
        assert "engine_version" in report

    def test_failure_stage_persisted(self):
        """_update_job(failure_stage='crawl') saves to DB."""
        from backend.worker import _update_job
        from backend.models import Job

        db = _TestSession()
        job = Job(user_id="u1", url="https://example.com", state="queued", progress=0)
        db.add(job)
        db.commit()
        job_id = job.id
        db.close()

        with patch("backend.worker.SessionLocal", _TestSession):
            _update_job(job_id, failure_stage="crawl", message="Timed out")

        db = _TestSession()
        job = db.query(Job).filter(Job.id == job_id).first()
        assert job.failure_stage == "crawl"
        assert job.message == "Timed out"
        db.close()

    def test_trace_path_persisted(self):
        """_update_job(trace_path=...) saves to DB."""
        from backend.worker import _update_job
        from backend.models import Job

        db = _TestSession()
        job = Job(user_id="u1", url="https://example.com", state="queued", progress=0)
        db.add(job)
        db.commit()
        job_id = job.id
        db.close()

        with patch("backend.worker.SessionLocal", _TestSession):
            _update_job(job_id, trace_path="/tmp/qapal_traces/test-123")

        db = _TestSession()
        job = db.query(Job).filter(Job.id == job_id).first()
        assert job.trace_path == "/tmp/qapal_traces/test-123"
        db.close()


# ============================================================================
# Narration Service — Unit Tests
# ============================================================================

class TestNarration:
    def test_template_narration_high_score(self):
        """High score → positive narration."""
        from backend.services.narration import _template_narration

        text = _template_narration(
            url="https://example.com",
            score=95,
            issues=[],
            pages_crawled=3,
        )
        assert "example.com" in text
        assert "3 page" in text

    def test_template_narration_low_score(self):
        """Low score → urgent narration."""
        from backend.services.narration import _template_narration

        issues = [
            {"severity": "critical"},
            {"severity": "critical"},
            {"severity": "high"},
        ]
        text = _template_narration(
            url="https://example.com",
            score=30,
            issues=issues,
            pages_crawled=2,
        )
        assert "critical" in text.lower() or "significant" in text.lower()

    def test_template_narration_timeout(self):
        """Timeout → partial results narration."""
        from backend.services.narration import _template_narration

        text = _template_narration(
            url="https://example.com",
            score=50,
            issues=[{"severity": "high"}],
            pages_crawled=1,
            timed_out=True,
        )
        assert "timed out" in text.lower()

    def test_narration_prompt_structure(self):
        """Narration prompt includes all required data."""
        from backend.services.narration import _build_narration_prompt

        prompt = _build_narration_prompt(
            url="https://example.com",
            score=75,
            issues=[
                {"severity": "high", "message": "Click failed"},
                {"severity": "medium", "message": "Console error"},
            ],
            pages_crawled=3,
            actions_taken=15,
        )
        assert "example.com" in prompt
        assert "75/100" in prompt
        assert "HIGH" in prompt
        assert "MEDIUM" in prompt


# ============================================================================
# Sliding Window Rate Limiter — Unit Tests
# ============================================================================

class TestSlidingWindowLimiter:
    def test_allows_within_limit(self):
        from backend.services.rate_limit import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter()

        for i in range(10):
            allowed, _ = limiter.is_allowed("key1", max_requests=10, window_seconds=60)
            assert allowed is True

    def test_blocks_over_limit(self):
        from backend.services.rate_limit import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter()

        for _ in range(5):
            limiter.is_allowed("key1", max_requests=5, window_seconds=60)

        allowed, headers = limiter.is_allowed("key1", max_requests=5, window_seconds=60)
        assert allowed is False
        assert "Retry-After" in headers

    def test_keys_isolated(self):
        from backend.services.rate_limit import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter()

        for _ in range(5):
            limiter.is_allowed("key1", max_requests=5, window_seconds=60)

        allowed, _ = limiter.is_allowed("key2", max_requests=5, window_seconds=60)
        assert allowed is True

    def test_headers_include_limit_info(self):
        from backend.services.rate_limit import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter()

        _, headers = limiter.is_allowed("key1", max_requests=10, window_seconds=60)
        assert headers["X-RateLimit-Limit"] == "10"
        assert "X-RateLimit-Remaining" in headers
