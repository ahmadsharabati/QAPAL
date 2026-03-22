"""
Auth service — JWT validation + user upsert.

Stub mode:  When SECRET_KEY="dev", accepts any "Bearer dev-{user_id}" token.
Production: Validates Supabase JWTs using python-jose.
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import User

_bearer_scheme = HTTPBearer()


def _decode_token(token: str) -> dict:
    """
    Decode a JWT and return the payload.

    In dev mode (SECRET_KEY="dev"), accepts "dev-{user_id}" as a shortcut.
    In production, validates the JWT signature.
    """
    # ── Dev stub ──────────────────────────────────────────────────────
    if settings.SECRET_KEY == "dev":
        if token.startswith("dev-"):
            user_id = token[4:]  # strip "dev-" prefix
            return {
                "sub": user_id,
                "email": f"{user_id}@dev.local",
            }
        # In dev mode, also try real JWT decode as fallback
        # (so you can test with real tokens locally)

    # ── Real JWT ──────────────────────────────────────────────────────
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if "sub" not in payload:
            raise HTTPException(status_code=401, detail="Invalid token: missing sub")
        return payload
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="python-jose not installed; set SECRET_KEY=dev for stub auth",
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _upsert_user(db: Session, user_id: str, email: str) -> User:
    """Get existing user or create on first seen."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        user = User(id=user_id, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency — extracts and validates the auth token,
    upserts the user, and returns the User model.
    """
    payload = _decode_token(credentials.credentials)
    user = _upsert_user(db, payload["sub"], payload.get("email", ""))
    return user
