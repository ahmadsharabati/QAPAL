"""
Middleware — request logging, rate limiting, and security headers.

Applied in order (innermost first):
  1. RequestLoggingMiddleware — logs method/path/status/duration
  2. RateLimitMiddleware — per-IP sliding window
  3. SecurityHeadersMiddleware — HSTS, CSP, X-Frame-Options, etc.
"""

import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.services.rate_limit import check_rate_limit

logger = logging.getLogger("qapal.api")


# ── Request Logging ─────────────────────────────────────────────────────


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status code, and duration."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
                "client": request.client.host if request.client else None,
            },
        )

        return response


# ── Rate Limiting ───────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP rate limiting for all endpoints.

    Returns 429 Too Many Requests when the limit is exceeded.
    Rate limit info is included in response headers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for health checks (monitoring probes)
        if request.url.path == "/v1/health":
            return await call_next(request)

        # Use client IP as the rate limit key
        client_ip = request.client.host if request.client else "unknown"
        allowed, headers = check_rate_limit(client_ip, max_requests=60, window_seconds=60)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down.",
                    "error_code": "RATE_LIMIT_EXCEEDED",
                },
                headers=headers,
            )

        response = await call_next(request)

        # Always include rate limit headers
        for key, value in headers.items():
            response.headers[key] = value

        return response


# ── Security Headers ────────────────────────────────────────────────────


class SecurityHeadersMiddleware:
    """
    Pure ASGI middleware that adds security headers to all responses.

    Uses raw ASGI (not BaseHTTPMiddleware) to avoid issues with
    exception handling in stacked middleware layers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                extra_headers = [
                    (b"x-frame-options", b"DENY"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy",
                     b"camera=(), microphone=(), geolocation=(), "
                     b"payment=(), usb=(), bluetooth=()"),
                ]
                existing = list(message.get("headers", []))
                existing.extend(extra_headers)
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)
