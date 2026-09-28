from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user
from ..models import Media, PlaybackLog, Schedule, User
from ..schemas import PlayRequest, PlaybackLogOut
from ..services import player, scheduler as scheduler_service
from ..services.footprint import footprint

router = APIRouter(prefix="/api/playback", tags=["playback"])

MAX_UPCOMING = 5


def _upcoming(db: Session, payload: PlayRequest) -> list[dict]:
    """Schedules due while this announcement would play, on some of the
    same speakers: they'll wait for it to finish."""
    media = db.get(Media, payload.media_id)
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=max(1.0, media.duration_seconds or 0.0) if media else 1.0)
    fp = footprint(db, payload.target_type, payload.target_id)
    engine = scheduler_service.get_scheduler()
    due = []
    for sched in db.query(Schedule).filter(Schedule.enabled.is_(True)).all():
        job = engine.get_job(f"schedule-{sched.id}")
        if job and job.next_run_time and now <= job.next_run_time <= until \
                and footprint(db, sched.target_type, sched.target_id).intersects(fp):
            due.append({"schedule_id": sched.id, "name": sched.name,
                        "in_seconds": int((job.next_run_time - now).total_seconds())})
    return sorted(due, key=lambda d: d["in_seconds"])[:MAX_UPCOMING]


@router.post("/play", response_model=PlaybackLogOut)
async def play_now(payload: PlayRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        log = await player.play(
            db,
            media_id=payload.media_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            triggered_by_id=user.id,
            on_conflict=payload.on_conflict,
        )
    except player.SpeakersBusyError as exc:
        exc.params["upcoming"] = _upcoming(db, payload)
        raise exc.http(409) from exc
    except player.TargetResolutionError as exc:
        raise exc.http(400) from exc
    return log


@router.post("/stop/{log_id}")
def stop_playback(log_id: int, _: User = Depends(get_current_user)):
    stopped = player.stop_playback(log_id)
    if not stopped:
        raise AppError(404, "playback.not_active", "This playback is no longer running.")
    return {"ok": True}


@router.get("/active")
def active_playbacks(_: User = Depends(get_current_user)):
    return {"active_log_ids": player.list_active()}


@router.get("/history", response_model=list[PlaybackLogOut])
def history(limit: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(PlaybackLog).order_by(PlaybackLog.started_at.desc()).limit(limit).all()
