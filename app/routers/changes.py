from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import schemas, crud
from app.models import ChangeStatus, ChangeType, RescheduleStatus

router = APIRouter(prefix="/api/changes", tags=["变更管理"])


def _convert_change_request_to_schema(change, db: Session):
    conflict_count = len(change.conflicts) if hasattr(change, 'conflicts') else 0
    suggestion_count = len(change.reschedule_suggestions) if hasattr(change, 'reschedule_suggestions') else 0

    return schemas.ChangeRequest(
        id=change.id,
        session_id=change.session_id,
        session_title=change.session.title if change.session else "",
        requester=change.requester,
        change_type=change.change_type,
        old_start_time=change.old_start_time,
        old_end_time=change.old_end_time,
        old_audience_count=change.old_audience_count,
        old_guides_needed=change.old_guides_needed,
        new_start_time=change.new_start_time,
        new_end_time=change.new_end_time,
        new_audience_count=change.new_audience_count,
        new_guides_needed=change.new_guides_needed,
        reason=change.reason,
        status=change.status,
        reviewer=change.reviewer,
        review_comment=change.review_comment,
        reviewed_at=change.reviewed_at,
        conflict_count=conflict_count,
        suggestion_count=suggestion_count,
        created_at=change.created_at,
        updated_at=change.updated_at
    )


def _convert_conflict_to_schema(conflict):
    return schemas.SessionConflict(
        id=conflict.id,
        change_request_id=conflict.change_request_id,
        conflict_type=conflict.conflict_type,
        staff_id=conflict.staff_id,
        staff_name=conflict.staff.name if conflict.staff else None,
        assignment_id=conflict.assignment_id,
        message=conflict.message,
        detail=conflict.detail,
        status=conflict.status,
        resolved_at=conflict.resolved_at,
        created_at=conflict.created_at
    )


def _convert_suggestion_to_schema(suggestion):
    return schemas.RescheduleSuggestion(
        id=suggestion.id,
        change_request_id=suggestion.change_request_id,
        conflict_id=suggestion.conflict_id,
        staff_id=suggestion.staff_id,
        staff_name=suggestion.staff.name if suggestion.staff else None,
        suggested_staff_id=suggestion.suggested_staff_id,
        suggested_staff_name=suggestion.suggested_staff.name if suggestion.suggested_staff else None,
        action=suggestion.action,
        priority=suggestion.priority,
        reason=suggestion.reason,
        is_applied=suggestion.is_applied,
        applied_at=suggestion.applied_at,
        created_at=suggestion.created_at
    )


def _convert_history_to_schema(history):
    return schemas.ChangeHistory(
        id=history.id,
        change_request_id=history.change_request_id,
        session_id=history.session_id,
        session_title=history.session.title if history.session else "",
        operator=history.operator,
        action=history.action,
        old_values=history.old_values,
        new_values=history.new_values,
        change_type=history.change_type,
        description=history.description,
        created_at=history.created_at
    )


@router.get("", response_model=List[schemas.ChangeRequest])
def list_change_requests(
    skip: int = 0,
    limit: int = 100,
    session_id: Optional[int] = Query(None, description="场次ID"),
    status: Optional[ChangeStatus] = Query(None, description="变更状态"),
    change_type: Optional[ChangeType] = Query(None, description="变更类型"),
    db: Session = Depends(get_db)
):
    """获取变更申请列表"""
    changes = crud.get_change_request_list(db, skip=skip, limit=limit,
                                           session_id=session_id, status=status,
                                           change_type=change_type)
    return [_convert_change_request_to_schema(c, db) for c in changes]


@router.get("/{change_id}", response_model=schemas.ChangeRequestWithDetails)
def get_change_request(change_id: int, db: Session = Depends(get_db)):
    """获取变更申请详情（包含冲突和建议）"""
    change = crud.get_change_request(db, change_id)
    if not change:
        raise HTTPException(status_code=404, detail="变更申请不存在")

    base = _convert_change_request_to_schema(change, db)
    conflicts = [_convert_conflict_to_schema(c) for c in change.conflicts]
    suggestions = [_convert_suggestion_to_schema(s) for s in change.reschedule_suggestions]

    return schemas.ChangeRequestWithDetails(
        **base.model_dump(),
        conflicts=conflicts,
        suggestions=suggestions
    )


@router.post("", response_model=schemas.ChangeRequest)
def create_change_request(
    change_in: schemas.ChangeRequestCreate,
    db: Session = Depends(get_db)
):
    """提交变更申请（学校端发起）

    自动记录变更前后的快照数据，防止重复申请
    """
    change, errors = crud.create_change_request(db, change_in)
    if not change:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return _convert_change_request_to_schema(change, db)


@router.put("/{change_id}/review", response_model=schemas.ChangeRequestWithDetails)
def review_change_request(
    change_id: int,
    review_in: schemas.ChangeRequestReview,
    db: Session = Depends(get_db)
):
    """审核变更申请

    审核通过时自动触发冲突检测，生成冲突和重排建议
    """
    change, errors = crud.review_change_request(db, change_id, review_in)
    if not change:
        raise HTTPException(status_code=400, detail={"errors": errors})

    base = _convert_change_request_to_schema(change, db)
    conflicts = [_convert_conflict_to_schema(c) for c in change.conflicts]
    suggestions = [_convert_suggestion_to_schema(s) for s in change.reschedule_suggestions]

    return schemas.ChangeRequestWithDetails(
        **base.model_dump(),
        conflicts=conflicts,
        suggestions=suggestions
    )


@router.post("/{change_id}/execute", response_model=schemas.ChangeExecuteResult)
def execute_change_request(
    change_id: int,
    operator: str = Body(..., embed=True, description="操作人"),
    db: Session = Depends(get_db)
):
    """执行变更并自动重排

    自动应用重排建议，更新场次时间/人数，重新校验人员配置
    """
    result = crud.execute_change_request(db, change_id, operator)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.model_dump())
    return result


@router.get("/{change_id}/conflicts", response_model=List[schemas.SessionConflict])
def list_conflicts(
    change_id: int,
    status: Optional[RescheduleStatus] = Query(None, description="冲突状态"),
    db: Session = Depends(get_db)
):
    """获取变更关联的冲突列表"""
    change = crud.get_change_request(db, change_id)
    if not change:
        raise HTTPException(status_code=404, detail="变更申请不存在")

    conflicts = crud.get_conflict_list(db, change_request_id=change_id, status=status)
    return [_convert_conflict_to_schema(c) for c in conflicts]


@router.get("/{change_id}/suggestions", response_model=List[schemas.RescheduleSuggestion])
def list_suggestions(
    change_id: int,
    is_applied: Optional[bool] = Query(None, description="是否已应用"),
    db: Session = Depends(get_db)
):
    """获取变更关联的重排建议列表"""
    change = crud.get_change_request(db, change_id)
    if not change:
        raise HTTPException(status_code=404, detail="变更申请不存在")

    suggestions = crud.get_suggestion_list(db, change_request_id=change_id, is_applied=is_applied)
    return [_convert_suggestion_to_schema(s) for s in suggestions]


@router.post("/suggestions/{suggestion_id}/apply")
def apply_suggestion(
    suggestion_id: int,
    operator: str = Body(..., embed=True, description="操作人"),
    db: Session = Depends(get_db)
):
    """手动应用单条重排建议"""
    success, errors = crud.apply_suggestion(db, suggestion_id, operator)
    if not success:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return {"success": True, "message": "建议已应用"}


@router.get("/conflicts", response_model=List[schemas.SessionConflict])
def list_all_conflicts(
    skip: int = 0,
    limit: int = 100,
    status: Optional[RescheduleStatus] = Query(None, description="冲突状态"),
    db: Session = Depends(get_db)
):
    """获取所有冲突列表"""
    conflicts = crud.get_conflict_list(db, skip=skip, limit=limit, status=status)
    return [_convert_conflict_to_schema(c) for c in conflicts]


@router.get("/history", response_model=List[schemas.ChangeHistory])
def list_change_history(
    skip: int = 0,
    limit: int = 100,
    session_id: Optional[int] = Query(None, description="场次ID"),
    change_request_id: Optional[int] = Query(None, description="变更申请ID"),
    db: Session = Depends(get_db)
):
    """获取变更历史记录"""
    histories = crud.get_change_history_list(db, skip=skip, limit=limit,
                                             session_id=session_id,
                                             change_request_id=change_request_id)
    return [_convert_history_to_schema(h) for h in histories]


@router.post("/{session_id}/check-conflicts", response_model=schemas.ConflictCheckResult)
def pre_check_conflicts(
    session_id: int,
    new_start_time: Optional[str] = Body(None),
    new_end_time: Optional[str] = Body(None),
    new_audience_count: Optional[int] = Body(None),
    new_guides_needed: Optional[int] = Body(None),
    db: Session = Depends(get_db)
):
    """预检查变更可能产生的冲突（提交前预览）"""
    from datetime import datetime
    from app.models import ChangeRequest, ChangeType

    session = crud.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="场次不存在")

    change_type = ChangeType.OTHER
    has_time = new_start_time or new_end_time
    has_count = new_audience_count is not None or new_guides_needed is not None
    if has_time and has_count:
        change_type = ChangeType.BOTH
    elif has_time:
        change_type = ChangeType.TIME
    elif has_count:
        change_type = ChangeType.COUNT

    temp_change = ChangeRequest(
        session_id=session_id,
        session=session,
        change_type=change_type,
        new_start_time=datetime.fromisoformat(new_start_time) if new_start_time else None,
        new_end_time=datetime.fromisoformat(new_end_time) if new_end_time else None,
        new_audience_count=new_audience_count,
        new_guides_needed=new_guides_needed
    )

    conflicts, suggestions = crud.check_conflicts_and_generate_suggestions(db, temp_change)

    conflict_schemas = [_convert_conflict_to_schema(c) for c in conflicts]
    suggestion_schemas = [_convert_suggestion_to_schema(s) for s in suggestions]

    summary_parts = []
    time_conflicts = sum(1 for c in conflicts if c.conflict_type.value == "时间冲突")
    shortage_conflicts = sum(1 for c in conflicts if c.conflict_type.value == "人员不足")

    if time_conflicts > 0:
        summary_parts.append(f"检测到 {time_conflicts} 个时间冲突")
    if shortage_conflicts > 0:
        summary_parts.append(f"检测到 {shortage_conflicts} 个人员缺口")
    if not conflicts:
        summary_parts.append("未检测到冲突")
    if suggestions:
        summary_parts.append(f"生成 {len(suggestions)} 条重排建议")

    summary = "，".join(summary_parts)

    return schemas.ConflictCheckResult(
        has_conflicts=len(conflicts) > 0,
        conflicts=conflict_schemas,
        suggestions=suggestion_schemas,
        summary=summary
    )
