"""Custom dates: named lists of days (closures, exam days...) that
schedules can skip or be limited to — see services/calendars.py."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_user
from ..errors import AppError
from ..models import CustomCalendar, CustomCalendarDate, ScheduleCalendar, User
from ..schemas import CalendarIn, CalendarOut, OverlapWarning
from ..services import overlap, references

router = APIRouter(prefix="/api/calendars", tags=["calendars"])
logger = logging.getLogger("zonecast.calendars")


class CalendarSaved(CalendarOut):
    # Overlaps between schedules this change created (e.g. a closure
    # removed: two bells that never met on those days now do).
    warnings: list[OverlapWarning] = []


def _usage(db: Session) -> dict[int, int]:
    return dict(db.query(ScheduleCalendar.calendar_id, func.count()).group_by(ScheduleCalendar.calendar_id).all())


def _out(cal: CustomCalendar, usage: dict[int, int], warnings=()) -> dict:
    return {"id": cal.id, "name": cal.name, "description": cal.description or "", "dates": cal.dates,
            "schedule_count": usage.get(cal.id, 0), "warnings": list(warnings)}


def _entries(payload: CalendarIn) -> list[CustomCalendarDate]:
    return [CustomCalendarDate(start_date=d.start_date, end_date=d.end_date, label=d.label, yearly=d.yearly)
            for d in payload.dates]


@router.get("", response_model=list[CalendarOut])
def list_calendars(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    usage = _usage(db)
    cals = db.query(CustomCalendar).options(selectinload(CustomCalendar.dates)).order_by(CustomCalendar.name).all()
    return [_out(c, usage) for c in cals]


@router.post("", response_model=CalendarSaved)
def create_calendar(payload: CalendarIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.query(CustomCalendar.id).filter(CustomCalendar.name == payload.name).first():
        raise AppError(400, "calendars.name_taken", "A custom-dates list with this name already exists.")
    cal = CustomCalendar(name=payload.name, description=payload.description, dates=_entries(payload))
    db.add(cal)
    db.commit()
    db.refresh(cal)
    logger.info("Date personalizzate '%s' create (%d voci, utente %s)", cal.name, len(cal.dates), user.username)
    return _out(cal, {})


@router.put("/{calendar_id}", response_model=CalendarSaved)
def update_calendar(calendar_id: int, payload: CalendarIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    with overlap.SAVE_LOCK:
        cal = db.query(CustomCalendar).filter(CustomCalendar.id == calendar_id).first()
        if not cal:
            raise AppError(404, "calendars.not_found", "Custom-dates list not found.")
        if db.query(CustomCalendar.id).filter(CustomCalendar.name == payload.name, CustomCalendar.id != cal.id).first():
            raise AppError(400, "calendars.name_taken", "A custom-dates list with this name already exists.")
        used = bool(references.schedules_using_calendar(db, cal.id))
        before = overlap.all_pairs(db) if used else []
        cal.name, cal.description = payload.name, payload.description
        cal.dates = _entries(payload)
        db.flush()
        warnings = overlap.new_pairs(before, overlap.all_pairs(db)) if used else []
        db.commit()
    db.refresh(cal)
    logger.info("Date personalizzate '%s' modificate (%d voci, utente %s)", cal.name, len(cal.dates), user.username)
    return _out(cal, _usage(db), warnings)


@router.delete("/{calendar_id}")
def delete_calendar(calendar_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cal = db.query(CustomCalendar).filter(CustomCalendar.id == calendar_id).first()
    if not cal:
        raise AppError(404, "calendars.not_found", "Custom-dates list not found.")
    references.refuse_if_used(references.schedules_using_calendar(db, calendar_id), "calendars.in_use",
                              "This custom-dates list")
    name = cal.name
    db.delete(cal)
    db.commit()
    logger.info("Date personalizzate '%s' eliminate (utente %s)", name, user.username)
    return {"ok": True}
