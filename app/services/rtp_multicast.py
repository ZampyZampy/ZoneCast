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
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import settings

RTP_VERSION = 2
SAMPLE_WIDTH = 2  # 16-bit PCM input
SAMPLE_RATE = 8000


class RtpStreamError(RuntimeError):
    pass


@dataclass
class StreamHandle:
    """Returned to callers so they can cooperatively stop an in-flight stream."""
    task: Optional[asyncio.Task] = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    def stop(self):
        self.stop_event.set()
        if self.task and not self.task.done():
            self.task.cancel()


def _build_rtp_header(seq: int, timestamp: int, ssrc: int, marker: bool, payload_type: int) -> bytes:
    b0 = (RTP_VERSION << 6)  # padding=0, extension=0, CC=0
    b1 = (0x80 if marker else 0x00) | (payload_type & 0x7F)
    return struct.pack("!BBHII", b0, b1, seq & 0xFFFF, timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)


def _make_socket(ttl: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    sock.setblocking(False)
    return sock


async def stream_pcm_over_rtp(
    pcm_wav_path: Path,
    multicast_address: str,
    multicast_port: int,
    stop_event: asyncio.Event,
    payload_type: int = settings.rtp_payload_type,
    packet_ms: int = settings.rtp_packet_ms,
    ttl: int = settings.rtp_multicast_ttl,
) -> None:
    """Stream an 8kHz mono 16-bit PCM WAV file as RTP/G.711 to a multicast
    group, paced in real time. Stops early if stop_event is set."""
    if not pcm_wav_path.exists():
        raise RtpStreamError(f"PCM file not found: {pcm_wav_path}")

    samples_per_packet = int(SAMPLE_RATE * packet_ms / 1000)  # e.g. 160 @ 20ms
    bytes_per_packet = samples_per_packet * SAMPLE_WIDTH

    encode = audioop.lin2ulaw if payload_type == 0 else audioop.lin2alaw

    sock = _make_socket(ttl)
    loop = asyncio.get_event_loop()
    dest = (multicast_address, multicast_port)

    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    ssrc = random.randint(0, 0xFFFFFFFF)

    try:
        with wave.open(str(pcm_wav_path), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != SAMPLE_WIDTH:
                raise RtpStreamError("PCM file is not 8kHz mono 16-bit — run audio_convert first")

            first = True
            next_send_time = loop.time()
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

                next_send_time += packet_ms / 1000.0
                delay = next_send_time - loop.time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=delay)
                        break  # stop_event was set
                    except asyncio.TimeoutError:
                        pass
    finally:
        sock.close()
