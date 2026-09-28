"""
Lets zone membership be managed entirely from the ZoneCast dashboard
instead of by hand on each speaker's own web UI: whenever a speaker's
zones (or a zone's multicast address or name) change, this service
recomputes that speaker's multicast paging list — its own dedicated
group, one slot per zone it belongs to, and the global all-call group —
and asks that speaker's brand driver (see services/drivers/) to push it
to the device.

This module is brand-agnostic: it never talks HTTP/CGI itself, it just
computes the target paging list and delegates to whichever driver
`services/drivers/registry.py` resolves for the speaker's `brand`. If
no driver is registered for that brand, or the driver doesn't support
pushing, `push_to_speaker` returns a result flagged
`unsupported_brand=True` instead of attempting anything — callers
(the speakers router, the dashboard) show that as "configure manually
on the device" rather than a technical failure.

Why not a full auto-provisioning config resync (for brands that
support it) instead: a resync makes the device re-fetch and re-apply
an entire config file, and any setting not present in that file (SIP
account, network, etc.) risks being reset depending on firmware
behaviour — and if the device is already pointed at another
provisioning source for its SIP config, redirecting it to ZoneCast
would break that too. Writing individual `paging.*` parameters live
(what the Fanvil driver does) sidesteps both problems by construction.

Pushes go through `request_push`: at most one push per device at a time
(a Fanvil can't cope with concurrent requests), a second request while
one is running just makes it run once more afterwards with the data as
it is then — so the device always ends up with the latest list — and a
few devices at a time in parallel.
"""
import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import Speaker
from ..timeutil import utcnow
from .drivers import PagingEntry, PushResult, get_driver

logger = logging.getLogger("zonecast.multicast_provisioning")

PUSH_CONCURRENCY = 4
# Keyed by event loop like player._group_locks (tests run several loops).
_push_state: dict[tuple[int, int], dict] = {}
_push_limits: dict[int, asyncio.Semaphore] = {}


def max_zones(speaker: Speaker) -> int | None:
    """How many zones this speaker can belong to: its device's paging
    slots minus its own group and the all-call group. None = no limit
    known (no driver pushes to it: configured by hand)."""
    driver = get_driver(speaker.brand)
    if not driver or not driver.supports_multicast_push or not driver.max_paging_slots:
        return None
    return driver.max_paging_slots - 2


def compute_entries(speaker: Speaker) -> list[PagingEntry]:
    """Returns the multicast paging list this speaker should have,
    computed fresh from its current zones. Brand-agnostic. Zones keep a
    stable slot (ordered by id), so renaming one never reshuffles them."""
    entries: list[PagingEntry] = [
        PagingEntry(1, speaker.own_multicast_address, speaker.own_multicast_port, speaker.name, 1),
    ]
    for zone in speaker.zones:
        index = len(entries) + 1
        entries.append(PagingEntry(index, zone.multicast_address, zone.multicast_port, f"Zona: {zone.name}", index))
    index = len(entries) + 1
    entries.append(PagingEntry(index, settings.global_all_call_address, settings.global_all_call_port, "global", index))
    return entries


async def push_to_speaker(speaker: Speaker) -> PushResult:
    """Write this speaker's current paging list to the device via its
    brand driver. Returns `unsupported_brand=True` without attempting
    anything if no driver is registered for `speaker.brand`, or that
    driver doesn't support pushing."""
    driver = get_driver(speaker.brand)
    if not driver or not driver.supports_multicast_push:
        logger.info(
            "Speaker %s (%s, brand=%r): push automatico saltato — nessun driver con supporto push, configurare manualmente",
            speaker.id, speaker.ip_address, speaker.brand,
        )
        return PushResult(success=False, unsupported_brand=True)

    entries = compute_entries(speaker)
    result = await driver.push_multicast_config(speaker, entries)

    if result.success:
        logger.info("Multicast paging list applied to speaker %s (%s)", speaker.id, speaker.ip_address)
    else:
        logger.warning(
            "Speaker %s (%s): %d/%d parametri applicati, falliti: %s",
            speaker.id, speaker.ip_address,
            len(result.applied_keys), len(result.applied_keys) + len(result.failed_keys),
            ", ".join(result.failed_keys),
        )
    return result


def record_outcome(db, speaker_id: int, result: PushResult) -> None:
    """Stores how the last push went, for the dashboard's "not in sync"
    badge and the login alert."""
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        return
    if result.unsupported_brand:
        speaker.paging_sync_ok, speaker.paging_sync_error = None, None
    elif result.success:
        speaker.paging_sync_ok, speaker.paging_sync_error = True, None
    else:
        speaker.paging_sync_ok = False
        speaker.paging_sync_error = "; ".join(result.failed_keys)[:500] or "push failed"
    speaker.paging_synced_at = utcnow()
    db.commit()


async def _push_and_record(speaker_id: int) -> None:
    from ..database import SessionLocal

    db = SessionLocal(expire_on_commit=False)
    try:
        speaker = db.query(Speaker).options(selectinload(Speaker.zones)).filter(Speaker.id == speaker_id).first()
        # Hand the connection back before the (seconds long) device
        # round trip — see scheduler._check_all_speakers_job.
        db.commit()
        if not speaker:
            return
        result = await push_to_speaker(speaker)
        record_outcome(db, speaker_id, result)
    except Exception:
        db.rollback()
        logger.exception("Push della configurazione multicast all'altoparlante %s non riuscito", speaker_id)
    finally:
        db.close()


async def request_push(speaker_id: int) -> None:
    """Brings the device's paging list in line with the database, see
    the module docstring for the one-at-a-time/latest-wins rules."""
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, speaker_id)
    state = _push_state.get(key)
    if state is not None:
        state["dirty"] = True  # the running push loop goes round once more
        return
    state = _push_state[key] = {"dirty": True}
    limit = _push_limits.setdefault(loop_id, asyncio.Semaphore(PUSH_CONCURRENCY))
    try:
        while state["dirty"]:
            state["dirty"] = False
            async with limit:
                await _push_and_record(speaker_id)
    finally:
        _push_state.pop(key, None)


async def request_pushes(speaker_ids: Iterable[int]) -> None:
    """Background-task entry point for the routers: each speaker once."""
    await asyncio.gather(*(request_push(sid) for sid in dict.fromkeys(speaker_ids)))
