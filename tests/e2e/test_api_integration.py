"""
tests/e2e/test_api_integration.py

Full API contract tests using the FastAPI TestClient and in-memory DB.
No network required. Tests the complete request/response lifecycle for
every endpoint that matters for the GitHub Action and Chrome Extension.
"""

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

AUTH = {"Authorization": "Bearer dev-testuser"}
AUTH2 = {"Authorization": "Bearer dev-user2"}


# ============================================================================
# Health
# ============================================================================

class TestHealth:
    def test_health_returns_ok(self, api_client):
        r = api_client.get("/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("ok", "degraded")  # degraded if AI key absent
        assert data["db"] == "ok"

    def test_health_no_auth_required(self, api_client):
        r = api_client.get("/v1/health")
        assert r.status_code == 200

    def test_health_schema_complete(self, api_client):
        """Every field the extension and GitHub Action depend on is present."""
        data = api_client.get("/v1/health").json()
        for field in ("status", "db", "ai", "playwright", "disk", "version"):
            assert field in data, f"Missing field in health response: {field}"


# ============================================================================
# Auth
# ============================================================================

class TestAuth:
    def test_unauthenticated_request_returns_401(self, api_client):
        r = api_client.get("/v1/user/profile")
        assert r.status_code in (401, 403)

    def test_dev_token_bootstraps_user(self, api_client):
        """Dev stub auth creates a user on first call, returns it on second."""
        r = api_client.get("/v1/user/profile", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "testuser@dev.local"
        assert data["tier"] == "free"

    def test_second_call_returns_same_user(self, api_client):
        api_client.get("/v1/user/profile", headers=AUTH)
        r2 = api_client.get("/v1/user/profile", headers=AUTH)
        assert r2.status_code == 200

    def test_different_tokens_give_different_users(self, api_client):
        r1 = api_client.get("/v1/user/profile", headers=AUTH)
        r2 = api_client.get("/v1/user/profile", headers=AUTH2)
        assert r1.json()["email"] != r2.json()["email"]


# ============================================================================
# Job lifecycle — the critical path for GitHub Action
# ============================================================================

class TestJobLifecycle:
    def test_create_job_returns_201_with_id(self, api_client):
        r = api_client.post("/v1/jobs", json={"url": "https://example.com"}, headers=AUTH)
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert data["url"] == "https://example.com"
        assert data["state"] in ("queued", "running", "complete")

    def test_poll_job_returns_progress(self, api_client):
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        r = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert "state" in data
        assert "progress" in data
        assert 0 <= data["progress"] <= 100

    def test_completed_job_has_report(self, api_client):
        """Stub worker completes immediately — report is embedded in the job response."""
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        # Report is embedded in GET /v1/jobs/{id} (no separate /report endpoint)
        r = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 200
        job = r.json()
        assert "report" in job
        report = job["report"]
        if report is not None:  # null while running, populated on complete
            assert "score" in report
            assert "issues" in report
            assert isinstance(report["issues"], list)

    def test_report_issue_schema(self, api_client):
        """Each issue object must have the fields the GitHub Action reads."""
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        job = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        issues = (job.get("report") or {}).get("issues", [])
        for issue in issues:
            assert "ruleId" in issue
            assert "severity" in issue
            assert "title" in issue
            assert issue["severity"] in ("critical", "major", "medium", "minor")

    def test_list_jobs_returns_only_own_jobs(self, api_client):
        api_client.post("/v1/jobs", json={"url": "https://a.com"}, headers=AUTH)
        api_client.post("/v1/jobs", json={"url": "https://b.com"}, headers=AUTH2)

        r1 = api_client.get("/v1/jobs", headers=AUTH)
        r2 = api_client.get("/v1/jobs", headers=AUTH2)

        ids1 = {j["id"] for j in r1.json()["jobs"]}
        ids2 = {j["id"] for j in r2.json()["jobs"]}
        assert ids1.isdisjoint(ids2), "Users can see each other's jobs"

    def test_other_user_cannot_access_job(self, api_client):
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        r = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH2)
        assert r.status_code in (403, 404)

    def test_unknown_job_returns_404(self, api_client):
        r = api_client.get("/v1/jobs/nonexistent-id", headers=AUTH)
        assert r.status_code == 404


# ============================================================================
# Quota enforcement — the premium boundary
# ============================================================================

class TestQuotaEnforcement:
    def test_quota_starts_at_zero_used(self, api_client):
        r = api_client.get("/v1/user/quota", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["used"] == 0

    def test_quota_decrements_after_job(self, api_client):
        api_client.post("/v1/jobs", json={"url": "https://example.com"}, headers=AUTH)
        r = api_client.get("/v1/user/quota", headers=AUTH)
        assert r.json()["used"] == 1

    def test_quota_exceeded_returns_403(self, api_client):
        from backend.config import settings
        for i in range(settings.FREE_TIER_LIMIT):
            r = api_client.post(
                "/v1/jobs", json={"url": f"https://example.com/{i}"}, headers=AUTH
            )
            assert r.status_code == 201, f"Job {i} should succeed, got {r.status_code}"

        r = api_client.post(
            "/v1/jobs", json={"url": "https://example.com/over"}, headers=AUTH
        )
        assert r.status_code == 403

    def test_quota_error_is_structured(self, api_client):
        """The GitHub Action and Extension both parse structured error bodies."""
        from backend.config import settings
        for i in range(settings.FREE_TIER_LIMIT):
            api_client.post("/v1/jobs", json={"url": f"https://example.com/{i}"}, headers=AUTH)

        r = api_client.post("/v1/jobs", json={"url": "https://example.com/x"}, headers=AUTH)
        detail = r.json()["detail"]
        assert isinstance(detail, dict), "detail should be a structured object"
        assert detail["error"] == "QUOTA_EXCEEDED"
        assert "message" in detail
        assert "upgrade" in detail["message"].lower()

    def test_quota_not_shared_between_users(self, api_client):
        """User A exhausting quota does not affect User B."""
        from backend.config import settings
        for i in range(settings.FREE_TIER_LIMIT):
            api_client.post("/v1/jobs", json={"url": f"https://example.com/{i}"}, headers=AUTH)

        r = api_client.post("/v1/jobs", json={"url": "https://example.com"}, headers=AUTH2)
        assert r.status_code == 201


# ============================================================================
# Rate limiting
# ============================================================================

class TestRateLimiting:
    def test_rate_limit_allows_up_to_window(self, api_client):
        """First N requests should all succeed."""
        for i in range(5):
            r = api_client.post(
                "/v1/jobs", json={"url": f"https://example.com/{i}"}, headers=AUTH
            )
            assert r.status_code in (201, 403), f"Unexpected {r.status_code} on request {i}"

    def test_rate_limit_headers_present(self, api_client):
        """X-RateLimit-* headers must be on every non-health response."""
        r = api_client.post("/v1/jobs", json={"url": "https://example.com"}, headers=AUTH)
        # Rate limit headers are added by middleware
        for header in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-window"):
            assert header in r.headers, f"Missing header: {header}"


# ============================================================================
# Security headers — required for Chrome Web Store submission
# ============================================================================

class TestSecurityHeaders:
    def test_x_frame_options(self, api_client):
        r = api_client.get("/v1/health")
        assert r.headers.get("x-frame-options", "").upper() == "DENY"

    def test_x_content_type_options(self, api_client):
        r = api_client.get("/v1/health")
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_referrer_policy(self, api_client):
        r = api_client.get("/v1/health")
        assert "referrer-policy" in r.headers

    def test_security_headers_on_error_responses(self, api_client):
        """Security headers must be present even on 404 responses."""
        r = api_client.get("/v1/jobs/nonexistent", headers=AUTH)
        assert r.headers.get("x-frame-options") == "DENY"


# ============================================================================
# SSRF protection
# ============================================================================

class TestSSRF:
    @pytest.mark.parametrize("url", [
        "http://localhost/admin",
        "http://127.0.0.1/etc/passwd",
        "http://0.0.0.0:8080/",
        "http://169.254.169.254/latest/meta-data/",   # AWS metadata
        "http://192.168.1.1/",                         # private network
    ])
    def test_ssrf_blocked(self, api_client, url):
        """Internal/private URLs must be rejected before a job is created."""
        r = api_client.post("/v1/jobs", json={"url": url}, headers=AUTH)
        assert r.status_code in (400, 422), (
            f"SSRF URL {url!r} was accepted — should be blocked"
        )


# ============================================================================
# Narration
# ============================================================================

class TestNarration:
    def test_completed_job_has_narration_field(self, api_client):
        """Report must include a narration field (even if null)."""
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        job = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        report = job.get("report") or {}
        assert "narration" in report

    def test_narration_is_string_or_null(self, api_client):
        job_id = api_client.post(
            "/v1/jobs", json={"url": "https://example.com"}, headers=AUTH
        ).json()["id"]

        job = api_client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        report = job.get("report") or {}
        assert report.get("narration") is None or isinstance(report["narration"], str)
