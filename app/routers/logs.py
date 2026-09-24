import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import EventLog, User
from ..schemas import EventLogOut, LogSettingsOut, LogSettingsUpdate
from ..services.app_settings import get_settings

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _filtered_query(db: Session, level: str | None, q: str | None):
    query = db.query(EventLog)
    if level:
        query = query.filter(EventLog.level == level.upper())
    if q:
        query = query.filter(EventLog.message.ilike(f"%{q}%"))
    return query.order_by(EventLog.id.desc())


@router.get("", response_model=list[EventLogOut])
def list_logs(
    limit: int = 200,
    level: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return _filtered_query(db, level, q).limit(min(limit, 1000)).all()


@router.get("/export")
def export_logs(
    format: str = "csv",
    level: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Downloads the full (filtered) log history — not capped like the
    dashboard's live view — as CSV or JSON, for archiving or sharing
    outside the app."""
    rows = _filtered_query(db, level, q).all()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if format == "json":
        payload = [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "level": r.level,
                "logger_name": r.logger_name,
                "message": r.message,
            }
            for r in rows
        ]
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="zonecast_logs_{ts}.json"'},
        )

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "created_at", "level", "logger_name", "message"])
        for r in rows:
            writer.writerow([r.id, r.created_at.isoformat(), r.level, r.logger_name, r.message])
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="zonecast_logs_{ts}.csv"'},
        )

    raise HTTPException(status_code=400, detail="Formato non valido: usare 'csv' o 'json'")


@router.get("/settings", response_model=LogSettingsOut)
def get_log_settings(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return LogSettingsOut(retention_days=get_settings(db).log_retention_days)


@router.put("/settings", response_model=LogSettingsOut)
def update_log_settings(payload: LogSettingsUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    settings_row = get_settings(db)
    settings_row.log_retention_days = payload.retention_days
    db.commit()
    return LogSettingsOut(retention_days=settings_row.log_retention_days)


@router.delete("")
def clear_logs(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Manual "Pulisci log" action — deletes the full history now,
    independent of the automatic retention-by-age setting above."""
    deleted = db.query(EventLog).delete()
    db.commit()
    return {"ok": True, "deleted": deleted}
