"""
Host disk/RAM/CPU snapshot for the Sistema tab. Pure stdlib (no
psutil dependency): disk via shutil.disk_usage, RAM via /proc/meminfo,
CPU via a short two-sample delta read of /proc/stat. Works on both
deployments — on Docker these reflect the container's view (its disk
mount, its cgroup-visible /proc), which is what's actually relevant to
whoever is looking at the dashboard there.
"""
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

PROC_MEMINFO = Path("/proc/meminfo")
PROC_STAT = Path("/proc/stat")
DISK_PATH = "/"


class HostResourcesError(RuntimeError):
    pass


@dataclass
class HostResources:
    disk_total_bytes: int
    disk_used_bytes: int
    disk_percent: float
    mem_total_bytes: int
    mem_used_bytes: int
    mem_percent: float
    cpu_percent: float
    cpu_count: int


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in PROC_MEMINFO.read_text().splitlines():
            key, _, rest = line.partition(":")
            rest = rest.strip().split()
            if not rest:
                continue
            values[key.strip()] = int(rest[0]) * 1024  # /proc/meminfo is in kB
    except (OSError, ValueError) as exc:
        raise HostResourcesError(f"Impossibile leggere {PROC_MEMINFO}: {exc}") from exc
    return values


def _cpu_times() -> tuple[int, int]:
    """Returns (idle, total) jiffies from the aggregate 'cpu' line."""
    try:
        first_line = PROC_STAT.read_text().splitlines()[0]
    except (OSError, IndexError) as exc:
        raise HostResourcesError(f"Impossibile leggere {PROC_STAT}: {exc}") from exc
    parts = first_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        raise HostResourcesError("formato inatteso in /proc/stat")
    nums = [int(x) for x in parts[1:]]
    idle = nums[3] + nums[4]  # idle + iowait
    total = sum(nums)
    return idle, total


def get_snapshot(cpu_sample_seconds: float = 0.2) -> HostResources:
    disk = shutil.disk_usage(DISK_PATH)
    disk_percent = (disk.used / disk.total * 100) if disk.total else 0.0

    mem = _read_meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_available = mem.get("MemAvailable", mem.get("MemFree", 0))
    mem_used = max(mem_total - mem_available, 0)
    mem_percent = (mem_used / mem_total * 100) if mem_total else 0.0

    idle1, total1 = _cpu_times()
    time.sleep(cpu_sample_seconds)
    idle2, total2 = _cpu_times()
    delta_idle = idle2 - idle1
    delta_total = total2 - total1
    cpu_percent = ((delta_total - delta_idle) / delta_total * 100) if delta_total > 0 else 0.0

    return HostResources(
        disk_total_bytes=disk.total,
        disk_used_bytes=disk.used,
        disk_percent=round(disk_percent, 1),
        mem_total_bytes=mem_total,
        mem_used_bytes=mem_used,
        mem_percent=round(mem_percent, 1),
        cpu_percent=round(max(0.0, min(cpu_percent, 100.0)), 1),
        cpu_count=os.cpu_count() or 1,
    )
