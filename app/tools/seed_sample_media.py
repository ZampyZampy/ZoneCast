"""
Loads the sample announcement sounds shipped in `sounds/` (repo root)
into the Media library, going through the exact same conversion/
analysis pipeline as a normal dashboard upload (routers/media.py) —
so they show up ready to play, with frequency-balance hints already
computed, right after a fresh install.

Idempotent: a file already present (matched by original filename) is
skipped, so re-running (e.g. every deploy) never creates duplicates.

Usage:
    python -m app.tools.seed_sample_media
    python -m app.tools.seed_sample_media --source /path/to/other/dir
"""
import argparse
import sys
from pathlib import Path

from ..config import BASE_DIR, settings
from ..database import Base, SessionLocal, engine
from ..models import Media, User, UserRole
from ..services import audio_analysis
from ..services.audio_convert import AudioConversionError, convert_to_pcm8k

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus", ".aiff"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Precarica i suoni di esempio in Media")
    parser.add_argument("--source", default=str(BASE_DIR / "sounds"), help="Cartella con i file audio da caricare (default: sounds/)")
    args = parser.parse_args()

    source_dir = Path(args.source)
    if not source_dir.is_dir():
        print(f"Cartella non trovata: {source_dir}", file=sys.stderr)
        return 1

    files = sorted(p for p in source_dir.iterdir() if p.suffix.lower() in _AUDIO_EXTS)
    if not files:
        print(f"Nessun file audio trovato in {source_dir}.")
        return 0

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing_names = {name for (name,) in db.query(Media.original_filename).all()}
        admin = db.query(User).filter(User.role == UserRole.admin).order_by(User.id).first()

        added = 0
        for src in files:
            if src.name in existing_names:
                print(f"Salta '{src.name}' (già presente in Media).")
                continue

            import uuid
            token = uuid.uuid4().hex
            ext = src.suffix.lower()
            stored_name = f"{token}{ext}"
            pcm_name = f"{token}.pcm8k.wav"
            dst_path = settings.media_dir / stored_name
            pcm_path = settings.media_dir / pcm_name
            dst_path.write_bytes(src.read_bytes())

            try:
                duration = convert_to_pcm8k(dst_path, pcm_path)
            except AudioConversionError as exc:
                print(f"Errore convertendo '{src.name}': {exc}", file=sys.stderr)
                dst_path.unlink(missing_ok=True)
                continue

            media = Media(
                original_filename=src.name,
                stored_filename=stored_name,
                pcm_filename=pcm_name,
                duration_seconds=duration,
                size_bytes=dst_path.stat().st_size,
                content_type="",
                uploaded_by_id=admin.id if admin else None,
            )
            try:
                analysis = audio_analysis.analyze(dst_path)
                media.peak_db = analysis.peak_db
                media.mean_db = analysis.mean_db
                media.band_low_pct = analysis.band_low_pct
                media.band_mid_pct = analysis.band_mid_pct
                media.band_high_pct = analysis.band_high_pct
                media.suggested_gain_db = analysis.suggested_gain_db
            except audio_analysis.AudioAnalysisError as exc:
                print(f"Analisi audio non riuscita per '{src.name}' (caricato comunque): {exc}")

            db.add(media)
            db.commit()
            added += 1
            print(f"Caricato '{src.name}' ({duration:.1f}s).")

        print(f"Fatto: {added} file aggiunti, {len(files) - added} già presenti/saltati.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
