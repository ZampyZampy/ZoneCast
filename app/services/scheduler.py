"""
Scheduled playback engine. Each enabled Schedule row becomes one
APScheduler cron job (fires at time_of_day on the configured weekdays);
the job itself re-checks the holiday rule (for the schedule's own
holiday_country, see HOLIDAY_COUNTRIES below) and the start/end date
window at run time, using the `holidays` library, so editing a
schedule's holiday behaviour doesn't require recomputing a fixed list
of future dates.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

import holidays
from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import settings
from ..database import SessionLocal
from ..models import Schedule, PlaybackSource, Speaker
from . import calendars, player, event_log, multicast_provisioning, speaker_status
from .app_settings import get_settings

logger = logging.getLogger("zonecast.scheduler")

_scheduler: AsyncIOScheduler | None = None

# A bell due during a live announcement on some of the same speakers waits
# for it to end (up to this long) rather than cutting in or being lost.
LIVE_WAIT_SECONDS = 60

# One country per supported UI language (app/static/js/i18n.js), plus a
# couple of common extras for "English" since it doesn't map to a single
# country. Keys are ISO 3166-1 alpha-2 codes the `holidays` package
# recognizes via holidays.country_holidays(); Schedule.holiday_country
# stores one of these — holiday filtering itself is opt-in per schedule
# (see exclude_holidays/holidays_only on the model), so no country is
# assumed unless a schedule explicitly turns it on.
HOLIDAY_COUNTRIES = {
    "IT": "Italy", "US": "United States", "GB": "United Kingdom",
    "FR": "France", "DE": "Germany", "ES": "Spain", "JP": "Japan", "CN": "China",
}
_holiday_calendars: dict[str, "holidays.HolidayBase"] = {}


def _job_id(schedule_id: int) -> str:
    return f"schedule-{schedule_id}"


_timezone: str | None = None


def scheduler_timezone() -> str:
    """Timezone schedules fire in: the one chosen from Sistema together
    with the host timezone (app_settings.scheduler_timezone), else
    TIMEZONE from .env — see routers/system.py's set_timezone."""
    global _timezone
    if _timezone is None:
        db = SessionLocal()
        try:
            _timezone = get_settings(db).scheduler_timezone or settings.timezone
        finally:
            db.close()
    return _timezone


def set_timezone(tz: str) -> None:
    """Re-registers every schedule in `tz` (the host timezone was just
    changed from Sistema) so bells keep ringing at the wall-clock time
    shown in the dashboard."""
    global _timezone
    _timezone = tz
    load_all_schedules()
    _register_backup_job()
    logger.info("Fuso orario delle schedulazioni impostato a %s", tz)


def _register_backup_job(startup: bool = False) -> None:
    from . import auto_backup

    try:
        auto_backup.register_job(startup=startup)
    except Exception:
        logger.exception("Backup automatico: impossibile programmare il job")


def wakeup() -> None:
    """The scheduler sleeps on the monotonic clock until the next job:
    after the wall clock is stepped (manual time, NTP toggle) it must
    recompute that, or a bell can fire minutes late or be missed."""
    if _scheduler is not None and _scheduler.running:
        _scheduler.wakeup()


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=scheduler_timezone())
    return _scheduler


def _is_holiday(d: date, country_code: str) -> bool:
    # An unrecognized code (e.g. stale data from before a country was
    # supported) means "no calendar to check", not "assume Italy" —
    # holiday filtering is opt-in and country-specific, never a
    # fallback to any one country.
    if country_code not in HOLIDAY_COUNTRIES:
        return False
    calendar = _holiday_calendars.get(country_code)
    if calendar is None:
        calendar = holidays.country_holidays(country_code)
        _holiday_calendars[country_code] = calendar
    return d in calendar


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@lru_cache(maxsize=256)
def weekday_set(days_of_week: str) -> frozenset[int]:
    """Weekday numbers (0 = Monday) a days_of_week value means. Saved
    values are "mon,tue,..."; rows from before 1.5.8 may hold anything
    the cron trigger accepts ("mon-fri", "*", "0-4"), so those are read
    with the trigger's own parser — the job fires on those days and must
    not then skip itself."""
    parts = {p.strip().lower() for p in days_of_week.split(",") if p.strip()}
    if parts and parts <= set(WEEKDAYS):
        return frozenset(WEEKDAYS.index(p) for p in parts)
    try:
        trigger = CronTrigger(day_of_week=days_of_week, hour=0, minute=0, second=0, timezone="UTC")
    except ValueError:
        return frozenset()
    found, moment = set(), datetime(2024, 1, 1, tzinfo=ZoneInfo("UTC"))  # a Monday
    for _ in range(7):
        fire = trigger.get_next_fire_time(None, moment)
        if fire is None or (fire - datetime(2024, 1, 1, tzinfo=ZoneInfo("UTC"))).days >= 7:
            break
        found.add(fire.weekday())
        moment = fire + timedelta(days=1)
    return frozenset(found)


def skip_reason(sched, day: date, rules: tuple = ()) -> Optional[str]:
    """Why `sched` doesn't run on `day` — or None if it does. The one
    definition of "runs that day", used by the job itself and by the
    overlap check (services/overlap.py). `sched` is a Schedule or
    anything with the same attributes; `rules` its custom-dates rules
    (calendars.rules_for)."""
    if sched.start_date and day < sched.start_date:
        return "before_start"
    if sched.end_date and day > sched.end_date:
        return "after_end"
    if day.weekday() not in weekday_set(sched.days_of_week or ""):
        return "weekday"
    if sched.exclude_holidays or sched.holidays_only:
        holiday = _is_holiday(day, sched.holiday_country)
        if sched.exclude_holidays and holiday:
            return "holiday"
        if sched.holidays_only and not holiday:
            return "not_holiday"
    return calendars.rules_allow(rules, day)


def fire_date(time_of_day, now: datetime) -> date:
    """The calendar day a run belongs to. A 23:58 bell started late (up
    to misfire_grace_time) after midnight is still that evening's bell:
    its date range, holidays and custom dates are those of the day before."""
    return now.date() if now.time() >= time_of_day else now.date() - timedelta(days=1)


async def _run_schedule(schedule_id: int):
    db = SessionLocal()
    try:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not sched or not sched.enabled:
            return

        # The cron trigger fires in scheduler_timezone(), which can differ
        # from the host/container clock — judge the date range, holidays
        # and custom dates on that same calendar day.
        day = fire_date(sched.time_of_day, datetime.now(ZoneInfo(scheduler_timezone())))
        reason = skip_reason(sched, day, calendars.schedule_rules(db, sched.id))
        if reason:
            logger.info("Schedule %s skipped on %s: %s", schedule_id, day, reason)
            return

        logger.info("Running schedule %s (%s)", schedule_id, sched.name)

        async def start():
            await player.play(
                db,
                media_id=sched.media_id,
                target_type=sched.target_type,
                target_id=sched.target_id,
                source=PlaybackSource.schedule,
                schedule_id=sched.id,
            )

        try:
            try:
                await start()
            except player.GroupBusyError as busy:
                logger.info("Schedulazione %s (%s): annuncio live in corso, attendo che finisca", schedule_id, sched.name)
                loop = asyncio.get_running_loop()
                waited_from = loop.time()
                deadline = waited_from + LIVE_WAIT_SECONDS
                # Keep waiting while live announcements follow one another
                # on these speakers, up to the deadline.
                while True:
                    db.commit()  # don't hold a pooled connection while waiting
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise busy
                    await player.wait_for(busy.params.get("log_ids", []), timeout=remaining)
                    try:
                        await start()
                        break
                    except player.GroupBusyError as again:
                        busy = again
                logger.info("Schedulazione %s (%s) eseguita con %.0f s di ritardo", schedule_id, sched.name,
                            loop.time() - waited_from)
        except (player.TargetResolutionError, player.GroupBusyError) as exc:
            # e.g. its audio file or zone was deleted, or the announcement
            # went on too long: recorded as a failed run so the login alert
            # and the history show it.
            logger.warning("Schedulazione %s (%s) non eseguita: %s", schedule_id, sched.name, exc)
            player.record_failed_run(db, sched, str(exc))
    except Exception:
        logger.exception("Failed running schedule %s", schedule_id)
    finally:
        db.close()


def _on_job_missed(event) -> None:
    """A schedule's run time passed without it firing (the service was
    busy or the clock was stepped past it beyond misfire_grace_time)."""
    if not event.job_id.startswith("schedule-"):
        return
    schedule_id = int(event.job_id.removeprefix("schedule-"))
    db = SessionLocal()
    try:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if sched:
            logger.warning("Schedulazione %s (%s) saltata: orario previsto %s", schedule_id, sched.name, event.scheduled_run_time)
            player.record_failed_run(db, sched, f"Saltata: non eseguita all'orario previsto ({event.scheduled_run_time:%Y-%m-%d %H:%M})")
    except Exception:
        logger.exception("Registrazione della schedulazione saltata %s non riuscita", schedule_id)
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
        timezone=scheduler_timezone(),
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
            # One unloadable row (e.g. saved before days_of_week was
            # validated) must not take the whole PA scheduler down with it.
            try:
                add_or_update_job(sched)
            except Exception:
                logger.exception("Schedulazione %s (%s) non caricata: configurazione non valida", sched.id, sched.name)
        logger.info("Loaded %d active schedules", len([j for j in get_scheduler().get_jobs() if j.id.startswith("schedule-")]))
    finally:
        db.close()


# Well below the DB connection pool (5 + 10 overflow for SQLite).
SWEEP_CONCURRENCY = 8


async def _check_all_speakers_job():
    """Checks run concurrently (one at a time, 150 offline speakers x 2 s
    timeout would overrun the 5-minute interval) and each in its own
    session: a speaker deleted mid-sweep fails only its own check."""
    db = SessionLocal()
    try:
        speaker_ids = [sid for (sid,) in db.query(Speaker.id).all()]
    finally:
        db.close()
    limit = asyncio.Semaphore(SWEEP_CONCURRENCY)
    # Devices that missed their last paging-list push (offline, rebooting)
    # get it again as soon as they answer.
    to_resync: list[int] = []

    async def check(speaker_id: int) -> None:
        async with limit:
            session = SessionLocal(expire_on_commit=False)
            try:
                speaker = session.query(Speaker).filter(Speaker.id == speaker_id).first()
                # Hand the connection back before waiting on the network:
                # held across a 2 s probe, a handful of checks would empty
                # the pool and stall every other DB user — bells included.
                session.commit()
                if speaker and await speaker_status.check_and_update(session, speaker) and speaker.paging_sync_ok is False:
                    to_resync.append(speaker_id)
            except Exception:
                session.rollback()
                logger.exception("Controllo raggiungibilità fallito per l'altoparlante %s", speaker_id)
            finally:
                session.close()

    await asyncio.gather(*(check(sid) for sid in speaker_ids))
    if to_resync:
        await multicast_provisioning.request_pushes(to_resync)


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
        _register_backup_job(startup=True)
        sched_engine.add_job(_prune_logs_job, trigger="cron", hour=3, minute=30, id="log-retention-prune", replace_existing=True)
        sched_engine.add_job(
            _check_all_speakers_job, trigger="interval",
            minutes=speaker_status.CHECK_INTERVAL_MINUTES,
            id="speaker-status-check", replace_existing=True,
            next_run_time=datetime.now(ZoneInfo(scheduler_timezone())),  # also run once immediately at startup
        )
        sched_engine.add_listener(_on_job_missed, EVENT_JOB_MISSED)
        sched_engine.start()


def shutdown():
    sched_engine = get_scheduler()
    if sched_engine.running:
        sched_engine.shutdown(wait=False)
