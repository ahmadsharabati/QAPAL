"""
FastAPI application factory.

Creates the app, mounts routers, configures middleware, and handles startup.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.database import create_tables
from backend.middleware import RequestLoggingMiddleware
from backend.routers import health, jobs, user


# ── Logging ──────────────────────────────────────────────────────────────


class _QAPALFormatter(logging.Formatter):
    """Log formatter that auto-prefixes [job:<id>] when a job_id is present."""

    def format(self, record):
        job_id = getattr(record, "job_id", None)
        if job_id:
            record.msg = f"[job:{job_id[:8]}] {record.msg}"
        return super().format(record)


_handler = logging.StreamHandler()
_handler.setFormatter(_QAPALFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logging.root.addHandler(_handler)
logging.root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB tables.  Shutdown: nothing yet."""
    create_tables()
    logging.getLogger("qapal.api").info(
        "QAPAL backend started (version=%s)", settings.APP_VERSION
    )
    yield


# ── App ──────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
    )

    # Request logging (added first → innermost middleware)
    app.add_middleware(RequestLoggingMiddleware)

    # CORS (added second → outermost, so it handles OPTIONS before anything else)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # allow all origins (extensions have dynamic IDs)
        allow_credentials=False,  # must be False when allow_origins=["*"]
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(user.router)

    # Global error handler — never expose stack traces
    @app.exception_handler(Exception)
    async def _global_error_handler(request: Request, exc: Exception):
        logging.getLogger("qapal.api").exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


# Module-level app instance for `uvicorn backend.app:app`
app = create_app()
