"""
Lets zone membership be managed entirely from the ZoneCast dashboard
instead of by hand on each speaker's own web UI: whenever a speaker's
zone (or a zone's multicast address) changes, this service recomputes
that speaker's multicast paging list — its own dedicated group, its
zone's group, and the global all-call group — and asks that speaker's
brand driver (see services/drivers/) to push it to the device.

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
"""
import logging

from ..config import settings
from ..models import Speaker
from .drivers import PagingEntry, PushResult, get_driver

logger = logging.getLogger("zonecast.multicast_provisioning")


def compute_entries(speaker: Speaker) -> list[PagingEntry]:
    """Returns the multicast paging list this speaker should have,
    computed fresh from its current zone assignment. Brand-agnostic."""
    entries: list[PagingEntry] = [
        PagingEntry(1, speaker.own_multicast_address, speaker.own_multicast_port, speaker.name, 1),
    ]
    next_index = 2
    if speaker.zone is not None:
        entries.append(PagingEntry(
            next_index, speaker.zone.multicast_address, speaker.zone.multicast_port,
            f"Zona: {speaker.zone.name}", 2,
        ))
        next_index += 1
    entries.append(PagingEntry(
        next_index, settings.global_all_call_address, settings.global_all_call_port,
        "global", 3,
    ))
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


async def push_to_speaker_id(speaker_id: int) -> PushResult:
    """Background-task-friendly variant that opens its own DB session."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
        if not speaker:
            return PushResult(success=False, failed_keys=["speaker non trovato"])
        return await push_to_speaker(speaker)
    finally:
        db.close()


async def push_to_zone_speakers(zone_id: int) -> None:
    """Push the (unchanged) speaker-level paging list to every speaker
    in a zone after the zone's own multicast address/port changed."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        speaker_ids = [s.id for s in db.query(Speaker).filter(Speaker.zone_id == zone_id).all()]
    finally:
        db.close()
    for speaker_id in speaker_ids:
        await push_to_speaker_id(speaker_id)
