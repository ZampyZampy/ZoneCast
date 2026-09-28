"""
Who uses what: lookups that keep deletes from silently breaking
schedules. SQLite doesn't enforce this project's foreign keys (and
Schedule.target_id can't have one: it points at a speaker OR a zone), so
without these checks deleting an audio file or a zone left schedules
that failed only when they fired.
"""
from sqlalchemy.orm import Session

from ..errors import AppError

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


def refuse_if_used(schedules: list[Schedule], code: str, what: str) -> None:
    """409 naming the schedules that would break, so the user knows what
    to edit or delete first."""
    if not schedules:
        return
    names = ", ".join(s.name for s in schedules[:5]) + (", …" if len(schedules) > 5 else "")
    raise AppError(409, code, f"{what} is used by {len(schedules)} schedule(s): {names}. Edit or delete them first.",
                   count=len(schedules), names=names)


def check_schedule_references(db: Session, sched: Schedule) -> None:
    """422 if a schedule points at an audio file or target that doesn't exist."""
    if not db.query(Media.id).filter(Media.id == sched.media_id).first():
        raise AppError(422, "schedules.media_missing", "The selected audio file no longer exists.", media_id=sched.media_id)
    if sched.target_type == TargetType.zone and not db.query(Zone.id).filter(Zone.id == sched.target_id).first():
        raise AppError(422, "schedules.zone_missing", "The selected zone no longer exists.", target_id=sched.target_id)
    if sched.target_type == TargetType.speaker and not db.query(Speaker.id).filter(Speaker.id == sched.target_id).first():
        raise AppError(422, "schedules.speaker_missing", "The selected speaker no longer exists.", target_id=sched.target_id)
