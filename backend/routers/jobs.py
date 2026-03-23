"""
Job router — create, poll, list, and delete scan jobs.

Every endpoint enforces ownership: a user can only access their own jobs.
Quota, rate limits, and concurrent scan limits are checked before creation.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Job, User
from backend.schemas import JobCreate, JobResponse, JobListResponse, ErrorResponse
from backend.services.auth import get_current_user
from backend.services.quota import check_quota, increment_usage, get_remaining, _tier_limit
from backend.services.rate_limit import (
    check_scan_rate_limit,
    can_start_scan,
    register_active_scan,
    deregister_active_scan,
)
from backend.worker import run_deep_scan

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


# ── Helpers ──────────────────────────────────────────────────────────


def _get_owned_job(db: Session, job_id: str, user: User) -> Job:
    """Fetch a job, verify ownership, raise 404 if missing or not owned."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state == "deleted":
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Endpoints ────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=JobResponse,
    status_code=201,
    responses={
        403: {"model": ErrorResponse, "description": "Quota exceeded"},
        429: {"model": ErrorResponse, "description": "Rate limit or concurrency limit"},
    },
)
def create_job(
    body: JobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new scan job.

    Validates auth, checks quota, rate limits, and concurrent scan limits,
    persists the job as 'queued', and launches a background task.
    """
    # Scan rate limit (5 creates per minute per user)
    allowed, _headers = check_scan_rate_limit(user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many scan requests. Please wait a moment before starting another scan.",
            headers=_headers,
        )

    # Concurrent scan limit
    if not can_start_scan(user.id, user.tier):
        tier_names = {"free": "Free", "starter": "Starter", "pro": "Pro"}
        limits = {"free": 1, "starter": 2, "pro": 5}
        current_limit = limits.get(user.tier, 1)
        raise HTTPException(
            status_code=429,
            detail=(
                f"You already have {current_limit} scan(s) running. "
                f"{tier_names.get(user.tier, 'Free')} tier allows {current_limit} "
                f"concurrent scan(s). Upgrade to increase your limit."
            ),
        )

    # Quota check
    if not check_quota(db, user):
        limit = _tier_limit(user.tier)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "QUOTA_EXCEEDED",
                "message": (
                    f"Monthly scan quota exceeded ({limit} scans/month on "
                    f"{user.tier.title()} tier). Upgrade to Starter or Pro for more scans."
                )
            },
        )

    # Tier-based max_pages cap
    tier_caps = {"free": 3, "starter": 10, "pro": 25}
    options = body.options or {}
    max_pages = options.get("max_pages", tier_caps.get(user.tier, 3))
    max_pages = min(max_pages, tier_caps.get(user.tier, 3))
    options["max_pages"] = max_pages

    # Create job
    job = Job(
        user_id=user.id,
        url=body.url,
        options=options,
        state="queued",
        progress=0,
        message="Job queued",
    )
    db.add(job)
    increment_usage(db, user.id)
    db.commit()
    db.refresh(job)

    # Track concurrent scan
    register_active_scan(user.id, job.id)

    # Launch background task (deregisters on completion)
    background_tasks.add_task(_run_and_deregister, job.id, user.id)

    return job


async def _run_and_deregister(job_id: str, user_id: str):
    """Run deep scan and deregister from active scans when done."""
    try:
        await run_deep_scan(job_id)
    finally:
        deregister_active_scan(user_id, job_id)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a single job's status, progress, and report."""
    job = _get_owned_job(db, job_id, user)
    return job


@router.get("", response_model=JobListResponse)
def list_jobs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List the authenticated user's jobs (paginated, newest first)."""
    query = (
        db.query(Job)
        .filter(Job.user_id == user.id, Job.state != "deleted")
        .order_by(Job.created_at.desc())
    )
    total = query.count()
    jobs = query.offset((page - 1) * per_page).limit(per_page).all()

    return JobListResponse(
        jobs=[JobResponse.model_validate(j) for j in jobs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a job (state → 'deleted')."""
    job = _get_owned_job(db, job_id, user)
    if not job.transition("deleted"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete job in state '{job.state}'",
        )
    db.commit()
