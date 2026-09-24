"""
Converts any uploaded audio file (mp3/wav, any samplerate/channels) into the
8kHz mono 16-bit PCM WAV format required for G.711 encoding and RTP
streaming to the Fanvil speakers. Requires ffmpeg to be available on PATH
(installed in the Docker image).
"""
import subprocess
import wave
from pathlib import Path


class AudioConversionError(RuntimeError):
    pass


def convert_to_pcm8k(src_path: Path, dst_path: Path) -> float:
    """Convert src_path to 8kHz mono 16-bit PCM WAV at dst_path.

    Returns duration in seconds.
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(src_path),
        "-ac", "1", "-ar", "8000", "-sample_fmt", "s16",
        "-f", "wav", str(dst_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise AudioConversionError("ffmpeg non è installato nel container/host") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError("timeout durante la conversione del file audio") from exc
    if result.returncode != 0:
        raise AudioConversionError(f"ffmpeg failed: {result.stderr[-2000:]}")

    with wave.open(str(dst_path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        duration = frames / float(rate) if rate else 0.0
    return duration


def probe_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return frames / float(rate) if rate else 0.0
    except Exception:
        return 0.0
