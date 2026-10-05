from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app import schemas, crud
from app.models import SessionStatus, AssignmentRole

router = APIRouter(prefix="/api/sessions", tags=["场次管理"])


def _convert_session_to_schema(session, db: Session):
    assignments = []
    for a in session.assignments:
        assignments.append(schemas.Assignment(
            id=a.id,
            staff_id=a.staff_id,
            staff_name=a.staff.name if a.staff else "",
            staff_type=a.staff.staff_type.value if a.staff else "",
            star_rating=a.staff.star_rating if a.staff else 0,
            role=a.role,
            is_primary=a.is_primary,
            created_at=a.created_at
        ))

    return schemas.Session(
        id=session.id,
        title=session.title,
        theme_id=session.theme_id,
        theme_name=session.theme.name if session.theme else "",
        venue_id=session.venue_id,
        venue_name=session.venue.name if session.venue else "",
        session_type=session.session_type,
        start_time=session.start_time,
        end_time=session.end_time,
        audience_type=session.audience_type,
        audience_count=session.audience_count,
        school_id=session.school_id,
        school_name=session.school.name if session.school else None,
        guides_needed=session.guides_needed,
        needs_lecturer=session.needs_lecturer,
        status=session.status,
        description=session.description,
        assignments=assignments,
        is_fully_staffed=crud.is_session_fully_staffed(db, session.id),
        created_at=session.created_at,
        updated_at=session.updated_at or session.created_at
    )


@router.get("", response_model=List[schemas.Session])
def list_sessions(
    skip: int = 0,
    limit: int = 100,
    status: Optional[SessionStatus] = Query(None, description="场次状态"),
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    theme_id: Optional[int] = Query(None, description="主题ID"),
    db: Session = Depends(get_db)
):
    """获取场次列表"""
    sessions = crud.get_session_list(db, skip=skip, limit=limit,
                                     status=status, start_date=start_date,
                                     end_date=end_date, theme_id=theme_id)
    return [_convert_session_to_schema(s, db) for s in sessions]


@router.get("/{session_id}", response_model=schemas.Session)
def get_session(session_id: int, db: Session = Depends(get_db)):
    """获取场次详情"""
    session = crud.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="场次不存在")
    return _convert_session_to_schema(session, db)


@router.post("", response_model=schemas.Session)
def create_session(session_in: schemas.SessionCreate, db: Session = Depends(get_db)):
    """创建场次"""
    session = crud.create_session(db, session_in)
    return _convert_session_to_schema(session, db)


@router.put("/{session_id}", response_model=schemas.Session)
def update_session(
    session_id: int,
    session_in: schemas.SessionUpdate,
    db: Session = Depends(get_db)
):
    """更新场次（时间或人数变动时自动校验排班冲突）"""
    session, errors = crud.update_session(db, session_id, session_in)
    if not session:
        raise HTTPException(status_code=404, detail="场次不存在")
    if errors:
        raise HTTPException(status_code=400, detail={"message": "更新成功但存在冲突", "errors": errors})
    return _convert_session_to_schema(session, db)


@router.post("/{session_id}/assignments", response_model=schemas.Assignment)
def create_assignment(
    session_id: int,
    assignment_in: schemas.AssignmentCreate,
    db: Session = Depends(get_db)
):
    """添加排班（自动校验时间冲突和资格）"""
    assignment, errors = crud.create_assignment(db, session_id, assignment_in)
    if not assignment:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return schemas.Assignment(
        id=assignment.id,
        staff_id=assignment.staff_id,
        staff_name=assignment.staff.name if assignment.staff else "",
        staff_type=assignment.staff.staff_type.value if assignment.staff else "",
        star_rating=assignment.staff.star_rating if assignment.staff else 0,
        role=assignment.role,
        is_primary=assignment.is_primary,
        created_at=assignment.created_at
    )


@router.delete("/{session_id}/assignments/{assignment_id}")
def delete_assignment(session_id: int, assignment_id: int, db: Session = Depends(get_db)):
    """取消排班"""
    success, errors = crud.delete_assignment(db, assignment_id)
    if not success:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return {"success": True, "message": "排班已取消"}


@router.get("/{session_id}/validate", response_model=schemas.AssignmentValidationResult)
def validate_staff(
    session_id: int,
    staff_id: int,
    role: AssignmentRole,
    db: Session = Depends(get_db)
):
    """预校验某人员是否可安排到该场次"""
    return crud.validate_assignment(db, session_id, staff_id, role)


@router.post("/{session_id}/auto-assign", response_model=schemas.BulkAssignmentResponse)
def auto_assign_staff(session_id: int, db: Session = Depends(get_db)):
    """自动为场次推荐并分配最合适的人员"""
    session = crud.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="场次不存在")

    errors = []
    assigned_staff = []

    if session.needs_lecturer:
        has_lecturer = any(a.role == AssignmentRole.LECTURER for a in session.assignments)
        if not has_lecturer:
            lecturers = crud.get_recommended_staff(db, session_id, AssignmentRole.LECTURER, limit=5)
            available_lecturers = [l for l in lecturers if l.is_available]
            if available_lecturers:
                assignment, errs = crud.create_assignment(db, session_id, schemas.AssignmentCreate(
                    staff_id=available_lecturers[0].staff_id,
                    role=AssignmentRole.LECTURER,
                    is_primary=True
                ))
                if assignment:
                    assigned_staff.append(available_lecturers[0].staff_id)
                else:
                    errors.extend(errs)
            else:
                errors.append("无可用讲师")

    current_guides = sum(1 for a in session.assignments if a.role == AssignmentRole.GUIDE)
    guides_needed = max(0, session.guides_needed - current_guides)

    if guides_needed > 0:
        guides = crud.get_recommended_staff(db, session_id, AssignmentRole.GUIDE, limit=guides_needed * 2)
        available_guides = [g for g in guides if g.is_available and g.staff_id not in assigned_staff]
        for guide in available_guides[:guides_needed]:
            assignment, errs = crud.create_assignment(db, session_id, schemas.AssignmentCreate(
                staff_id=guide.staff_id,
                role=AssignmentRole.GUIDE
            ))
            if assignment:
                assigned_staff.append(guide.staff_id)
            else:
                errors.extend(errs)

    success = len(assigned_staff) > 0 and len(errors) == 0
    message = f"成功分配 {len(assigned_staff)} 名人员" if success else "部分或全部人员分配失败"

    return schemas.BulkAssignmentResponse(
        success=success,
        message=message,
        assigned_staff=assigned_staff,
        errors=errors
    )
