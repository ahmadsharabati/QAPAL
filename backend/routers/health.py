"""
Health endpoint — readiness check for the API and its dependencies.

Checks: database, AI provider config, Playwright installation, disk space.
"""

import os
import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.schemas import HealthResponse

router = APIRouter(tags=["health"])


# ── Dependency checks ─────────────────────────────────────────────────────


def _check_ai() -> str:
    """Verify AI provider env var and corresponding API key exist (no API call)."""
    provider = os.getenv("QAPAL_AI_PROVIDER", "").strip()
    if not provider:
        return "missing_provider"
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "grok": "XAI_API_KEY",
    }
    key_var = key_map.get(provider)
    if key_var and not os.getenv(key_var, "").strip():
        return "missing_key"
    return "ok"


def _check_playwright() -> str:
    """Check that Playwright is importable (lightweight, no browser launch)."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return "ok"
    except ImportError:
        return "not_installed"


def _check_browser_pool() -> str:
    """Report the browser pool state: ok / degraded / stopped."""
    try:
        from backend.services.browser_pool import browser_pool
        if not browser_pool._started:
            return "stopped"
        if not browser_pool.is_healthy:
            return "degraded"
        return f"ok ({browser_pool.active}/{browser_pool._size} active)"
    except Exception:
        return "unknown"


def _check_disk() -> str:
    """Check available disk space in /tmp (where traces and temp DBs live)."""
    try:
        usage = shutil.disk_usage("/tmp")
        if usage.free < 500 * 1024 * 1024:  # < 500 MB
            return "low"
        return "ok"
    except Exception:
        return "ok"  # fail open


# ── Endpoint ──────────────────────────────────────────────────────────────


@router.get("/v1/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    """
    Returns service status.  Checks DB, AI config, Playwright, and disk.
    Returns "unhealthy" if the database is down, "degraded" for other issues.
    """
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    ai_status = _check_ai()
    pw_status = _check_playwright()
    disk_status = _check_disk()
    pool_status = _check_browser_pool()

    critical_statuses = [db_status]
    all_statuses = [db_status, ai_status, pw_status, disk_status, pool_status]

    if db_status == "error":
        overall = "unhealthy"
    elif all(s == "ok" or s.startswith("ok (") for s in all_statuses):
        overall = "ok"
    else:
        overall = "degraded"

    return HealthResponse(
        status=overall,
        db=db_status,
        ai=ai_status,
        playwright=pw_status,
        disk=disk_status,
        version=settings.APP_VERSION,
    )
