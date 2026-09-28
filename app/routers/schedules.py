import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user
from ..models import Schedule, ScheduleCalendar, TargetType, User
from ..schemas import OverlapWarning, ScheduleCreate, ScheduleUpdate, ScheduleOut
from ..services import overlap, references, scheduler as scheduler_service

router = APIRouter(prefix="/api/schedules", tags=["schedules"])
logger = logging.getLogger("zonecast.schedules")


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Schedule).order_by(Schedule.time_of_day).all()


@router.get("/overlaps", response_model=list[OverlapWarning])
def list_overlaps(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Enabled schedules that already play on the same speakers at the
    same time — saved before the check existed, or made to overlap later
    by a zone's members or a custom-dates change."""
    return overlap.all_pairs(db)


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


def _rule_rows(rules: list[dict]) -> list[ScheduleCalendar]:
    return [ScheduleCalendar(calendar_id=r["calendar_id"], mode=r["mode"]) for r in rules]


@router.post("", response_model=ScheduleOut)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rules = [r.model_dump() for r in payload.calendars]
    sched = Schedule(**payload.model_dump(exclude={"calendars"}), created_by_id=user.id)
    references.check_schedule_references(db, sched, rules)
    with overlap.SAVE_LOCK:
        if sched.enabled:
            conflicts = overlap.find_conflicts(db, sched, rules)
            if conflicts:
                raise overlap.refusal(conflicts)
        sched.calendar_rules = _rule_rows(rules)
        db.add(sched)
        _save_with_job(db, sched)
    db.refresh(sched)
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    with overlap.SAVE_LOCK:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not sched:
            raise AppError(404, "schedules.not_found", "Schedule not found.")
        # Overlaps this row already had (saved before the check existed)
        # don't block an unrelated edit, like a rename: only new ones do.
        was_enabled = sched.enabled
        before = {c["schedule_id"] for c in overlap.find_conflicts(db, sched, exclude_id=sched.id)} if was_enabled else set()
        changes = payload.model_dump(exclude_unset=True)
        rules = changes.pop("calendars", None)
        rules = [dict(r) for r in rules] if rules is not None else sched.calendars
        for k, v in changes.items():
            setattr(sched, k, v)
        if sched.target_type != TargetType.all and sched.target_id is None:
            db.rollback()
            raise AppError(422, "schedules.target_required", "Choose the zone or speaker to play on.")
        if sched.start_date and sched.end_date and sched.start_date > sched.end_date:
            db.rollback()
            raise AppError(422, "schedules.dates_inverted", "The start date is after the end date.")
        if sched.exclude_holidays and sched.holidays_only:
            db.rollback()
            raise AppError(422, "schedules.holiday_mode_invalid", "Choose either \"skip holidays\" or \"only on holidays\".")
        try:
            references.check_schedule_references(db, sched, rules)
        except AppError:
            db.rollback()
            raise
        if sched.enabled:
            conflicts = overlap.find_conflicts(db, sched, rules, exclude_id=sched.id)
            new = [c for c in conflicts if c["schedule_id"] not in before] if was_enabled else conflicts
            if new:
                db.rollback()
                raise overlap.refusal(new)
        if "calendars" in payload.model_fields_set:
            sched.calendar_rules = _rule_rows(rules)
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
