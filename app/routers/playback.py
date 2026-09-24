from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User, PlaybackLog
from ..schemas import PlayRequest, PlaybackLogOut
from ..services import player

router = APIRouter(prefix="/api/playback", tags=["playback"])


@router.post("/play", response_model=PlaybackLogOut)
async def play_now(payload: PlayRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        log = await player.play(
            db,
            media_id=payload.media_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            triggered_by_id=user.id,
        )
    except player.TargetResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return log


@router.post("/stop/{log_id}")
def stop_playback(log_id: int, _: User = Depends(get_current_user)):
    stopped = player.stop_playback(log_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Nessuna riproduzione attiva con questo id")
    return {"ok": True}


@router.get("/active")
def active_playbacks(_: User = Depends(get_current_user)):
    return {"active_log_ids": player.list_active()}


@router.get("/history", response_model=list[PlaybackLogOut])
def history(limit: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(PlaybackLog).order_by(PlaybackLog.started_at.desc()).limit(limit).all()
