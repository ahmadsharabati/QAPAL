"""
Pydantic schemas — request/response contracts for the API.

These define the wire format.  The extension and CLI depend on these shapes.
"""

import ipaddress
import re
from datetime import datetime, date
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, field_validator


# ── SSRF Guard ───────────────────────────────────────────────────────────

_PRIVATE_PATTERNS = [
    re.compile(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])"),
    re.compile(r"^https?://10\."),
    re.compile(r"^https?://172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^https?://192\.168\."),
    re.compile(r"^https?://169\.254\."),
]


def is_ssrf_target(url: str) -> bool:
    """Check if a URL points to a private/internal address."""
    for pattern in _PRIVATE_PATTERNS:
        if pattern.match(url):
            return True
    # Also check if the hostname resolves to a private IP (best-effort)
    try:
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        if hostname:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
    except (ValueError, TypeError):
        pass  # hostname is a real domain, not an IP literal — fine
    return False


# ── Requests ─────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    """POST /v1/jobs request body."""
    url: str
    options: Optional[Dict[str, Any]] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if is_ssrf_target(v):
            raise ValueError("URL targets a private/internal address")
        return v


# ── Responses ────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    """Normalized job response — stable shape for the extension UI."""
    id: str
    state: str
    progress: int
    message: Optional[str] = None
    url: str
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    failure_stage: Optional[str] = None
    trace_path: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated job list."""
    jobs: List[JobResponse]
    total: int
    page: int
    per_page: int


class HealthResponse(BaseModel):
    """GET /v1/health response."""
    status: str           # "ok" | "degraded" | "unhealthy"
    db: str               # "ok" | "error"
    ai: str               # "ok" | "missing_key" | "missing_provider"
    playwright: str       # "ok" | "not_installed"
    disk: str             # "ok" | "low"
    version: str


class UserProfile(BaseModel):
    """GET /v1/user/profile response."""
    id: str
    email: str
    tier: str
    quota_remaining: int

    model_config = {"from_attributes": True}


class QuotaResponse(BaseModel):
    """GET /v1/user/quota response."""
    used: int
    limit: int
    resets_at: str  # ISO date of next month start


class NarrationRequest(BaseModel):
    """Internal: data passed to the narration service."""
    url: str
    score: int
    issues_summary: str
    pages_crawled: int
    actions_taken: int


class ErrorResponse(BaseModel):
    """Generic error response."""
    detail: str
    error_code: Optional[str] = None
