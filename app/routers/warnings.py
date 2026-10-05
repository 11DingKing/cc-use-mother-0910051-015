from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/warnings", tags=["预警管理"])


def _convert_warning_to_schema(warning):
    return schemas.Warning(
        id=warning.id,
        theme_id=warning.theme_id,
        theme_name=warning.theme.name if warning.theme else None,
        warning_type=warning.warning_type,
        message=warning.message,
        resolved=warning.resolved,
        created_at=warning.created_at
    )


@router.get("", response_model=List[schemas.Warning])
def list_warnings(
    resolved: Optional[bool] = Query(None, description="是否已解决"),
    db: Session = Depends(get_db)
):
    """获取预警列表"""
    warnings = crud.get_warning_list(db, resolved=resolved)
    return [_convert_warning_to_schema(w) for w in warnings]


@router.post("/check", response_model=List[schemas.Warning])
def check_shortage(db: Session = Depends(get_db)):
    """检查人员缺口并生成预警"""
    warnings = crud.check_staff_shortage(db)
    return [_convert_warning_to_schema(w) for w in warnings]


@router.put("/{warning_id}/resolve", response_model=schemas.Warning)
def resolve_warning(warning_id: int, db: Session = Depends(get_db)):
    """标记预警为已解决"""
    from app.models import Warning as WarningModel
    warning = db.query(WarningModel).filter(WarningModel.id == warning_id).first()
    if not warning:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="预警不存在")
    warning.resolved = True
    db.commit()
    db.refresh(warning)
    return _convert_warning_to_schema(warning)
