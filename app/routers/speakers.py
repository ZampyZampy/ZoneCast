import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import Speaker, SpeakerConfigBackup, User
from ..schemas import SpeakerCreate, SpeakerUpdate, SpeakerOut, SpeakerBackupOut
from ..services import multicast_provisioning, speaker_status
from ..services.drivers import ConfigExportError, get_driver
from ..services.multicast_addressing import MulticastAddressConflict, check_address_available

router = APIRouter(prefix="/api/speakers", tags=["speakers"])

# Fields that change what this speaker's multicast paging list should
# contain — only these trigger an automatic push to the device.
_PAGING_RELEVANT_FIELDS = {"zone_id", "own_multicast_address", "own_multicast_port", "paging_volume"}


@router.get("", response_model=list[SpeakerOut])
def list_speakers(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Speaker).order_by(Speaker.name).all()


@router.post("", response_model=SpeakerOut)
def create_speaker(
    payload: SpeakerCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if db.query(Speaker).filter(Speaker.ip_address == payload.ip_address).first():
        raise HTTPException(status_code=400, detail="IP già registrato")
    try:
        check_address_available(db, payload.own_multicast_address, payload.own_multicast_port)
    except MulticastAddressConflict as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    speaker = Speaker(**payload.model_dump())
    db.add(speaker)
    db.commit()
    db.refresh(speaker)
    background_tasks.add_task(multicast_provisioning.push_to_speaker_id, speaker.id)
    background_tasks.add_task(_check_reachability_by_id, speaker.id)
    return speaker


async def _check_reachability_by_id(speaker_id: int) -> None:
    """Background-task wrapper: opens its own DB session, since this
    runs after the request's own session has already been closed —
    same pattern as multicast_provisioning.push_to_speaker_id."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
        if speaker:
            await speaker_status.check_and_update(db, speaker)
    finally:
        db.close()


@router.put("/{speaker_id}", response_model=SpeakerOut)
def update_speaker(
    speaker_id: int,
    payload: SpeakerUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    changed_fields = payload.model_dump(exclude_unset=True)
    if "own_multicast_address" in changed_fields or "own_multicast_port" in changed_fields:
        new_address = changed_fields.get("own_multicast_address", speaker.own_multicast_address)
        new_port = changed_fields.get("own_multicast_port", speaker.own_multicast_port)
        try:
            check_address_available(db, new_address, new_port, exclude_speaker_id=speaker.id)
        except MulticastAddressConflict as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for k, v in changed_fields.items():
        setattr(speaker, k, v)
    db.commit()
    db.refresh(speaker)
    if _PAGING_RELEVANT_FIELDS & changed_fields.keys():
        background_tasks.add_task(multicast_provisioning.push_to_speaker_id, speaker.id)
    return speaker


@router.delete("/{speaker_id}")
def delete_speaker(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    # Config backups deliberately outlive the speaker (see
    # SpeakerConfigBackup docstring) — detach rather than cascade-delete.
    db.query(SpeakerConfigBackup).filter(SpeakerConfigBackup.speaker_id == speaker_id).update(
        {SpeakerConfigBackup.speaker_id: None}
    )
    db.delete(speaker)
    db.commit()
    return {"ok": True}


@router.post("/{speaker_id}/ping", response_model=SpeakerOut)
async def ping_speaker(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    await speaker_status.check_and_update(db, speaker)
    db.refresh(speaker)
    return speaker


@router.get("/{speaker_id}/multicast-preview")
def multicast_preview(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Shows exactly what would be pushed to the device — nothing is
    sent, this is read-only, for verification before/after enabling
    auto-push on a given speaker."""
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    entries = multicast_provisioning.compute_entries(speaker)
    return [
        {"index": e.index, "address": e.address, "port": e.port, "label": e.label, "priority": e.priority}
        for e in entries
    ]


@router.post("/{speaker_id}/push-config")
async def push_config_now(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Manually (re)apply this speaker's multicast paging list now,
    instead of waiting for an automatic trigger — e.g. after the device
    was reset or the field-side config was reverted by hand.

    Reports exactly which parameters the device confirmed (its CGI
    response body, not just the HTTP status — see fanvil_http.py) and
    which did not, rather than a single opaque success/fail flag."""
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    result = await multicast_provisioning.push_to_speaker(speaker)
    if result.unsupported_brand:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Applicazione automatica non supportata per il brand '{speaker.brand or '(non impostato)'}' — solo Fanvil è supportato. Configura i gruppi multicast manualmente sul device (usa Anteprima).",
                "unsupported_brand": True,
            },
        )
    if not result.success:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Applicazione parziale o fallita — verificare credenziali/raggiungibilità/sintassi CGI per questo firmware",
                "applied": result.applied_keys,
                "failed": result.failed_keys,
            },
        )
    return {"ok": True, "applied": result.applied_keys}


# ---------- Configuration backups (Fanvil-only, admin-only) ----------
@router.post("/{speaker_id}/backups", response_model=SpeakerBackupOut)
async def create_backup(
    speaker_id: int,
    fmt: str = "txt",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Fetches the device's own configuration export and stores it
    server-side, via whichever driver is registered for this speaker's
    brand — confirmed working against a real Fanvil A233
    (/default_user_config.txt et al., see drivers/fanvil_http.py)."""
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Altoparlante non trovato")
    driver = get_driver(speaker.brand)
    if not driver or not driver.supports_config_backup:
        raise HTTPException(
            status_code=400,
            detail=f"Backup della configurazione non supportato per il brand '{speaker.brand or '(non impostato)'}'",
        )

    try:
        content = await driver.export_config(speaker, fmt=fmt)
    except ConfigExportError as exc:
        raise HTTPException(status_code=502, detail=f"Impossibile scaricare la configurazione dal device: {exc}") from exc

    stored_filename = f"{uuid.uuid4().hex}.{fmt}"
    (settings.backups_dir / stored_filename).write_text(content, encoding="utf-8")

    backup = SpeakerConfigBackup(
        speaker_id=speaker.id,
        speaker_name=speaker.name,
        speaker_ip=speaker.ip_address,
        format=fmt,
        stored_filename=stored_filename,
        size_bytes=len(content.encode("utf-8")),
        created_by_id=user.id,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


@router.get("/{speaker_id}/backups", response_model=list[SpeakerBackupOut])
def list_backups_for_speaker(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Convenience filtered view for the per-speaker Backup modal — see
    routers/backups.py for the full archive (including backups whose
    speaker has since been deleted)."""
    return (
        db.query(SpeakerConfigBackup)
        .filter(SpeakerConfigBackup.speaker_id == speaker_id)
        .order_by(SpeakerConfigBackup.created_at.desc())
        .all()
    )
