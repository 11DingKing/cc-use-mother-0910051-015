from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app import schemas, crud

router = APIRouter(prefix="/api/statistics", tags=["统计分析"])


@router.get("/theme-sessions", response_model=List[schemas.ThemeSessionStats])
def get_theme_session_stats(
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    db: Session = Depends(get_db)
):
    """各主题场次量统计"""
    return crud.get_theme_session_stats(db, start_date, end_date)


@router.get("/staff-saturation", response_model=List[schemas.StaffSaturationStats])
def get_staff_saturation(
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    db: Session = Depends(get_db)
):
    """人员排班饱和度统计"""
    return crud.get_staff_saturation(db, start_date, end_date)


@router.get("/staff-ranking", response_model=List[schemas.StaffRankingItem])
def get_staff_ranking(
    limit: int = Query(10, description="返回数量"),
    db: Session = Depends(get_db)
):
    """讲解员服务榜单"""
    return crud.get_staff_ranking(db, limit)


@router.get("/session-changes", response_model=List[schemas.SessionChangeStats])
def get_session_change_stats(
    session_id: Optional[int] = Query(None, description="场次ID"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """各场次变更频次统计"""
    return crud.get_session_change_stats(db, session_id=session_id, skip=skip, limit=limit)


@router.get("/change-frequency", response_model=List[schemas.ChangeFrequencyStats])
def get_change_frequency_stats(
    period: str = Query("month", description="统计周期：day/week/month"),
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    db: Session = Depends(get_db)
):
    """场次变更频次趋势统计

    按日/周/月统计变更数量、类型分布、审核通过率、平均处理时长
    """
    return crud.get_change_frequency_stats(db, period=period, start_date=start_date, end_date=end_date)


@router.get("/point-trend", response_model=List[schemas.PointTrendItem])
def get_point_trend_stats(
    period: str = Query("day", description="统计周期：day/week/month"),
    start_date: Optional[datetime] = Query(None, description="开始日期"),
    end_date: Optional[datetime] = Query(None, description="结束日期"),
    db: Session = Depends(get_db)
):
    """积分变动趋势统计

    按日/周/月统计积分发放数量和参与讲解员人数
    """
    return crud.get_point_trend(db, period=period, start_date=start_date, end_date=end_date)


@router.get("/level-distribution", response_model=List[schemas.LevelDistributionItem])
def get_level_distribution_stats(db: Session = Depends(get_db)):
    """讲解员等级分布统计

    统计各等级讲解员的人数和占比
    """
    return crud.get_level_distribution(db)


@router.get("/staff-points", response_model=List[schemas.StaffPointDetail])
def get_staff_point_stats(
    year: Optional[int] = Query(None, description="年份，默认当前年"),
    month: Optional[int] = Query(None, description="月份，默认当前月"),
    limit: int = Query(100, description="返回数量"),
    db: Session = Depends(get_db)
):
    """讲解员积分明细统计

    展示所有讲解员的积分、等级、好评率等详细信息
    """
    return crud.get_staff_point_details(db, year=year, month=month, limit=limit)
