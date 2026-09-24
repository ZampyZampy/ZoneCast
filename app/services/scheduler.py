"""
Scheduled playback engine. Each enabled Schedule row becomes one
APScheduler cron job (fires at time_of_day on the configured weekdays);
the job itself re-checks the holiday rule (for the schedule's own
holiday_country, see HOLIDAY_COUNTRIES below) and the start/end date
window at run time, using the `holidays` library, so editing a
schedule's holiday behaviour doesn't require recomputing a fixed list
of future dates.
"""
import logging
from datetime import date, datetime

import holidays
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import settings
from ..database import SessionLocal
from ..models import Schedule, PlaybackSource, Speaker
from . import player, event_log, speaker_status
from .app_settings import get_settings

logger = logging.getLogger("zonecast.scheduler")

_scheduler: AsyncIOScheduler | None = None

# One country per supported UI language (app/static/js/i18n.js), plus a
# couple of common extras for "English" since it doesn't map to a single
# country. Keys are ISO 3166-1 alpha-2 codes the `holidays` package
# recognizes via holidays.country_holidays(); Schedule.holiday_country
# stores one of these, defaulting to "IT".
HOLIDAY_COUNTRIES = {
    "IT": "Italy", "US": "United States", "GB": "United Kingdom",
    "FR": "France", "DE": "Germany", "ES": "Spain", "JP": "Japan", "CN": "China",
}
_holiday_calendars: dict[str, "holidays.HolidayBase"] = {}


def _job_id(schedule_id: int) -> str:
    return f"schedule-{schedule_id}"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    return _scheduler


def _is_holiday(d: date, country_code: str) -> bool:
    calendar = _holiday_calendars.get(country_code)
    if calendar is None:
        calendar = holidays.country_holidays(country_code if country_code in HOLIDAY_COUNTRIES else "IT")
        _holiday_calendars[country_code] = calendar
    return d in calendar


async def _run_schedule(schedule_id: int):
    db = SessionLocal()
    try:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not sched or not sched.enabled:
            return

        today = datetime.now().date()
        if sched.start_date and today < sched.start_date:
            return
        if sched.end_date and today > sched.end_date:
            return

        holiday_today = _is_holiday(today, sched.holiday_country)
        if sched.exclude_holidays and holiday_today:
            logger.info("Schedule %s skipped: today is a %s holiday", schedule_id, sched.holiday_country)
            return
        if sched.holidays_only and not holiday_today:
            logger.info("Schedule %s skipped: runs only on holidays", schedule_id)
            return

        logger.info("Running schedule %s (%s)", schedule_id, sched.name)
        await player.play(
            db,
            media_id=sched.media_id,
            target_type=sched.target_type,
            target_id=sched.target_id,
            source=PlaybackSource.schedule,
            schedule_id=sched.id,
        )
    except Exception:
        logger.exception("Failed running schedule %s", schedule_id)
    finally:
        db.close()


def add_or_update_job(schedule: Schedule):
    sched_engine = get_scheduler()
    job_id = _job_id(schedule.id)
    sched_engine.remove_job(job_id) if sched_engine.get_job(job_id) else None

    if not schedule.enabled:
        return

    trigger = CronTrigger(
        day_of_week=schedule.days_of_week,
        hour=schedule.time_of_day.hour,
        minute=schedule.time_of_day.minute,
        second=schedule.time_of_day.second,
        timezone=settings.timezone,
    )
    sched_engine.add_job(
        _run_schedule,
        trigger=trigger,
        id=job_id,
        args=[schedule.id],
        replace_existing=True,
        misfire_grace_time=300,
    )


def remove_job(schedule_id: int):
    sched_engine = get_scheduler()
    job_id = _job_id(schedule_id)
    if sched_engine.get_job(job_id):
        sched_engine.remove_job(job_id)


def load_all_schedules():
    db = SessionLocal()
    try:
        for sched in db.query(Schedule).filter(Schedule.enabled.is_(True)).all():
            add_or_update_job(sched)
        logger.info("Loaded %d active schedules", len(get_scheduler().get_jobs()))
    finally:
        db.close()


async def _check_all_speakers_job():
    db = SessionLocal()
    try:
        speakers = db.query(Speaker).all()
        for sp in speakers:
            try:
                await speaker_status.check_and_update(db, sp)
            except Exception:
                logger.exception("Controllo raggiungibilità fallito per l'altoparlante %s", sp.id)
    finally:
        db.close()


def _prune_logs_job():
    db = SessionLocal()
    try:
        retention_days = get_settings(db).log_retention_days
        if retention_days is None:
            return  # "mai" — keep everything (row-count safety net still applies)
        deleted = event_log.prune_by_age(db, retention_days)
        if deleted:
            logger.info("Pulizia log automatica: rimosse %d righe più vecchie di %d giorni", deleted, retention_days)
    finally:
        db.close()


def start():
    sched_engine = get_scheduler()
    if not sched_engine.running:
        load_all_schedules()
        sched_engine.add_job(_prune_logs_job, trigger="cron", hour=3, minute=30, id="log-retention-prune", replace_existing=True)
        sched_engine.add_job(
            _check_all_speakers_job, trigger="interval",
            minutes=speaker_status.CHECK_INTERVAL_MINUTES,
            id="speaker-status-check", replace_existing=True,
            next_run_time=datetime.now(),  # also run once immediately at startup
        )
        sched_engine.start()


def shutdown():
    sched_engine = get_scheduler()
    if sched_engine.running:
        sched_engine.shutdown(wait=False)
