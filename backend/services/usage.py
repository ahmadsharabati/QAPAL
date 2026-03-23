from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Job, User
from backend.config import settings

# Quota limits per tier (monthly)
TIER_LIMITS = {
    "free": settings.FREE_TIER_LIMIT,         # 5
    "starter": settings.STARTER_TIER_LIMIT,   # 50
    "pro": settings.PRO_TIER_LIMIT,           # 999999 (unlimited)
}

# Ensure -1 is handled as unlimited
if TIER_LIMITS["pro"] == -1:
    TIER_LIMITS["pro"] = 999_999_999

def check_and_consume_quota(db: Session, user: User) -> bool:
    """
    Verify if the user has enough quota for one more Deep Scan.
    Returns True if allowed, False if quota exceeded.
    
    This counts jobs created since the 1st of the current UTC month.
    """
    # 1. Get current month boundaries
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # 2. Count jobs created this month
    usage_count = db.query(Job).filter(
        Job.user_id == user.id,
        Job.created_at >= start_of_month
    ).count()
    
    limit = TIER_LIMITS.get(user.tier, 0)
    
    if usage_count >= limit:
        return False
        
    return True

def get_monthly_usage(db: Session, user_id: str) -> int:
    """Utility to return current month's usage count."""
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(Job).filter(
        Job.user_id == user_id,
        Job.created_at >= start_of_month
    ).count()
