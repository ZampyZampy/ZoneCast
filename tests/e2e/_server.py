"""ZoneCast for browser tests: the real app, with device I/O replaced so
no request ever reaches a LAN host (settings come from the environment
set by tests/e2e/conftest.py; RTP_MULTICAST_TTL=0 keeps audio local)."""
import os
from datetime import datetime

from app.models import SpeakerStatus
from app.services import multicast_provisioning, speaker_status
from app.services.drivers.base import PushResult


async def _fake_check(db, speaker):
    speaker.status = SpeakerStatus.online
    speaker.last_seen = datetime.utcnow()
    db.commit()
    return True


async def _noop(*_args, **_kwargs):
    return None


async def _fake_push(speaker):
    return PushResult(success=True)


speaker_status.check_and_update = _fake_check
multicast_provisioning.push_to_speaker_id = _noop
multicast_provisioning.push_to_zone_speakers = _noop
multicast_provisioning.push_to_speaker = _fake_push

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ["E2E_PORT"]), log_level="warning")
