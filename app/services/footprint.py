"""
Which speakers a destination actually reaches. Two streams (or two
schedules) collide when they share a speaker — a speaker in several
zones, a single speaker inside a zone, anything against "all" — or
when they use the same multicast group, even an empty zone's: two RTP
flows on one group interleave into noise.

Used by the player (live-announcement conflicts, schedules yielding to a
live announcement) and by the schedule overlap check.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Speaker, TargetType, zone_members


@dataclass(frozen=True)
class Footprint:
    speakers: frozenset[int]
    group: tuple

    def intersects(self, other: "Footprint") -> bool:
        return self.group == other.group or not self.speakers.isdisjoint(other.speakers)


def footprint(db: Session, target_type: TargetType, target_id: Optional[int]) -> Footprint:
    """Never raises for a target that no longer exists (legacy schedules
    can point at a deleted zone or speaker): it just reaches no one."""
    target_type = TargetType(target_type)
    if target_type == TargetType.all:
        return Footprint(frozenset(db.scalars(select(Speaker.id)).all()), ("all",))
    if target_type == TargetType.zone:
        members = db.scalars(select(zone_members.c.speaker_id).where(zone_members.c.zone_id == target_id)).all()
        return Footprint(frozenset(members), ("zone", target_id))
    exists = db.scalar(select(Speaker.id).where(Speaker.id == target_id))
    return Footprint(frozenset({exists}) if exists is not None else frozenset(), ("speaker", target_id))
