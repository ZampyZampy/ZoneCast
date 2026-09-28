from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .errors import AppError
from .models import User, UserRole


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.not_authenticated", "Not signed in.")
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()  # deleted or deactivated account: drop the stale cookie
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.not_authenticated", "Not signed in.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.admin_required", "Administrator privileges are required.")
    return user
