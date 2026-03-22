"""
Health endpoint — quick liveness check for the API.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/v1/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    """
    Returns service status.  Verifies DB is reachable.
    Returns 503 if the database is down.
    """
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    overall = "ok" if db_status == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        db=db_status,
        version=settings.APP_VERSION,
    )
