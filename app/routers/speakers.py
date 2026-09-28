import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..errors import AppError
from ..deps import get_current_user, require_admin
from ..models import Speaker, SpeakerConfigBackup, TargetType, User
from ..schemas import SpeakerCreate, SpeakerUpdate, SpeakerOut, SpeakerBackupOut
from ..services import multicast_provisioning, references, speaker_status
from ..services.drivers import ConfigExportError, get_driver
from ..services.multicast_addressing import MulticastAddressConflict, check_address_available

router = APIRouter(prefix="/api/speakers", tags=["speakers"])

# Fields that change what this speaker's multicast paging list should
# contain — these trigger an automatic push to the device...
_PAGING_RELEVANT_FIELDS = {"zone_id", "own_multicast_address", "own_multicast_port", "paging_volume"}
# ...and so do these: a device replaced at a new IP, corrected
# credentials after a failed push, or a switch to a supported brand all
# mean the device may not have the right list yet.
_DEVICE_ACCESS_FIELDS = {"ip_address", "http_port", "http_username", "http_password", "brand"}


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
        raise AppError(400, "speakers.ip_taken", "A speaker with this IP address already exists.")
    try:
        check_address_available(db, payload.own_multicast_address, payload.own_multicast_port)
    except MulticastAddressConflict as exc:
        raise exc.http(400) from exc
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
        raise AppError(404, "speakers.not_found", "Speaker not found.")
    changed_fields = payload.model_dump(exclude_unset=True)
    new_ip = changed_fields.get("ip_address")
    if new_ip is not None and db.query(Speaker.id).filter(Speaker.ip_address == new_ip, Speaker.id != speaker.id).first():
        raise AppError(400, "speakers.ip_taken", "A speaker with this IP address already exists.")
    if "own_multicast_address" in changed_fields or "own_multicast_port" in changed_fields:
        new_address = changed_fields.get("own_multicast_address", speaker.own_multicast_address)
        new_port = changed_fields.get("own_multicast_port", speaker.own_multicast_port)
        try:
            check_address_available(db, new_address, new_port, exclude_speaker_id=speaker.id)
        except MulticastAddressConflict as exc:
            raise exc.http(400) from exc
    # The form always sends every field: compare values, or any edit (a
    # typo in 'location') would trigger a full rewrite of the device.
    really_changed = {k for k, v in changed_fields.items() if getattr(speaker, k) != v}
    for k, v in changed_fields.items():
        setattr(speaker, k, v)
    db.commit()
    db.refresh(speaker)
    if (_PAGING_RELEVANT_FIELDS | _DEVICE_ACCESS_FIELDS) & really_changed:
        background_tasks.add_task(multicast_provisioning.push_to_speaker_id, speaker.id)
    return speaker


@router.delete("/{speaker_id}")
def delete_speaker(speaker_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id).first()
    if not speaker:
        raise AppError(404, "speakers.not_found", "Speaker not found.")
    references.refuse_if_used(references.schedules_targeting(db, TargetType.speaker, speaker_id), "speakers.in_use", "This speaker")
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
        raise AppError(404, "speakers.not_found", "Speaker not found.")
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
        raise AppError(404, "speakers.not_found", "Speaker not found.")
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
        raise AppError(404, "speakers.not_found", "Speaker not found.")
    result = await multicast_provisioning.push_to_speaker(speaker)
    if result.unsupported_brand:
        raise AppError(400, "speakers.push_unsupported",
                       f"Automatic setup isn't supported for brand \"{speaker.brand or '-'}\": configure the multicast groups on the device (see Preview).",
                       brand=speaker.brand or "-")
    if not result.success:
        raise AppError(502, "speakers.push_failed",
                       "The device accepted only part of the configuration, or none of it: check credentials, reachability and firmware.",
                       applied=result.applied_keys, failed=result.failed_keys)
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
        raise AppError(404, "speakers.not_found", "Speaker not found.")
    driver = get_driver(speaker.brand)
    if not driver or not driver.supports_config_backup:
        raise AppError(400, "speakers.backup_unsupported",
                       f"Configuration backup isn't supported for brand \"{speaker.brand or '-'}\".", brand=speaker.brand or "-")

    try:
        content = await driver.export_config(speaker, fmt=fmt)
    except ConfigExportError as exc:
        raise AppError(502, "speakers.device_unreachable", "Couldn't download the configuration from the device.", detail=str(exc)) from exc

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
