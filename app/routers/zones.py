from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Zone, User
from ..schemas import ZoneCreate, ZoneOut
from ..services import multicast_provisioning
from ..services.multicast_addressing import MulticastAddressConflict, check_address_available

router = APIRouter(prefix="/api/zones", tags=["zones"])

_PAGING_RELEVANT_FIELDS = {"multicast_address", "multicast_port"}


@router.get("", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Zone).order_by(Zone.name).all()


@router.post("", response_model=ZoneOut)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.query(Zone).filter(Zone.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Zona già esistente")
    try:
        check_address_available(db, payload.multicast_address, payload.multicast_port)
    except MulticastAddressConflict as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        raise HTTPException(status_code=404, detail="Zona non trovata")
    new_values = payload.model_dump()
    paging_changed = any(getattr(zone, f) != new_values[f] for f in _PAGING_RELEVANT_FIELDS)
    if paging_changed:
        try:
            check_address_available(
                db, new_values["multicast_address"], new_values["multicast_port"], exclude_zone_id=zone.id,
            )
        except MulticastAddressConflict as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
def delete_zone(zone_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    zone = db.query(Zone).filter(Zone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zona non trovata")
    db.delete(zone)
    db.commit()
    return {"ok": True}
