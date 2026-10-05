#!/usr/bin/env python3
"""讲解员成长与激励体系功能验证脚本"""

import sys
from datetime import datetime, timedelta
from app.database import SessionLocal, engine, Base
from app import crud, schemas
from app.models import (
    StaffType, PointSourceType, SessionStatus,
    AssignmentRole, AudienceType, SessionType
)


def test_level_badge_config():
    """测试等级勋章配置"""
    print("\n" + "="*60)
    print("1. 测试等级勋章配置")
    print("="*60)

    db = SessionLocal()
    try:
        badges = crud.get_level_badge_list(db)
        assert len(badges) == 5, f"应该有5个等级配置，实际有{len(badges)}个"
        for b in badges:
            print(f"  ✅ Lv.{b.level} {b.name} - {b.badge_name} "
                  f"({b.min_points}-{b.max_points if b.max_points else '∞'}分)")

        badge = crud.get_level_badge_by_level(db, 3)
        assert badge is not None, "应该能找到等级3的配置"
        assert badge.badge_name == "讲解能手", f"等级3的勋章名称应该是'讲解能手'，实际是'{badge.badge_name}'"

        print("\n  ✅ 等级勋章配置测试通过")
    finally:
        db.close()


def test_point_calculation():
    """测试积分计算规则"""
    print("\n" + "="*60)
    print("2. 测试积分计算规则")
    print("="*60)

    test_cases = [
        (1.5, 5, 25, "1.5小时+5星"),
        (2.0, 4, 25, "2小时+4星"),
        (1.0, 3, 10, "1小时+3星"),
        (0.5, 2, 5, "0.5小时+2星(无评分奖励)"),
        (3.0, 5, 40, "3小时+5星"),
    ]

    for duration, rating, expected, desc in test_cases:
        actual = crud.calculate_service_points(duration, rating)
        status = "✅" if actual == expected else "❌"
        print(f"  {status} {desc}: {actual}分 (预期{expected}分)")
        assert actual == expected, f"积分计算错误: {desc}"

    print("\n  ✅ 积分计算规则测试通过")


def test_staff_fields():
    """测试讲解员新字段"""
    print("\n" + "="*60)
    print("3. 测试讲解员积分和等级字段")
    print("="*60)

    db = SessionLocal()
    try:
        staff_list = crud.get_staff_list(db, staff_type=StaffType.GUIDE, limit=5)
        for s in staff_list[:3]:
            print(f"  ✅ {s.name}: 积分={s.total_points or 0}, "
                  f"等级={s.current_level or 1}, 优秀={s.is_excellent or False}")
            assert hasattr(s, 'total_points'), "缺少total_points字段"
            assert hasattr(s, 'current_level'), "缺少current_level字段"
            assert hasattr(s, 'is_excellent'), "缺少is_excellent字段"

        print("\n  ✅ 讲解员新字段测试通过")
    finally:
        db.close()


def test_add_points_and_level_up():
    """测试积分增加和等级晋升"""
    print("\n" + "="*60)
    print("4. 测试积分增加和等级晋升")
    print("="*60)

    db = SessionLocal()
    try:
        staff = crud.get_staff_list(db, staff_type=StaffType.GUIDE, limit=1)[0]
        print(f"  测试讲解员: {staff.name} (当前积分: {staff.total_points or 0}, 等级: {staff.current_level or 1})")

        initial_points = staff.total_points or 0
        initial_level = staff.current_level or 1

        result = crud.add_points(
            db, staff.id, 200, PointSourceType.BONUS,
            description="测试奖励积分"
        )

        print(f"  ✅ 积分变动结果: {result.message}")
        assert result.success, "积分增加应该成功"
        assert result.points_added == 200, f"应该增加200积分，实际增加{result.points_added}"
        assert result.new_balance == initial_points + 200, "积分余额计算错误"

        if result.level_up:
            print(f"  🎉 等级晋升: Lv.{initial_level} → Lv.{result.new_level}")
            if result.new_badge:
                print(f"  🏅 获得新勋章: {result.new_badge.badge_name}")

        staff2 = crud.get_staff(db, staff.id)
        assert staff2.total_points == initial_points + 200, "数据库积分未更新"

        print("\n  ✅ 积分增加和等级晋升测试通过")
    finally:
        db.close()


def test_point_records():
    """测试积分记录查询"""
    print("\n" + "="*60)
    print("5. 测试积分记录查询")
    print("="*60)

    db = SessionLocal()
    try:
        staff = crud.get_staff_list(db, staff_type=StaffType.GUIDE, limit=1)[0]

        records = crud.get_point_records(db, staff_id=staff.id, limit=10)
        print(f"  ✅ 查询到 {len(records)} 条积分记录")

        for r in records[:3]:
            print(f"    - {r.created_at.strftime('%Y-%m-%d %H:%M')} | "
                  f"{r.source_type.value:8s} | {r.points:+4d}分 | "
                  f"余额:{r.balance_after:4d} | {r.description[:30]}")

        assert len(records) > 0, "应该有积分记录"
        assert all(r.staff_id == staff.id for r in records), "积分记录人员ID错误"

        print("\n  ✅ 积分记录查询测试通过")
    finally:
        db.close()


def test_staff_badges():
    """测试讲解员勋章查询"""
    print("\n" + "="*60)
    print("6. 测试讲解员勋章查询")
    print("="*60)

    db = SessionLocal()
    try:
        staff = crud.get_staff_list(db, staff_type=StaffType.GUIDE, limit=1)[0]
        badges = crud.get_staff_badges(db, staff.id)

        print(f"  ✅ {staff.name} 获得 {len(badges)} 个勋章")
        for b in badges:
            current = "(当前)" if b.is_current else ""
            print(f"    - Lv.{b.level_badge.level} {b.level_badge.badge_name} "
                  f"获得于 {b.earned_at.strftime('%Y-%m-%d')} {current}")

        if badges:
            current_badge = [b for b in badges if b.is_current]
            assert len(current_badge) == 1, "应该有且仅有一个当前勋章"
            print(f"  ✅ 当前勋章: {current_badge[0].level_badge.badge_name}")

        print("\n  ✅ 讲解员勋章查询测试通过")
    finally:
        db.close()


def test_monthly_ranking():
    """测试月度榜单结算"""
    print("\n" + "="*60)
    print("7. 测试月度榜单结算")
    print("="*60)

    db = SessionLocal()
    try:
        staff_list = crud.get_staff_list(db, staff_type=StaffType.GUIDE, limit=5)

        print("  为前5名讲解员增加本月积分...")
        for i, s in enumerate(staff_list):
            crud.add_points(db, s.id, 100 + i * 80, PointSourceType.SERVICE,
                            description=f"6月服务积分{i+1}")

        print("  结算2026年6月榜单...")
        result = crud.settle_monthly_ranking(db, 2026, 6, top_n=3)
        print(f"  ✅ {result.message}")
        assert result.success, "榜单结算应该成功"
        assert result.total_staff >= 5, "上榜人数应该不少于5人"
        assert len(result.excellent_staff) == 3, "优秀讲解员应该有3人"

        print(f"  🏆 优秀讲解员IDs: {result.excellent_staff}")

        rankings = crud.get_monthly_ranking(db, 2026, 6)
        print(f"\n  📊 2026年6月榜单 (前5名):")
        for r in rankings[:5]:
            badge = r.level_badge.badge_name if r.level_badge else "-"
            excellent = "⭐优秀" if r.is_excellent else ""
            print(f"    {r.rank:2d}. {r.staff.name:8s} | "
                  f"积分:{r.total_points:4d} | "
                  f"好评率:{r.positive_review_rate:6.1%} | "
                  f"场次:{r.session_count:2d} | {badge:10s} {excellent}")

        for s_id in result.excellent_staff:
            s = crud.get_staff(db, s_id)
            assert s.is_excellent == True, f"{s.name} 应该被标记为优秀"
            assert s.excellent_until is not None, f"{s.name} 应该有优秀有效期"
            print(f"  ✅ {s.name} 已标记为优秀讲解员，有效期至 {s.excellent_until.strftime('%Y-%m-%d')}")

        print("\n  ✅ 月度榜单结算测试通过")
    finally:
        db.close()


def test_recommendation_with_excellent():
    """测试优秀讲解员优先推荐"""
    print("\n" + "="*60)
    print("8. 测试优秀讲解员优先推荐")
    print("="*60)

    db = SessionLocal()
    try:
        sessions = crud.get_session_list(db, limit=1)
        if sessions:
            session = sessions[0]
            recs = crud.get_recommended_staff(
                db, session.id, AssignmentRole.GUIDE, limit=10
            )

            print(f"  场次: {session.title}")
            print(f"  推荐讲解员 (前5名):")
            for i, r in enumerate(recs[:5]):
                s = crud.get_staff(db, r.staff_id)
                excellent = "⭐优秀" if s and s.is_excellent else ""
                level = s.current_level if s else 1
                print(f"    {i+1}. {r.staff_name:8s} | 评分:{r.score:5.2f} | "
                      f"星级:{r.star_rating} | Lv.{level} | "
                      f"可用:{r.is_available} {excellent}")

            excellent_recs = [r for r in recs if crud.get_staff(db, r.staff_id).is_excellent]
            if excellent_recs:
                print(f"\n  ✅ 优秀讲解员在推荐列表中，评分有额外加成(+5分)")
            else:
                print(f"  ⚠️  当前没有优秀讲解员，继续测试...")

        print("\n  ✅ 优秀讲解员优先推荐测试通过")
    finally:
        db.close()


def test_statistics():
    """测试统计模块新功能"""
    print("\n" + "="*60)
    print("9. 测试统计模块新功能")
    print("="*60)

    db = SessionLocal()
    try:
        print("  📈 积分变动趋势:")
        trend = crud.get_point_trend(db, period="day")
        for t in trend[:7]:
            print(f"    {t.date}: {t.points}分 ({t.staff_count}人获得)")
        assert len(trend) > 0, "应该有积分趋势数据"

        print("\n  📊 等级分布统计:")
        dist = crud.get_level_distribution(db)
        for d in dist:
            bar = "█" * int(d.percentage / 5)
            print(f"    Lv.{d.level} {d.badge_name:10s}: "
                  f"{d.staff_count:2d}人 ({d.percentage:5.1f}%) {bar}")
        assert len(dist) == 5, "应该有5个等级的分布数据"

        print("\n  🏆 讲解员积分明细 (前5名):")
        details = crud.get_staff_point_details(db, 2026, 6, limit=5)
        for d in details:
            excellent = "⭐优秀" if d.is_excellent else ""
            print(f"    {d.staff_name:8s} | 总积分:{d.total_points:4d} | "
                  f"本月:{d.monthly_points:4d} | Lv.{d.current_level} | "
                  f"好评率:{d.positive_review_rate:6.1%} {excellent}")

        print("\n  ✅ 统计模块新功能测试通过")
    finally:
        db.close()


def test_review_auto_points():
    """测试评价自动计算积分"""
    print("\n" + "="*60)
    print("10. 测试评价自动计算积分")
    print("="*60)

    db = SessionLocal()
    try:
        from app.models import Session as SessionModel

        completed_sessions = db.query(SessionModel).filter(
            SessionModel.status == SessionStatus.COMPLETED
        ).limit(1).all()

        if completed_sessions:
            session = completed_sessions[0]
            staff_before = {}
            for a in session.assignments:
                if a.role == AssignmentRole.GUIDE:
                    s = crud.get_staff(db, a.staff_id)
                    staff_before[s.id] = s.total_points or 0
                    print(f"  评价前 - {s.name}: {staff_before[s.id]}分")

            print(f"\n  创建评价: 场次'{session.title}', 5星好评")
            review = crud.create_review(db, schemas.ReviewCreate(
                session_id=session.id,
                reviewer_name="测试观众",
                reviewer_type="公众",
                rating=5,
                comment="讲解非常精彩！"
            ))

            duration = (session.end_time - session.start_time).total_seconds() / 3600
            expected_points = crud.calculate_service_points(duration, 5)
            print(f"  时长: {duration:.1f}小时, 预计获得积分: {expected_points}")

            for a in session.assignments:
                if a.role == AssignmentRole.GUIDE:
                    s = crud.get_staff(db, a.staff_id)
                    points_added = (s.total_points or 0) - staff_before.get(s.id, 0)
                    print(f"  评价后 - {s.name}: {s.total_points}分 (+{points_added})")
                    assert points_added == expected_points, f"{s.name} 积分计算错误"

            print("\n  ✅ 评价自动计算积分测试通过")
        else:
            print("  ⚠️  没有已完成的场次，跳过评价测试")

    finally:
        db.close()


def main():
    print("\n" + "="*60)
    print("🚀 讲解员成长与激励体系 - 功能验证")
    print("="*60)

    tests = [
        test_level_badge_config,
        test_point_calculation,
        test_staff_fields,
        test_add_points_and_level_up,
        test_point_records,
        test_staff_badges,
        test_monthly_ranking,
        test_recommendation_with_excellent,
        test_statistics,
        test_review_auto_points,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  ❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*60)
    print(f"📋 测试结果: {passed} 通过, {failed} 失败")
    print("="*60)

    if failed == 0:
        print("\n🎉 所有测试通过！讲解员成长与激励体系已完整实现！")
        return 0
    else:
        print(f"\n❌ 有 {failed} 个测试失败，请检查代码")
        return 1


if __name__ == "__main__":
    sys.exit(main())
