"""
Prevents two things from ending up listening for (or being configured
to expect) the same multicast group: a speaker's own dedicated address,
a zone's shared address, and the global all-call address must all be
distinct pairs of (address, port) — otherwise unrelated audio would
overlap on the wire and/or a device's paging list would end up with
two entries pointing at the same group.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Speaker, Zone


class MulticastAddressConflict(ValueError):
    pass


def _key(address: str, port: int) -> tuple[str, int]:
    return address.strip(), port


def check_address_available(
    db: Session,
    address: str,
    port: int,
    *,
    exclude_speaker_id: int | None = None,
    exclude_zone_id: int | None = None,
) -> None:
    """Raises MulticastAddressConflict if (address, port) is already in
    use by the global all-call channel, another speaker, or another
    zone. Pass exclude_speaker_id/exclude_zone_id when validating an
    update, so a record doesn't conflict with its own current value."""
    key = _key(address, port)

    if key == _key(settings.global_all_call_address, settings.global_all_call_port):
        raise MulticastAddressConflict(
            f"{address}:{port} è riservato al canale globale (all-call) — usa un altro indirizzo"
        )

    speakers_q = db.query(Speaker)
    if exclude_speaker_id is not None:
        speakers_q = speakers_q.filter(Speaker.id != exclude_speaker_id)
    for speaker in speakers_q.all():
        if _key(speaker.own_multicast_address, speaker.own_multicast_port) == key:
            raise MulticastAddressConflict(
                f"{address}:{port} è già usato dall'altoparlante '{speaker.name}'"
            )

    zones_q = db.query(Zone)
    if exclude_zone_id is not None:
        zones_q = zones_q.filter(Zone.id != exclude_zone_id)
    for zone in zones_q.all():
        if _key(zone.multicast_address, zone.multicast_port) == key:
            raise MulticastAddressConflict(
                f"{address}:{port} è già usato dalla zona '{zone.name}'"
            )
