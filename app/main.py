import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings, BASE_DIR, session_secret
from .database import SessionLocal, get_db
from .migrate import run_migrations
from . import errors
from .models import User, UserRole
from .security import hash_password
from .services import event_log
from .services import scheduler as scheduler_service
from .services import player
from .services.app_settings import get_settings as get_app_settings, derive_theme_shades
from .services.sun_position import coords_for_timezone
from .routers import auth, auto_backup, backups, calendars, logs, media, playback, schedules, speakers, system, zones

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zonecast")

# Cache-busting query string for /static/* asset links (style.css,
# app.js) — set once per process start. Without this, browsers keep
# serving a stale cached CSS/JS after a deploy until the user manually
# hard-refreshes, since StaticFiles has no content hashing on its own.
ASSET_VERSION = str(int(time.time()))


def bootstrap_admin():
    run_migrations()
    event_log.install()
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username=settings.default_admin_username,
                password_hash=hash_password(settings.default_admin_password),
                full_name="Amministratore",
                role=UserRole.admin,
            )
            db.add(admin)
            db.commit()
            logger.warning(
                "Creato utente admin di default '%s' — CAMBIARE LA PASSWORD al primo accesso.",
                settings.default_admin_username,
            )
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_admin()
    player.reconcile_interrupted()
    scheduler_service.start()
    yield
    await player.stop_all()
    scheduler_service.shutdown()
    event_log.shutdown()


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)

class SlidingSession:
    """Makes session_max_age_seconds an inactivity timeout instead of a
    fixed lifetime from sign-in: SessionMiddleware re-signs the cookie
    (restarting its max_age, which it also enforces on the signature)
    only when the session changes, so a signed-in request touches it —
    at most once a minute. Requests the dashboard makes on its own
    (pollers, see static/js/lib/poller.js) carry BACKGROUND_HEADER and
    don't count: a tab left open is not someone using it."""

    BACKGROUND_HEADER = b"x-zonecast-background"
    RENEW_AFTER_SECONDS = 60

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        session = scope.get("session") if scope["type"] == "http" else None
        if session and session.get("user_id") and not any(k == self.BACKGROUND_HEADER for k, _ in scope["headers"]):
            now = int(time.time())
            if now - session.get("seen", 0) >= self.RENEW_AFTER_SECONDS:
                session["seen"] = now
        await self.app(scope, receive, send)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
errors.install(app)
# Added first, so it runs inside SessionMiddleware, with the session loaded.
app.add_middleware(SlidingSession)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), max_age=settings.session_max_age_seconds)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline hardening headers on every response. No
    Strict-Transport-Security here deliberately: this app doesn't know
    whether it's reached directly or through a TLS-terminating reverse
    proxy — set HSTS at the proxy if/when one is in front of it."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # No inline scripts or event handlers anywhere (see app/static/js), so
    # scripts can be limited to our own files; styles still need inline
    # for the theme colours and a few style="" attributes.
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if request.url.path.startswith("/static/"):
        # Revalidate every time (a cheap 304 when unchanged): ES modules are
        # imported by plain URL, so this is what makes a deploy take effect
        # without users having to hard-refresh.
        response.headers["Cache-Control"] = "no-cache"
    return response

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app.include_router(auth.router)
app.include_router(zones.router)
app.include_router(speakers.router)
app.include_router(media.router)
app.include_router(playback.router)
app.include_router(schedules.router)
app.include_router(calendars.router)
app.include_router(system.router)
app.include_router(logs.router)
app.include_router(backups.router)
app.include_router(auto_backup.router)


def _page_context(db) -> dict:
    lat, lon = coords_for_timezone(scheduler_service.scheduler_timezone())
    return {
        "app_name": settings.app_name,
        "theme": derive_theme_shades(get_app_settings(db).theme_color),
        "asset_version": ASSET_VERSION,
        "sun_lat": lat,
        "sun_lon": lon,
    }


def _session_user(request: Request, db) -> User | None:
    """The signed-in, still active user — or None, clearing a session left
    behind by a deleted or deactivated account: / and /login would
    otherwise redirect to each other until the cookie expires."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()
    return user


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db=Depends(get_db)):
    user = _session_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    # Admin-only panels aren't sent to operators at all: nothing to hide
    # client-side, and no admin endpoint polled by a page that can't use it.
    return templates.TemplateResponse(request, "dashboard.html", {
        **_page_context(db),
        "is_admin": user.role == UserRole.admin,
        "max_upload_mb": settings.max_upload_mb,
        "max_duration_seconds": settings.max_duration_seconds,
    })


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    if _session_user(request, db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "login.html", _page_context(db))


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
