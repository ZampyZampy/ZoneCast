"""Amplify (normalize): failure paths that must not damage the file."""
import subprocess
from types import SimpleNamespace

from app.database import SessionLocal
from app.models import Media
from app.services import audio_analysis


def test_amplify_writes_wav_for_an_upload_without_a_usable_extension(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        open(cmd[-1], "wb").close()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    audio_analysis.apply_gain(tmp_path / "x.audio", 3.0)
    audio_analysis.apply_gain(tmp_path / "y.mp3", 3.0)
    assert seen[0][-3:-1] == ["-f", "wav"]
    assert "-f" not in seen[1]


def test_a_failed_conversion_after_amplifying_does_not_amplify_twice(admin_client, monkeypatch):
    from app.config import settings
    from app.routers import media as media_router

    db = SessionLocal()
    try:
        media = Media(original_filename="loud.wav", stored_filename="amplify-twice.wav",
                      pcm_filename="amplify-twice.pcm8k.wav", suggested_gain_db=6.0)
        db.add(media)
        db.commit()
        media_id = media.id
    finally:
        db.close()
    (settings.media_dir / "amplify-twice.wav").write_bytes(b"RIFF")

    def fail_convert(*a):
        raise media_router.AudioConversionError("boom")

    monkeypatch.setattr(audio_analysis, "apply_gain", lambda path, gain: None)
    monkeypatch.setattr(media_router, "convert_to_pcm8k", fail_convert)
    monkeypatch.setattr(audio_analysis, "analyze", lambda path: audio_analysis.AudioAnalysis(
        peak_db=-1.0, mean_db=-12.0, suggested_gain_db=0.0, band_low_pct=30, band_mid_pct=50, band_high_pct=20))
    try:
        assert admin_client.post(f"/api/media/{media_id}/normalize").status_code == 502
        res = admin_client.post(f"/api/media/{media_id}/normalize")
        assert res.json()["detail"]["code"] == "media.already_max"  # level re-read: no second gain
    finally:
        (settings.media_dir / "amplify-twice.wav").unlink(missing_ok=True)
        db = SessionLocal()
        db.query(Media).filter(Media.id == media_id).delete()
        db.commit()
        db.close()
