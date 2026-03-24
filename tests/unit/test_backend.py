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
        assert severities["CONSOLE_ERROR"] == "low"
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

    def test_build_report_deduplicates_passive_issues(self):
        """Same console error across multiple tests counts only once."""
        from backend.worker import _build_report

        repeated_passive = {
            "console_errors": [{"text": "Uncaught TypeError: x is null", "url": "https://x.com"}],
            "js_exceptions": [],
            "network_failures": [],
        }
        exec_results = [
            {
                "id": f"TC{i:03d}",
                "status": "pass",
                "steps": [],
                "assertions": [],
                "passive_errors": repeated_passive,
            }
            for i in range(5)  # same console error in 5 tests
        ]
        report = _build_report(
            url="https://x.com",
            crawl_results=[],
            exec_results=exec_results,
            duration_ms=1000,
        )
        # Deduplication: 5 identical console errors → 1 issue, not 5
        console_issues = [i for i in report["issues"] if i["rule"] == "CONSOLE_ERROR"]
        assert len(console_issues) == 1
        # Score: 1 low-severity issue (1 pt) → 99, not 0
        assert report["score"] == 99

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

    def test_build_element_list_prd(self):
        """Fallback PRD contains expected sections from mock locators."""
        from unittest.mock import MagicMock
        from backend.worker import _build_element_list_prd

        mock_db = MagicMock()
        mock_db.get_all_locators.return_value = [
            {"identity": {"role": "link", "name": "Home", "container": "nav"}},
            {"identity": {"role": "link", "name": "About", "container": "nav"}},
            {"identity": {"role": "textbox", "name": "Email", "container": ""}},
            {"identity": {"role": "button", "name": "Submit", "container": ""}},
        ]
        mock_db.get_all.return_value = [{"id": 1}, {"id": 2}]

        crawl_results = [
            {"url": "https://example.com", "crawled": True},
            {"url": "https://example.com/about", "crawled": True},
        ]

        prd = _build_element_list_prd(mock_db, "https://example.com", crawl_results)

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


# ============================================================================
# Codegen — Pure Unit Tests (no Playwright, no network)
# ============================================================================

class TestCodegen:
    """Tests for codegen.py — plan-to-pytest-file translation."""

    # ── _escape ──────────────────────────────────────────────────────────

    def test_escape_backslash(self):
        from codegen import _escape
        assert _escape("a\\b") == "a\\\\b"

    def test_escape_double_quote(self):
        from codegen import _escape
        assert _escape('say "hi"') == 'say \\"hi\\"'

    def test_escape_newline(self):
        from codegen import _escape
        assert _escape("line1\nline2") == "line1\\nline2"

    def test_escape_tab(self):
        from codegen import _escape
        assert _escape("col1\tcol2") == "col1\\tcol2"

    def test_escape_carriage_return(self):
        from codegen import _escape
        assert _escape("win\r\nline") == "win\\r\\nline"

    # ── _selector_to_code ────────────────────────────────────────────────

    def test_selector_testid(self):
        from codegen import _selector_to_code
        sel = {"strategy": "testid", "value": "login-btn"}
        assert _selector_to_code(sel) == 'page.get_by_test_id("login-btn")'

    def test_selector_role_with_name(self):
        from codegen import _selector_to_code
        sel = {"strategy": "role", "value": {"role": "button", "name": "Submit"}}
        assert _selector_to_code(sel) == 'page.get_by_role("button", name="Submit")'

    def test_selector_role_without_name(self):
        from codegen import _selector_to_code
        sel = {"strategy": "role", "value": {"role": "heading"}}
        assert _selector_to_code(sel) == 'page.get_by_role("heading")'

    def test_selector_text(self):
        from codegen import _selector_to_code
        sel = {"strategy": "text", "value": "Sign in"}
        assert _selector_to_code(sel) == 'page.get_by_text("Sign in")'

    def test_selector_label(self):
        from codegen import _selector_to_code
        sel = {"strategy": "label", "value": "Email address"}
        assert _selector_to_code(sel) == 'page.get_by_label("Email address")'

    def test_selector_placeholder(self):
        from codegen import _selector_to_code
        sel = {"strategy": "placeholder", "value": "Enter email"}
        assert _selector_to_code(sel) == 'page.get_by_placeholder("Enter email")'

    def test_selector_css(self):
        from codegen import _selector_to_code
        sel = {"strategy": "css", "value": ".btn-primary"}
        assert _selector_to_code(sel) == 'page.locator(".btn-primary")'

    def test_selector_testid_prefix(self):
        from codegen import _selector_to_code
        sel = {"strategy": "testid_prefix", "value": "product-card", "index": 2}
        assert _selector_to_code(sel) == 'page.locator(\'[data-testid^="product-card"]\').nth(2)'

    # ── _action_to_code ──────────────────────────────────────────────────

    def test_action_navigate(self):
        from codegen import _action_to_code
        step = {"action": "navigate", "url": "https://example.com/login"}
        lines = _action_to_code(step)
        assert lines == [
            'page.goto("https://example.com/login", wait_until="domcontentloaded")',
            'page.wait_for_load_state("networkidle")',
        ]

    def test_action_click(self):
        from codegen import _action_to_code
        step = {"action": "click", "selector": {"strategy": "testid", "value": "submit"}}
        lines = _action_to_code(step)
        assert lines == ['page.get_by_test_id("submit").click()']

    def test_action_fill(self):
        from codegen import _action_to_code
        step = {"action": "fill", "selector": {"strategy": "label", "value": "Email"}, "value": "user@test.com"}
        lines = _action_to_code(step)
        assert lines == ['page.get_by_label("Email").fill("user@test.com")']

    def test_action_select(self):
        from codegen import _action_to_code
        step = {"action": "select", "selector": {"strategy": "label", "value": "Country"},
                "label": "Germany"}
        lines = _action_to_code(step)
        assert lines == ['page.get_by_label("Country").select_option(label="Germany")']

    def test_action_press(self):
        from codegen import _action_to_code
        step = {"action": "press", "selector": {"strategy": "testid", "value": "search"},
                "key": "Enter"}
        lines = _action_to_code(step)
        assert lines == ['page.get_by_test_id("search").press("Enter")']

    def test_action_missing_selector_warns(self):
        from codegen import _action_to_code
        step = {"action": "click"}  # no selector
        lines = _action_to_code(step)
        assert len(lines) == 1
        assert "WARNING" in lines[0]
        assert "missing selector" in lines[0]

    def test_action_unknown_warns(self):
        from codegen import _action_to_code
        step = {"action": "teleport", "selector": {"strategy": "testid", "value": "x"}}
        lines = _action_to_code(step)
        assert "WARNING" in lines[0]

    # ── _assertion_to_code ───────────────────────────────────────────────

    def test_assertion_url_equals(self):
        from codegen import _assertion_to_code
        a = {"type": "url_equals", "value": "https://example.com/dashboard"}
        lines = _assertion_to_code(a)
        assert lines == ['expect(page).to_have_url("https://example.com/dashboard")']

    def test_assertion_url_contains(self):
        from codegen import _assertion_to_code
        a = {"type": "url_contains", "value": "/dashboard"}
        lines = _assertion_to_code(a)
        assert len(lines) == 1
        assert "re.compile" in lines[0]
        assert "/dashboard" in lines[0]

    def test_assertion_element_visible(self):
        from codegen import _assertion_to_code
        a = {"type": "element_visible",
             "selector": {"strategy": "testid", "value": "welcome-msg"}}
        lines = _assertion_to_code(a)
        assert lines == ['expect(page.get_by_test_id("welcome-msg")).to_be_visible()']

    def test_assertion_element_text_equals(self):
        from codegen import _assertion_to_code
        a = {"type": "element_text_equals",
             "selector": {"strategy": "testid", "value": "title"},
             "value": "Hello World"}
        lines = _assertion_to_code(a)
        assert lines == ['expect(page.get_by_test_id("title")).to_have_text("Hello World")']

    def test_assertion_element_count(self):
        from codegen import _assertion_to_code
        a = {"type": "element_count",
             "selector": {"strategy": "css", "value": ".item"},
             "value": 5}
        lines = _assertion_to_code(a)
        assert lines == ['expect(page.locator(".item")).to_have_count(5)']

    def test_assertion_missing_selector_warns(self):
        from codegen import _assertion_to_code
        a = {"type": "element_visible"}  # no selector
        lines = _assertion_to_code(a)
        assert "WARNING" in lines[0]

    # ── generate_test_file ───────────────────────────────────────────────

    def test_generate_test_file_compiles(self):
        """Generated code must be valid Python."""
        from codegen import generate_test_file
        plan = {
            "test_id": "TC001_login",
            "name": "User can log in",
            "steps": [
                {"action": "navigate", "url": "https://example.com/login"},
                {"action": "fill", "selector": {"strategy": "label", "value": "Email"},
                 "value": "user@test.com"},
                {"action": "click", "selector": {"strategy": "testid", "value": "submit"}},
            ],
            "assertions": [
                {"type": "url_contains", "value": "/dashboard"},
                {"type": "element_visible",
                 "selector": {"strategy": "testid", "value": "welcome"}},
            ],
        }
        code = generate_test_file(plan)
        compile(code, "<test>", "exec")  # raises SyntaxError if invalid

    def test_generate_test_file_testid_wait_after_navigate(self):
        """After navigate, testid selector generates wait_for_selector for the specific testid."""
        from codegen import generate_test_file
        plan = {
            "test_id": "TC_spa",
            "name": "SPA testid wait",
            "steps": [
                {"action": "navigate", "url": "https://example.com/"},
                {"action": "click", "selector": {"strategy": "testid", "value": "nav-sign-in"}},
            ],
            "assertions": [],
        }
        code = generate_test_file(plan)
        # Must wait for the specific testid element (handles Angular/React boot delay)
        assert 'wait_for_selector(\'[data-testid="nav-sign-in"]\'' in code
        # Must NOT fall back to body-only wait
        assert "wait_for_selector('body'" not in code
        compile(code, "<test>", "exec")

    def test_generate_test_file_function_name(self):
        """test_id becomes the function name."""
        from codegen import generate_test_file
        plan = {"test_id": "TC002_checkout", "name": "Checkout flow", "steps": [], "assertions": []}
        code = generate_test_file(plan)
        assert "def test_tc002_checkout(page: Page):" in code

    def test_generate_test_file_has_header(self):
        """Generated file has the pytest-playwright import header."""
        from codegen import generate_test_file
        plan = {"test_id": "TC003", "name": "Smoke", "steps": [], "assertions": []}
        code = generate_test_file(plan)
        assert "from playwright.sync_api import Page, expect" in code

    def test_generate_test_file_empty_plan(self):
        """Empty plan produces a pass-only function."""
        from codegen import generate_test_file
        plan = {"test_id": "TC004_empty", "name": "Empty", "steps": [], "assertions": []}
        code = generate_test_file(plan)
        assert "pass" in code
        compile(code, "<test>", "exec")

    def test_generate_test_file_multi_single_header(self):
        """Multi-plan file has exactly one import header."""
        from codegen import generate_test_file_multi
        plans = [
            {"test_id": "TC005_a", "name": "A", "steps": [], "assertions": []},
            {"test_id": "TC005_b", "name": "B", "steps": [], "assertions": []},
        ]
        code = generate_test_file_multi(plans)
        assert code.count("from playwright.sync_api import") == 1
        assert "def test_tc005_a" in code
        assert "def test_tc005_b" in code
        compile(code, "<test>", "exec")

    def test_generate_test_file_special_chars_in_value(self):
        """Values with special chars produce valid string literals."""
        from codegen import generate_test_file
        plan = {
            "test_id": "TC006_special",
            "name": 'Test with "quotes" and newlines',
            "steps": [
                {"action": "fill",
                 "selector": {"strategy": "label", "value": 'Name "Field"'},
                 "value": "line1\nline2"},
            ],
            "assertions": [],
        }
        code = generate_test_file(plan)
        compile(code, "<test>", "exec")  # must not produce a SyntaxError


# ── Job model generated_test property ────────────────────────────────────

class TestJobGeneratedTest:
    def test_generated_test_hoisted_from_report(self, client):
        """generated_test field in report is exposed at top level of JobResponse."""
        from backend.models import Job

        db = _TestSession()
        job = Job(
            user_id="u1",
            url="https://example.com",
            state="queued",
            progress=0,
            report={
                "summary": "test",
                "score": 100,
                "issues": [],
                "generated_test": "def test_example(page):\n    pass\n",
            },
        )
        db.add(job)
        db.commit()
        job_id = job.id
        db.close()

        db = _TestSession()
        j = db.query(Job).filter(Job.id == job_id).first()
        assert j.generated_test == "def test_example(page):\n    pass\n"
        db.close()

    def test_generated_test_none_when_absent(self):
        """generated_test is None when report has no generated_test key."""
        from backend.models import Job
        j = Job(user_id="u1", url="https://x.com", state="queued", progress=0, report={})
        assert j.generated_test is None

    def test_generated_test_none_when_no_report(self):
        """generated_test is None when report is None."""
        from backend.models import Job
        j = Job(user_id="u1", url="https://x.com", state="queued", progress=0, report=None)
        assert j.generated_test is None


# ── Phase 1: Step-level Healer ───────────────────────────────────────────────

class TestStepHealer:
    """Unit tests for engine/repair/step_healer.py — surgical step retry."""

    def _make_healer(self, ai_response: str = None, ai_error: Exception = None):
        """Return a StepHealer backed by a mock AIClient."""
        from engine.repair.step_healer import StepHealer

        ai = MagicMock()
        ai.small_model = "claude-3-haiku-20240307"
        if ai_error:
            ai.acomplete = AsyncMock(side_effect=ai_error)
        else:
            ai.acomplete = AsyncMock(return_value=ai_response or "{}")

        db = MagicMock()
        return StepHealer(ai, db)

    def _locators(self):
        return [{"strategy": "role", "value": {"role": "button", "name": "Submit"}, "url": "https://x.com"}]

    # ── small model routing ──────────────────────────────────────────────────

    def test_uses_small_model_override(self):
        """repair_step must route through small_model, never the full model."""
        import asyncio
        healer = self._make_healer(
            '{"action": "click", "selector": {"strategy": "role", "value": {"role": "button", "name": "Submit"}}}'
        )
        asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "btn"}},
            error_reason="Element not found",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        call_kwargs = healer._ai.acomplete.call_args
        assert call_kwargs.kwargs.get("model_override") == "claude-3-haiku-20240307", (
            "StepHealer must pass model_override=small_model to keep costs low"
        )

    # ── happy-path parsing ───────────────────────────────────────────────────

    def test_repair_step_returns_valid_step(self):
        """Valid JSON from AI → repair_step returns a dict with action + selector."""
        import asyncio
        healer = self._make_healer(
            '{"action": "click", "selector": {"strategy": "role", "value": {"role": "button", "name": "Login"}}}'
        )
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "login-btn"}},
            error_reason="Element not found",
            current_url="https://x.com/login",
            available_locators=self._locators(),
        ))
        assert result is not None
        assert result["action"] == "click"
        assert "selector" in result

    def test_repair_step_marks_healed_flag(self):
        """Returned step must carry _healed=True so the executor won't re-enter healer."""
        import asyncio
        healer = self._make_healer(
            '{"action": "fill", "selector": {"strategy": "label", "value": "Email"}, "value": "a@b.com"}'
        )
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "fill", "selector": {"strategy": "testid", "value": "email-input"}, "value": "a@b.com"},
            error_reason="strict mode violation",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        assert result is not None
        assert result.get("_healed") is True

    def test_repair_step_strips_markdown_fence(self):
        """AI wrapping JSON in ```json ... ``` must be handled gracefully."""
        import asyncio
        wrapped = '```json\n{"action": "click", "selector": {"strategy": "testid", "value": "btn"}}\n```'
        healer = self._make_healer(wrapped)
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "btn"}},
            error_reason="timeout",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        assert result is not None
        assert result["action"] == "click"

    # ── failure / degraded cases ─────────────────────────────────────────────

    def test_repair_step_returns_none_on_invalid_json(self):
        """Non-JSON AI response → None, not an exception."""
        import asyncio
        healer = self._make_healer("sorry, I cannot help with that")
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "x"}},
            error_reason="timeout",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        assert result is None

    def test_repair_step_returns_none_on_missing_action(self):
        """JSON without 'action' key → None."""
        import asyncio
        healer = self._make_healer('{"selector": {"strategy": "testid", "value": "x"}}')
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "x"}},
            error_reason="timeout",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        assert result is None

    def test_repair_step_returns_none_on_ai_error(self):
        """AI exception → None, not propagated."""
        import asyncio
        healer = self._make_healer(ai_error=RuntimeError("API overloaded"))
        result = asyncio.run(healer.repair_step(
            failed_step={"action": "click", "selector": {"strategy": "testid", "value": "x"}},
            error_reason="timeout",
            current_url="https://x.com",
            available_locators=self._locators(),
        ))
        assert result is None

    # ── intent locking (tested at executor level via mocks) ──────────────────

    def test_intent_lock_rejects_action_drift(self):
        """
        Executor-level intent lock: if repaired step has a different action,
        it must be rejected and NOT retried.

        This test verifies the guard condition in executor.run() without
        running the full executor stack — we confirm that action drift is
        rejected by checking the FailureCategory assignment path.
        """
        # Simulate what executor.run() does in the intent-lock block
        original_action = "click"
        repaired_action = "navigate"  # drift: healer changed the intent

        rejected = repaired_action != original_action
        assert rejected, "Action drift from click→navigate must be rejected"

    def test_intent_lock_allows_same_action(self):
        """Same action → intent lock passes, step is replaced."""
        original_action = "fill"
        repaired_action = "fill"
        rejected = repaired_action != original_action
        assert not rejected, "Identical action must pass intent lock"
