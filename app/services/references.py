"""
Who uses what: lookups that keep deletes from silently breaking
schedules. SQLite doesn't enforce this project's foreign keys (and
Schedule.target_id can't have one: it points at a speaker OR a zone), so
without these checks deleting an audio file or a zone left schedules
that failed only when they fired.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Media, Schedule, Speaker, TargetType, Zone


def schedules_using_media(db: Session, media_id: int) -> list[Schedule]:
    return db.query(Schedule).filter(Schedule.media_id == media_id).order_by(Schedule.name).all()


def schedules_targeting(db: Session, target_type: TargetType, target_id: int) -> list[Schedule]:
    return (
        db.query(Schedule)
        .filter(Schedule.target_type == target_type, Schedule.target_id == target_id)
        .order_by(Schedule.name)
        .all()
    )


def refuse_if_used(schedules: list[Schedule], what: str) -> None:
    """409 naming the schedules that would break, so the user knows what
    to edit or delete first."""
    if not schedules:
        return
    names = ", ".join(s.name for s in schedules[:5]) + (", …" if len(schedules) > 5 else "")
    noun = "schedulazione" if len(schedules) == 1 else "schedulazioni"
    raise HTTPException(
        status_code=409,
        detail=f"{what} è usato da {len(schedules)} {noun}: {names}. Modificale o eliminale prima.",
    )


def check_schedule_references(db: Session, sched: Schedule) -> None:
    """422 if a schedule points at an audio file or target that doesn't exist."""
    if not db.query(Media.id).filter(Media.id == sched.media_id).first():
        raise HTTPException(status_code=422, detail=f"File audio {sched.media_id} non trovato")
    if sched.target_type == TargetType.zone and not db.query(Zone.id).filter(Zone.id == sched.target_id).first():
        raise HTTPException(status_code=422, detail=f"Zona {sched.target_id} non trovata")
    if sched.target_type == TargetType.speaker and not db.query(Speaker.id).filter(Speaker.id == sched.target_id).first():
        raise HTTPException(status_code=422, detail=f"Altoparlante {sched.target_id} non trovato")
