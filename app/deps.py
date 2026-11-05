import hashlib

from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .errors import AppError
from .models import User, UserRole


def password_stamp(user: User) -> str:
    """Kept in the session at sign-in: a password change or reset ends
    every other session. A digest, since the session cookie is readable."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def sign_in(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["pw"] = password_stamp(user)


def session_user(request: Request, db: Session) -> User | None:
    """The signed-in, still active user, or None (clearing a cookie left by
    a deleted or deactivated account, or one whose password changed since)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user or request.session.get("pw") != password_stamp(user):
        request.session.clear()
        return None
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = session_user(request, db)
    if not user:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.not_authenticated", "Not signed in.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.admin_required", "Administrator privileges are required.")
    return user
