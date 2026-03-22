"""
Quota service — enforces monthly scan limits per tier.

Tiers:
  free    →  5 scans/month
  starter → 50 scans/month
  pro     → unlimited
"""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import User, Usage


def _current_month() -> date:
    """First day of the current month (UTC)."""
    now = datetime.now(timezone.utc)
    return date(now.year, now.month, 1)


def _next_month() -> date:
    """First day of next month (UTC)."""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return date(now.year + 1, 1, 1)
    return date(now.year, now.month + 1, 1)


def _tier_limit(tier: str) -> int:
    """Return the monthly scan limit for a tier."""
    limits = {
        "free": settings.FREE_TIER_LIMIT,
        "starter": settings.STARTER_TIER_LIMIT,
        "pro": settings.PRO_TIER_LIMIT,
    }
    return limits.get(tier, settings.FREE_TIER_LIMIT)


def _get_or_create_usage(db: Session, user_id: str) -> Usage:
    """Get or create this month's usage record."""
    month = _current_month()
    usage = db.query(Usage).filter(
        Usage.user_id == user_id,
        Usage.month == month,
    ).first()

    if usage is None:
        usage = Usage(user_id=user_id, month=month, scan_count=0)
        db.add(usage)
        db.commit()
        db.refresh(usage)

    return usage


def check_quota(db: Session, user: User) -> bool:
    """
    Return True if the user can run another scan this month.
    Pro users always pass.
    """
    limit = _tier_limit(user.tier)
    if limit < 0:  # unlimited
        return True

    usage = _get_or_create_usage(db, user.id)
    return usage.scan_count < limit


def increment_usage(db: Session, user_id: str) -> None:
    """Increment this month's scan counter."""
    usage = _get_or_create_usage(db, user_id)
    usage.scan_count += 1
    db.commit()


def get_remaining(db: Session, user: User) -> int:
    """Return how many scans the user has left this month."""
    limit = _tier_limit(user.tier)
    if limit < 0:
        return 999  # unlimited sentinel

    usage = _get_or_create_usage(db, user.id)
    return max(0, limit - usage.scan_count)


def get_quota_info(db: Session, user: User) -> dict:
    """Return full quota info for the API response."""
    limit = _tier_limit(user.tier)
    usage = _get_or_create_usage(db, user.id)
    return {
        "used": usage.scan_count,
        "limit": limit if limit >= 0 else -1,
        "resets_at": _next_month().isoformat(),
    }
