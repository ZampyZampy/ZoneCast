"""
RTP/G.711 multicast paging sender.

This is the primary transport used to push audio to the Fanvil A233
speakers. Fanvil (like most IP paging/PA hardware — Algo, Cyberdata,
Grandstream GSC, etc.) implements "Multicast Paging": each phone is
provisioned, in its own web UI, with a list of multicast group
addresses it listens to. Any RTP/G.711 stream sent to one of those
groups is decoded and played immediately by every speaker subscribed
to it — with sample-accurate synchronization and no per-device call
setup, which is exactly what's needed for "play now on this zone / on
all speakers at once".

We model this by giving each Speaker its own dedicated multicast group
(for single-speaker targeting), each Zone a group shared by its member
speakers, and a global "all-call" group every speaker also listens to.
Sending a file is therefore always the same operation: stream one G.711
RTP flow to one multicast address. See README.md for the exact
provisioning steps on the Fanvil web UI.

Alternative transports (SIP auto-answer intercom calls, or a
device-specific HTTP CGI "play URL" action) are also viable and are
sketched in fanvil_http.py as a fallback, but multicast is the most
robust for simultaneous/synchronized playback and does not require
holding a SIP registration or dialog per speaker.
"""
import asyncio
import audioop
import random
import socket
import struct
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import settings

RTP_VERSION = 2
SAMPLE_WIDTH = 2  # 16-bit PCM input
SAMPLE_RATE = 8000
# If the sender ever falls further behind than this (the host stalled),
# it drops the backlog and restarts pacing from "now": a stall must be
# heard as a short gap, never followed by a burst of late packets that
# overflows the speakers' jitter buffers.
MAX_LAG_PACKETS = 3


class RtpStreamError(RuntimeError):
    pass


@dataclass
class StreamHandle:
    """Returned to callers so they can cooperatively stop an in-flight
    stream. A threading.Event, not an asyncio one: the sender runs in its
    own thread (see stream_pcm_over_rtp) and checks it between packets,
    so a stop takes effect within one packet interval."""
    task: Optional[asyncio.Task] = None
    stop_event: threading.Event = field(default_factory=threading.Event)

    def stop(self):
        self.stop_event.set()


def _build_rtp_header(seq: int, timestamp: int, ssrc: int, marker: bool, payload_type: int) -> bytes:
    b0 = (RTP_VERSION << 6)  # padding=0, extension=0, CC=0
    b1 = (0x80 if marker else 0x00) | (payload_type & 0x7F)
    return struct.pack("!BBHII", b0, b1, seq & 0xFFFF, timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)


def _make_socket(ttl: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    return sock


async def stream_pcm_over_rtp(
    pcm_wav_path: Path,
    multicast_address: str,
    multicast_port: int,
    stop_event: threading.Event,
    payload_type: int = settings.rtp_payload_type,
    packet_ms: int = settings.rtp_packet_ms,
    ttl: int = settings.rtp_multicast_ttl,
) -> None:
    """Stream an 8kHz mono 16-bit PCM WAV file as RTP/G.711 to a multicast
    group, paced in real time. Stops early if stop_event is set.

    The pacing loop runs in a dedicated thread rather than on the event
    loop: an upload being converted, a slow DB write or any other hiccup
    in the web app then can't delay packets of a bell that's playing."""
    if not pcm_wav_path.exists():
        raise RtpStreamError(f"PCM file not found: {pcm_wav_path}")

    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()

    def _finish(exc: BaseException | None) -> None:
        if done.done():
            return
        if exc is None:
            done.set_result(None)
        else:
            done.set_exception(exc)

    def _runner() -> None:
        try:
            _send_paced(pcm_wav_path, (multicast_address, multicast_port), stop_event, payload_type, packet_ms, ttl)
        except BaseException as exc:  # noqa: BLE001 — handed back to the awaiting coroutine
            loop.call_soon_threadsafe(_finish, exc)
        else:
            loop.call_soon_threadsafe(_finish, None)

    threading.Thread(target=_runner, name=f"rtp-{multicast_address}:{multicast_port}", daemon=True).start()
    try:
        await asyncio.shield(done)
    except asyncio.CancelledError:
        stop_event.set()  # the thread can't be cancelled, only asked to stop
        raise


def _send_paced(pcm_wav_path: Path, dest: tuple[str, int], stop_event: threading.Event,
                payload_type: int, packet_ms: int, ttl: int) -> None:
    samples_per_packet = int(SAMPLE_RATE * packet_ms / 1000)  # e.g. 160 @ 20ms
    bytes_per_packet = samples_per_packet * SAMPLE_WIDTH
    packet_s = packet_ms / 1000.0
    encode = audioop.lin2ulaw if payload_type == 0 else audioop.lin2alaw

    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    ssrc = random.randint(0, 0xFFFFFFFF)

    sock = _make_socket(ttl)
    try:
        with wave.open(str(pcm_wav_path), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != SAMPLE_WIDTH:
                raise RtpStreamError("PCM file is not 8kHz mono 16-bit — run audio_convert first")

            first = True
            next_send_time = time.perf_counter()
            while not stop_event.is_set():
                chunk = w.readframes(samples_per_packet)
                if not chunk:
                    break
                if len(chunk) < bytes_per_packet:
                    chunk = chunk + b"\x00" * (bytes_per_packet - len(chunk))

                payload = encode(chunk, SAMPLE_WIDTH)
                header = _build_rtp_header(seq, timestamp, ssrc, first, payload_type)
                sock.sendto(header + payload, dest)

                seq += 1
                timestamp += samples_per_packet
                first = False

                next_send_time += packet_s
                delay = next_send_time - time.perf_counter()
                if delay > 0:
                    if stop_event.wait(delay):
                        break
                elif delay < -MAX_LAG_PACKETS * packet_s:
                    next_send_time = time.perf_counter()
    finally:
        sock.close()
