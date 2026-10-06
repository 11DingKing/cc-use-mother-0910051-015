#!/usr/bin/env python3
"""团体申请容量分配功能验证脚本。

覆盖：自动拆分、学校确认形成正式占用、部分接受回候补、缩减人数、
超时未确认释放、跨场取消释放与候补原子递补、人数守恒校验、原因追踪。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uuid
from datetime import datetime, timedelta

from app.database import SessionLocal, engine, Base
from app import crud, schemas, group_allocation
from app.models import (
    SessionType, SessionStatus, AudienceType,
    GroupApplicationStatus, AllocationStatus, WaitlistStatus, AllocationEventType
)


def test_header(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def day(offset, hour=9, minute=0):
    base = datetime.now() + timedelta(days=offset)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]

    try:
        test_header("1. 初始化测试数据（主题/场地/学校/场次）")

        theme = crud.create_theme(db, schemas.ThemeCreate(
            name=f"陶瓷研学-{suffix}", description="陶瓷文化研学", category="历史"))
        school1 = crud.create_school(db, schemas.SchoolCreate(
            name=f"第一实验小学-{suffix}", contact_person="王老师"))
        school2 = crud.create_school(db, schemas.SchoolCreate(
            name=f"第二实验小学-{suffix}", contact_person="李老师"))

        def make_venue(name, capacity):
            return crud.create_venue(db, schemas.VenueCreate(
                name=f"{name}-{suffix}", venue_type="展厅", capacity=capacity, location="1楼"))

        v1, v2, v3 = make_venue("一号厅", 60), make_venue("二号厅", 40), make_venue("三号厅", 100)
        v4, v6, v7 = make_venue("四号厅", 50), make_venue("六号厅", 40), make_venue("七号厅", 100)

        def make_session(title, venue, start, end):
            return crud.create_session(db, schemas.SessionCreate(
                title=f"{title}-{suffix}", theme_id=theme.id, venue_id=venue.id,
                session_type=SessionType.RESEARCH, start_time=start, end_time=end,
                audience_type=AudienceType.SCHOOL, audience_count=0,
                guides_needed=0, needs_lecturer=False))

        sa = make_session("场次A", v1, day(2, 9), day(2, 11))    # 容量60
        sb = make_session("场次B", v2, day(2, 14), day(2, 16))   # 容量40
        sc = make_session("场次C", v3, day(3, 9), day(3, 11))    # 容量100
        sd = make_session("场次D", v4, day(5, 9), day(5, 11))    # 容量50
        print(f"✓ 场次A(容量60)/B(40)/C(100)/D(50) 创建完成")

        test_header("2. 自动拆分：报名130人超过单场容量，拆到多个场次")

        app1, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=130,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(2, 0), preferred_end=day(2, 23)))
        assert not errors, errors
        detail1 = group_allocation.build_detail(db, app1)
        proposed = {a.session_id: a for a in detail1.allocations
                    if a.status == AllocationStatus.PROPOSED}
        assert proposed[sa.id].allocated_count == 60, "场次A应分配60人"
        assert proposed[sb.id].allocated_count == 40, "场次B应分配40人"
        assert sc.id not in proposed, "场次C不在时间偏好窗口内，不应拆分"
        assert detail1.waiting_count == 30, f"应有30人候补，实际{detail1.waiting_count}"
        assert detail1.conservation.conserved, detail1.conservation.message
        assert all(a.reason for a in detail1.allocations), "每次拆分都应记录原因"
        split_events = [e for e in detail1.events if e.event_type == AllocationEventType.SPLIT]
        assert len(split_events) == 2 and all("拆分第" in e.reason for e in split_events)
        print(f"✓ 拆分为 A=60人、B=40人（待学校确认），30人进入候补")
        print(f"  拆分原因示例：{split_events[0].reason}")
        print(f"  守恒校验：{detail1.conservation.message}")

        test_header("3. 学校确认：确认后才形成正式占用")

        app1, errors = group_allocation.confirm_application(
            db, app1.id, schemas.GroupApplicationConfirmRequest(
                items=[schemas.ConfirmAllocationItem(allocation_id=a.id, accepted_count=a.allocated_count)
                       for a in detail1.allocations],
                operator="第一实验小学"))
        assert not errors, errors
        db.expire_all()
        assert crud.get_session(db, sa.id).audience_count == 60, "确认后A正式占用60"
        assert crud.get_session(db, sb.id).audience_count == 40, "确认后B正式占用40"
        detail1 = group_allocation.build_detail(db, app1)
        assert detail1.confirmed_count == 100 and detail1.waiting_count == 30
        assert detail1.status == GroupApplicationStatus.PARTIALLY_CONFIRMED
        assert detail1.conservation.conserved, detail1.conservation.message
        print(f"✓ 确认100人形成正式占用（A=60、B=40），剩余30人仍候补")
        print(f"  守恒校验：{detail1.conservation.message}")

        test_header("4. 部分接受：未接受部分回到候补，容量释放后按资格递补")

        app2, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school2.id, theme_id=theme.id, total_count=50,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(2, 0), preferred_end=day(4, 23)))
        assert not errors, errors
        detail2 = group_allocation.build_detail(db, app2)
        alloc2 = detail2.allocations[0]
        assert alloc2.session_id == sc.id and alloc2.allocated_count == 50

        app2, errors = group_allocation.confirm_application(
            db, app2.id, schemas.GroupApplicationConfirmRequest(
                items=[schemas.ConfirmAllocationItem(allocation_id=alloc2.id, accepted_count=30)],
                operator="第二实验小学"))
        assert not errors, errors
        detail2 = group_allocation.build_detail(db, app2)
        assert detail2.confirmed_count == 30, "确认30人正式占用"
        # 未接受的20人回到候补后，场次C容量充足，按当前资格被原子递补为新的待确认分配
        assert detail2.proposed_count == 20, f"剩余20人应被递补为待确认，实际{detail2.proposed_count}"
        assert detail2.waiting_count == 0
        assert detail2.conservation.conserved, detail2.conservation.message
        promote_events = [e for e in detail2.events
                          if e.event_type == AllocationEventType.PROMOTE]
        assert promote_events and "候补递补" in promote_events[0].reason
        print(f"✓ 部分接受30人，剩余20人回候补后被递补为待确认")
        print(f"  递补原因：{promote_events[0].reason}")

        test_header("5. 缩减人数：依次从待确认/已确认扣减并保持守恒")

        app2, errors = group_allocation.reduce_application(
            db, app2.id, schemas.GroupApplicationReduceRequest(
                new_total_count=30, operator="第二实验小学", reason="两个班级请假"))
        assert not errors, errors
        detail2 = group_allocation.build_detail(db, app2)
        assert detail2.proposed_count == 0 and detail2.confirmed_count == 30
        assert detail2.released_count == 20
        assert detail2.conservation.conserved, detail2.conservation.message

        app2, errors = group_allocation.reduce_application(
            db, app2.id, schemas.GroupApplicationReduceRequest(
                new_total_count=25, operator="第二实验小学", reason="再减5人"))
        assert not errors, errors
        detail2 = group_allocation.build_detail(db, app2)
        assert detail2.confirmed_count == 25 and detail2.released_count == 25
        db.expire_all()
        assert crud.get_session(db, sc.id).audience_count == 25, "缩减后C正式占用25"
        assert detail2.conservation.conserved, detail2.conservation.message
        print(f"✓ 50人缩减到25人，释放25人，场次C正式占用同步降至25")
        print(f"  守恒校验：{detail2.conservation.message}")

        test_header("6. 超时未确认：软占用释放，人数回候补队尾")

        app3, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=40,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(3, 0), preferred_end=day(4, 23),
                confirm_deadline=datetime.now() - timedelta(hours=1)))
        assert not errors, errors
        result = group_allocation.expire_overdue(db)
        assert result.expired_count == 1, f"应有1条超时分配，实际{result.expired_count}"
        detail3 = group_allocation.build_detail(db, group_allocation.get_application(db, app3.id))
        expired = [a for a in detail3.allocations if a.status == AllocationStatus.EXPIRED]
        assert len(expired) == 1 and expired[0].allocated_count == 40
        expire_events = [e for e in detail3.events
                         if e.event_type == AllocationEventType.EXPIRE]
        assert expire_events and "未确认" in expire_events[0].reason
        # 回到候补的40人按当前资格再次被递补为待确认（获得新的确认时限）
        assert detail3.proposed_count == 40 and detail3.waiting_count == 0
        assert detail3.conservation.conserved, detail3.conservation.message
        print(f"✓ 超时分配已释放并回候补，按当前资格重新递补40人")
        print(f"  超时原因：{expire_events[0].reason}")

        test_header("7. 候补顺序依据：容量释放时先排队者优先递补")

        appx, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=50,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(5, 0), preferred_end=day(5, 23)))
        assert not errors, errors
        detailx = group_allocation.build_detail(db, appx)
        appx, errors = group_allocation.confirm_application(
            db, appx.id, schemas.GroupApplicationConfirmRequest(
                items=[schemas.ConfirmAllocationItem(
                    allocation_id=detailx.allocations[0].id, accepted_count=50)]))
        assert not errors, errors

        app4, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school2.id, theme_id=theme.id, total_count=30,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(5, 0), preferred_end=day(5, 23)))
        assert not errors, errors
        app5, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=20,
                min_group_size=20, co_travel_size=10,
                preferred_start=day(5, 0), preferred_end=day(5, 23)))
        assert not errors, errors
        detail4 = group_allocation.build_detail(db, app4)
        detail5 = group_allocation.build_detail(db, app5)
        assert detail4.waiting_count == 30 and detail5.waiting_count == 20
        seq4 = detail4.waitlist_entries[0].queue_seq
        seq5 = detail5.waitlist_entries[0].queue_seq
        assert seq4 < seq5, "先进入候补者序号更小"

        appx, errors = group_allocation.reduce_application(
            db, appx.id, schemas.GroupApplicationReduceRequest(
                new_total_count=20, operator="第一实验小学", reason="缩减30人"))
        assert not errors, errors
        detail4 = group_allocation.build_detail(db, app4)
        detail5 = group_allocation.build_detail(db, app5)
        assert detail4.proposed_count == 30, "先排队的申请4应递补30人"
        assert detail5.waiting_count == 20, "容量已用完，申请5仍候补20人"
        assert detail4.conservation.conserved and detail5.conservation.conserved
        print(f"✓ 释放30人容量，序号{seq4}的申请4优先递补30人，序号{seq5}的申请5继续候补")

        test_header("8. 跨场取消：场次取消释放占用，候补自动接替到其他场次")

        se = make_session("场次E", make_venue("五号厅", 50), day(2, 16), day(2, 18))
        print(f"✓ 新增场次E（容量50，与A/B同日在时间偏好窗口内）")

        updated, errors = crud.update_session(db, sb.id, schemas.SessionUpdate(
            status=SessionStatus.CANCELLED))
        assert updated.status == SessionStatus.CANCELLED
        db.expire_all()
        assert crud.get_session(db, sb.id).audience_count == 0, "取消后B占用清零"

        detail1 = group_allocation.build_detail(db, group_allocation.get_application(db, app1.id))
        cancelled_b = [a for a in detail1.allocations
                       if a.session_id == sb.id and a.status == AllocationStatus.CANCELLED]
        assert cancelled_b, "B上的已确认分配应被取消"
        promoted_e = [a for a in detail1.allocations
                      if a.session_id == se.id and a.status == AllocationStatus.PROPOSED]
        assert sum(a.allocated_count for a in promoted_e) == 50, \
            f"候补应自动接替到E共50人，实际{sum(a.allocated_count for a in promoted_e)}"
        assert detail1.waiting_count == 20, f"接替后应剩20人候补，实际{detail1.waiting_count}"
        assert detail1.conservation.conserved, detail1.conservation.message
        cancel_events = [e for e in detail1.events
                         if e.event_type == AllocationEventType.SESSION_CANCEL]
        assert cancel_events and "场次取消" in cancel_events[0].reason
        print(f"✓ 场次B取消：40人正式占用释放回候补，50人自动接替到场次E，20人继续候补")
        print(f"  释放原因：{cancel_events[0].reason}")
        print(f"  守恒校验：{detail1.conservation.message}")

        test_header("9. 最小成团人数与同行约束校验")

        sf = make_session("场次F", v6, day(6, 9), day(6, 11))    # 容量40
        sg = make_session("场次G", v7, day(6, 14), day(6, 16))   # 容量100

        app6, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school2.id, theme_id=theme.id, total_count=25,
                min_group_size=20, co_travel_size=15,
                preferred_start=day(6, 0), preferred_end=day(6, 23)))
        assert not errors, errors
        detail6 = group_allocation.build_detail(db, app6)
        assert not detail6.allocations, "25人按同行单元15取整为15，不足最小成团20，不应拆分"
        assert detail6.waiting_count == 25
        assert detail6.conservation.conserved
        wait_event = [e for e in detail6.events
                      if e.event_type == AllocationEventType.WAITLIST][0]
        assert "不足最小成团" in wait_event.reason
        print(f"✓ 25人（同行单元15）不满足最小成团20，整体进入候补")
        print(f"  候补原因：{wait_event.reason}")

        app7, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=30,
                min_group_size=20, co_travel_size=15,
                preferred_start=day(6, 0), preferred_end=day(6, 23)))
        assert not errors, errors
        detail7 = group_allocation.build_detail(db, app7)
        assert detail7.proposed_count == 30, "30人是同行单元15的整数倍，应拆分成功"
        print(f"✓ 30人（同行单元15的整数倍）拆分到场次F")

        test_header("10. 候补队列查询：顺序依据与当前资格说明")

        view = group_allocation.get_session_waitlist(db, sd.id)
        assert view.order_basis, "应说明候补顺序依据"
        assert len(view.entries) == 3, "此时应有3条候补（申请5/申请1/申请6）"
        positions = {e.application_id: e.queue_position for e in view.entries}
        assert positions[app5.id] == 1, "申请5先进入候补，应排第1位"
        entry5 = [e for e in view.entries if e.application_id == app5.id][0]
        assert not entry5.eligible, "场次D剩余容量为0，不可递补"
        assert "剩余容量0人" in entry5.eligibility_reason
        entry1 = [e for e in view.entries if e.application_id == app1.id][0]
        assert not entry1.eligible and "时间偏好" in entry1.eligibility_reason
        print(f"✓ 场次D候补队列共{len(view.entries)}条，按候补序号排序")
        print(f"  顺序依据：{view.order_basis}")
        print(f"  申请5资格说明：{entry5.eligibility_reason}")
        print(f"  申请1资格说明：{entry1.eligibility_reason}")

        test_header("11. 全部申请人数守恒总校验")

        for app_id, expect in [
            (app1.id, (130, 130)), (app2.id, (50, 25)), (app3.id, (40, 40)),
            (appx.id, (50, 20)), (app4.id, (30, 30)), (app5.id, (20, 20)),
            (app6.id, (25, 25)), (app7.id, (30, 30)),
        ]:
            summary = group_allocation.get_conservation(db, app_id)
            assert summary is not None
            assert summary.original_total_count == expect[0]
            assert summary.total_count == expect[1], \
                f"申请{app_id}总人数应为{expect[1]}，实际{summary.total_count}"
            assert summary.conserved, f"申请{app_id}不守恒：{summary.message}"
            print(f"  ✓ 申请{app_id}：{summary.message}")

        test_header("12. 参数校验")

        _, errors = group_allocation.create_application_and_split(
            db, schemas.GroupApplicationCreate(
                school_id=school1.id, theme_id=theme.id, total_count=10,
                min_group_size=20, co_travel_size=1))
        assert errors and "最小成团人数" in errors[0]
        print(f"✓ 最小成团人数超过总人数被拒绝：{errors[0]}")

        _, errors = group_allocation.confirm_application(
            db, app7.id, schemas.GroupApplicationConfirmRequest(
                items=[schemas.ConfirmAllocationItem(
                    allocation_id=detail7.allocations[0].id, accepted_count=99)]))
        assert errors and "超过分配人数" in errors[0]
        print(f"✓ 接受人数超过分配人数被拒绝：{errors[0]}")

        test_header("13. API 接口冒烟测试")

        school2_id, theme_id, sd_id = school2.id, theme.id, sd.id
        db.close()  # 释放读事务，避免与 TestClient 的写事务争锁
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        resp = client.post("/api/group-applications", json={
            "school_id": school2_id, "theme_id": theme_id, "total_count": 20,
            "min_group_size": 20, "co_travel_size": 10,
            "preferred_start": day(3, 0).isoformat(),
            "preferred_end": day(4, 23).isoformat()})
        assert resp.status_code == 200, resp.text
        api_app = resp.json()
        assert api_app["proposed_count"] == 20 and api_app["conservation"]["conserved"]
        assert api_app["allocations"][0]["reason"], "拆分方案应带原因"
        print(f"✓ POST /api/group-applications 拆分成功：{api_app['allocations'][0]['reason'][:50]}...")

        alloc_id = api_app["allocations"][0]["id"]
        resp = client.post(f"/api/group-applications/{api_app['id']}/confirm", json={
            "items": [{"allocation_id": alloc_id, "accepted_count": 20}],
            "operator": "接口测试"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed_count"] == 20
        print("✓ POST /{id}/confirm 确认形成正式占用")

        resp = client.get(f"/api/group-applications/{api_app['id']}/conservation")
        assert resp.status_code == 200 and resp.json()["conserved"]
        print(f"✓ GET /{{id}}/conservation：{resp.json()['message']}")

        resp = client.get(f"/api/group-applications/{api_app['id']}/events")
        assert resp.status_code == 200 and len(resp.json()) >= 2
        assert all(e["reason"] for e in resp.json()), "每个事件都应说明原因"
        print(f"✓ GET /{{id}}/events 共{len(resp.json())}条事件，均含原因")

        resp = client.get(f"/api/group-applications/session-waitlist/{sd_id}")
        assert resp.status_code == 200 and resp.json()["order_basis"]
        print("✓ GET /session-waitlist/{session_id} 返回候补队列与顺序依据")

        resp = client.get("/api/group-applications", params={"school_id": school2_id})
        assert resp.status_code == 200 and len(resp.json()) >= 1
        print(f"✓ GET /api/group-applications 列表返回{len(resp.json())}条申请")

        test_header("所有测试通过！团体申请容量分配功能验证完成")
        print("""
核心功能实现清单:
  ✓ 团体申请按时间偏好/最小成团人数/同行约束自动拆分到多个场次
  ✓ 拆分为待确认软占用，学校确认后才形成正式占用（audience_count）
  ✓ 部分接受的未接受部分回到候补队列
  ✓ 缩减人数依次扣减候补/待确认/已确认并释放占用
  ✓ 超时未确认自动释放软占用，人数回候补队尾
  ✓ 跨场取消释放占用，候补按当前资格原子递补到其他场次
  ✓ 候补队列有顺序依据（候补序号），递补时重新校验当前资格
  ✓ 申请总人数、各场占用与候补数量全程守恒
  ✓ 每次拆分/递补/释放均记录原因，可查询事件流水与守恒校验

涉及模块扩展:
  ✓ models.py - 新增4个数据模型 + 4个枚举类型
  ✓ schemas.py - 新增13个Pydantic数据结构
  ✓ group_allocation.py - 容量分配与候补递补核心服务
  ✓ routers/group_applications.py - 新增10个API接口
  ✓ crud.py - 场次取消时联动释放团体占用
  ✓ main.py - 注册新路由模块
        """)

    except Exception as e:
        print(f"\n✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
