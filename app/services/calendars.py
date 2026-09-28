"""
Custom dates: is a given day in a calendar? Shared by the scheduler
(which decides at run time) and the schedule overlap check (which
enumerates future run days), so the two can never disagree.
"""
import calendar as _cal
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from sqlalchemy.orm import Session, selectinload

from ..models import CustomCalendar, ScheduleCalendar


@dataclass(frozen=True)
class DateEntry:
    start: date
    end: Optional[date]
    yearly: bool


@dataclass(frozen=True)
class Rule:
    mode: str  # "exclude" | "only"
    entries: tuple[DateEntry, ...]


def _month_day(d: date, leap: bool) -> tuple[int, int]:
    # A yearly Feb 29 also means Feb 28 in the years that don't have it.
    return (2, 28) if (d.month, d.day) == (2, 29) and not leap else (d.month, d.day)


def entry_contains(entry: DateEntry, day: date) -> bool:
    end = entry.end or entry.start
    if not entry.yearly:
        return entry.start <= day <= end
    leap = _cal.isleap(day.year)
    first, last, md = _month_day(entry.start, leap), _month_day(end, leap), (day.month, day.day)
    if first <= last:
        return first <= md <= last
    return md >= first or md <= last  # across the new year, e.g. Dec 24 - Jan 6


def calendar_contains(entries: Iterable[DateEntry], day: date) -> bool:
    return any(entry_contains(e, day) for e in entries)


def rules_allow(rules: Iterable[Rule], day: date) -> Optional[str]:
    """None if the custom-dates rules let a schedule run on `day`, else
    the reason it doesn't ("excluded" / "not_included")."""
    rules = list(rules)
    if any(r.mode == "exclude" and calendar_contains(r.entries, day) for r in rules):
        return "excluded"
    only = [r for r in rules if r.mode == "only"]
    if only and not any(calendar_contains(r.entries, day) for r in only):
        return "not_included"
    return None


def load_calendars(db: Session) -> dict[int, tuple[DateEntry, ...]]:
    """Every calendar's entries, keyed by id (one query, for bulk checks)."""
    calendars = db.query(CustomCalendar).options(selectinload(CustomCalendar.dates)).all()
    return {c.id: tuple(DateEntry(d.start_date, d.end_date, d.yearly) for d in c.dates) for c in calendars}


def rules_for(schedule_rules: Iterable, calendars: dict[int, tuple[DateEntry, ...]]) -> tuple[Rule, ...]:
    """`schedule_rules`: ScheduleCalendar rows or {calendar_id, mode}
    objects/dicts. A rule pointing at a deleted calendar is ignored."""
    out = []
    for r in schedule_rules:
        calendar_id = r["calendar_id"] if isinstance(r, dict) else r.calendar_id
        mode = r["mode"] if isinstance(r, dict) else r.mode
        if calendar_id in calendars:
            out.append(Rule(mode, calendars[calendar_id]))
    return tuple(out)


def schedule_rules(db: Session, schedule_id: int) -> tuple[Rule, ...]:
    rows = db.query(ScheduleCalendar).filter(ScheduleCalendar.schedule_id == schedule_id).all()
    return rules_for(rows, load_calendars(db)) if rows else ()
