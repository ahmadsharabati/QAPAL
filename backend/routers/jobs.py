"""
Job router — create, poll, list, and delete scan jobs.

Every endpoint enforces ownership: a user can only access their own jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Job, User
from backend.schemas import JobCreate, JobResponse, JobListResponse
from backend.services.auth import get_current_user
from backend.services.quota import check_quota, increment_usage
from backend.worker import run_scan_stub

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_owned_job(db: Session, job_id: str, user: User) -> Job:
    """Fetch a job, verify ownership, raise 404 if missing or not owned."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state == "deleted":
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    body: JobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new scan job.

    Validates auth, checks quota, persists the job as 'queued',
    and launches a background task to process it.
    """
    # Quota check
    if not check_quota(db, user):
        raise HTTPException(status_code=403, detail="Monthly scan quota exceeded")

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

    # Launch background task
    background_tasks.add_task(run_scan_stub, job.id)

    return job


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
