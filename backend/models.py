"""
SQLAlchemy models — Job, User, Usage.

Job state machine (allowed transitions):
  queued  → running  → complete
  queued  → running  → failed
  queued  → deleted
  running → failed
  complete → deleted
  failed  → deleted
"""

import uuid
from datetime import datetime, timezone, date
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, Date, JSON, Boolean,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# ── Allowed state transitions ────────────────────────────────────────────

ALLOWED_TRANSITIONS = {
    "queued":   {"running", "deleted"},
    "running":  {"complete", "failed"},
    "complete": {"deleted"},
    "failed":   {"deleted"},
    "deleted":  set(),
}


def valid_transition(current: str, target: str) -> bool:
    """Check if a job state transition is allowed."""
    return target in ALLOWED_TRANSITIONS.get(current, set())


# ── User ─────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    tier = Column(String, nullable=False, default="free")  # free | starter | pro
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    jobs = relationship("Job", back_populates="user", lazy="dynamic")
    usage_records = relationship("Usage", back_populates="user", lazy="dynamic")


# ── Usage (monthly scan counter) ─────────────────────────────────────────

class Usage(Base):
    __tablename__ = "usage"

    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    month = Column(Date, primary_key=True)
    scan_count = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="usage_records")


# ── Job ──────────────────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Target
    url = Column(Text, nullable=False)
    options = Column(JSON, default=dict)

    # Lifecycle
    state = Column(String, nullable=False, default="queued", index=True)
    progress = Column(Integer, nullable=False, default=0)       # 0–100
    message = Column(String, nullable=True)                     # human-readable status

    # Result
    report = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    # Diagnostics
    failure_stage = Column(String, nullable=True)   # "crawl" | "plan" | "execute" | None
    trace_path = Column(String, nullable=True)      # path to Playwright trace dir

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="jobs")

    @property
    def generated_test(self) -> Optional[str]:
        """Hoist generated_test out of the report JSON for direct API access."""
        return (self.report or {}).get("generated_test")

    def transition(self, target: str) -> bool:
        """
        Attempt a state transition.  Returns True if allowed, False otherwise.
        Does NOT commit — caller must commit the session.
        """
        if not valid_transition(self.state, target):
            return False

        self.state = target
        now = _utcnow()

        if target == "running":
            self.started_at = now
        elif target in ("complete", "failed"):
            self.completed_at = now

        return True
