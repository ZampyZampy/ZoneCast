import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user
from ..models import Speaker, TargetType, Zone, User
from ..schemas import ZoneCreate, ZoneOut
from ..services import multicast_provisioning, overlap, references
from ..services.multicast_addressing import MulticastAddressConflict, check_address_available

router = APIRouter(prefix="/api/zones", tags=["zones"])
logger = logging.getLogger("zonecast.zones")

_ADDRESS_FIELDS = {"multicast_address", "multicast_port"}
# What the members' devices store for this zone: its group, and its name
# as the slot label.
_PAGING_RELEVANT_FIELDS = _ADDRESS_FIELDS | {"name"}


@router.get("", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Zone).options(selectinload(Zone.speakers)).order_by(Zone.name).all()


def _load_members(db: Session, speaker_ids: list[int]) -> list[Speaker]:
    ids = list(dict.fromkeys(speaker_ids))
    speakers = db.query(Speaker).options(selectinload(Speaker.zones)).filter(Speaker.id.in_(ids)).all() if ids else []
    missing = sorted(set(ids) - {s.id for s in speakers})
    if missing:
        raise AppError(422, "zones.speaker_missing", "Some of the selected speakers no longer exist.",
                       ids=", ".join(map(str, missing)))
    return speakers


def _check_zone_limit(zone_id: int | None, added: list[Speaker]) -> None:
    """A device listens to a bounded number of multicast groups (20 on a
    Fanvil): refuse a membership that couldn't be written to it."""
    for speaker in added:
        limit = multicast_provisioning.max_zones(speaker)
        if limit is not None and len([z for z in speaker.zones if z.id != zone_id]) + 1 > limit:
            raise AppError(422, "zones.speaker_zone_limit",
                           f"{speaker.name} is already in {limit} zones, the most its device can listen to.",
                           name=speaker.name, max=limit)


def _log_membership(zone: Zone, before: dict[int, str], after: dict[int, str], user: User) -> None:
    added = [after[i] for i in after.keys() - before.keys()]
    removed = [before[i] for i in before.keys() - after.keys()]
    if added or removed:
        logger.info("Zona '%s': aggiunti [%s], rimossi [%s] (utente %s)",
                    zone.name, ", ".join(sorted(added)), ", ".join(sorted(removed)), user.username)


@router.post("", response_model=ZoneOut)
def create_zone(
    payload: ZoneCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.query(Zone).filter(Zone.name == payload.name).first():
        raise AppError(400, "zones.name_taken", "A zone with this name already exists.")
    try:
        check_address_available(db, payload.multicast_address, payload.multicast_port)
    except MulticastAddressConflict as exc:
        raise exc.http(400) from exc
    zone = Zone(**payload.model_dump(exclude={"speaker_ids"}))
    members = _load_members(db, payload.speaker_ids or [])
    _check_zone_limit(None, members)
    zone.speakers = members
    db.add(zone)
    multicast_provisioning.mark_pending(db, [s.id for s in members])
    db.commit()
    db.refresh(zone)
    _log_membership(zone, {}, {s.id: s.name for s in zone.speakers}, user)
    if members:
        background_tasks.add_task(multicast_provisioning.request_pushes, [s.id for s in members])
    return zone


@router.put("/{zone_id}", response_model=ZoneOut)
def update_zone(
    zone_id: int,
    payload: ZoneCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    zone = db.query(Zone).options(selectinload(Zone.speakers)).filter(Zone.id == zone_id).first()
    if not zone:
        raise AppError(404, "zones.not_found", "Zone not found.")
    new_values = payload.model_dump(exclude={"speaker_ids"})
    if db.query(Zone.id).filter(Zone.name == new_values["name"], Zone.id != zone.id).first():
        raise AppError(400, "zones.name_taken", "A zone with this name already exists.")
    if any(getattr(zone, f) != new_values[f] for f in _ADDRESS_FIELDS):
        try:
            check_address_available(
                db, new_values["multicast_address"], new_values["multicast_port"], exclude_zone_id=zone.id,
            )
        except MulticastAddressConflict as exc:
            raise exc.http(400) from exc
    before = {s.id: s.name for s in zone.speakers}
    members = None
    if payload.speaker_ids is not None:
        members = _load_members(db, payload.speaker_ids)
        _check_zone_limit(zone.id, [s for s in members if s.id not in before])
    paging_changed = any(getattr(zone, f) != new_values[f] for f in _PAGING_RELEVANT_FIELDS)
    warnings = []
    with overlap.SAVE_LOCK:
        # New members can make schedules on this zone overlap others on
        # those speakers: saved anyway (blocking could stop the very fix),
        # but reported.
        membership_changes = members is not None and {s.id for s in members} != before.keys()
        pairs_before = overlap.all_pairs(db) if membership_changes else []
        for k, v in new_values.items():
            setattr(zone, k, v)
        if members is not None:
            zone.speakers = members
        if membership_changes:
            db.flush()
            warnings = overlap.new_pairs(pairs_before, overlap.all_pairs(db))
        # Members that joined or left need their paging list rewritten;
        # all of them when what their devices store for this zone changed.
        after_ids = {s.id for s in members} if members is not None else set(before)
        targets = (before.keys() | after_ids) if paging_changed else (before.keys() ^ after_ids)
        multicast_provisioning.mark_pending(db, targets)
        db.commit()
    db.refresh(zone)
    zone.warnings = warnings
    after = {s.id: s.name for s in zone.speakers}
    _log_membership(zone, before, after, user)
    if targets:
        background_tasks.add_task(multicast_provisioning.request_pushes, sorted(targets))
    return zone


@router.delete("/{zone_id}")
def delete_zone(
    zone_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    zone = db.query(Zone).filter(Zone.id == zone_id).first()
    if not zone:
        raise AppError(404, "zones.not_found", "Zone not found.")
    references.refuse_if_used(references.schedules_targeting(db, TargetType.zone, zone_id), "zones.in_use", "This zone")
    member_ids, name = zone.speaker_ids, zone.name
    zone.speakers = []
    db.delete(zone)
    multicast_provisioning.mark_pending(db, member_ids)
    db.commit()
    logger.info("Zona '%s' eliminata (utente %s)", name, user.username)
    # The devices still listen on the deleted zone's group until told
    # otherwise — and that address can now be reused by a new zone.
    if member_ids:
        background_tasks.add_task(multicast_provisioning.request_pushes, member_ids)
    return {"ok": True}
