from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import require_admin
from ..models import SpeakerConfigBackup, User
from ..schemas import SpeakerBackupOut

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("", response_model=list[SpeakerBackupOut])
def list_all_backups(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """The full backup archive, including backups whose speaker has
    since been deleted (speaker_id null, speaker_exists false) — those
    stay downloadable via speaker_name/speaker_ip snapshotted at
    creation time. See SpeakerConfigBackup model docstring."""
    return db.query(SpeakerConfigBackup).order_by(SpeakerConfigBackup.created_at.desc()).all()


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    backup = db.query(SpeakerConfigBackup).filter(SpeakerConfigBackup.id == backup_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup non trovato")
    path = settings.backups_dir / backup.stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File di backup non presente sul server")
    ts = backup.created_at.strftime("%Y%m%d_%H%M%S")
    filename = f"{backup.speaker_name or 'speaker'}_{ts}.{backup.format}"
    return FileResponse(path, filename=filename, media_type="text/plain")


@router.delete("/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    backup = db.query(SpeakerConfigBackup).filter(SpeakerConfigBackup.id == backup_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup non trovato")
    (settings.backups_dir / backup.stored_filename).unlink(missing_ok=True)
    db.delete(backup)
    db.commit()
    return {"ok": True}
