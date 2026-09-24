"""
Shared online/offline check for a speaker — used by the manual "Ping"
button (routers/speakers.py), right after a speaker is created, and by
the periodic background check (services/scheduler.py's
_check_all_speakers_job, every CHECK_INTERVAL_MINUTES).
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Speaker, SpeakerStatus
from .drivers import generic_check_reachable, get_driver

CHECK_INTERVAL_MINUTES = 5

logger = logging.getLogger("zonecast.speakers")


async def check_and_update(db: Session, speaker: Speaker) -> bool:
    driver = get_driver(speaker.brand)
    reachable = await (driver.check_reachable(speaker) if driver else generic_check_reachable(speaker))
    previous = speaker.status
    speaker.status = SpeakerStatus.online if reachable else SpeakerStatus.offline
    if reachable:
        speaker.last_seen = datetime.utcnow()
    db.commit()
    if previous != speaker.status:
        logger.info("Altoparlante '%s' (%s): %s -> %s", speaker.name, speaker.ip_address, previous.value, speaker.status.value)
    return reachable
