from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app import schemas, crud
from app.models import StaffType, AssignmentRole

router = APIRouter(prefix="/api/staff", tags=["人员管理"])


def _convert_staff_to_schema(staff):
    themes = []
    for st in staff.themes:
        themes.append(schemas.StaffTheme(
            id=st.id,
            theme_id=st.theme_id,
            theme_name=st.theme.name if st.theme else "",
            proficiency_level=st.proficiency_level
        ))

    venues = []
    for sv in staff.venues:
        venues.append(schemas.StaffVenue(
            id=sv.id,
            venue_id=sv.venue_id,
            venue_name=sv.venue.name if sv.venue else "",
            is_certified=sv.is_certified
        ))

    return schemas.Staff(
        id=staff.id,
        name=staff.name,
        staff_type=staff.staff_type,
        phone=staff.phone,
        email=staff.email,
        total_service_hours=staff.total_service_hours,
        star_rating=staff.star_rating,
        review_count=staff.review_count,
        is_active=staff.is_active,
        total_points=staff.total_points or 0,
        current_level=staff.current_level or 1,
        current_badge_id=staff.current_badge_id,
        is_excellent=staff.is_excellent or False,
        excellent_until=staff.excellent_until,
        themes=themes,
        venues=venues,
        created_at=staff.created_at
    )


@router.get("", response_model=List[schemas.Staff])
def list_staff(
    skip: int = 0,
    limit: int = 100,
    staff_type: Optional[StaffType] = Query(None, description="人员类型"),
    theme_id: Optional[int] = Query(None, description="擅长主题ID"),
    db: Session = Depends(get_db)
):
    """获取人员列表"""
    staff_list = crud.get_staff_list(db, skip=skip, limit=limit,
                                     staff_type=staff_type, theme_id=theme_id)
    return [_convert_staff_to_schema(s) for s in staff_list]


@router.get("/{staff_id}", response_model=schemas.Staff)
def get_staff(staff_id: int, db: Session = Depends(get_db)):
    """获取人员详情"""
    staff = crud.get_staff(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    return _convert_staff_to_schema(staff)


@router.post("", response_model=schemas.Staff)
def create_staff(staff_in: schemas.StaffCreate, db: Session = Depends(get_db)):
    """新增人员"""
    staff = crud.create_staff(db, staff_in)
    return _convert_staff_to_schema(staff)


@router.put("/{staff_id}", response_model=schemas.Staff)
def update_staff(staff_id: int, staff_in: schemas.StaffUpdate, db: Session = Depends(get_db)):
    """更新人员信息"""
    staff = crud.update_staff(db, staff_id, staff_in)
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    return _convert_staff_to_schema(staff)


@router.get("/{staff_id}/availability")
def check_availability(
    staff_id: int,
    start_time: datetime,
    end_time: datetime,
    db: Session = Depends(get_db)
):
    """检查人员在指定时间段是否可用"""
    staff = crud.get_staff(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    available = crud.is_staff_available(db, staff_id, start_time, end_time)
    return {"staff_id": staff_id, "available": available}


@router.get("/{session_id}/recommend", response_model=List[schemas.StaffRecommendation])
def get_recommendations(
    session_id: int,
    role: AssignmentRole,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """获取场次推荐人员（星级高的优先）"""
    return crud.get_recommended_staff(db, session_id, role, limit)


@router.get("/stats/saturation", response_model=List[schemas.StaffSaturationStats])
def get_saturation_stats(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """获取人员排班饱和度统计"""
    return crud.get_staff_saturation(db, start_date, end_date)


@router.get("/stats/ranking", response_model=List[schemas.StaffRankingItem])
def get_ranking(limit: int = 10, db: Session = Depends(get_db)):
    """获取讲解员服务榜单"""
    return crud.get_staff_ranking(db, limit)


@router.get("/{staff_id}/points", response_model=List[schemas.PointRecord])
def get_staff_points(
    staff_id: int,
    skip: int = 0,
    limit: int = 100,
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    db: Session = Depends(get_db)
):
    """获取讲解员积分记录"""
    staff = crud.get_staff(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    records = crud.get_point_records(db, staff_id=staff_id, start_date=start_date, end_date=end_date, skip=skip, limit=limit)
    result = []
    for r in records:
        result.append(schemas.PointRecord(
            id=r.id,
            staff_id=r.staff_id,
            session_id=r.session_id,
            review_id=r.review_id,
            level_badge_id=r.level_badge_id,
            source_type=r.source_type,
            points=r.points,
            balance_after=r.balance_after,
            description=r.description,
            session_title=r.session.title if r.session else None,
            level_badge_name=r.level_badge.badge_name if r.level_badge else None,
            created_at=r.created_at
        ))
    return result


@router.get("/{staff_id}/badges", response_model=List[schemas.StaffBadge])
def get_staff_badges(staff_id: int, db: Session = Depends(get_db)):
    """获取讲解员获得的勋章列表"""
    staff = crud.get_staff(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    badges = crud.get_staff_badges(db, staff_id)
    result = []
    for b in badges:
        result.append(schemas.StaffBadge(
            id=b.id,
            staff_id=b.staff_id,
            level_badge_id=b.level_badge_id,
            earned_at=b.earned_at,
            is_current=b.is_current,
            level_badge_name=b.level_badge.badge_name if b.level_badge else "",
            level=b.level_badge.level if b.level_badge else 0,
            icon=b.level_badge.icon if b.level_badge else None
        ))
    return result


@router.post("/{staff_id}/points", response_model=schemas.PointChangeResult)
def adjust_staff_points(
    staff_id: int,
    points: int = Query(..., description="调整积分（正数增加，负数扣除）"),
    source_type: str = Query("BONUS", description="积分类型：SERVICE/RATING/BONUS/DEDUCTION"),
    description: str = Query(..., description="调整原因"),
    db: Session = Depends(get_db)
):
    """人工调整讲解员积分"""
    from app.models import PointSourceType
    try:
        source_type_enum = PointSourceType(source_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的积分类型")
    result = crud.adjust_staff_points(db, staff_id, points, source_type_enum, description)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return result


@router.get("/badges", response_model=List[schemas.LevelBadge])
def list_level_badges(
    is_active: Optional[bool] = Query(None, description="是否启用"),
    db: Session = Depends(get_db)
):
    """获取等级勋章配置列表"""
    return crud.get_level_badge_list(db, is_active=is_active)


@router.post("/badges", response_model=schemas.LevelBadge)
def create_level_badge(badge_in: schemas.LevelBadgeCreate, db: Session = Depends(get_db)):
    """新增等级勋章配置"""
    return crud.create_level_badge(db, badge_in)


@router.put("/badges/{badge_id}", response_model=schemas.LevelBadge)
def update_level_badge(badge_id: int, badge_in: schemas.LevelBadgeUpdate, db: Session = Depends(get_db)):
    """更新等级勋章配置"""
    badge = crud.update_level_badge(db, badge_id, badge_in)
    if not badge:
        raise HTTPException(status_code=404, detail="勋章配置不存在")
    return badge
