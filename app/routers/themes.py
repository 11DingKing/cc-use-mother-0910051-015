from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/themes", tags=["主题管理"])


@router.get("", response_model=List[schemas.Theme])
def list_themes(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取主题列表"""
    return crud.get_theme_list(db, skip=skip, limit=limit)


@router.get("/{theme_id}", response_model=schemas.Theme)
def get_theme(theme_id: int, db: Session = Depends(get_db)):
    """获取主题详情"""
    theme = crud.get_theme(db, theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="主题不存在")
    return theme


@router.post("", response_model=schemas.Theme)
def create_theme(theme_in: schemas.ThemeCreate, db: Session = Depends(get_db)):
    """新增主题"""
    return crud.create_theme(db, theme_in)


@router.put("/{theme_id}", response_model=schemas.Theme)
def update_theme(theme_id: int, theme_in: schemas.ThemeUpdate, db: Session = Depends(get_db)):
    """更新主题"""
    theme = crud.get_theme(db, theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="主题不存在")
    update_data = theme_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(theme, field, value)
    db.commit()
    db.refresh(theme)
    return theme
