from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/venues", tags=["场地管理"])


@router.get("", response_model=List[schemas.Venue])
def list_venues(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取场地列表"""
    return crud.get_venue_list(db, skip=skip, limit=limit)


@router.get("/{venue_id}", response_model=schemas.Venue)
def get_venue(venue_id: int, db: Session = Depends(get_db)):
    """获取场地详情"""
    venue = crud.get_venue(db, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")
    return venue


@router.post("", response_model=schemas.Venue)
def create_venue(venue_in: schemas.VenueCreate, db: Session = Depends(get_db)):
    """新增场地"""
    return crud.create_venue(db, venue_in)


@router.put("/{venue_id}", response_model=schemas.Venue)
def update_venue(venue_id: int, venue_in: schemas.VenueUpdate, db: Session = Depends(get_db)):
    """更新场地"""
    venue = crud.get_venue(db, venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")
    update_data = venue_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(venue, field, value)
    db.commit()
    db.refresh(venue)
    return venue
