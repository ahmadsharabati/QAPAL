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

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging
    app.add_middleware(RequestLoggingMiddleware)

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
