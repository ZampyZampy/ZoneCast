"""
Two schedules must not play on the same speaker at the same time: a new
or edited schedule is refused when one of its runs overlaps a run of
another enabled schedule that reaches some of the same speakers (see
services/footprint.py). A run lasts from its start time to the end of
its audio file.

The run days come from scheduler.skip_reason — the very rule the job
applies when it fires — enumerated day by day over the next two years
(the date range, weekdays, holidays and custom dates make anything
coarser wrong around midnight and year ends). A run that spills past
midnight is compared with the other schedule's runs of the next day.

Saves that change who a schedule reaches (zone members) or on which
days (custom dates) don't block: they report the overlaps they create.
"""
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import Media, Schedule, Speaker, TargetType, Zone
from . import calendars
from .footprint import Footprint, footprint
from .scheduler import WEEKDAYS, weekday_set

# Held from the check to the commit, so two saves can't both pass the
# check with each other's overlap. A thread lock is enough: the service
# runs a single uvicorn worker (Dockerfile, deploy/zonecast.service).
SAVE_LOCK = threading.Lock()

HORIZON_DAYS = 731
MAX_CONFLICTS = 10
MAX_NAMES = 5
DAY = timedelta(days=1)


@dataclass
class Plan:
    """A schedule as the check sees it: a stored row or unsaved values."""
    id: Optional[int]
    name: str
    target_type: TargetType
    target_id: Optional[int]
    time_of_day: time
    days_of_week: str
    start_date: Optional[date]
    end_date: Optional[date]
    exclude_holidays: bool
    holidays_only: bool
    holiday_country: str
    rules: tuple
    duration: int
    footprint: Footprint
    key: int = 0  # unique within one _Context: the run-day cache key


class _Context:
    """Per-check caches: footprints, calendars, run days."""

    def __init__(self, db: Session):
        from .scheduler import scheduler_timezone

        self.db = db
        self.now = datetime.now(ZoneInfo(scheduler_timezone())).replace(tzinfo=None)
        self.today = self.now.date()
        self.calendars = calendars.load_calendars(db)
        self._footprints: dict = {}
        self._days: dict = {}
        self._serial = 0

    def footprint(self, target_type, target_id) -> Footprint:
        key = (TargetType(target_type), target_id)
        if key not in self._footprints:
            self._footprints[key] = footprint(self.db, *key)
        return self._footprints[key]

    def plan(self, sched, rules=None) -> Plan:
        """`rules`: the custom-dates rules to use instead of the stored ones
        (a form being saved); list of {calendar_id, mode}."""
        media = self.db.get(Media, sched.media_id) if sched.media_id is not None else None
        duration = max(1, math.ceil(media.duration_seconds or 0)) if media else 1
        source = rules if rules is not None else (sched.calendar_rules if sched.id is not None else [])
        return Plan(
            id=sched.id, name=sched.name, target_type=TargetType(sched.target_type), target_id=sched.target_id,
            time_of_day=sched.time_of_day, days_of_week=sched.days_of_week or "",
            start_date=sched.start_date, end_date=sched.end_date,
            exclude_holidays=bool(sched.exclude_holidays), holidays_only=bool(sched.holidays_only),
            holiday_country=sched.holiday_country or "", rules=calendars.rules_for(source, self.calendars),
            duration=duration, footprint=self.footprint(sched.target_type, sched.target_id),
            key=self._next_key(),
        )

    def _next_key(self) -> int:
        # Not id(plan): a plan freed inside a loop can hand its address to
        # the next one, which would then get the old plan's run days.
        self._serial += 1
        return self._serial

    def run_days(self, plan: Plan, lo: date, hi: date) -> frozenset[date]:
        from .scheduler import skip_reason

        key = (plan.key, lo, hi)
        if key not in self._days:
            first = max(lo, plan.start_date) if plan.start_date else lo
            last = min(hi, plan.end_date) if plan.end_date else hi
            days, d = set(), first
            while d <= last:
                if skip_reason(plan, d, plan.rules) is None:
                    days.add(d)
                d += DAY
            self._days[key] = frozenset(days)
        return self._days[key]


def _may_meet_in_a_day(a: Plan, b: Plan) -> bool:
    """Cheap test on the times of day alone: can the two windows touch,
    on the same day or across midnight? Skips the day enumeration for
    most pairs."""
    ta = a.time_of_day.hour * 3600 + a.time_of_day.minute * 60 + a.time_of_day.second
    tb = b.time_of_day.hour * 3600 + b.time_of_day.minute * 60 + b.time_of_day.second
    if a.duration >= 86400 or b.duration >= 86400:
        return True
    return any(ta < tb + k * 86400 + b.duration and tb + k * 86400 < ta + a.duration for k in (-1, 0, 1))


def first_clash(ctx: _Context, a: Plan, b: Plan) -> Optional[datetime]:
    """When a run of `a` first overlaps a run of `b` (touching ends don't
    count), within HORIZON_DAYS from the later of today and the starts."""
    if not _may_meet_in_a_day(a, b):
        return None
    base = max(d for d in (ctx.today - DAY, a.start_date, b.start_date) if d is not None)
    lo, hi = base - DAY, base + timedelta(days=HORIZON_DAYS)
    a_days = ctx.run_days(a, lo, hi)
    b_days = ctx.run_days(b, lo - DAY, hi + DAY)
    if not a_days or not b_days:
        return None
    a_len, b_len = timedelta(seconds=a.duration), timedelta(seconds=b.duration)
    # The window starts the day before so that yesterday's late run
    # spilling past midnight is seen; a clash already over isn't.
    since = ctx.now
    for d in sorted(a_days):
        a0 = datetime.combine(d, a.time_of_day)
        for offset in (-1, 0, 1):
            e = d + offset * DAY
            if e in b_days:
                b0 = datetime.combine(e, b.time_of_day)
                if a0 < b0 + b_len and b0 < a0 + a_len and min(a0 + a_len, b0 + b_len) > since:
                    return max(a0, b0)
    return None


def _target_label(db: Session, plan: Plan) -> str:
    if plan.target_type == TargetType.zone:
        return db.scalar(select(Zone.name).where(Zone.id == plan.target_id)) or ""
    if plan.target_type == TargetType.speaker:
        return db.scalar(select(Speaker.name).where(Speaker.id == plan.target_id)) or ""
    return ""


def _describe(db: Session, mine: Plan, other: Plan, clash: datetime) -> dict:
    shared = mine.footprint.speakers & other.footprint.speakers
    names = db.scalars(select(Speaker.name).where(Speaker.id.in_(shared)).order_by(Speaker.name).limit(MAX_NAMES)).all()
    return {
        "schedule_id": other.id,
        "name": other.name,
        "target_type": other.target_type.value,
        "target_label": _target_label(db, other),
        "time": other.time_of_day.strftime("%H:%M"),
        "days": [WEEKDAYS[i] for i in sorted(weekday_set(other.days_of_week))],
        "first_clash": clash.strftime("%Y-%m-%dT%H:%M"),
        "shared_count": len(shared),
        "shared_speakers": list(names),
    }


def find_conflicts(db: Session, sched, rules=None, exclude_id: Optional[int] = None) -> list[dict]:
    """Enabled schedules (other than `exclude_id`) that `sched` would
    overlap, soonest clash first. `sched` is a Schedule, saved or not."""
    ctx = _Context(db)
    mine = ctx.plan(sched, rules)
    found = []
    for other_row in db.query(Schedule).filter(Schedule.enabled.is_(True)).order_by(Schedule.id).all():
        if other_row.id == exclude_id or other_row is sched:
            continue
        other = ctx.plan(other_row)
        if not mine.footprint.intersects(other.footprint):
            continue
        clash = first_clash(ctx, mine, other)
        if clash:
            found.append((clash, _describe(db, mine, other, clash)))
    return [d for _, d in sorted(found, key=lambda x: x[0])]


def refusal(conflicts: list[dict]) -> AppError:
    first = conflicts[0]
    return AppError(
        409, "schedules.overlap",
        f"It would play on the same speakers as {len(conflicts)} other schedule(s), first \"{first['name']}\" "
        f"on {first['first_clash'].replace('T', ' ')}.",
        count=len(conflicts), first_name=first["name"], first_clash=first["first_clash"],
        conflicts=conflicts[:MAX_CONFLICTS],
    )


def all_pairs(db: Session) -> list[dict]:
    """Every pair of enabled schedules that overlap (for the schedules
    list, the login alert, and the warnings of non-blocking saves)."""
    ctx = _Context(db)
    plans = [ctx.plan(s) for s in db.query(Schedule).filter(Schedule.enabled.is_(True)).order_by(Schedule.id).all()]
    pairs = []
    for i, a in enumerate(plans):
        for b in plans[i + 1:]:
            if a.footprint.intersects(b.footprint):
                clash = first_clash(ctx, a, b)
                if clash:
                    pairs.append({"a_id": a.id, "a_name": a.name, "b_id": b.id, "b_name": b.name,
                                  "first_clash": clash.strftime("%Y-%m-%dT%H:%M")})
    return pairs


def new_pairs(before: list[dict], after: list[dict]) -> list[dict]:
    seen = {(p["a_id"], p["b_id"]) for p in before}
    return [p for p in after if (p["a_id"], p["b_id"]) not in seen]
