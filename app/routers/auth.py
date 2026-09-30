import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user, require_admin
from ..models import User, UserRole
from ..schemas import (
    LoginRequest, LoginResult, TwoFactorLoginRequest, UserOut, UserCreate, UserUpdate,
    ChangePasswordRequest, TwoFactorSetupOut, TwoFactorConfirmRequest, TwoFactorConfirmOut,
    TwoFactorDisableRequest, TwoFactorStatusOut,
)
from ..security import verify_password, hash_password
from ..services import rate_limit, totp_2fa

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger("zonecast.auth")

MIN_PASSWORD_LENGTH = 8


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=LoginResult)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _client_ip(request)
    locked_for = rate_limit.is_locked_out(payload.username, client_ip)
    if locked_for:
        logger.warning("Login bloccato (rate limit) per '%s' da %s — riprovare tra %ds", payload.username, client_ip, locked_for)
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "auth.too_many_attempts",
                       f"Too many failed attempts. Try again in {locked_for} seconds.", seconds=locked_for)

    user = db.query(User).filter(User.username == payload.username, User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        rate_limit.record_failure(payload.username, client_ip)
        logger.warning("Login fallito per utente '%s' da %s", payload.username, client_ip)
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.invalid_credentials", "Invalid username or password.")

    rate_limit.record_success(payload.username, client_ip)

    if user.totp_enabled:
        # Password correct, but the session isn't established until the
        # second factor is verified too — see /login/2fa below.
        request.session.clear()
        request.session["pending_2fa_user_id"] = user.id
        logger.info("Login (1/2, password ok) per utente '%s' da %s — in attesa codice 2FA", user.username, client_ip)
        return LoginResult(requires_2fa=True)

    logger.info("Login riuscito: utente '%s' da %s", user.username, client_ip)
    request.session["user_id"] = user.id
    return LoginResult(requires_2fa=False, user=user)


@router.post("/login/2fa", response_model=UserOut)
def login_2fa(payload: TwoFactorLoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _client_ip(request)
    user_id = request.session.get("pending_2fa_user_id")
    if not user_id:
        raise AppError(status.HTTP_400_BAD_REQUEST, "auth.no_login_in_progress", "No sign-in in progress: start again.")
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user or not user.totp_enabled:
        request.session.clear()
        raise AppError(status.HTTP_400_BAD_REQUEST, "auth.no_login_in_progress", "No sign-in in progress: start again.")

    rl_key = f"2fa:{user.username}"
    locked_for = rate_limit.is_locked_out(rl_key, client_ip)
    if locked_for:
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "auth.too_many_attempts",
                       f"Too many failed attempts. Try again in {locked_for} seconds.", seconds=locked_for)

    if totp_2fa.verify_code(user.totp_secret, payload.code):
        rate_limit.record_success(rl_key, client_ip)
    else:
        updated_codes = totp_2fa.consume_recovery_code(user.totp_recovery_codes, payload.code)
        if updated_codes is not None:
            user.totp_recovery_codes = updated_codes
            db.commit()
            logger.warning("Login con codice di recupero 2FA per utente '%s' da %s — ne restano %d", user.username, client_ip, totp_2fa.remaining_recovery_codes(updated_codes))
            rate_limit.record_success(rl_key, client_ip)
        else:
            rate_limit.record_failure(rl_key, client_ip)
            logger.warning("Codice 2FA errato per utente '%s' da %s", user.username, client_ip)
            raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.invalid_code", "Invalid code.")

    request.session.clear()
    request.session["user_id"] = user.id
    logger.info("Login riuscito (2FA): utente '%s' da %s", user.username, client_ip)
    return user


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


def _check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(400, "auth.password_too_short", f"The password must be at least {MIN_PASSWORD_LENGTH} characters long.", min=MIN_PASSWORD_LENGTH)


@router.post("/me/password")
def change_my_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise AppError(400, "auth.wrong_current_password", "The current password is incorrect.")
    _check_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


# ---------- Two-factor auth (self-service, any logged-in user, own account only) ----------
@router.get("/me/2fa", response_model=TwoFactorStatusOut)
def two_factor_status(user: User = Depends(get_current_user)):
    return TwoFactorStatusOut(
        enabled=user.totp_enabled,
        remaining_recovery_codes=totp_2fa.remaining_recovery_codes(user.totp_recovery_codes) if user.totp_enabled else 0,
    )


@router.post("/me/2fa/setup", response_model=TwoFactorSetupOut)
def two_factor_setup(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Generates a new candidate secret and stores it (not yet active —
    totp_enabled stays False until /me/2fa/confirm proves the user's
    authenticator app is actually reading it correctly)."""
    if user.totp_enabled:
        raise AppError(400, "auth.2fa_already_enabled", "Two-factor authentication is already enabled.")
    secret = totp_2fa.generate_secret()
    user.totp_secret = secret
    db.commit()
    uri = totp_2fa.provisioning_uri(secret, user.username)
    return TwoFactorSetupOut(secret=secret, otpauth_uri=uri, qr_svg=totp_2fa.qr_code_svg(uri))


@router.post("/me/2fa/confirm", response_model=TwoFactorConfirmOut)
def two_factor_confirm(payload: TwoFactorConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.totp_enabled:
        raise AppError(400, "auth.2fa_already_enabled", "Two-factor authentication is already enabled.")
    if not user.totp_secret:
        raise AppError(400, "auth.2fa_no_setup", "No two-factor setup in progress: start it again.")
    if not totp_2fa.verify_code(user.totp_secret, payload.code):
        raise AppError(400, "auth.invalid_code", "Invalid code.")
    codes, hashed = totp_2fa.generate_recovery_codes()
    user.totp_enabled = True
    user.totp_recovery_codes = hashed
    db.commit()
    logger.info("2FA attivata per utente '%s'", user.username)
    return TwoFactorConfirmOut(recovery_codes=codes)


@router.post("/me/2fa/disable")
def two_factor_disable(payload: TwoFactorDisableRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(payload.password, user.password_hash):
        raise AppError(400, "auth.wrong_password", "Incorrect password.")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_recovery_codes = None
    db.commit()
    logger.info("2FA disattivata per utente '%s'", user.username)
    return {"ok": True}


@router.post("/me/2fa/recovery-codes/regenerate", response_model=TwoFactorConfirmOut)
def two_factor_regenerate_codes(payload: TwoFactorDisableRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not user.totp_enabled:
        raise AppError(400, "auth.2fa_not_enabled", "Two-factor authentication is not enabled.")
    if not verify_password(payload.password, user.password_hash):
        raise AppError(400, "auth.wrong_password", "Incorrect password.")
    codes, hashed = totp_2fa.generate_recovery_codes()
    user.totp_recovery_codes = hashed
    db.commit()
    logger.info("Codici di recupero 2FA rigenerati per utente '%s'", user.username)
    return TwoFactorConfirmOut(recovery_codes=codes)


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if db.query(User).filter(User.username == payload.username).first():
        raise AppError(400, "users.username_taken", "That username already exists.")
    _check_password_strength(payload.password)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError(404, "users.not_found", "User not found.")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None and payload.role != user.role:
        # Undeletable is not enough for "always a way back in": demoted,
        # the default admin could no longer reach Users to fix things.
        if user.is_protected and payload.role != UserRole.admin:
            raise AppError(400, "users.cannot_demote_default", "The default admin account must stay an administrator.")
        user.role = payload.role
    if payload.new_password:
        _check_password_strength(payload.new_password)
        user.password_hash = hash_password(payload.new_password)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if user_id == admin.id:
        raise AppError(400, "users.cannot_delete_self", "You can't delete your own account.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError(404, "users.not_found", "User not found.")
    if user.is_protected:
        raise AppError(400, "users.cannot_delete_default", "The default admin account can't be deleted.")
    db.delete(user)
    db.commit()
    return {"ok": True}
