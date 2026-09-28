import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user
from ..models import Schedule, TargetType, User
from ..schemas import ScheduleCreate, ScheduleUpdate, ScheduleOut
from ..services import references, scheduler as scheduler_service

router = APIRouter(prefix="/api/schedules", tags=["schedules"])
logger = logging.getLogger("zonecast.schedules")


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Schedule).order_by(Schedule.time_of_day).all()


def _restore_job(db: Session, schedule_id: int | None) -> None:
    """After a rollback, puts the scheduler back in line with what's
    actually stored: the previous job for an existing row, no job for a
    row that was never committed."""
    if schedule_id is None:
        return
    try:
        stored = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if stored:
            scheduler_service.add_or_update_job(stored)
        else:
            scheduler_service.remove_job(schedule_id)
    except Exception:
        logger.exception("Schedulazione %s: impossibile ripristinare il job precedente", schedule_id)


def _save_with_job(db: Session, sched: Schedule) -> None:
    """Flush, register the cron job, commit — as one unit. The job is
    registered before committing so a schedule the scheduler can't
    accept is never persisted (it would break the next startup); if any
    step fails, the DB is rolled back and the job restored, so the
    scheduler and the stored row never disagree."""
    try:
        db.flush()
        scheduler_service.add_or_update_job(sched)
        db.commit()
    except Exception as exc:
        schedule_id = sched.id
        db.rollback()
        _restore_job(db, schedule_id)
        if isinstance(exc, ValueError):
            raise AppError(422, "schedules.invalid", "The scheduler can't accept this schedule.", detail=str(exc)) from exc
        raise


@router.post("", response_model=ScheduleOut)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sched = Schedule(**payload.model_dump(), created_by_id=user.id)
    references.check_schedule_references(db, sched)
    db.add(sched)
    _save_with_job(db, sched)
    db.refresh(sched)
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise AppError(404, "schedules.not_found", "Schedule not found.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(sched, k, v)
    if sched.target_type != TargetType.all and sched.target_id is None:
        db.rollback()
        raise AppError(422, "schedules.target_required", "Choose the zone or speaker to play on.")
    if sched.start_date and sched.end_date and sched.start_date > sched.end_date:
        db.rollback()
        raise AppError(422, "schedules.dates_inverted", "The start date is after the end date.")
    try:
        references.check_schedule_references(db, sched)
    except AppError:
        db.rollback()
        raise
    _save_with_job(db, sched)
    db.refresh(sched)
    return sched


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise AppError(404, "schedules.not_found", "Schedule not found.")
    db.delete(sched)
    db.commit()
    scheduler_service.remove_job(schedule_id)
    return {"ok": True}
