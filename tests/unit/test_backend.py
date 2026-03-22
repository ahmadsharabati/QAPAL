"""
Backend API tests — uses FastAPI TestClient (no server needed).

Tests cover: health, auth, quota, job lifecycle, SSRF, ownership, state machine.
"""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.config import settings

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


@pytest.fixture()
def client():
    """FastAPI TestClient with in-memory DB and stubbed worker."""
    from backend.app import create_app

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    # Patch the worker's SessionLocal to use our test DB
    with patch("backend.worker.SessionLocal", _TestSession):
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
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert data["version"] == settings.APP_VERSION

    def test_health_no_auth_required(self, client):
        """Health endpoint should work without auth."""
        r = client.get("/v1/health")
        assert r.status_code == 200


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
        assert "quota" in r.json()["detail"].lower()


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
            "report", "error", "created_at", "started_at", "completed_at",
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
# Worker Stub
# ============================================================================

class TestWorkerStub:
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


# ============================================================================
# Startup / Table Creation
# ============================================================================

class TestStartup:
    def test_tables_exist_after_boot(self, client):
        """Health check works, which means tables were created on startup."""
        r = client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["db"] == "ok"
