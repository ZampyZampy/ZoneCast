import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings, BASE_DIR
from .database import Base, engine, SessionLocal, get_db
from .models import User, UserRole
from .security import hash_password
from .services import event_log
from .services import scheduler as scheduler_service
from .services.app_settings import get_settings as get_app_settings, derive_theme_shades
from .routers import auth, zones, speakers, media, playback, schedules, system, logs, backups

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zonecast")

# Cache-busting query string for /static/* asset links (style.css,
# app.js) — set once per process start. Without this, browsers keep
# serving a stale cached CSS/JS after a deploy until the user manually
# hard-refreshes, since StaticFiles has no content hashing on its own.
ASSET_VERSION = str(int(time.time()))


def bootstrap_admin():
    Base.metadata.create_all(bind=engine)
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
    scheduler_service.start()
    yield
    scheduler_service.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=settings.session_max_age_seconds)


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
    return response

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app.include_router(auth.router)
app.include_router(zones.router)
app.include_router(speakers.router)
app.include_router(media.router)
app.include_router(playback.router)
app.include_router(schedules.router)
app.include_router(system.router)
app.include_router(logs.router)
app.include_router(backups.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db=Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "dashboard.html", {
        "app_name": settings.app_name,
        "max_upload_mb": settings.max_upload_mb,
        "max_duration_seconds": settings.max_duration_seconds,
        "theme": derive_theme_shades(get_app_settings(db).theme_color),
        "asset_version": ASSET_VERSION,
    })


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "login.html", {
        "app_name": settings.app_name,
        "theme": derive_theme_shades(get_app_settings(db).theme_color),
        "asset_version": ASSET_VERSION,
    })


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
