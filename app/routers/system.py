import os
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..config import settings as app_config
from ..database import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import (
    TimeStatusOut, SetManualTimeRequest, SetNtpEnabledRequest, SetTimezoneRequest, SetNtpServersRequest,
    NetworkStatusOut, NetworkApplyRequest, NetworkApplyOut,
    ThemeOut, ThemeUpdate, ExportRequest, ImportStagedOut, HostResourcesOut, VersionOut, AlertOut,
)
from ..services import system_time, bundle, network_config, pending_import, host_resources, alerts as alerts_service
from ..version import APP_VERSION, CHANGELOG
from ..services.app_settings import get_settings

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/export")
def export_bundle(payload: ExportRequest, _: User = Depends(require_admin)):
    """Full data export (DB + encryption key + media + config backups)
    for moving ZoneCast to a new machine — see services/bundle.py. The
    password is optional: leaving it empty still produces a valid
    (weakly protected) bundle, but the file contains device
    credentials, password hashes and TOTP secrets regardless, so it
    should still be handled as sensitive."""
    try:
        data = bundle.build_export(
            password=payload.password,
            db_path=app_config.db_path,
            secret_key_path=app_config.secret_key_path,
            media_dir=app_config.media_dir,
            backups_dir=app_config.backups_dir,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export fallito: {exc}") from exc
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="zonecast_export_{ts}.zcbundle"'},
    )


@router.post("/import", response_model=ImportStagedOut)
async def import_bundle(
    file: UploadFile = File(...),
    password: str = Form(""),
    _: User = Depends(require_admin),
):
    """Restores a full export (see /export above) into THIS
    installation, replacing its database/media/backups. Validates and
    stages the bundle here (a wrong password fails immediately, before
    touching anything), then forces the process to exit — Docker's
    `restart: unless-stopped` / systemd's `Restart=always` bring it
    back up, and the swap is completed at that next startup, before
    the database is opened (see services/pending_import.py). The
    current data is backed up alongside (timestamped) rather than
    deleted, in case this was run against live data by mistake."""
    data = await file.read()
    try:
        pending_import.stage(data, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Import fallito: {exc}") from exc

    def _restart_soon():
        time.sleep(1.5)  # let the HTTP response reach the client first
        os._exit(1)  # non-zero: triggers restart under both restart policies

    threading.Thread(target=_restart_soon, daemon=True).start()
    return ImportStagedOut(
        ok=True,
        message="Import verificato e messo in coda. Il servizio si riavvia ora per completarlo — la pagina si disconnetterà per qualche secondo.",
    )


@router.get("/time", response_model=TimeStatusOut)
def get_time(_: User = Depends(require_admin)):
    try:
        return system_time.get_status().__dict__
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/time/manual")
def set_manual_time(payload: SetManualTimeRequest, _: User = Depends(require_admin)):
    try:
        ntp_disabled = system_time.set_manual_time(payload.datetime_local)
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "ntp_disabled": ntp_disabled}


@router.get("/time/timezones", response_model=list[str])
def get_timezones(_: User = Depends(require_admin)):
    try:
        return system_time.list_timezones()
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/time/ntp")
def set_ntp(payload: SetNtpEnabledRequest, _: User = Depends(require_admin)):
    try:
        system_time.set_ntp_enabled(payload.enabled)
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/time/timezone")
def set_timezone(payload: SetTimezoneRequest, _: User = Depends(require_admin)):
    try:
        system_time.set_timezone(payload.timezone)
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/time/ntp-servers")
def set_ntp_servers(payload: SetNtpServersRequest, _: User = Depends(require_admin)):
    try:
        system_time.set_ntp_servers(payload.servers)
    except system_time.SystemTimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/alerts", response_model=list[AlertOut])
def get_alerts(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [a.__dict__ for a in alerts_service.get_alerts(db)]


@router.get("/resources", response_model=HostResourcesOut)
def get_resources(_: User = Depends(require_admin)):
    try:
        return host_resources.get_snapshot().__dict__
    except host_resources.HostResourcesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/network", response_model=NetworkStatusOut)
def get_network(interface: str | None = None, _: User = Depends(require_admin)):
    try:
        return network_config.get_current(interface).__dict__
    except network_config.NetworkConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/network/apply", response_model=NetworkApplyOut)
def apply_network(payload: NetworkApplyRequest, _: User = Depends(require_admin)):
    """Applies immediately with an auto-revert safety net — see
    services/network_config.py and deploy/zonecast-netctl.sh. If the
    new address is wrong, the dashboard becomes unreachable at the OLD
    address; reconnect at the NEW one and call /network/confirm within
    the watchdog window, or it reverts on its own."""
    try:
        seconds = network_config.apply(
            payload.interface, payload.address_cidr, payload.gateway, payload.dns_servers
        )
    except network_config.NetworkConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return NetworkApplyOut(watchdog_seconds=seconds)


@router.post("/network/confirm")
def confirm_network(_: User = Depends(require_admin)):
    try:
        network_config.confirm()
    except network_config.NetworkConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/network/pending")
def network_pending(_: User = Depends(require_admin)):
    return {"pending_seconds": network_config.pending()}


@router.get("/version", response_model=VersionOut)
def get_version():
    # No auth dependency: shown on the login page too (see below).
    return VersionOut(version=APP_VERSION, changelog=CHANGELOG)


@router.get("/theme", response_model=ThemeOut)
def get_theme(db: Session = Depends(get_db)):
    # No auth dependency: the login page (pre-authentication) also needs
    # this to render with the right colors, and the value isn't sensitive.
    return get_settings(db)


@router.put("/theme", response_model=ThemeOut)
def set_theme(payload: ThemeUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    settings_row = get_settings(db)
    settings_row.theme_color = payload.theme_color
    db.commit()
    db.refresh(settings_row)
    return settings_row
