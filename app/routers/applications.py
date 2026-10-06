"""团体申请容量分配与候补递补接口"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import schemas
from app.models import (
    AllocationStatus,
    ApplicationAllocation,
    GroupApplication,
    GroupApplicationStatus,
    WaitlistEntry,
)
from app.services import group_allocation as svc

router = APIRouter(prefix="/api/group-applications", tags=["团体容量分配"])


def _line_schema(line: ApplicationAllocation) -> schemas.AllocationLine:
    return schemas.AllocationLine(
        id=line.id,
        session_id=line.session_id,
        session_title=line.session.title if line.session else "",
        session_start_time=line.session.start_time if line.session else None,
        count=line.count,
        status=line.status.value,
        source=line.source.value,
        source_waitlist_seq=line.source_waitlist_seq,
        confirm_deadline=line.confirm_deadline,
        confirmed_at=line.confirmed_at,
        cancel_reason=line.cancel_reason,
    )


def _waitlist_item(entry: WaitlistEntry) -> schemas.WaitlistItem:
    try:
        prefs = [int(x) for x in json.loads(entry.preferred_session_ids or "[]")]
    except (ValueError, TypeError):
        prefs = []
    return schemas.WaitlistItem(
        seq=entry.seq,
        application_id=entry.application_id,
        school_name=entry.application.school.name if entry.application.school else "",
        remaining_count=entry.remaining_count,
        original_count=entry.original_count,
        status=entry.status.value,
        preferred_session_ids=prefs,
    )


def _conservation(db: Session, app: GroupApplication) -> schemas.ConservationSummary:
    active = svc._active_allocations(db, app.id)
    confirmed = sum(a.count for a in active if a.status == AllocationStatus.CONFIRMED)
    proposed = sum(a.count for a in active if a.status == AllocationStatus.PROPOSED)
    waiting = svc._waiting_count(db, app.id)
    current = confirmed + proposed + waiting
    terminal = app.status in (GroupApplicationStatus.CANCELLED, GroupApplicationStatus.EXPIRED)
    conserved = (current == 0) if terminal else (current == app.total_count)
    if terminal:
        detail = (f"申请已终结（{app.status.value}）：全部 {app.total_count} 人已通过取消/"
                  f"过期事件逐条核销，当前在场人数为 {current}")
    else:
        detail = (f"守恒校验：申请总人数 {app.total_count} = 正式占用 {confirmed} + "
                  f"待确认预留 {proposed} + 候补 {waiting}")
    return schemas.ConservationSummary(
        total_count=app.total_count,
        occupied_confirmed=confirmed,
        soft_reserved=proposed,
        waiting=waiting,
        current_total=current,
        conserved=conserved,
        detail=detail,
    )


def _summary(db: Session, app: GroupApplication) -> schemas.GroupApplicationSummary:
    c = _conservation(db, app)
    return schemas.GroupApplicationSummary(
        id=app.id,
        school_id=app.school_id,
        school_name=app.school.name if app.school else "",
        total_count=app.total_count,
        min_group_size=app.min_group_size,
        status=app.status.value,
        companion_note=app.companion_note,
        confirm_deadline=app.confirm_deadline,
        occupied_confirmed=c.occupied_confirmed,
        soft_reserved=c.soft_reserved,
        waiting=c.waiting,
        current_total=c.current_total,
        conserved=c.conserved,
        created_at=app.created_at,
    )


def _detail(db: Session, app: GroupApplication) -> schemas.GroupApplicationDetail:
    base = _summary(db, app)
    prefs = sorted(app.preferences, key=lambda p: p.seq)
    events = []
    for e in sorted(app.events, key=lambda x: x.id):
        try:
            snapshot = json.loads(e.snapshot or "{}")
        except (ValueError, TypeError):
            snapshot = {}
        events.append(schemas.AllocationEventSchema(
            id=e.id,
            action=e.action.value,
            count=e.count,
            session_id=e.session_id,
            session_title=e.session.title if e.session else None,
            waitlist_seq=e.waitlist_seq,
            reason=e.reason,
            snapshot=snapshot,
            created_at=e.created_at,
        ))
    return schemas.GroupApplicationDetail(
        **base.model_dump(),
        contact_person=app.contact_person,
        phone=app.phone,
        preferences=[p.session_id for p in prefs],
        allocations=[_line_schema(a) for a in sorted(app.allocations, key=lambda x: x.id)],
        waitlist=[_waitlist_item(e) for e in sorted(app.waitlist_entries, key=lambda x: x.seq)],
        events=events,
        conservation=_conservation(db, app),
    )


def _handle_value_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# 申请提交 / 查询 / 确认 / 缩减 / 取消
# ---------------------------------------------------------------------------

@router.post("", response_model=schemas.GroupApplicationDetail, status_code=201)
def submit_application(payload: schemas.GroupApplicationCreate,
                       db: Session = Depends(get_db)):
    """提交团体申请：系统按时间偏好、最小成团人数与同行约束原子拆分，
    未满足部分进入有顺序依据的候补队列（学校确认前仅为软预留）。"""
    app = _handle_value_error(svc.create_application, db, payload)
    return _detail(db, app)


@router.get("", response_model=List[schemas.GroupApplicationSummary])
def list_applications(
    school_id: Optional[int] = Query(None),
    status: Optional[GroupApplicationStatus] = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return [_summary(db, a) for a in svc.list_applications(db, school_id, status, skip, limit)]


@router.get("/{application_id}", response_model=schemas.GroupApplicationDetail)
def get_application(application_id: int, db: Session = Depends(get_db)):
    """申请详情：含每场占用、候补条目与全部拆分/递补事件（每条都带原因与守恒快照）。"""
    try:
        app = svc.get_application(db, application_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _detail(db, app)


@router.post("/{application_id}/confirm", response_model=schemas.GroupApplicationDetail)
def confirm_application(application_id: int,
                        payload: Optional[schemas.GroupApplicationConfirm] = None,
                        db: Session = Depends(get_db)):
    """学校确认（请求体为空表示全部接受；只传部分明细ID为部分接受，其余退回候补）。"""
    ids = payload.accept_allocation_ids if payload else None
    app = _handle_value_error(svc.confirm_application, db, application_id, ids)
    return _detail(db, app)


@router.post("/{application_id}/reduce", response_model=schemas.GroupApplicationDetail)
def reduce_application(application_id: int,
                       payload: schemas.GroupApplicationReduce,
                       db: Session = Depends(get_db)):
    """学校缩减人数：按 候补→待确认→已确认 顺序腾出，释放容量原子递补。"""
    app = _handle_value_error(svc.reduce_application, db, application_id, payload.new_total)
    return _detail(db, app)


@router.post("/{application_id}/cancel", response_model=schemas.GroupApplicationDetail)
def cancel_application(application_id: int,
                       payload: Optional[schemas.ReasonRequest] = None,
                       db: Session = Depends(get_db)):
    """整体取消申请：释放全部容量并触发候补递补。"""
    reason = payload.reason if payload else None
    app = _handle_value_error(svc.cancel_application, db, application_id, reason)
    return _detail(db, app)


@router.post("/{application_id}/allocations/{allocation_id}/cancel",
             response_model=schemas.GroupApplicationDetail)
def cancel_allocation_line(application_id: int, allocation_id: int,
                           payload: Optional[schemas.ReasonRequest] = None,
                           db: Session = Depends(get_db)):
    """退出某一场（跨场调整）：人数退回候补，该场容量立即递补给队列中的团队。"""
    reason = payload.reason if payload else None
    app = _handle_value_error(svc.cancel_allocation_line,
                              db, application_id, allocation_id, reason)
    return _detail(db, app)


# ---------------------------------------------------------------------------
# 候补队列与场次容量
# ---------------------------------------------------------------------------

@router.get("/waitlist/queue", response_model=List[schemas.WaitlistItem],
            tags=["团体容量分配"])
def get_waitlist(session_id: Optional[int] = Query(None, description="按意向场次过滤"),
                 db: Session = Depends(get_db)):
    """查看全局候补队列；顺序依据为排队序号 seq 升序。"""
    return [_waitlist_item(e) for e in svc.list_waitlist(db, session_id)]


@router.put("/waitlist/{seq}/preferences", response_model=schemas.WaitlistItem)
def update_waitlist_preferences(seq: int,
                                payload: schemas.WaitlistPrefsUpdate,
                                db: Session = Depends(get_db)):
    """补登候补团队的时间偏好（原偏好场次全部取消后使用），保存后立即尝试递补。"""
    entry = _handle_value_error(svc.update_waitlist_preferences,
                                db, seq, payload.preferred_session_ids)
    return _waitlist_item(entry)


@router.post("/sessions/{session_id}/promote")
def promote_session(session_id: int, db: Session = Depends(get_db)):
    """人工触发某场按候补顺序与当前资格原子递补，返回本次递补生成的明细数。"""
    count = _handle_value_error(svc.promote_session_manually, db, session_id)
    return {"session_id": session_id, "promoted_lines": count}


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: int,
                   payload: Optional[schemas.SessionCancelRequest] = None,
                   db: Session = Depends(get_db)):
    """跨场取消整场：解除全部占用与讲解员排班，受影响团队按其余偏好重新入候补。"""
    reason = payload.reason if payload else None
    operator = payload.operator if payload else "工作人员"
    try:
        result = svc.cancel_session(db, session_id, reason, operator)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.get("/sessions/{session_id}/capacity", response_model=schemas.SessionCapacityView)
def session_capacity(session_id: int, db: Session = Depends(get_db)):
    """场次容量视图：正式占用、软预留、剩余容量与意向该场的候补队列。"""
    try:
        return svc.session_capacity_view(db, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/waitlist/expire-overdue")
def expire_overdue(db: Session = Depends(get_db)):
    """扫描并处理超时未确认的申请（建议定时调用），返回被过期处理的申请ID列表。"""
    ids = svc.expire_overdue(db)
    return {"expired_application_ids": ids, "count": len(ids)}
