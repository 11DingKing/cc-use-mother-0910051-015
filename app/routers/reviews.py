from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/reviews", tags=["评价管理"])


def _convert_review_to_schema(review):
    return schemas.Review(
        id=review.id,
        session_id=review.session_id,
        session_title=review.session.title if review.session else "",
        reviewer_name=review.reviewer_name,
        reviewer_type=review.reviewer_type,
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at
    )


@router.get("", response_model=List[schemas.Review])
def list_reviews(
    skip: int = 0,
    limit: int = 100,
    session_id: Optional[int] = Query(None, description="场次ID"),
    staff_id: Optional[int] = Query(None, description="人员ID"),
    db: Session = Depends(get_db)
):
    """获取评价列表"""
    reviews = crud.get_review_list(db, skip=skip, limit=limit,
                                   session_id=session_id, staff_id=staff_id)
    return [_convert_review_to_schema(r) for r in reviews]


@router.get("/{review_id}", response_model=schemas.Review)
def get_review(review_id: int, db: Session = Depends(get_db)):
    """获取评价详情"""
    review = crud.get_review(db, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="评价不存在")
    return _convert_review_to_schema(review)


@router.post("", response_model=schemas.Review)
def create_review(review_in: schemas.ReviewCreate, db: Session = Depends(get_db)):
    """新增评价（自动更新人员星级和服务时长）"""
    review = crud.create_review(db, review_in)
    return _convert_review_to_schema(review)
