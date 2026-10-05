from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/schools", tags=["学校管理"])


@router.get("", response_model=List[schemas.School])
def list_schools(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取学校列表"""
    return crud.get_school_list(db, skip=skip, limit=limit)


@router.get("/{school_id}", response_model=schemas.School)
def get_school(school_id: int, db: Session = Depends(get_db)):
    """获取学校详情"""
    school = crud.get_school(db, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="学校不存在")
    return school


@router.post("", response_model=schemas.School)
def create_school(school_in: schemas.SchoolCreate, db: Session = Depends(get_db)):
    """新增学校"""
    return crud.create_school(db, school_in)


@router.put("/{school_id}", response_model=schemas.School)
def update_school(school_id: int, school_in: schemas.SchoolUpdate, db: Session = Depends(get_db)):
    """更新学校"""
    school = crud.get_school(db, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="学校不存在")
    update_data = school_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(school, field, value)
    db.commit()
    db.refresh(school)
    return school
