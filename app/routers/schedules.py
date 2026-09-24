from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Schedule, User
from ..schemas import ScheduleCreate, ScheduleUpdate, ScheduleOut
from ..services import scheduler as scheduler_service

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Schedule).order_by(Schedule.time_of_day).all()


@router.post("", response_model=ScheduleOut)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sched = Schedule(**payload.model_dump(), created_by_id=user.id)
    db.add(sched)
    db.commit()
    db.refresh(sched)
    scheduler_service.add_or_update_job(sched)
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedulazione non trovata")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(sched, k, v)
    db.commit()
    db.refresh(sched)
    scheduler_service.add_or_update_job(sched)
    return sched


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedulazione non trovata")
    db.delete(sched)
    db.commit()
    scheduler_service.remove_job(schedule_id)
    return {"ok": True}
