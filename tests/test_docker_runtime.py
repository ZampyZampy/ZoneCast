"""What the app does differently when it runs in the Docker image: no
timedatectl (NTP state comes from the kernel), and bind-mounted data
folders that may not belong to the app's user (1.6.3)."""
import ctypes
import subprocess
import sys

import pytest

from app import config
from app.services import system_time


def _no_timedatectl(monkeypatch):
    monkeypatch.setattr(system_time, "_timedatectl_show", lambda: None)
    monkeypatch.setattr(system_time, "_run", lambda args: subprocess.CompletedProcess(args, 0, "2026-09-30 10:00:00 CEST\n", ""))
    monkeypatch.setattr(system_time, "_chrony_installed", lambda: False)
    monkeypatch.setattr(system_time, "TIMESYNCD_CONF", system_time.Path("/nonexistent/timesyncd.conf"))


@pytest.mark.parametrize("kernel_says", [True, False])
def test_without_timedatectl_the_sync_state_comes_from_the_kernel(monkeypatch, kernel_says):
    _no_timedatectl(monkeypatch)
    monkeypatch.setattr(system_time, "_kernel_clock_synchronized", lambda: kernel_says)
    status = system_time.get_status()
    assert status.ntp_synchronized is kernel_says
    assert status.controllable is False and status.ntp_servers == []  # the host's business


@pytest.mark.skipif(sys.platform != "linux", reason="adjtimex is Linux-only")
def test_the_kernel_is_really_asked():
    if ctypes.sizeof(ctypes.c_long) == 8:
        assert ctypes.sizeof(system_time._Timex) == 208  # struct timex on 64-bit Linux
    assert isinstance(system_time._kernel_clock_synchronized(), bool)


@pytest.mark.skipif(sys.platform == "linux", reason="checks the fallback where adjtimex doesn't exist")
def test_no_kernel_answer_elsewhere():
    assert system_time._kernel_clock_synchronized() is None


def test_an_unwritable_data_folder_stops_the_start_with_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "media_dir", tmp_path / "not-created")
    with pytest.raises(RuntimeError, match=r"sudo chown -R 1000:1000 data media backups"):
        config.require_writable_dirs()


def test_writable_folders_pass(monkeypatch, tmp_path):
    for name in ("data_dir", "media_dir", "backups_dir"):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(config.settings, name, tmp_path / name)
    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{(tmp_path / 'data_dir' / 'z.db').as_posix()}")
    config.require_writable_dirs()
