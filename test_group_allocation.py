#!/usr/bin/env python3
"""团体申请容量分配与候补递补 —— 端到端场景验证

场次容量：S1=30 S2=40 S3=50 S4=60 S5=40 S6=40(S5/S6同时段) S7=60 S8=30 S9=30
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, SessionLocal, engine
from app.models import (
    AssignmentRole, AudienceType, Session, SessionStatus, SessionType,
    School, Staff, StaffTheme, StaffVenue, StaffType, Theme, Venue,
    AllocationEventType, GroupApplicationStatus,
)
from app import crud, schemas
from app.services import group_allocation as svc

Base.metadata.create_all(bind=engine)

BASE = datetime(2026, 11, 1, 8, 0)
failures = []


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        failures.append(msg)


def header(t):
    print("\n" + "=" * 72)
    print(f"  {t}")
    print("=" * 72)


def assert_conserved(db, app_id, tag):
    app = svc.get_application(db, app_id)
    active = svc._active_count(db, app_id)
    waiting = svc._waiting_count(db, app_id)
    terminal = app.status in (GroupApplicationStatus.CANCELLED, GroupApplicationStatus.EXPIRED)
    ok = (active + waiting == 0) if terminal else (active + waiting == app.total_count)
    check(ok, f"[{tag}] 守恒：总{app.total_count} = 占用{active} + 候补{waiting}"
              f"（{app.status.value}）")


def events_of(db, app_id, action=None, keyword=None):
    app = svc.get_application(db, app_id)
    out = []
    for e in app.events:
        if action and e.action != action:
            continue
        if keyword and keyword not in e.reason:
            continue
        out.append(e)
    return out


def line_counts(db, app_id):
    app = svc.get_application(db, app_id)
    return sorted((a.session_id, a.count, a.status.value) for a in app.allocations)


def main():
    db = SessionLocal()
    try:
        header("0. 基础数据：学校、讲解员、9个场次")
        theme = Theme(name="青铜主题", category="历史")
        db.add(theme)
        db.flush()
        caps = [30, 40, 50, 60, 40, 40, 60, 30, 30]
        venues = []
        for i, cap in enumerate(caps, start=1):
            v = Venue(name=f"场地{i}", venue_type="展厅", capacity=cap)
            db.add(v)
            venues.append(v)
        db.flush()
        school = School(name="守恒中学", contact_person="王老师")
        db.add(school)
        db.flush()

        day = datetime(2026, 12, 1, 0, 0)
        specs = [
            (venues[0], day.replace(hour=9), day.replace(hour=10, minute=30)),    # S1
            (venues[1], day.replace(hour=11), day.replace(hour=12, minute=30)),   # S2
            (venues[2], day.replace(hour=14), day.replace(hour=15, minute=30)),   # S3
            (venues[3], day.replace(hour=16), day.replace(hour=17, minute=30)),   # S4
            (venues[4], day.replace(hour=19), day.replace(hour=20, minute=30)),   # S5
            (venues[5], day.replace(hour=19), day.replace(hour=20, minute=30)),   # S6 与S5重叠
            (venues[6], day.replace(hour=21), day.replace(hour=22, minute=30)),   # S7
            (venues[7], day.replace(hour=8), day.replace(hour=8, minute=30)),     # S8
            (venues[8], day.replace(hour=7), day.replace(hour=7, minute=30)),     # S9
        ]
        sessions = []
        for idx, (v, st, et) in enumerate(specs, start=1):
            s = Session(title=f"研学场{idx}", theme_id=theme.id, venue_id=v.id,
                        session_type=SessionType.RESEARCH, start_time=st, end_time=et,
                        audience_type=AudienceType.SCHOOL, guides_needed=1)
            db.add(s)
            sessions.append(s)
        db.flush()
        S = [s.id for s in sessions]

        guide = Staff(name="顾讲解", staff_type=StaffType.GUIDE)
        db.add(guide)
        db.flush()
        db.add(StaffTheme(staff_id=guide.id, theme_id=theme.id, proficiency_level=5))
        db.add(StaffVenue(staff_id=guide.id, venue_id=venues[1].id, is_certified=True))
        db.flush()

        header("1. 申请A 100人/最小团30 偏好[S1,S2,S3] → 30+40+30 三场拆分")
        a = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=100, min_group_size=30,
            preferred_session_ids=S[0:3], companion_note="同年级须同行"), now=BASE)
        check(line_counts(db, a.id) == sorted([(S[0], 30, "待确认"), (S[1], 40, "待确认"),
                                               (S[2], 30, "待确认")]),
              f"A 拆分 {line_counts(db, a.id)}")
        assert_conserved(db, a.id, "A拆分")
        check(len(events_of(db, a.id, AllocationEventType.SPLIT)) == 3,
              "A 留下3条拆分事件并逐条说明原因")

        header("2. B部分入位+候补；C全候补；E受同行约束跳过重叠的S6")
        b = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=100, min_group_size=30,
            preferred_session_ids=[S[0], S[1], S[2], S[3]]), now=BASE)
        # S1/S2满、S3余20<30跳过、S4余60 → 放60，候补40（不留碎尾）
        check(line_counts(db, b.id) == [(S[3], 60, "待确认")],
              f"B 仅 S4 放入60：{line_counts(db, b.id)}")
        check(svc._waiting_count(db, b.id) == 40, "B 候补40人")
        c = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=50, min_group_size=25,
            preferred_session_ids=[S[1], S[2]]), now=BASE)
        check(svc._active_count(db, c.id) == 0 and svc._waiting_count(db, c.id) == 50,
              "C 所有偏好场容量不足最小团，50人全部进入候补")
        e = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=70, min_group_size=30,
            preferred_session_ids=[S[4], S[5]],
            companion_note="同时段只能去一场"), now=BASE)
        check(line_counts(db, e.id) == [(S[4], 40, "待确认")], "E 仅放入 S5 40人")
        check(svc._waiting_count(db, e.id) == 30, "E 候补30人")
        check(any("同行" in x.reason and "重叠" in x.reason
                  for x in events_of(db, e.id, AllocationEventType.SPLIT)),
              "E 的 S6 因同行时间重叠被跳过，事件可查原因")
        wl = svc.list_waitlist(db)
        check([(x.seq, x.application_id, x.remaining_count) for x in wl]
              == [(1, b.id, 40), (2, c.id, 50), (3, e.id, 30)],
              f"候补按序号有序：{[(x.seq, x.application_id, x.remaining_count) for x in wl]}")
        for aid, tag in [(b.id, "B"), (c.id, "C"), (e.id, "E")]:
            assert_conserved(db, aid, tag)

        header("3. 学校确认前只是软预留；A/B/E 确认后形成正式占用（S2含讲解员排班）")
        assignment, errs = crud.create_assignment(db, S[1], schemas.AssignmentCreate(
            staff_id=guide.id, role=AssignmentRole.GUIDE, is_primary=True))
        check(not errs, f"S2 讲解员排班成功 {errs}")
        svc.confirm_application(db, a.id, now=BASE)
        svc.confirm_application(db, b.id, now=BASE)
        svc.confirm_application(db, e.id, now=BASE)
        view = svc.session_capacity_view(db, S[1])
        check((view.capacity, view.occupied_confirmed, view.soft_reserved, view.available)
              == (40, 40, 0, 0), "S2 正式占用40/40，讲解员与场地锁定")

        header("4. 跨场取消 S2：A的40人解除并重入候补，讲解员排班与场地释放")
        result = svc.cancel_session(db, S[1], reason="讲解员临时缺位", operator="调度员")
        check(result["released_people"] == 40 and result["released_allocations"] == 1
              and result["released_assignments"] == 1,
              f"S2 释放40人/1条占用/1条讲解员排班：{result}")
        check(db.query(Session).get(S[1]).status == SessionStatus.CANCELLED,
              "S2 已取消，场地不再被锁")
        a_wl = svc._waiting_entries(db, a.id)
        check(len(a_wl) == 1 and a_wl[0].remaining_count == 40
              and S[1] not in json.loads(a_wl[0].preferred_session_ids),
              "A 的40人重新入候补，意向移除已取消的S2")
        check(any("场次取消" in x.reason for x in events_of(db, a.id)),
              "A 事件流写明场次取消原因与重新入队原因")
        assert_conserved(db, a.id, "A取消后")
        assert_conserved(db, b.id, "B意向更新")
        assert_conserved(db, c.id, "C意向更新")

        header("5. A 缩减100→30：先撤候补40再撤S3的30；S3释放后队首B递补40，C因最小团被跳过")
        svc.reduce_application(db, a.id, 30, now=BASE)
        assert_conserved(db, a.id, "A缩减")
        a_active = sorted((x.session_id, x.count, x.status.value)
                          for x in svc._active_allocations(db, a.id))
        check(svc.get_application(db, a.id).status.value == "已确认"
              and a_active == [(S[0], 30, "已确认")],
              f"A 有效占用仅剩 S1 30人正式占用，状态已确认（历史明细仍可追溯）：{a_active}")
        b_lines = svc._active_allocations(db, b.id)
        check(sorted((x.session_id, x.count, x.status.value) for x in b_lines)
              == [(S[2], 40, "待确认"), (S[3], 60, "已确认")],
              "S3释放50位，队首B（seq1）原子递补40人形成待确认预留")
        check(any("不足其最小成团" in x.reason
                  for x in events_of(db, c.id, AllocationEventType.PROMOTE_SKIP)),
              "队中C（剩50人/最小25）面对S3剩余10位不足成团被跳过，原因留痕")
        assert_conserved(db, b.id, "B递补")
        assert_conserved(db, c.id, "C跳过后")

        header("6. A 退出S1释放30位：F队40人补30会留10人碎尾被跳过，G队30人顺延递补")
        f = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=40, min_group_size=30,
            preferred_session_ids=[S[0]]), now=BASE)
        g = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=30, min_group_size=30,
            preferred_session_ids=[S[0]]), now=BASE)
        check(svc._waiting_count(db, f.id) == 40 and svc._waiting_count(db, g.id) == 30,
              "F(40人)/G(30人) 因S1满员均进入候补，F 序号在前")
        s1_line = [x for x in svc._active_allocations(db, a.id) if x.session_id == S[0]][0]
        svc.cancel_allocation_line(db, a.id, s1_line.id, reason="改期", now=BASE)
        assert_conserved(db, a.id, "A退出S1")
        check(svc._active_count(db, a.id) == 0 and svc._waiting_count(db, a.id) == 30,
              "A 的30人退出S1后保留在申请内、回到候补（意向不含刚退出的S1）")
        check(any("碎尾" in x.reason or "不足团" in x.reason
                  for x in events_of(db, f.id, AllocationEventType.PROMOTE_SKIP)),
              "F（40人/最小团30）面对S1的30空位：补30会剩10人不足成团，按当前资格跳过并留痕")
        g_lines = svc._active_allocations(db, g.id)
        check(len(g_lines) == 1 and g_lines[0].session_id == S[0]
              and g_lines[0].count == 30 and g_lines[0].source.value == "候补递补",
              "跳过F后队列顺延，G 的30人恰好成团，原子递补进 S1")
        svc.confirm_application(db, g.id, now=BASE)
        assert_conserved(db, g.id, "G确认")
        assert_conserved(db, f.id, "F跳过后")

        header("7. B 部分接受：仅确认S4已有占用，拒绝新递补的S3 → 40人回候补，C顺延递补S3")
        # B 当前 S4=60已确认、S3=40待确认；构造可拒绝的第二待确认：先补登使B再获一场
        # 直接对 S3 明细做"拒绝"：学校不接受 S3 的40人（部分接受=接受集合为空非法，
        # 故用独立的 P/Q 场景演示严格部分接受，B 这里演示拒绝递补结果经由缩减候补实现）
        # —— 严格部分接受场景：P 60人分S8/S9各30，只确认S8，S9的30释放给队首Q
        p = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=60, min_group_size=30,
            preferred_session_ids=[S[7], S[8]]), now=BASE)
        q = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=30, min_group_size=30,
            preferred_session_ids=[S[8]]), now=BASE)
        check(line_counts(db, p.id) == sorted([(S[7], 30, "待确认"), (S[8], 30, "待确认")]),
              "P 拆为 S8/S9 各30人待确认")
        check(svc._waiting_count(db, q.id) == 30, "Q 因S9满员，30人全部候补")
        s8_line = [x for x in svc._active_allocations(db, p.id) if x.session_id == S[7]][0]
        svc.confirm_application(db, p.id, accept_allocation_ids=[s8_line.id], now=BASE)
        assert_conserved(db, p.id, "P部分接受")
        check(line_counts(db, p.id) == [(S[7], 30, "已确认")],
              "P 仅 S8 30人转为正式占用")
        check(svc._waiting_count(db, p.id) == 30, "被拒的 S9 30人退回候补（人数守恒）")
        q_lines = svc._active_allocations(db, q.id)
        check(len(q_lines) == 1 and q_lines[0].session_id == S[8]
              and q_lines[0].count == 30 and q_lines[0].source.value == "候补递补",
              "S9释放后，候补的 Q 按顺序原子递补30人")
        svc.confirm_application(db, q.id, now=BASE)
        check(any("部分接受" in x.reason for x in events_of(db, p.id)),
              "P 的部分接受事件写明拒绝明细与退回候补原因")
        # B 拒绝 S3 的40人：经退出该场接口同样完成"不接受+容量释放"
        b_s3 = [x for x in svc._active_allocations(db, b.id) if x.session_id == S[2]][0]
        svc.cancel_allocation_line(db, b.id, b_s3.id, reason="学校不接受该场时间", now=BASE)
        assert_conserved(db, b.id, "B拒S3")
        c_lines = svc._active_allocations(db, c.id)
        check(len(c_lines) == 1 and c_lines[0].session_id == S[2]
              and c_lines[0].count == 50,
              "B释放S3后，C（seq2）50人原子递补进S3")
        svc.confirm_application(db, c.id, now=BASE)
        assert_conserved(db, c.id, "C确认")
        assert_conserved(db, b.id, "B重排后")

        header("8. D 60人全在S7但超时1小时未确认 → 整体过期，S7容量释放")
        d = svc.create_application(db, schemas.GroupApplicationCreate(
            school_id=school.id, total_count=60, min_group_size=30,
            preferred_session_ids=[S[6]], confirm_deadline_hours=1), now=BASE)
        check(line_counts(db, d.id) == [(S[6], 60, "待确认")], "D 初始60人全在S7")
        expired_ids = svc.expire_overdue(db, now=BASE + timedelta(hours=49))
        check(expired_ids == [d.id], f"仅D整体过期：{expired_ids}")
        check(svc.get_application(db, d.id).status.value == "已过期", "D 状态为已过期")
        check(svc._session_active_count(db, S[6]) == 0, "S7 的60个预留全部释放")

        header("9. 给队首B补登S7偏好 → 立即按资格递补40人（偏好更新触发递补）")
        b_entry = sorted(svc._waiting_entries(db, b.id), key=lambda x: x.seq)[0]
        old_prefs = json.loads(b_entry.preferred_session_ids)
        svc.update_waitlist_preferences(db, b_entry.seq, old_prefs + [S[6]], now=BASE + timedelta(hours=49))
        b_lines = svc._active_allocations(db, b.id)
        check(sorted((x.session_id, x.count, x.status.value, x.source.value) for x in b_lines)
              == [(S[3], 60, "已确认", "初始拆分"),
                  (S[6], 40, "待确认", "候补递补")],
              "B 凭队列顺序递补 S7 40人形成新预留")
        assert_conserved(db, b.id, "B补登递补")

        header("10. 查询可追踪：容量视图、候补顺序、事件原因、守恒快照")
        view3 = svc.session_capacity_view(db, S[2])
        check((view3.capacity, view3.occupied_confirmed, view3.available) == (50, 50, 0),
              f"S3 正式占用50/50：{view3.model_dump()}")
        queue = svc.list_waitlist(db)
        check(all(x.status.value == "候补中" for x in queue)
              and [x.seq for x in queue] == sorted(x.seq for x in queue),
              f"候补队列只含候补中且按seq有序：{[(x.seq, x.application_id, x.remaining_count) for x in queue]}")
        b_kinds = {x.action.value for x in events_of(db, b.id)}
        check({"进入候补", "候补递补", "学校确认", "重新入队", "场次取消"} <= b_kinds,
              f"B 的事件类型覆盖拆分→候补→取消→递补→重排全流程：{b_kinds}")
        check(bool(events_of(db, f.id, AllocationEventType.PROMOTE_SKIP))
              and bool(events_of(db, c.id, AllocationEventType.PROMOTE_SKIP)),
              "F 与 C 的候补资格跳过事件（碎尾/不足成团）均可查询")
        for aid in (a.id, b.id, c.id, e.id, p.id, q.id, f.id, g.id):
            evs = events_of(db, aid)
            ok = all(json.loads(x.snapshot)["conservation_ok"] for x in evs)
            check(ok, f"申请{aid} 全部 {len(evs)} 条事件快照守恒标志均为 true")
        promote_ev = [x for x in events_of(db, c.id, AllocationEventType.PROMOTE)
                      if "递补 50 人" in x.reason]
        check(bool(promote_ev) and "排队顺序" in promote_ev[0].reason
              and "资格" in promote_ev[0].reason,
              "C 的递补事件同时说明排队顺序依据与当前资格依据")

        header("11. HTTP API 冒烟（TestClient）")
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get("/api/group-applications/waitlist/queue")
        check(r.status_code == 200 and isinstance(r.json(), list)
              and all("remaining_count" in x for x in r.json()), "GET 候补队列")
        r = client.get(f"/api/group-applications/{a.id}")
        check(r.status_code == 200 and r.json()["conservation"]["conserved"]
              and len(r.json()["events"]) >= 3, "GET 申请详情含守恒结论与事件流")
        r = client.get(f"/api/group-applications/sessions/{S[2]}/capacity")
        check(r.status_code == 200 and r.json()["occupied_confirmed"] == 50,
              "GET 场次容量视图")
        r = client.post(f"/api/group-applications/sessions/{S[6]}/promote")
        check(r.status_code == 200 and r.json()["promoted_lines"] == 0,
              "S7剩20位不足任何队首最小团，手动递补返回0")
        r = client.post(f"/api/group-applications/waitlist/expire-overdue")
        check(r.status_code == 200 and r.json()["expired_application_ids"] == [],
              "再次扫描超时：无新增过期（B的S7预留截止时间在49h之后）")
        # 非法入参校验
        r = client.post("/api/group-applications", json={
            "school_id": school.id, "total_count": 10, "min_group_size": 30,
            "preferred_session_ids": [S[0]]})
        check(r.status_code == 400, "最小团>总人数被拒绝(400)")
        r = client.post("/api/group-applications", json={
            "school_id": school.id, "total_count": 10, "min_group_size": 5,
            "preferred_session_ids": [S[1]]})
        check(r.status_code == 400, "偏好已取消场次被拒绝(400)")

        header("12. 并发报名不超卖：12线程×20人抢 S4（容量60，最小团10）")
        import threading
        v10 = Venue(name="并发场地", venue_type="展厅", capacity=60)
        db.add(v10)
        db.flush()
        s10 = Session(title="并发场", theme_id=theme.id, venue_id=v10.id,
                      session_type=SessionType.RESEARCH,
                      start_time=day.replace(hour=6), end_time=day.replace(hour=6, minute=30),
                      audience_type=AudienceType.SCHOOL)
        db.add(s10)
        db.commit()
        sid10 = s10.id
        results, errors_lock, errs = [], threading.Lock(), []

        def worker(i):
            from app.database import SessionLocal as SL
            tdb = SL()
            try:
                ap = svc.create_application(tdb, schemas.GroupApplicationCreate(
                    school_id=school.id, total_count=20, min_group_size=10,
                    preferred_session_ids=[sid10]), now=BASE + timedelta(minutes=i))
                with errors_lock:
                    results.append(ap.id)
            except Exception as ex:  # noqa
                with errors_lock:
                    errs.append(str(ex))
            finally:
                tdb.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(not errs, f"12 个并发报名均无异常：{errs[:2]}")
        active10 = svc._session_active_count(db, sid10)
        check(active10 <= 60, f"S10 有效占用 {active10} 不超过容量 60（无超卖）")
        placed = sum(1 for aid in results
                     if svc._active_count(db, aid) > 0)
        check(placed == 3, f"恰好 3 个申请各放入20人占满60，其余入候补：实际放入 {placed} 个")
        all_ok = True
        for aid in results:
            ap = svc.get_application(db, aid)
            if svc._active_count(db, aid) + svc._waiting_count(db, aid) != ap.total_count:
                all_ok = False
        check(all_ok, "并发产生的 12 个申请全部满足人数守恒")
        queue10 = svc.list_waitlist(db, sid10)
        check([x.seq for x in queue10] == sorted(x.seq for x in queue10)
              and sum(x.remaining_count for x in queue10) == 180,
              f"未满足的 {9*20} 人按全局序号有序候补")

    except Exception:
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()

    print("\n" + "=" * 72)
    if failures:
        print(f"  存在 {len(failures)} 个失败断言：")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print("  所有测试通过：拆分/确认占用/候补顺序递补/缩减/部分接受/超时/跨场取消/守恒/原因可查")
    print("=" * 72)


if __name__ == "__main__":
    main()
