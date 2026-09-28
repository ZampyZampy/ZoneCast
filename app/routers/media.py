import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Media, User
from ..schemas import MediaOut
from ..services import audio_analysis, references
from ..services.audio_convert import convert_to_pcm8k, AudioConversionError

router = APIRouter(prefix="/api/media", tags=["media"])
logger = logging.getLogger("zonecast.media")

_SAFE_EXT_RE = re.compile(r"^\.[a-zA-Z0-9]{1,8}$")
# One normalize at a time per file: the gain is applied in place, so a
# second click arriving mid-way must see the re-analysed level, not apply
# the stale one again (that would clip the audio for good).
_normalize_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _safe_extension(filename: str) -> str:
    """Sanitizes the upload's extension for use in a stored filename.
    Purely cosmetic (used for the download filename) — validity of the
    actual audio content is decided by whether ffmpeg can decode it,
    not by the extension."""
    ext = Path(filename).suffix.lower()
    return ext if _SAFE_EXT_RE.match(ext) else ".audio"


@router.get("", response_model=list[MediaOut])
def list_media(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Media).order_by(Media.uploaded_at.desc()).all()


@router.post("/upload", response_model=MediaOut)
async def upload_media(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # No extension whitelist: any format ffmpeg can decode is accepted
    # (mp3, wav, m4a/aac, ogg, flac, wma, opus, ...). Invalid/non-audio
    # uploads are rejected below when the ffmpeg conversion itself fails.
    ext = _safe_extension(file.filename)

    token = uuid.uuid4().hex
    stored_name = f"{token}{ext}"
    pcm_name = f"{token}.pcm8k.wav"

    src_path = settings.media_dir / stored_name
    pcm_path = settings.media_dir / pcm_name
    # Copied to disk in chunks, never held whole in memory.
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with src_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=400, detail=f"File troppo grande (max {settings.max_upload_mb} MB)")
                out.write(chunk)
    except BaseException:
        src_path.unlink(missing_ok=True)
        raise

    # ffmpeg runs in a worker thread: on the event loop it would stall
    # everything else for seconds, including announcements being played.
    try:
        duration = await asyncio.to_thread(convert_to_pcm8k, src_path, pcm_path)
    except AudioConversionError as exc:
        src_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Impossibile elaborare il file come audio (formato non riconosciuto o file corrotto): {exc}",
        ) from exc

    if duration > settings.max_duration_seconds:
        src_path.unlink(missing_ok=True)
        pcm_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Durata troppo lunga: {duration:.0f}s (max {settings.max_duration_seconds}s)",
        )

    media = Media(
        original_filename=file.filename,
        stored_filename=stored_name,
        pcm_filename=pcm_name,
        duration_seconds=duration,
        size_bytes=size,
        content_type=file.content_type or "",
        uploaded_by_id=user.id,
    )
    await asyncio.to_thread(_run_analysis, media, src_path)
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


def _run_analysis(media: Media, src_path: Path) -> None:
    """Populates the frequency-balance/headroom fields on `media` — not
    fatal on failure (an unanalyzable file still gets uploaded/played,
    it just won't show the PA-suitability hints)."""
    try:
        analysis = audio_analysis.analyze(src_path)
    except audio_analysis.AudioAnalysisError as exc:
        logger.warning("Analisi audio fallita per '%s': %s", media.original_filename, exc)
        return
    media.peak_db = analysis.peak_db
    media.mean_db = analysis.mean_db
    media.band_low_pct = analysis.band_low_pct
    media.band_mid_pct = analysis.band_mid_pct
    media.band_high_pct = analysis.band_high_pct
    media.suggested_gain_db = analysis.suggested_gain_db


@router.post("/{media_id}/normalize", response_model=MediaOut)
async def normalize_media(media_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Applies the suggested gain (see audio_analysis.py) as a straight
    linear amplification — same waveform, just scaled up to leave ~1 dB
    of headroom under 0 dBFS — then regenerates the streaming copy and
    re-analyzes so the stored levels reflect the new file."""
    lock = _normalize_locks.setdefault((id(asyncio.get_running_loop()), media_id), asyncio.Lock())
    async with lock:
        return await _normalize(media_id, db)


async def _normalize(media_id: int, db: Session) -> Media:
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="File non trovato")
    db.refresh(media)  # the previous holder of the lock may just have changed it
    if not media.suggested_gain_db:
        raise HTTPException(status_code=400, detail="Questo file è già al massimo livello utile, nessuna amplificazione necessaria")

    src_path = settings.media_dir / media.stored_filename
    pcm_path = settings.media_dir / media.pcm_filename
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="File non presente sul server")

    try:
        await asyncio.to_thread(audio_analysis.apply_gain, src_path, media.suggested_gain_db)
        duration = await asyncio.to_thread(convert_to_pcm8k, src_path, pcm_path)
    except (audio_analysis.AudioAnalysisError, AudioConversionError) as exc:
        raise HTTPException(status_code=502, detail=f"Amplificazione fallita: {exc}") from exc

    media.duration_seconds = duration
    media.size_bytes = src_path.stat().st_size
    media.normalized = True
    await asyncio.to_thread(_run_analysis, media, src_path)
    db.commit()
    db.refresh(media)
    return media


@router.get("/{media_id}/download")
def download_media(media_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="File non trovato")
    path = settings.media_dir / media.stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File non presente sul server")
    return FileResponse(path, filename=media.original_filename, media_type=media.content_type or "application/octet-stream")


@router.delete("/{media_id}")
def delete_media(media_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="File non trovato")
    references.refuse_if_used(references.schedules_using_media(db, media_id), f"Il file «{media.original_filename}»")
    for fname in (media.stored_filename, media.pcm_filename):
        if fname:
            (settings.media_dir / fname).unlink(missing_ok=True)
    db.delete(media)
    db.commit()
    return {"ok": True}
