"""
E2E test configuration and shared fixtures.

Test markers:
  - (no marker)    : fast tests, no network, no Playwright — run in CI always
  - @pytest.mark.network : requires outbound internet access
  - @pytest.mark.live    : runs Quick Scan against a real public URL
  - @pytest.mark.slow    : >30s, excluded from default pytest run

Run targets:
  pytest tests/e2e/                          # all e2e except live/slow
  pytest tests/e2e/ -m live                 # live-site gauntlet
  pytest tests/e2e/ -m "not slow"           # all except slow
  pytest tests/e2e/ -m network              # all network-requiring tests
"""

import asyncio
import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── Pytest markers ─────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "network: test requires outbound internet")
    config.addinivalue_line("markers", "live: test runs Quick Scan against a real URL")
    config.addinivalue_line("markers", "slow: test takes >30 seconds")


# ── Test database ──────────────────────────────────────────────────────────

import backend.models  # noqa: ensure models are registered with Base

from backend.database import Base, get_db

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
def _fresh_db():
    """Recreate all tables before each test, drop after."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset in-memory rate limit state between tests."""
    from backend.services.rate_limit import _global_limiter, _scan_limiter, _active_scans
    _global_limiter._requests.clear()
    _scan_limiter._requests.clear()
    _active_scans.clear()
    yield


# ── API client ─────────────────────────────────────────────────────────────

AUTH = {"Authorization": "Bearer dev-testuser"}
AUTH2 = {"Authorization": "Bearer dev-user2"}


def _stub_worker(job_id: str, user_id: str = ""):
    """Synchronous deep-scan stub: marks job complete with a canned report."""
    from datetime import datetime, timezone
    from backend.models import Job
    from backend.services.rate_limit import deregister_active_scan

    try:
        db = _TestSession()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return
            job.transition("running")
            job.progress = 100
            job.message = "Scan complete (stub)"
            job.report = {
                "summary": f"Stub scan for {job.url}",
                "score": 85,
                "issues": [
                    {
                        "ruleId": "a11y/img-alt",
                        "severity": "major",
                        "category": "accessibility",
                        "title": "Image missing alt text",
                        "message": "Image has no alt attribute.",
                        "selector": "img",
                    }
                ],
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "pages_crawled": 1,
                "actions_taken": 0,
                "duration_ms": 100,
                "engine_version": "test-stub",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "narration": "Stub scan completed with one accessibility issue.",
            }
            job.transition("complete")
            db.commit()
        finally:
            db.close()
    finally:
        deregister_active_scan(user_id, job_id)


@pytest.fixture()
def api_client():
    """TestClient with in-memory DB and stubbed worker. Fast, no network."""
    from unittest.mock import patch
    from backend.app import create_app

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    with patch("backend.routers.jobs._run_and_deregister", _stub_worker):
        yield TestClient(app, raise_server_exceptions=False)


# ── Async helper ───────────────────────────────────────────────────────────

@pytest.fixture()
def event_loop():
    """Provide a fresh event loop per test (for async tests)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
