from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import schemas, group_allocation
from app.models import GroupApplicationStatus

router = APIRouter(prefix="/api/group-applications", tags=["团体申请容量分配"])


@router.post("", response_model=schemas.GroupApplicationDetail)
def create_group_application(
    app_in: schemas.GroupApplicationCreate,
    db: Session = Depends(get_db)
):
    """提交团体申请：系统按时间偏好、最小成团人数和同行约束自动拆分到多个场次。

    拆分结果仅为待确认方案（软占用），学校确认后才形成正式占用；
    未满足部分进入有顺序依据的候补队列。
    """
    application, errors = group_allocation.create_application_and_split(db, app_in)
    if not application:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return group_allocation.build_detail(db, application)


@router.get("", response_model=List[schemas.GroupApplicationItem])
def list_group_applications(
    school_id: Optional[int] = Query(None, description="学校ID"),
    status: Optional[GroupApplicationStatus] = Query(None, description="申请状态"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取团体申请列表（含各状态人数汇总）"""
    return group_allocation.get_application_list(
        db, school_id=school_id, status=status, skip=skip, limit=limit)


@router.post("/expire", response_model=schemas.ExpireSweepResult)
def expire_overdue_allocations(db: Session = Depends(get_db)):
    """清理超时未确认的待确认分配：软占用释放，人数回到候补队列队尾并触发递补"""
    return group_allocation.expire_overdue(db)


@router.get("/session-waitlist/{session_id}", response_model=schemas.SessionWaitlistView)
def get_session_waitlist(session_id: int, db: Session = Depends(get_db)):
    """查看某场次的候补队列（顺序依据 + 每个候补的当前资格说明）"""
    view = group_allocation.get_session_waitlist(db, session_id)
    if not view:
        raise HTTPException(status_code=404, detail="场次不存在")
    return view


@router.post("/process-waitlist", response_model=dict)
def process_waitlist(
    session_id: Optional[int] = Query(None, description="场次ID，不传则全局扫描"),
    db: Session = Depends(get_db)
):
    """手动触发候补递补：容量释放时按当前资格原子递补"""
    promoted = group_allocation.process_waitlist(db, session_id=session_id)
    return {"success": True, "promoted_count": promoted,
            "message": f"候补递补完成，共递补{promoted}人"}


@router.get("/{application_id}", response_model=schemas.GroupApplicationDetail)
def get_group_application(application_id: int, db: Session = Depends(get_db)):
    """获取团体申请详情：拆分方案、候补、事件流水（含每次拆分/递补原因）与守恒校验"""
    application = group_allocation.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="团体申请不存在")
    return group_allocation.build_detail(db, application)


@router.post("/{application_id}/confirm", response_model=schemas.GroupApplicationDetail)
def confirm_group_application(
    application_id: int,
    confirm_in: schemas.GroupApplicationConfirmRequest,
    db: Session = Depends(get_db)
):
    """学校确认拆分方案：确认人数转为正式占用，未接受部分回到候补队列"""
    application, errors = group_allocation.confirm_application(db, application_id, confirm_in)
    if not application:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return group_allocation.build_detail(db, application)


@router.post("/{application_id}/reduce", response_model=schemas.GroupApplicationDetail)
def reduce_group_application(
    application_id: int,
    reduce_in: schemas.GroupApplicationReduceRequest,
    db: Session = Depends(get_db)
):
    """学校缩减总人数：依次从候补、待确认、已确认中扣减，保持人数守恒"""
    application, errors = group_allocation.reduce_application(db, application_id, reduce_in)
    if not application:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return group_allocation.build_detail(db, application)


@router.post("/{application_id}/cancel", response_model=schemas.GroupApplicationDetail)
def cancel_group_application(
    application_id: int,
    cancel_in: schemas.GroupApplicationCancelRequest,
    db: Session = Depends(get_db)
):
    """取消整个团体申请：候补、待确认、已确认全部释放"""
    application, errors = group_allocation.cancel_application(db, application_id, cancel_in)
    if not application:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return group_allocation.build_detail(db, application)


@router.get("/{application_id}/conservation", response_model=schemas.ConservationSummary)
def get_application_conservation(application_id: int, db: Session = Depends(get_db)):
    """人数守恒校验：待确认 + 已确认 + 候补 = 当前总人数；当前总人数 + 已释放 = 原始报名"""
    summary = group_allocation.get_conservation(db, application_id)
    if not summary:
        raise HTTPException(status_code=404, detail="团体申请不存在")
    return summary


@router.get("/{application_id}/events", response_model=List[schemas.AllocationEventItem])
def get_application_events(application_id: int, db: Session = Depends(get_db)):
    """获取申请的事件流水：每次拆分、确认、缩减、超时、递补、取消的原因"""
    application = group_allocation.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="团体申请不存在")
    return group_allocation.get_event_list(db, application_id)
