"""
Post-upload audio analysis aimed at PA horn speakers specifically: horn
compression drivers (the Fanvil A233 included) reproduce the 300 Hz -
a few kHz "presence" range well but struggle with real bass, so a file
that's bass-heavy will sound weak/muddy through them even though it's
perfectly fine on a hi-fi system. This also reports peak headroom so a
quiet file can be brought up to its maximum safe level (a straight
linear gain, not compression — same waveform shape, just scaled) before
it ever reaches a speaker.

Deliberately ffmpeg-only (no numpy/scipy): reuses `volumedetect`, the
same building block already relied on for audio_convert.py, run once
full-band and once through each of three band-pass filters. Four
sequential ffmpeg decodes per analysis is simple and reliable; these
are short PA announcement clips (capped by MAX_DURATION_SECONDS), not
long-form audio, so the extra latency is negligible.
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Band edges chosen for PA horn behavior, not general audio engineering:
#   < 300 Hz   - true bass, the range horns reproduce worst
#   300-2000 Hz - voice/presence range, where horns are strongest
#   > 2000 Hz   - clarity/"air" — still transmitted fine (the RTP/G.711
#                 path is 8kHz/4kHz-Nyquist, so content above ~4kHz gets
#                 cut in transmission regardless of the source)
LOW_HIGH_HZ = 300
MID_HIGH_HZ = 2000

TARGET_CEILING_DB = -1.0     # normalize up to here, not 0, to leave safety headroom
MIN_USEFUL_GAIN_DB = 0.5     # below this the file is already effectively at max level
MAX_SENSIBLE_GAIN_DB = 30.0  # more than this and the file is likely near-silent, not just quiet

_VOL_RE = re.compile(r"(mean|max)_volume:\s*(-inf|-?\d+(?:\.\d+)?)\s*dB")
_SILENCE_DB = -120.0


class AudioAnalysisError(RuntimeError):
    pass


@dataclass
class AudioAnalysis:
    peak_db: float
    mean_db: float
    suggested_gain_db: float | None
    band_low_pct: float
    band_mid_pct: float
    band_high_pct: float


def _volumedetect(path: Path, extra_filters: str = "") -> tuple[float, float]:
    """Returns (mean_volume_db, max_volume_db) — mean is an RMS-like
    average level, max is the true sample peak. Both in dBFS (0 = full
    scale)."""
    filters = f"{extra_filters},volumedetect" if extra_filters else "volumedetect"
    cmd = ["ffmpeg", "-i", str(path), "-vn", "-af", filters, "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise AudioAnalysisError("ffmpeg non è installato nel container/host") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError("timeout durante l'analisi audio") from exc

    values = {}
    for m in _VOL_RE.finditer(result.stderr):
        raw = m.group(2)
        values[m.group(1)] = _SILENCE_DB if raw == "-inf" else float(raw)
    if "mean" not in values or "max" not in values:
        raise AudioAnalysisError(f"impossibile leggere i livelli audio: {result.stderr[-500:]}")
    return values["mean"], values["max"]


def analyze(path: Path) -> AudioAnalysis:
    mean_db, peak_db = _volumedetect(path)

    suggested_gain_db = None
    headroom_db = TARGET_CEILING_DB - peak_db
    if headroom_db >= MIN_USEFUL_GAIN_DB:
        suggested_gain_db = round(min(headroom_db, MAX_SENSIBLE_GAIN_DB), 1)

    low_mean, _ = _volumedetect(path, f"lowpass=f={LOW_HIGH_HZ}")
    mid_mean, _ = _volumedetect(path, f"highpass=f={LOW_HIGH_HZ},lowpass=f={MID_HIGH_HZ}")
    high_mean, _ = _volumedetect(path, f"highpass=f={MID_HIGH_HZ}")

    def _power(db: float) -> float:
        return 10 ** (db / 10.0)

    p_low, p_mid, p_high = _power(low_mean), _power(mid_mean), _power(high_mean)
    total = p_low + p_mid + p_high
    if total <= 0:
        band_low_pct = band_mid_pct = band_high_pct = 0.0
    else:
        band_low_pct = round(p_low / total * 100, 1)
        band_mid_pct = round(p_mid / total * 100, 1)
        band_high_pct = round(100 - band_low_pct - band_mid_pct, 1)  # avoid rounding drift

    return AudioAnalysis(
        peak_db=round(peak_db, 1),
        mean_db=round(mean_db, 1),
        suggested_gain_db=suggested_gain_db,
        band_low_pct=band_low_pct,
        band_mid_pct=band_mid_pct,
        band_high_pct=band_high_pct,
    )


def apply_gain(path: Path, gain_db: float) -> None:
    """Re-encodes `path` in place with a straight linear gain applied
    (volume filter, not compression — the waveform is only scaled, not
    reshaped). The caller is expected to have sized gain_db so the
    result doesn't clip (see TARGET_CEILING_DB above)."""
    tmp_path = path.with_name(f"{path.stem}.gaintmp{path.suffix}")
    cmd = ["ffmpeg", "-y", "-i", str(path), "-vn", "-af", f"volume={gain_db}dB", str(tmp_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise AudioAnalysisError("ffmpeg non è installato nel container/host") from exc
    except subprocess.TimeoutExpired as exc:
        tmp_path.unlink(missing_ok=True)
        raise AudioAnalysisError("timeout durante l'amplificazione del file") from exc
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise AudioAnalysisError(f"ffmpeg failed: {result.stderr[-2000:]}")
    tmp_path.replace(path)
