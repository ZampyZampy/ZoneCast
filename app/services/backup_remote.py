"""
Where scheduled backups go besides this machine: an FTP server (explicit
FTPS, or plain FTP only when the admin says so) or an SMB2/3 share.

Every operation opens its own connection and closes it: backups run at
most a few times a day, and a long-lived session would outlive a changed
password. Uploads go to a hidden temporary name and are renamed once
complete, then their size is checked — a cut connection never leaves
something that looks like a valid backup.
"""
import ftplib
import hashlib
import logging
import os
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path

from ..errors import CodedError

logger = logging.getLogger("zonecast.backup")
# smbprotocol logs every packet at DEBUG/INFO, credentials exchange included.
for _name in ("smbprotocol", "spnego"):
    logging.getLogger(_name).setLevel(logging.WARNING)

TIMEOUT = 30
DEFAULT_PORTS = {"ftps": 21, "ftp": 21, "smb": 445}
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9$._ -]{1,80}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._ -]{1,100}$")
_smb_lock = threading.Lock()  # smbclient keeps a process-wide session pool


class BackupError(CodedError):
    pass


@dataclass
class Target:
    destination: str  # ftps | ftp | smb
    host: str
    port: int | None
    share: str
    remote_dir: str
    username: str
    password: str
    smb_encrypt: bool = True
    tls_fingerprint: str = ""

    @property
    def effective_port(self) -> int:
        return self.port or DEFAULT_PORTS[self.destination]


def segments(remote_dir: str) -> list[str]:
    return [s for s in re.split(r"[\\/]+", remote_dir or "") if s]


def validate(t: Target) -> None:
    """Refuses anything that could turn into a path outside the chosen
    folder or a malformed command, before a connection is ever made."""
    if not _HOST_RE.match(t.host or ""):
        raise BackupError("backup.invalid_target", "Invalid server name or address.", field="host")
    if t.destination == "smb" and not _SHARE_RE.match(t.share or ""):
        raise BackupError("backup.invalid_target", "Invalid share name.", field="share")
    for seg in segments(t.remote_dir):
        if seg in (".", "..") or not _SEGMENT_RE.match(seg):
            raise BackupError("backup.invalid_target", "Invalid folder name.", field="remote_dir")
    if t.port is not None and not 0 < t.port < 65536:
        raise BackupError("backup.invalid_target", "Invalid port.", field="port")


def normalize_fingerprint(value: str) -> str:
    hexdigits = re.sub(r"[^0-9a-fA-F]", "", value or "").lower()
    return ":".join(hexdigits[i:i + 2] for i in range(0, len(hexdigits), 2))


def _fingerprint(der: bytes) -> str:
    return normalize_fingerprint(hashlib.sha256(der).hexdigest())


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


# ---------- FTP / FTPS ----------
class _FTPS(ftplib.FTP_TLS):
    """Explicit FTPS that reuses the control connection's TLS session on
    the data connection — vsftpd (require_ssl_reuse) and FileZilla Server
    refuse uploads otherwise, and the stdlib doesn't do it."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def server_fingerprint(host: str, port: int) -> str:
    """The FTPS server's certificate fingerprint, read WITHOUT trusting
    it — only shown to the admin, who decides whether to pin it."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp = ftplib.FTP_TLS(context=ctx, timeout=TIMEOUT)
    try:
        ftp.connect(host, port)
        ftp.auth()
        return _fingerprint(ftp.sock.getpeercert(binary_form=True))
    finally:
        try:
            ftp.close()
        except OSError:
            pass


class _FtpRemote:
    def __init__(self, t: Target):
        self.t = t
        self.ftp = None

    def __enter__(self):
        try:
            return self._open()
        except BaseException:
            self._close()  # a failed login or handshake must not leave the socket open
            raise

    def _close(self):
        if self.ftp is not None:
            try:
                self.ftp.close()
            except OSError:
                pass

    def _open(self):
        t = self.t
        if t.destination == "ftps":
            ctx = ssl.create_default_context()
            if t.tls_fingerprint:  # a pinned (e.g. self-signed) certificate, checked below
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp = _FTPS(context=ctx, timeout=TIMEOUT)
        else:
            ftp = ftplib.FTP(timeout=TIMEOUT)
        self.ftp = ftp
        try:
            ftp.connect(t.host, t.effective_port)
        except socket.gaierror as exc:
            raise BackupError("backup.host_unresolved", "The server name can't be resolved.", detail=_short(exc)) from exc
        except OSError as exc:
            raise BackupError("backup.connect_failed", "Couldn't connect to the server.", detail=_short(exc)) from exc
        if t.destination == "ftps":
            try:
                ftp.auth()
            except (ssl.SSLError, OSError, ftplib.Error) as exc:
                raise BackupError("backup.tls_failed", "The secure (TLS) connection failed.", detail=_short(exc)) from exc
            if t.tls_fingerprint:
                seen = _fingerprint(ftp.sock.getpeercert(binary_form=True))
                if seen != normalize_fingerprint(t.tls_fingerprint):
                    raise BackupError("backup.tls_fingerprint_mismatch",
                                      "The server's certificate is not the one that was trusted.", fingerprint=seen)
        try:
            ftp.login(t.username or "anonymous", t.password or "")
        except ftplib.error_perm as exc:
            raise BackupError("backup.auth_failed", "The server refused the username or password.", detail=_short(exc)) from exc
        except (OSError, ftplib.Error) as exc:
            raise BackupError("backup.connect_failed", "Couldn't connect to the server.", detail=_short(exc)) from exc
        try:
            if t.destination == "ftps":
                ftp.prot_p()
            ftp.set_pasv(True)
            for seg in segments(t.remote_dir):
                try:
                    ftp.cwd(seg)
                except ftplib.error_perm:
                    ftp.mkd(seg)
                    ftp.cwd(seg)
        except (OSError, ftplib.Error) as exc:
            raise BackupError("backup.upload_failed", "Couldn't open or create the folder on the server.",
                              detail=_short(exc)) from exc
        return self

    def __exit__(self, *exc):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except (OSError, ftplib.Error, EOFError):
                pass
            self._close()

    def upload(self, local: Path, name: str) -> None:
        tmp = f".{name}.part"
        try:
            with open(local, "rb") as f:
                self.ftp.storbinary(f"STOR {tmp}", f)
            self.ftp.rename(tmp, name)
        except (OSError, ftplib.Error, EOFError) as exc:
            try:
                self.ftp.delete(tmp)  # no half-written bundle left behind
            except (OSError, ftplib.Error, EOFError):
                pass
            if isinstance(exc, ftplib.error_perm) and str(exc).startswith("550"):
                raise BackupError("backup.access_denied", "This account can't write to that folder.",
                                  detail=_short(exc)) from exc
            raise BackupError("backup.upload_failed", "The upload failed.", detail=_short(exc)) from exc
        try:
            self.ftp.voidcmd("TYPE I")
            size = self.ftp.size(name)
        except (OSError, ftplib.Error) as exc:
            raise BackupError("backup.verify_failed", "The uploaded file couldn't be checked.", detail=_short(exc)) from exc
        if size != local.stat().st_size:
            raise BackupError("backup.verify_failed", "The uploaded file has the wrong size.",
                              detail=f"{size} != {local.stat().st_size}")

    def list(self) -> list[str]:
        return [n.rsplit("/", 1)[-1] for n in self.ftp.nlst()]

    def delete(self, name: str) -> None:
        self.ftp.delete(name)

    def probe(self) -> None:
        name = f".zonecast-probe-{os.urandom(4).hex()}.tmp"
        try:
            self.ftp.storbinary(f"STOR {name}", _BytesReader(b"zonecast"))
            self.ftp.delete(name)
        except (OSError, ftplib.Error) as exc:
            raise BackupError("backup.upload_failed", "A test file couldn't be written.", detail=_short(exc)) from exc


class _BytesReader:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, n=-1):
        chunk, self.data = (self.data, b"") if n < 0 else (self.data[:n], self.data[n:])
        return chunk


# ---------- SMB ----------
def _smbclient():
    try:
        import smbclient
    except ImportError as exc:
        raise BackupError("backup.smb_unavailable", "SMB support isn't installed on this server.") from exc
    return smbclient


# NTSTATUS codes smbclient reports (as SMBOSError.ntstatus) after login.
_NT_AUTH = {0xC000006D, 0xC0000072, 0xC0000071, 0xC0000064, 0xC000006A, 0xC0000070, 0xC0000234}
_NT_DENIED = {0xC0000022}
_NT_NOT_FOUND = {0xC00000CC, 0xC000003A, 0xC0000034, 0xC00000BE}


def _smb_error(exc: BaseException) -> BackupError:
    status = getattr(exc, "ntstatus", None)
    if status is not None:
        status &= 0xFFFFFFFF
        if status in _NT_AUTH:
            return BackupError("backup.auth_failed", "The server refused the username or password.", detail=_short(exc))
        if status in _NT_DENIED:
            return BackupError("backup.access_denied", "This account can't write to that share or folder.",
                               detail=_short(exc))
        if status in _NT_NOT_FOUND:
            return BackupError("backup.share_not_found", "The share or folder doesn't exist.", detail=_short(exc))
        return BackupError("backup.upload_failed", "The SMB operation failed.", detail=_short(exc))
    name = type(exc).__name__
    if isinstance(exc, socket.gaierror):
        return BackupError("backup.host_unresolved", "The server name can't be resolved.", detail=_short(exc))
    if name in ("LogonFailure", "AccessDenied", "SpnegoError", "BadPassword") or "Logon" in name:
        return BackupError("backup.auth_failed", "The server refused the username or password.", detail=_short(exc))
    if name in ("BadNetworkName", "ObjectPathNotFound", "ObjectNameNotFound"):
        return BackupError("backup.share_not_found", "The share or folder doesn't exist.", detail=_short(exc))
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "Connection" in name:
        return BackupError("backup.connect_failed", "Couldn't connect to the server.", detail=_short(exc))
    return BackupError("backup.upload_failed", "The SMB operation failed.", detail=_short(exc))


class _SmbRemote:
    def __init__(self, t: Target):
        self.t = t
        self.smb = _smbclient()
        self.base = "\\\\" + "\\".join([t.host, t.share, *segments(t.remote_dir)])
        # Passed to every call: smbclient looks connections up by host:port,
        # and without them a call would open its own, anonymous one on 445.
        self.kw = dict(username=t.username or None, password=t.password or None, port=t.effective_port,
                       encrypt=t.smb_encrypt, auth_protocol="ntlm", connection_timeout=TIMEOUT)

    def _path(self, name: str) -> str:
        return f"{self.base}\\{name}"

    def __enter__(self):
        _smb_lock.acquire()
        t = self.t
        try:
            self.smb.ClientConfig(skip_dfs=True)  # no referral may send the credentials to another host
            self.smb.register_session(t.host, **self.kw)
            self.smb.makedirs(self.base, exist_ok=True, **self.kw)
        except BackupError:
            self._release()
            raise
        except Exception as exc:  # smbprotocol has no common base class
            self._release()
            raise _smb_error(exc) from exc
        return self

    def _release(self):
        try:
            self.smb.delete_session(self.t.host, port=self.t.effective_port)
        except Exception:  # noqa: BLE001 — nothing to close
            pass
        finally:
            _smb_lock.release()

    def __exit__(self, *exc):
        self._release()

    def upload(self, local: Path, name: str) -> None:
        tmp = self._path(f".{name}.part")
        try:
            with open(local, "rb") as src, self.smb.open_file(tmp, mode="wb", **self.kw) as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            self.smb.rename(tmp, self._path(name), **self.kw)
            size = self.smb.stat(self._path(name), **self.kw).st_size
        except Exception as exc:
            try:
                self.smb.remove(tmp, **self.kw)  # no half-written bundle left behind
            except Exception:  # noqa: BLE001 — the connection may be gone
                pass
            raise _smb_error(exc) from exc
        if size != local.stat().st_size:
            raise BackupError("backup.verify_failed", "The uploaded file has the wrong size.",
                              detail=f"{size} != {local.stat().st_size}")

    def list(self) -> list[str]:
        return list(self.smb.listdir(self.base, **self.kw))

    def delete(self, name: str) -> None:
        self.smb.remove(self._path(name), **self.kw)

    def probe(self) -> None:
        path = self._path(f".zonecast-probe-{os.urandom(4).hex()}.tmp")
        try:
            with self.smb.open_file(path, mode="wb", **self.kw) as f:
                f.write(b"zonecast")
            self.smb.remove(path, **self.kw)
        except Exception as exc:
            raise _smb_error(exc) from exc


def open_remote(t: Target):
    validate(t)
    return _SmbRemote(t) if t.destination == "smb" else _FtpRemote(t)


def test(t: Target) -> dict:
    """Connects, writes and deletes a small file. For an FTPS server whose
    certificate isn't trusted, also returns its fingerprint so the admin
    can decide to pin it."""
    try:
        with open_remote(t) as remote:
            remote.probe()
        return {"ok": True, "code": None, "detail": "", "fingerprint": None}
    except BackupError as exc:
        fingerprint = exc.params.get("fingerprint")
        if exc.code == "backup.tls_failed" and t.destination == "ftps":
            try:
                fingerprint = server_fingerprint(t.host, t.effective_port)
            except (OSError, ssl.SSLError, ftplib.Error):
                fingerprint = None
        return {"ok": False, "code": exc.code, "detail": exc.params.get("detail", ""), "fingerprint": fingerprint}
