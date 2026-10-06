"""团体申请容量分配服务

容量模型
--------
一场研学活动的容量取场地容量 ``venue.capacity``。一个申请的人在任一时刻只可能处于
两类位置之一：

* 一条"分配明细"（待确认 PROPOSED / 已确认 CONFIRMED）—— PROPOSED 是学校确认前的
  软预留，同样占用容量；CONFIRMED 是学校确认后的正式占用，讲解员与场地据此锁定；
* 全局候补队列里的一个条目（WAITING），``seq`` 即排队顺序依据。

容量守恒（申请处于非终态时始终成立）::

    申请总人数 = Σ 有效分配(PROPOSED + CONFIRMED) 人数
               + Σ 候补中(WAITING) 条目剩余人数

* 学校缩减人数：显式核减总人数，削减量按 候补 → 待确认 → 已确认 的顺序腾出；
* 部分接受 / 超时退回 / 单场退出：人数不消失，退回候补队列；
* 场次取消：占用解除并按其余偏好重新入队；连一个偏好都不剩的条目保留为空偏好候补
  （WAITING、意向为空），人数依旧不丢，可在补登偏好后继续递补；
* 申请整体取消 / 整体超时未确认是终态：总人数冻结，全部人数通过事件流水逐条核销，
  释放出的容量立即按全局候补顺序原子递补。

每一次拆分、确认、缩减、递补、跳过、过期、取消都写 ``AllocationEvent``，带原因与
当时的守恒快照，申请查询可逐条追溯"为什么这么拆 / 为什么轮到它"。
"""
from __future__ import annotations

import functools
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.models import (
    ApplicationAllocation,
    ApplicationPreference,
    AllocationEvent,
    AllocationEventType,
    AllocationSource,
    AllocationStatus,
    Assignment,
    GroupApplication,
    GroupApplicationStatus,
    School,
    Session,
    SessionStatus,
    WaitlistEntry,
    WaitlistStatus,
)
from app import schemas

_TERMINAL_STATUSES = (GroupApplicationStatus.CANCELLED, GroupApplicationStatus.EXPIRED)

# 单容器部署下，所有容量写操作（拆分/确认/缩减/取消/递补）在进程内串行执行，
# 保证"读剩余容量→占位→提交"不被并发请求穿插而超卖；递补在同一写操作内完成。
_WRITE_LOCK = threading.RLock()


def serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now()


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite 中时间列以 naive datetime 存储，比较前统一去掉 tzinfo。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _prefs_list(entry: WaitlistEntry) -> List[int]:
    try:
        return [int(x) for x in json.loads(entry.preferred_session_ids or "[]")]
    except (ValueError, TypeError):
        return []


def _active_allocations(db: DbSession, application_id: int) -> List[ApplicationAllocation]:
    return (
        db.query(ApplicationAllocation)
        .filter(
            ApplicationAllocation.application_id == application_id,
            ApplicationAllocation.status.in_([AllocationStatus.PROPOSED,
                                             AllocationStatus.CONFIRMED]),
        )
        .all()
    )


def _active_count(db: DbSession, application_id: int) -> int:
    return sum(a.count for a in _active_allocations(db, application_id))


def _waiting_entries(db: DbSession, application_id: int) -> List[WaitlistEntry]:
    return (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.application_id == application_id,
            WaitlistEntry.status == WaitlistStatus.WAITING,
        )
        .all()
    )


def _waiting_count(db: DbSession, application_id: int) -> int:
    return sum(e.remaining_count for e in _waiting_entries(db, application_id))


def _current_total(db: DbSession, application_id: int) -> int:
    return _active_count(db, application_id) + _waiting_count(db, application_id)


def _session_active_count(db: DbSession, session_id: int) -> int:
    rows = (
        db.query(ApplicationAllocation.count)
        .filter(
            ApplicationAllocation.session_id == session_id,
            ApplicationAllocation.status.in_([AllocationStatus.PROPOSED,
                                             AllocationStatus.CONFIRMED]),
        )
        .all()
    )
    return sum(r[0] for r in rows)


def _session_confirmed_count(db: DbSession, session_id: int) -> int:
    rows = (
        db.query(ApplicationAllocation.count)
        .filter(ApplicationAllocation.session_id == session_id,
                ApplicationAllocation.status == AllocationStatus.CONFIRMED)
        .all()
    )
    return sum(r[0] for r in rows)


def _session_capacity(session: Session) -> Optional[int]:
    return session.venue.capacity if session.venue and session.venue.capacity else None


def _session_remaining(db: DbSession, session: Session) -> Optional[int]:
    cap = _session_capacity(session)
    return None if cap is None else cap - _session_active_count(db, session.id)


def _time_overlap(s1: Session, s2: Session) -> bool:
    if s1.id == s2.id:
        return False
    return s1.start_time < s2.end_time and s2.start_time < s1.end_time


def _next_seq(db: DbSession) -> int:
    last = db.query(WaitlistEntry).order_by(WaitlistEntry.seq.desc()).first()
    return (last.seq + 1) if last else 1


def _snapshot(db: DbSession, app: GroupApplication) -> Dict:
    lines = _active_allocations(db, app.id)
    entries = _waiting_entries(db, app.id)
    active = sum(a.count for a in lines)
    confirmed = sum(a.count for a in lines if a.status == AllocationStatus.CONFIRMED)
    waiting = sum(e.remaining_count for e in entries)
    terminal = app.status in _TERMINAL_STATUSES
    return {
        "status": app.status.value,
        "total": app.total_count,
        "active_total": active,
        "confirmed_total": confirmed,
        "proposed_total": active - confirmed,
        "waiting_total": waiting,
        "current_total": active + waiting,
        "conservation_ok": (active + waiting == 0) if terminal else (active + waiting == app.total_count),
        "lines": [
            {"allocation_id": a.id, "session_id": a.session_id,
             "count": a.count, "status": a.status.value}
            for a in sorted(lines, key=lambda x: x.id)
        ],
        "waitlist": [
            {"seq": e.seq, "remaining": e.remaining_count, "status": e.status.value,
             "preferred_session_ids": _prefs_list(e)}
            for e in sorted(entries, key=lambda x: x.seq)
        ],
    }


def _log(db: DbSession, app: GroupApplication, action: AllocationEventType,
         reason: str, count: int = 0, session_id: Optional[int] = None,
         waitlist_seq: Optional[int] = None) -> AllocationEvent:
    event = AllocationEvent(
        application_id=app.id,
        session_id=session_id,
        waitlist_seq=waitlist_seq,
        action=action,
        count=count,
        reason=reason,
        snapshot=json.dumps(_snapshot(db, app), ensure_ascii=False),
    )
    db.add(event)
    db.flush()
    return event


def _refresh_status(app: GroupApplication, db: DbSession) -> None:
    db.flush()  # autoflush=False：先落挂起改动，下面的统计查询才能读到最新状态
    if app.status in _TERMINAL_STATUSES:
        return
    lines = _active_allocations(db, app.id)
    has_proposed = any(a.status == AllocationStatus.PROPOSED for a in lines)
    has_confirmed = any(a.status == AllocationStatus.CONFIRMED for a in lines)
    waiting = _waiting_count(db, app.id)
    if not has_proposed and not has_confirmed and waiting == 0:
        app.status = GroupApplicationStatus.CANCELLED
    elif not has_proposed and waiting == 0:
        app.status = GroupApplicationStatus.CONFIRMED
    elif has_confirmed:
        app.status = GroupApplicationStatus.PARTIALLY_CONFIRMED
    else:
        app.status = GroupApplicationStatus.PENDING_CONFIRM


def _assert_conservation(db: DbSession, app: GroupApplication) -> None:
    """非终态申请守恒断言：总人数 = 有效占用 + 候补剩余，违反则回滚。"""
    db.flush()  # autoflush=False：确保按最新挂起状态统计
    if app.status in _TERMINAL_STATUSES:
        return
    active = _active_count(db, app.id)
    waiting = _waiting_count(db, app.id)
    if active + waiting != app.total_count:
        raise RuntimeError(
            f"申请 {app.id} 容量守恒校验失败：总人数 {app.total_count} != "
            f"有效占用 {active} + 候补 {waiting}"
        )


# ---------------------------------------------------------------------------
# 候补入队
# ---------------------------------------------------------------------------

def _line_prefs(db: DbSession, app: GroupApplication,
                line: ApplicationAllocation) -> List[int]:
    """明细失效后重新入队应使用的有序意向：

    候补递补来的明细沿用其来源候补条目当时记录的意向（可能含后补登场次），
    初始拆分的明细用申请偏好表；已取消场次与刚失效的本场都剔除。"""
    prefs: List[int] = []
    if line.source_waitlist_seq is not None:
        entry = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.seq == line.source_waitlist_seq,
                    WaitlistEntry.application_id == app.id)
            .first()
        )
        if entry is not None:
            prefs = _prefs_list(entry)
    if not prefs:
        prefs = [p.session_id for p in
                 db.query(ApplicationPreference)
                 .filter(ApplicationPreference.application_id == app.id)
                 .order_by(ApplicationPreference.seq).all()]
    result = []
    for sid in prefs:
        if sid == line.session_id:
            continue
        row = db.query(Session.status).filter(Session.id == sid).first()
        if row and row[0] != SessionStatus.CANCELLED:
            result.append(sid)
    return result


def _enqueue_silent(db: DbSession, app: GroupApplication, count: int,
                    preferred_session_ids: List[int]) -> WaitlistEntry:
    """排到候补队列尾部但暂不写事件（调用方在状态一致后统一写事件）。"""
    count = int(count)
    if count <= 0:
        raise ValueError("候补人数必须为正数")
    entry = WaitlistEntry(
        application_id=app.id,
        remaining_count=count,
        original_count=count,
        seq=_next_seq(db),
        preferred_session_ids=json.dumps(list(preferred_session_ids), ensure_ascii=False),
        status=WaitlistStatus.WAITING,
    )
    db.add(entry)
    db.flush()
    return entry


def _enqueue(db: DbSession, app: GroupApplication, count: int,
             preferred_session_ids: List[int], reason: str,
             action: AllocationEventType = AllocationEventType.WAITLIST_ENQUEUE) -> WaitlistEntry:
    """把 ``count`` 人排到全局候补队列尾部并写事件（要求调用时人数守恒已成立）。"""
    entry = _enqueue_silent(db, app, count, preferred_session_ids)
    _log(db, app, action, reason, count=count, waitlist_seq=entry.seq)
    return entry


# ---------------------------------------------------------------------------
# 提交申请与初始拆分
# ---------------------------------------------------------------------------

@serialized
def create_application(db: DbSession, payload: schemas.GroupApplicationCreate,
                       now: Optional[datetime] = None) -> GroupApplication:
    """按时间偏好顺序、最小成团人数与同行约束，把申请原子拆分到多个场次；
    放不下的部分整体进入全局候补队列。"""
    now = _naive(now) or _now()
    if payload.total_count < 1:
        raise ValueError("申请总人数必须为正整数")
    if not 1 <= payload.min_group_size <= payload.total_count:
        raise ValueError("最小成团人数需在 1 与申请总人数之间")
    if not db.query(School).filter(School.id == payload.school_id).first():
        raise ValueError("学校不存在")

    pref_ids: List[int] = []
    pref_sessions: List[Session] = []
    for sid in payload.preferred_session_ids or []:
        if sid in pref_ids:
            continue
        session = db.query(Session).filter(Session.id == sid).first()
        if session is None:
            raise ValueError(f"场次 {sid} 不存在")
        if session.status == SessionStatus.CANCELLED:
            raise ValueError(f"场次 {sid} 已取消，不可作为时间偏好")
        pref_ids.append(sid)
        pref_sessions.append(session)
    if not pref_ids:
        raise ValueError("至少需要提供一个时间偏好场次")

    deadline = now + timedelta(hours=payload.confirm_deadline_hours
                               if payload.confirm_deadline_hours is not None
                               else settings.GROUP_CONFIRM_DEADLINE_HOURS)

    app = GroupApplication(
        school_id=payload.school_id,
        contact_person=payload.contact_person,
        phone=payload.phone,
        total_count=payload.total_count,
        min_group_size=payload.min_group_size,
        companion_note=payload.companion_note,
        status=GroupApplicationStatus.PENDING_CONFIRM,
        confirm_deadline=deadline,
    )
    db.add(app)
    db.flush()
    for seq, sid in enumerate(pref_ids, start=1):
        db.add(ApplicationPreference(application_id=app.id, session_id=sid, seq=seq))

    # 贪心拆分：按偏好顺序依次放入；每片必须 >= 最小成团人数，且不给候补留碎尾
    remaining = payload.total_count
    planned: List[Tuple[Session, int]] = []
    chosen_sessions: List[Session] = []
    skip_reasons: List[str] = []
    for seq, session in enumerate(pref_sessions, start=1):
        if remaining == 0:
            break
        label = f"第{seq}偏好《{session.title}》"
        if any(_time_overlap(session, s) for s in chosen_sessions):
            skip_reasons.append(f"{label}与已选中场次时间重叠（同行者须同行，不能拆开），跳过")
            continue
        room = _session_remaining(db, session)
        room = remaining if room is None else room
        take = min(remaining, room)
        if take < app.min_group_size:
            skip_reasons.append(f"{label}仅剩 {room} 个容量位，不足最小成团人数"
                                f" {app.min_group_size} 人，跳过")
            continue
        leftover = remaining - take
        if 0 < leftover < app.min_group_size:
            # 放满会给候补留下不足成团的碎尾：尝试只削出一个最小团
            if remaining >= 2 * app.min_group_size:
                take = remaining - app.min_group_size
                leftover = app.min_group_size
            else:
                skip_reasons.append(f"{label}若放入 {take} 人会给候补留下 {leftover} 人的碎尾"
                                    f"（小于最小成团 {app.min_group_size} 人），且无法再削出"
                                    f"一个完整最小团，整场跳过")
                continue
        planned.append((session, take))
        chosen_sessions.append(session)
        remaining -= take

    for session, count in planned:
        db.add(ApplicationAllocation(
            application_id=app.id,
            session_id=session.id,
            count=count,
            status=AllocationStatus.PROPOSED,
            source=AllocationSource.INITIAL_SPLIT,
            confirm_deadline=deadline,
        ))

    # 先把状态改到守恒成立，再统一写事件，保证每条事件快照都自洽
    if remaining > 0:
        wl_entry = _enqueue_silent(db, app, remaining, pref_ids)
    db.flush()
    _assert_conservation(db, app)

    for session, count in planned:
        _log(db, app, AllocationEventType.SPLIT,
             f"按时间偏好顺序选中《{session.title}》"
             f"（{session.start_time:%Y-%m-%d %H:%M}），拆分 {count} 人形成待确认预留；"
             f"拆分依据：偏好序号优先、单场容量上限、每片不小于最小成团 "
             f"{app.min_group_size} 人、同行者不拆到时间重叠场次",
             count=count, session_id=session.id)
    for skip in skip_reasons:
        _log(db, app, AllocationEventType.SPLIT, skip, count=0)
    if remaining > 0:
        reason = ("；".join(skip_reasons) + "。" if skip_reasons else "")
        reason += (f"经上述拆分后仍有 {remaining} 人无法在保证每片不小于最小成团人数 "
                   f"{app.min_group_size} 的前提下安排进任何偏好场次，整体进入候补队列"
                   f"（排队序号 {wl_entry.seq}），待容量释放时按队列顺序与当前资格原子递补")
        _log(db, app, AllocationEventType.WAITLIST_ENQUEUE, reason,
             count=remaining, waitlist_seq=wl_entry.seq)

    db.commit()
    db.refresh(app)
    return app


# ---------------------------------------------------------------------------
# 学校确认 / 部分接受
# ---------------------------------------------------------------------------

@serialized
def confirm_application(db: DbSession, application_id: int,
                        accept_allocation_ids: Optional[List[int]] = None,
                        now: Optional[datetime] = None) -> GroupApplication:
    """学校确认拆分方案；只接受部分明细即为"部分接受"，其余人数退回候补。"""
    now = _naive(now) or _now()
    app = _get_active_application(db, application_id)
    proposed = [a for a in _active_allocations(db, app.id)
                if a.status == AllocationStatus.PROPOSED]
    if not proposed:
        raise ValueError("当前没有待确认的分配明细")

    if accept_allocation_ids is None:
        accepted = list(proposed)
    else:
        id_set = set(accept_allocation_ids or [])
        accepted = [a for a in proposed if a.id in id_set]
        unknown = id_set - {a.id for a in proposed}
        if unknown:
            raise ValueError(f"分配明细 {sorted(unknown)} 不存在或不是待确认状态")
        if not accepted:
            raise ValueError("至少需要接受一条待确认的分配明细")

    accepted_ids = {a.id for a in accepted}
    released_sessions: Set[int] = set()
    deferred: List[Dict] = []

    # 被学校放弃的明细：先完成"删明细 + 退回候补"使人数守恒，再统一写事件
    for line in proposed:
        if line.id in accepted_ids:
            continue
        sid, count = line.session_id, line.count
        title = line.session.title
        prefs = _line_prefs(db, app, line)
        released_sessions.add(sid)
        db.delete(line)
        entry = _enqueue_silent(db, app, count, prefs)
        deferred.append(dict(action=AllocationEventType.PARTIAL_CONFIRM,
                             reason=(f"学校部分接受方案，未接受《{title}》的 {count} 个预留名额；"
                                     f"该 {count} 人不消失、退回候补队列队尾（序号 {entry.seq}），"
                                     f"待偏好场次容量释放后按资格递补"),
                             count=count, waitlist_seq=entry.seq))

    for line in accepted:
        line.status = AllocationStatus.CONFIRMED
        line.confirmed_at = now
        deferred.append(dict(action=AllocationEventType.CONFIRM,
                             reason=(f"学校确认接受《{line.session.title}》{line.count} 人，"
                                     f"预留转为正式占用，对应讲解员排班与场地容量锁定"),
                             count=line.count, session_id=line.session_id))

    _refresh_status(app, db)
    db.flush()
    _assert_conservation(db, app)
    for d in deferred:
        _log(db, app, d.pop("action"), d.pop("reason"), **d)
    db.commit()
    if released_sessions:
        _promote_released_sessions(db, released_sessions, now)
    db.refresh(app)
    return app


# ---------------------------------------------------------------------------
# 学校缩减人数
# ---------------------------------------------------------------------------

@serialized
def reduce_application(db: DbSession, application_id: int, new_total: int,
                       now: Optional[datetime] = None) -> GroupApplication:
    """学校缩减总人数。

    削减顺序：候补条目（后入队先削）→ 待确认预留 → 已确认正式占用（偏好靠后先削）。
    任何整块要么整条撤销、要么削后仍不小于最小成团人数；被腾出的容量立即原子递补。
    """
    now = _naive(now) or _now()
    app = _get_active_application(db, application_id)
    new_total = int(new_total)
    if new_total < app.min_group_size:
        raise ValueError(f"缩减后人数不能小于最小成团人数 {app.min_group_size}；"
                         f"若全部退出请使用取消接口")
    current = _current_total(db, app.id)
    if new_total >= current:
        raise ValueError(f"新人数 {new_total} 不小于当前总人数 {current}，无需缩减")

    cut = current - new_total
    app.total_count = new_total
    released_sessions: Set[int] = set()
    deferred: List[Dict] = []

    # 1) 先削候补：后入队的条目先撤销
    for entry in sorted(_waiting_entries(db, app.id), key=lambda e: e.seq, reverse=True):
        if cut == 0:
            break
        if cut >= entry.remaining_count:
            cut -= entry.remaining_count
            n = entry.remaining_count
            entry.status = WaitlistStatus.CANCELLED
            entry.close_reason = "学校缩减人数，候补名额全部撤销"
            deferred.append(dict(action=AllocationEventType.REDUCE,
                                 reason=f"学校缩减人数，序号 {entry.seq} 候补条目 {n} 人整体撤销",
                                 count=n, waitlist_seq=entry.seq))
        elif cut <= entry.remaining_count - app.min_group_size:
            old = entry.remaining_count
            entry.remaining_count -= cut
            n, cut = cut, 0
            deferred.append(dict(action=AllocationEventType.REDUCE,
                                 reason=(f"学校缩减人数，序号 {entry.seq} 候补名额由 {old} 人减至 "
                                         f"{old - n} 人（削后仍不小于最小成团人数）"),
                                 count=n, waitlist_seq=entry.seq))
        # 其余情况：部分削会产生碎尾，整块撤销又超出削减量 → 跳过试下一块

    # 2) 再削场次明细：待确认优先于已确认（少动已锁定资源），同级按偏好倒序
    if cut > 0:
        pref_order = {p.session_id: p.seq for p in
                      db.query(ApplicationPreference)
                      .filter(ApplicationPreference.application_id == app.id).all()}
        lines = sorted(
            _active_allocations(db, app.id),
            key=lambda a: (
                0 if a.status == AllocationStatus.PROPOSED else 1,
                pref_order.get(a.session_id, 9999),
            ),
            reverse=True,
        )
        for line in lines:
            if cut == 0:
                break
            if cut >= line.count:
                cut -= line.count
                n = line.count
                label = "待确认预留" if line.status == AllocationStatus.PROPOSED else "正式占用"
                released_sessions.add(line.session_id)
                line.status = AllocationStatus.CANCELLED
                line.cancel_reason = "学校缩减人数，整条明细撤销"
                deferred.append(dict(action=AllocationEventType.REDUCE,
                                     reason=(f"学校缩减人数，《{line.session.title}》{label} {n} 人整条撤销，"
                                             f"容量立即释放给候补队列"),
                                     count=n, session_id=line.session_id))
            elif cut <= line.count - app.min_group_size:
                old = line.count
                line.count -= cut
                released_sessions.add(line.session_id)
                n, cut = cut, 0
                deferred.append(dict(action=AllocationEventType.REDUCE,
                                     reason=(f"学校缩减人数，《{line.session.title}》名额由 {old} 人减至 "
                                             f"{old - n} 人（削后仍不小于最小成团人数），释放 {n} 个容量位"),
                                     count=n, session_id=line.session_id))

    if cut > 0:
        db.rollback()
        raise ValueError("受最小成团人数与同行约束限制，无法恰好缩减到该人数；"
                         "请改取更大的目标人数，或先退出整场")

    _refresh_status(app, db)
    db.flush()
    _assert_conservation(db, app)
    for d in deferred:
        _log(db, app, d.pop("action"), d.pop("reason"), **d)
    db.commit()
    if released_sessions:
        _promote_released_sessions(db, released_sessions, now)
    db.refresh(app)
    return app


# ---------------------------------------------------------------------------
# 取消：整体取消 / 退出单场 / 整场取消（跨场波及）
# ---------------------------------------------------------------------------

@serialized
def cancel_application(db: DbSession, application_id: int, reason: Optional[str] = None,
                       now: Optional[datetime] = None) -> GroupApplication:
    """学校整体取消申请：释放全部预留与正式占用（触发原子递补），关闭全部候补。"""
    now = _naive(now) or _now()
    app = _get_active_application(db, application_id)
    released = {a.session_id for a in _active_allocations(db, app.id)}
    reason = reason or "未填写原因"
    deferred: List[Dict] = []

    for line in _active_allocations(db, app.id):
        label = "待确认预留" if line.status == AllocationStatus.PROPOSED else "正式占用"
        n = line.count
        line.status = AllocationStatus.CANCELLED
        line.cancel_reason = f"申请整体取消：{reason}"
        deferred.append(dict(action=AllocationEventType.CANCEL_APPLICATION,
                             reason=(f"申请整体取消（{reason}），《{line.session.title}》{label} {n} 人解除，"
                                     f"容量归还并立即按候补顺序原子递补"),
                             count=n, session_id=line.session_id))

    for entry in _waiting_entries(db, app.id):
        n = entry.remaining_count
        entry.status = WaitlistStatus.CANCELLED
        entry.close_reason = "申请整体取消"
        deferred.append(dict(action=AllocationEventType.CANCEL_APPLICATION,
                             reason=f"申请整体取消，序号 {entry.seq} 候补条目（{n} 人）关闭",
                             count=n, waitlist_seq=entry.seq))

    app.status = GroupApplicationStatus.CANCELLED
    db.flush()
    for d in deferred:
        _log(db, app, d.pop("action"), d.pop("reason"), **d)
    db.commit()
    if released:
        _promote_released_sessions(db, released, now)
    db.refresh(app)
    return app


@serialized
def cancel_allocation_line(db: DbSession, application_id: int, allocation_id: int,
                           reason: Optional[str] = None,
                           now: Optional[datetime] = None) -> GroupApplication:
    """跨场调整时退出申请在某一场上的占用：人数退回候补，该场容量立即递补。"""
    now = _naive(now) or _now()
    app = _get_active_application(db, application_id)
    line = (
        db.query(ApplicationAllocation)
        .filter(ApplicationAllocation.id == allocation_id,
                ApplicationAllocation.application_id == application_id,
                ApplicationAllocation.status.in_([AllocationStatus.PROPOSED,
                                                  AllocationStatus.CONFIRMED]))
        .first()
    )
    if line is None:
        raise ValueError("有效的分配明细不存在")

    sid, count, title = line.session_id, line.count, line.session.title
    label = "待确认预留" if line.status == AllocationStatus.PROPOSED else "正式占用"
    line.status = AllocationStatus.CANCELLED
    line.cancel_reason = f"退出该场：{reason or '未填写原因'}"
    entry = _enqueue_silent(db, app, count, _line_prefs(db, app, line))

    _refresh_status(app, db)
    db.flush()
    _assert_conservation(db, app)
    _log(db, app, AllocationEventType.CANCEL_LINE,
         f"学校退出《{title}》（{reason or '未填写原因'}），{label} {count} 人解除；"
         f"人数保留在申请内、退回候补队列，该场容量同时释放给队列中其他团队",
         count=count, session_id=sid)
    _log(db, app, AllocationEventType.READMISSION,
         f"退出《{title}》后 {count} 人回到候补队列队尾（序号 {entry.seq}），"
         f"等待其余偏好场次容量释放",
         count=count, waitlist_seq=entry.seq)
    db.commit()
    _promote_released_sessions(db, {sid}, now)
    db.refresh(app)
    return app


@serialized
def cancel_session(db: DbSession, session_id: int, reason: Optional[str] = None,
                   operator: str = "工作人员",
                   now: Optional[datetime] = None) -> Dict:
    """取消整场（跨场取消）。

    1. 该场全部预留 / 正式占用解除，人数按各申请其余时间偏好重新入队候补
       （无剩余偏好的以空意向保留候补，人数不丢，待补登偏好）；
    2. 候补条目从意向中移除本场；
    3. 解除该场讲解员排班并把场次置为已取消，讲解员与场地不再被锁。
    """
    now = _naive(now) or _now()
    session = db.query(Session).filter(Session.id == session_id).first()
    if session is None:
        raise ValueError("场次不存在")
    if session.status == SessionStatus.CANCELLED:
        raise ValueError("场次已取消，请勿重复操作")
    reason = reason or "未填写原因"

    affected: Dict[int, GroupApplication] = {}
    deferred: Dict[int, List[Dict]] = {}

    def _defer(app_id, **kw):
        deferred.setdefault(app_id, []).append(kw)

    lines = (
        db.query(ApplicationAllocation)
        .filter(ApplicationAllocation.session_id == session_id,
                ApplicationAllocation.status.in_([AllocationStatus.PROPOSED,
                                                  AllocationStatus.CONFIRMED]))
        .all()
    )
    for line in lines:
        app = db.query(GroupApplication).filter(GroupApplication.id == line.application_id).first()
        affected[app.id] = app
        n = line.count
        line.status = AllocationStatus.CANCELLED
        line.cancel_reason = f"场次取消：{reason}"
        remaining_prefs = _line_prefs(db, app, line)
        entry = _enqueue_silent(db, app, n, remaining_prefs)
        _defer(app.id,
               action=AllocationEventType.CANCEL_SESSION,
               reason=(f"《{session.title}》被{operator}取消（{reason}），原占用 {n} 人解除锁定，"
                       f"讲解员与场地同步释放；{n} 人按学校其余时间偏好"
                       f"{'重新入候补队列' if remaining_prefs else '以空意向保留候补（需补登偏好）'}"),
               count=n, session_id=session_id)
        _defer(app.id,
               action=AllocationEventType.READMISSION,
               reason=(f"原《{session.title}》因场次取消解除，{n} 人重新进入候补队列"
                       f"（序号 {entry.seq}）等待其他场次"),
               count=n, waitlist_seq=entry.seq)

    entries = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.status == WaitlistStatus.WAITING)
        .order_by(WaitlistEntry.seq)
        .all()
    )
    for entry in entries:
        prefs = _prefs_list(entry)
        if session_id not in prefs:
            continue
        app = db.query(GroupApplication).filter(GroupApplication.id == entry.application_id).first()
        affected[app.id] = app
        new_prefs = [p for p in prefs if p != session_id]
        entry.preferred_session_ids = json.dumps(new_prefs, ensure_ascii=False)
        if new_prefs:
            detail = f"移除该意向后仍有 {len(new_prefs)} 个偏好场次，继续按原序号候补"
        else:
            detail = "已无其他意向场次，以空意向保留候补，待工作人员补登偏好后再参与递补"
        _defer(app.id,
               action=AllocationEventType.CANCEL_SESSION,
               reason=(f"《{session.title}》取消，序号 {entry.seq} 候补条目"
                       f"（{entry.remaining_count} 人）从意向中移除本场：{detail}"),
               count=0, waitlist_seq=entry.seq, session_id=session_id)

    assignment_count = db.query(Assignment).filter(Assignment.session_id == session_id).delete()
    session.status = SessionStatus.CANCELLED

    for app in affected.values():
        _refresh_status(app, db)
        db.flush()
        _assert_conservation(db, app)
    for app_id, items in deferred.items():
        app = affected[app_id]
        for kw in items:
            action = kw.pop("action")
            reason_text = kw.pop("reason")
            _log(db, app, action, reason_text, **kw)

    db.commit()
    return {
        "session_id": session_id,
        "released_allocations": len(lines),
        "released_people": sum(l.count for l in lines),
        "released_assignments": assignment_count,
        "affected_applications": sorted(affected.keys()),
    }


# ---------------------------------------------------------------------------
# 超时未确认
# ---------------------------------------------------------------------------

@serialized
def expire_overdue(db: DbSession, now: Optional[datetime] = None) -> List[int]:
    """扫描超过确认时限仍为待确认的预留。

    * 从未确认过任何场次的申请：整体过期，预留释放给候补队列，其自身候补同步关闭；
    * 已确认过部分场次的申请：仅把超时的那条预留退回其候补队尾，申请继续有效。
    """
    now = _naive(now) or _now()
    overdue_lines = (
        db.query(ApplicationAllocation)
        .filter(ApplicationAllocation.status == AllocationStatus.PROPOSED,
                ApplicationAllocation.confirm_deadline.isnot(None),
                ApplicationAllocation.confirm_deadline < now)
        .all()
    )
    app_ids = sorted({a.application_id for a in overdue_lines})
    released_sessions: Set[int] = set()
    expired_app_ids: List[int] = []

    for app_id in app_ids:
        app = db.query(GroupApplication).filter(GroupApplication.id == app_id).first()
        if app is None or app.status in _TERMINAL_STATUSES:
            continue
        lines = [a for a in overdue_lines if a.application_id == app_id]
        confirmed_now = (
            db.query(ApplicationAllocation)
            .filter(ApplicationAllocation.application_id == app_id,
                    ApplicationAllocation.status == AllocationStatus.CONFIRMED)
            .count()
        )
        deferred: List[Dict] = []

        if confirmed_now == 0 and app.status == GroupApplicationStatus.PENDING_CONFIRM:
            for line in lines:
                released_sessions.add(line.session_id)
                n = line.count
                line.status = AllocationStatus.CANCELLED
                line.cancel_reason = "超过确认时限未确认，整体过期"
                deferred.append(dict(action=AllocationEventType.EXPIRE,
                                     reason=(f"截至 {now:%Y-%m-%d %H:%M} 已超过确认时限"
                                             f"（截止 {line.confirm_deadline:%Y-%m-%d %H:%M}），"
                                             f"学校未确认任何场次，申请整体过期；"
                                             f"《{line.session.title}》{n} 个预留名额释放给候补队列"),
                                     count=n, session_id=line.session_id))
            for entry in _waiting_entries(db, app.id):
                n = entry.remaining_count
                entry.status = WaitlistStatus.EXPIRED
                entry.close_reason = "申请整体超时未确认"
                deferred.append(dict(action=AllocationEventType.EXPIRE,
                                     reason=(f"申请整体超时未确认，序号 {entry.seq} 自身候补条目"
                                             f"（{n} 人）同步关闭"),
                                     count=n, waitlist_seq=entry.seq))
            app.status = GroupApplicationStatus.EXPIRED
        else:
            for line in lines:
                released_sessions.add(line.session_id)
                n = line.count
                line.status = AllocationStatus.CANCELLED
                line.cancel_reason = "超过确认时限未确认"
                entry = _enqueue_silent(db, app, n, _line_prefs(db, app, line))
                deferred.append(dict(action=AllocationEventType.EXPIRE,
                                     reason=(f"《{line.session.title}》{n} 个预留名额超过确认时限未确认，"
                                             f"容量释放给候补队列，该 {n} 人退回本申请候补队尾"
                                             f"（意向不含刚超时的本场，须改投其他偏好或补登偏好）"),
                                     count=n, session_id=line.session_id))
                deferred.append(dict(action=AllocationEventType.READMISSION,
                                     reason=(f"《{line.session.title}》预留超时释放，{n} 人重新入候补队尾"
                                             f"（序号 {entry.seq}）"),
                                     count=n, waitlist_seq=entry.seq))
            _refresh_status(app, db)

        db.flush()
        _assert_conservation(db, app)
        for d in deferred:
            action = d.pop("action")
            reason_text = d.pop("reason")
            _log(db, app, action, reason_text, **d)
        expired_app_ids.append(app_id)

    db.commit()
    if released_sessions:
        _promote_released_sessions(db, released_sessions, now)
    return expired_app_ids


# ---------------------------------------------------------------------------
# 候补原子递补
# ---------------------------------------------------------------------------

def _eligible(db: DbSession, entry: WaitlistEntry, session: Session, room: int
              ) -> Tuple[bool, str, Optional[int]]:
    """按"当前资格"判断候补条目能否递补进本场；返回 (是否可补, 原因, 本次可补人数)。"""
    app = entry.application
    if app.status in _TERMINAL_STATUSES:
        return False, "申请已终结", None
    if session.id not in _prefs_list(entry):
        return False, "该场次不在其当前时间偏好内", None
    if session.status == SessionStatus.CANCELLED:
        return False, "场次已取消", None
    if any(a.session_id == session.id for a in _active_allocations(db, app.id)):
        return False, "该申请在此场次已有占用，不重复递补", None
    if any(_time_overlap(session, a.session) for a in _active_allocations(db, app.id)):
        return False, "与该申请已占用场次时间重叠（同行约束）", None
    if room < app.min_group_size:
        return False, f"剩余容量 {room} 不足其最小成团人数 {app.min_group_size}", None
    take = min(entry.remaining_count, room)
    leftover = entry.remaining_count - take
    if 0 < leftover < app.min_group_size:
        return False, (f"本次仅能容纳 {take} 人，会给该团队留下 {leftover} 人的不足团碎尾，"
                       f"须等更大容量释放"), None
    return True, "", take


def _promote_one_session(db: DbSession, session: Session, now: datetime) -> int:
    """容量释放后对单场反复扫描全局候补队列，按 seq 顺序原子递补到无人可补为止。"""
    promoted_count = 0
    logged_skips: Set[Tuple[int, int]] = set()
    while True:
        room = _session_remaining(db, session)
        if room is None or room <= 0:
            break
        candidates = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.status == WaitlistStatus.WAITING)
            .order_by(WaitlistEntry.seq)
            .all()
        )
        progressed = False
        for entry in candidates:
            ok, reason, take = _eligible(db, entry, session, room)
            if not ok:
                if session.id in _prefs_list(entry) and (entry.id, session.id) not in logged_skips:
                    logged_skips.add((entry.id, session.id))
                    _log(db, entry.application, AllocationEventType.PROMOTE_SKIP,
                         f"《{session.title}》释放 {room} 个容量位时，序号 {entry.seq} 候补团队"
                         f"（剩 {entry.remaining_count} 人）按当前资格被跳过：{reason}；"
                         f"队列继续向后查找",
                         count=0, session_id=session.id, waitlist_seq=entry.seq)
                continue

            app = entry.application
            deadline = now + timedelta(hours=settings.GROUP_CONFIRM_DEADLINE_HOURS)
            db.add(ApplicationAllocation(
                application_id=app.id,
                session_id=session.id,
                count=take,
                status=AllocationStatus.PROPOSED,
                source=AllocationSource.WAITLIST_PROMOTION,
                source_waitlist_seq=entry.seq,
                confirm_deadline=deadline,
            ))
            entry.remaining_count -= take
            if entry.remaining_count == 0:
                entry.status = WaitlistStatus.PROMOTED
                entry.close_reason = "容量释放后按队列顺序与当前资格递补完成"
            db.flush()
            _log(db, app, AllocationEventType.PROMOTE,
                 f"《{session.title}》释放容量后，序号 {entry.seq} 候补团队凭排队顺序"
                 f"（排在前且当前满足资格：本场在其时间偏好内、与已占场次不冲突、"
                 f"本次可补 {take} 人且不留不足团碎尾）原子递补 {take} 人，"
                 f"形成待确认预留，须在 {deadline:%Y-%m-%d %H:%M} 前确认，"
                 f"否则名额将再次释放并继续顺延",
                 count=take, session_id=session.id, waitlist_seq=entry.seq)
            promoted_count += 1
            progressed = True
            break  # 容量已变化，重新从队首扫描
        if not progressed:
            break
    return promoted_count


def _promote_released_sessions(db: DbSession, session_ids, now: datetime) -> None:
    for sid in set(session_ids):
        session = db.query(Session).filter(Session.id == sid).first()
        if session and session.status != SessionStatus.CANCELLED:
            _promote_one_session(db, session, now)
    db.commit()


@serialized
def promote_session_manually(db: DbSession, session_id: int,
                             now: Optional[datetime] = None) -> int:
    """人工触发某场候补递补（容量经其他途径释放、或补登偏好后使用）。"""
    now = _naive(now) or _now()
    session = db.query(Session).filter(Session.id == session_id).first()
    if session is None:
        raise ValueError("场次不存在")
    if session.status == SessionStatus.CANCELLED:
        raise ValueError("场次已取消")
    count = _promote_one_session(db, session, now)
    db.commit()
    return count


@serialized
def update_waitlist_preferences(db: DbSession, waitlist_seq: int,
                                preferred_session_ids: List[int],
                                now: Optional[datetime] = None) -> WaitlistEntry:
    """补登 / 修改候补条目的时间偏好（如原偏好场次全部取消后补登新场次）。

    保存后立即对新偏好中的场次尝试递补。
    """
    now = _naive(now) or _now()
    entry = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.seq == waitlist_seq,
                WaitlistEntry.status == WaitlistStatus.WAITING)
        .first()
    )
    if entry is None:
        raise ValueError("候补中 的条目不存在")
    new_prefs: List[int] = []
    for sid in preferred_session_ids or []:
        if sid in new_prefs:
            continue
        session = db.query(Session).filter(Session.id == sid).first()
        if session is None:
            raise ValueError(f"场次 {sid} 不存在")
        if session.status == SessionStatus.CANCELLED:
            raise ValueError(f"场次 {sid} 已取消")
        new_prefs.append(sid)
    old_prefs = _prefs_list(entry)
    entry.preferred_session_ids = json.dumps(new_prefs, ensure_ascii=False)
    db.flush()
    _log(db, entry.application, AllocationEventType.UPDATE_PREF,
         f"候补条目序号 {entry.seq} 的时间偏好由 {old_prefs} 更新为 {new_prefs}，"
         f"立即按当前队列顺序重新评估递补资格",
         count=0, waitlist_seq=entry.seq)
    db.commit()
    for sid in new_prefs:
        session = db.query(Session).filter(Session.id == sid).first()
        _promote_one_session(db, session, now)
    db.commit()
    return entry


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def _get_active_application(db: DbSession, application_id: int) -> GroupApplication:
    app = db.query(GroupApplication).filter(GroupApplication.id == application_id).first()
    if app is None:
        raise ValueError("申请不存在")
    if app.status in _TERMINAL_STATUSES:
        raise ValueError(f"申请已处于“{app.status.value}”终态，不能再执行该操作")
    return app


def get_application(db: DbSession, application_id: int) -> GroupApplication:
    app = db.query(GroupApplication).filter(GroupApplication.id == application_id).first()
    if app is None:
        raise ValueError("申请不存在")
    return app


def list_applications(db: DbSession, school_id: Optional[int] = None,
                      status: Optional[GroupApplicationStatus] = None,
                      skip: int = 0, limit: int = 100) -> List[GroupApplication]:
    q = db.query(GroupApplication)
    if school_id:
        q = q.filter(GroupApplication.school_id == school_id)
    if status:
        q = q.filter(GroupApplication.status == status)
    return q.order_by(GroupApplication.id.desc()).offset(skip).limit(limit).all()


def list_waitlist(db: DbSession, session_id: Optional[int] = None,
                  include_closed: bool = False) -> List[WaitlistEntry]:
    """候补队列（顺序依据 seq 升序）；默认只返回候补中条目，可按意向场次过滤。"""
    q = db.query(WaitlistEntry).order_by(WaitlistEntry.seq)
    if not include_closed:
        q = q.filter(WaitlistEntry.status == WaitlistStatus.WAITING)
    entries = q.all()
    if session_id is not None:
        entries = [e for e in entries if session_id in _prefs_list(e)]
    return entries


def session_capacity_view(db: DbSession, session_id: int) -> schemas.SessionCapacityView:
    session = db.query(Session).filter(Session.id == session_id).first()
    if session is None:
        raise ValueError("场次不存在")
    cap = _session_capacity(session)
    confirmed = _session_confirmed_count(db, session.id)
    active = _session_active_count(db, session.id)
    return schemas.SessionCapacityView(
        session_id=session.id,
        session_title=session.title,
        status=session.status.value,
        start_time=session.start_time,
        capacity=cap,
        occupied_confirmed=confirmed,
        soft_reserved=active - confirmed,
        available=None if cap is None else cap - active,
        waitlist=[
            schemas.WaitlistItem(
                seq=e.seq,
                application_id=e.application_id,
                school_name=e.application.school.name if e.application.school else "",
                remaining_count=e.remaining_count,
                original_count=e.original_count,
                status=e.status.value,
                preferred_session_ids=_prefs_list(e),
            )
            for e in list_waitlist(db, session_id)
        ],
    )
