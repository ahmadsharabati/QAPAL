"""
User router — profile and quota endpoints.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User
from backend.schemas import UserProfile, QuotaResponse
from backend.services.auth import get_current_user
from backend.services.quota import get_remaining, get_quota_info

router = APIRouter(prefix="/v1/user", tags=["user"])


@router.get("/profile", response_model=UserProfile)
def get_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the authenticated user's profile and remaining quota."""
    remaining = get_remaining(db, user)
    return UserProfile(
        id=user.id,
        email=user.email,
        tier=user.tier,
        quota_remaining=remaining,
    )


@router.get("/quota", response_model=QuotaResponse)
def get_quota(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return detailed quota info: used, limit, reset date."""
    info = get_quota_info(db, user)
    return QuotaResponse(**info)
