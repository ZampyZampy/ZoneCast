from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user
from ..models import Speaker, TargetType, Zone, User
from ..schemas import ZoneCreate, ZoneOut
from ..services import multicast_provisioning, references
from ..services.multicast_addressing import MulticastAddressConflict, check_address_available

router = APIRouter(prefix="/api/zones", tags=["zones"])

_PAGING_RELEVANT_FIELDS = {"multicast_address", "multicast_port"}


@router.get("", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Zone).order_by(Zone.name).all()


@router.post("", response_model=ZoneOut)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.query(Zone).filter(Zone.name == payload.name).first():
        raise AppError(400, "zones.name_taken", "A zone with this name already exists.")
    try:
        check_address_available(db, payload.multicast_address, payload.multicast_port)
    except MulticastAddressConflict as exc:
        raise exc.http(400) from exc
    zone = Zone(**payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@router.put("/{zone_id}", response_model=ZoneOut)
def update_zone(
    zone_id: int,
    payload: ZoneCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    zone = db.query(Zone).filter(Zone.id == zone_id).first()
    if not zone:
        raise AppError(404, "zones.not_found", "Zone not found.")
    new_values = payload.model_dump()
    paging_changed = any(getattr(zone, f) != new_values[f] for f in _PAGING_RELEVANT_FIELDS)
    if paging_changed:
        try:
            check_address_available(
                db, new_values["multicast_address"], new_values["multicast_port"], exclude_zone_id=zone.id,
            )
        except MulticastAddressConflict as exc:
            raise exc.http(400) from exc
    for k, v in new_values.items():
        setattr(zone, k, v)
    db.commit()
    db.refresh(zone)
    if paging_changed:
        # Every speaker currently in this zone needs its paging list
        # (which embeds the zone's own multicast address) re-pushed.
        background_tasks.add_task(multicast_provisioning.push_to_zone_speakers, zone.id)
    return zone


@router.delete("/{zone_id}")
def delete_zone(
    zone_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    zone = db.query(Zone).filter(Zone.id == zone_id).first()
    if not zone:
        raise AppError(404, "zones.not_found", "Zone not found.")
    references.refuse_if_used(references.schedules_targeting(db, TargetType.zone, zone_id), "zones.in_use", "This zone")
    member_ids = [s.id for s in db.query(Speaker).filter(Speaker.zone_id == zone_id).all()]
    db.query(Speaker).filter(Speaker.zone_id == zone_id).update({Speaker.zone_id: None})
    db.delete(zone)
    db.commit()
    # The devices still listen on the deleted zone's group until told
    # otherwise — and that address can now be reused by a new zone.
    for speaker_id in member_ids:
        background_tasks.add_task(multicast_provisioning.push_to_speaker_id, speaker_id)
    return {"ok": True}
