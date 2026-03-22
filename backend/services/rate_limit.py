"""
Rate limiter — sliding-window per-key rate limiting.

Uses an in-memory dict with timestamps.  Suitable for single-process
deployments (SQLite-backed).  Swap to Redis for multi-process.

Limits:
  - Per-IP for unauthenticated endpoints (health)
  - Per-user for authenticated endpoints (jobs, user)
"""

import time
import threading
from collections import defaultdict
from typing import Optional

from backend.config import settings


class _SlidingWindowLimiter:
    """Thread-safe sliding window rate limiter."""

    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, dict]:
        """
        Check if a request is allowed.

        Returns (allowed: bool, headers: dict) where headers contain
        rate-limit info for the response.
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Prune expired entries
            timestamps = self._requests[key]
            timestamps[:] = [t for t in timestamps if t > cutoff]

            remaining = max(0, max_requests - len(timestamps))
            allowed = len(timestamps) < max_requests

            if allowed:
                timestamps.append(now)
                remaining -= 1

            headers = {
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Window": str(window_seconds),
            }

            if not allowed:
                # Calculate when the oldest request in the window will expire
                retry_after = int(timestamps[0] - cutoff) + 1
                headers["Retry-After"] = str(max(1, retry_after))

            return allowed, headers

    def cleanup(self, max_age: float = 3600):
        """Remove stale keys (call periodically to prevent memory growth)."""
        now = time.monotonic()
        with self._lock:
            stale = [k for k, v in self._requests.items() if not v or v[-1] < now - max_age]
            for k in stale:
                del self._requests[k]


# Singleton instances
_global_limiter = _SlidingWindowLimiter()
_scan_limiter = _SlidingWindowLimiter()


def check_rate_limit(key: str, max_requests: int = 60, window_seconds: int = 60) -> tuple[bool, dict]:
    """
    Check general API rate limit.

    Default: 60 requests per 60 seconds per key.
    """
    return _global_limiter.is_allowed(key, max_requests, window_seconds)


def check_scan_rate_limit(user_id: str) -> tuple[bool, dict]:
    """
    Check scan creation rate limit.

    Stricter limit: 10 scan creations per 60 seconds per user.
    Prevents accidental rapid-fire from buggy clients.
    """
    return _scan_limiter.is_allowed(f"scan:{user_id}", max_requests=10, window_seconds=60)


# ── Concurrent scan tracking ────────────────────────────────────────────

_active_scans: dict[str, set[str]] = defaultdict(set)  # user_id → set of job_ids
_active_lock = threading.Lock()

# Max concurrent scans per tier
CONCURRENT_LIMITS = {
    "free": 1,
    "starter": 2,
    "pro": 5,
}


def can_start_scan(user_id: str, tier: str) -> bool:
    """Check if user can start another concurrent scan."""
    limit = CONCURRENT_LIMITS.get(tier, 1)
    with _active_lock:
        return len(_active_scans.get(user_id, set())) < limit


def register_active_scan(user_id: str, job_id: str) -> None:
    """Register a scan as active (called when job is created)."""
    with _active_lock:
        _active_scans[user_id].add(job_id)


def deregister_active_scan(user_id: str, job_id: str) -> None:
    """Deregister a scan (called when job reaches terminal state)."""
    with _active_lock:
        _active_scans[user_id].discard(job_id)
        if not _active_scans[user_id]:
            del _active_scans[user_id]


def get_active_scan_count(user_id: str) -> int:
    """Return number of active scans for a user."""
    with _active_lock:
        return len(_active_scans.get(user_id, set()))
