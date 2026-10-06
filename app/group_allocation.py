"""团体申请容量分配服务。

核心不变量（人数守恒）：
  1. 待确认分配人数 + 已确认占用人数 + 候补人数 == 申请当前总人数 total_count
  2. total_count + 已释放人数 released_count == 原始报名人数 original_total_count

所有公开操作都在单个数据库事务内完成（末尾统一 commit，异常回滚），
候补递补（减少候补人数 + 生成待确认分配 + 记录事件）是原子操作。
"""
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional, Tuple
from datetime import datetime, timedelta

from app.models import (
    Session as SessionModel, School, Theme,
    GroupApplication, GroupAllocation, WaitlistEntry, AllocationEvent,
    GroupApplicationStatus, AllocationStatus, WaitlistStatus, AllocationEventType,
    SessionStatus, AudienceType
)
from app import schemas
from app.config import settings

# 场地未设置容量时视为不限容量
UNLIMITED_CAPACITY = 10 ** 9

ORDER_BASIS = "按候补序号升序（先进入候补者优先递补）；递补时按当前资格重新校验（主题、时间偏好、最小成团人数、同行约束与剩余容量）"


def _now() -> datetime:
    return datetime.now()


def _get_application(db: Session, application_id: int) -> Optional[GroupApplication]:
    return db.query(GroupApplication).options(
        joinedload(GroupApplication.school),
        joinedload(GroupApplication.theme)
    ).filter(GroupApplication.id == application_id).first()


def get_application(db: Session, application_id: int) -> Optional[GroupApplication]:
    return _get_application(db, application_id)


def _session_capacity(session: SessionModel) -> int:
    if session.venue and session.venue.capacity:
        return session.venue.capacity
    return UNLIMITED_CAPACITY


def _session_proposed_hold(db: Session, session_id: int) -> int:
    """场次上处于待确认状态的软占用人数（学校确认后才转为正式占用）。"""
    return db.query(func.coalesce(func.sum(GroupAllocation.allocated_count), 0)).filter(
        GroupAllocation.session_id == session_id,
        GroupAllocation.status == AllocationStatus.PROPOSED
    ).scalar() or 0


def _session_remaining(db: Session, session: SessionModel) -> int:
    """场次剩余可分配容量 = 场地容量 - 正式占用(audience_count) - 待确认软占用。"""
    return max(0, _session_capacity(session) - (session.audience_count or 0)
               - _session_proposed_hold(db, session.id))


def _fit_amount(demand: int, remaining: int, min_group_size: int, co_travel_size: int) -> int:
    """按同行约束取整后可安置的人数；不足最小成团人数返回0。"""
    fit = min(demand, remaining)
    fit -= fit % co_travel_size
    return fit if fit >= min_group_size else 0


def _next_queue_seq(db: Session) -> int:
    return (db.query(func.max(WaitlistEntry.queue_seq)).scalar() or 0) + 1


def _next_split_seq(db: Session, application_id: int) -> int:
    return (db.query(func.max(GroupAllocation.split_seq)).filter(
        GroupAllocation.application_id == application_id
    ).scalar() or 0) + 1


def _log_event(db: Session, application_id: int, event_type: AllocationEventType,
               quantity: int, reason: str, operator: str = "系统",
               allocation_id: Optional[int] = None,
               waitlist_entry_id: Optional[int] = None,
               session_id: Optional[int] = None) -> AllocationEvent:
    event = AllocationEvent(
        application_id=application_id,
        allocation_id=allocation_id,
        waitlist_entry_id=waitlist_entry_id,
        session_id=session_id,
        event_type=event_type,
        quantity=quantity,
        reason=reason,
        operator=operator
    )
    db.add(event)
    db.flush()
    return event


def _add_waitlist(db: Session, application: GroupApplication, count: int,
                  reason: str, session_id: Optional[int] = None) -> WaitlistEntry:
    entry = WaitlistEntry(
        application_id=application.id,
        session_id=session_id,
        queue_seq=_next_queue_seq(db),
        requested_count=count,
        status=WaitlistStatus.WAITING,
        reason=reason
    )
    db.add(entry)
    db.flush()
    _log_event(db, application.id, AllocationEventType.WAITLIST, count, reason,
               waitlist_entry_id=entry.id, session_id=session_id)
    return entry


def _application_counts(db: Session, application_id: int) -> Tuple[int, int, int]:
    """返回 (待确认人数, 已确认人数, 候补人数)。"""
    proposed = db.query(func.coalesce(func.sum(GroupAllocation.allocated_count), 0)).filter(
        GroupAllocation.application_id == application_id,
        GroupAllocation.status == AllocationStatus.PROPOSED
    ).scalar() or 0
    confirmed = db.query(func.coalesce(func.sum(GroupAllocation.confirmed_count), 0)).filter(
        GroupAllocation.application_id == application_id,
        GroupAllocation.status == AllocationStatus.CONFIRMED
    ).scalar() or 0
    waiting = db.query(func.coalesce(func.sum(WaitlistEntry.requested_count), 0)).filter(
        WaitlistEntry.application_id == application_id,
        WaitlistEntry.status == WaitlistStatus.WAITING
    ).scalar() or 0
    return proposed, confirmed, waiting


def _refresh_status(db: Session, application: GroupApplication):
    db.flush()
    if application.status == GroupApplicationStatus.CLOSED:
        return
    proposed, confirmed, waiting = _application_counts(db, application.id)
    if application.total_count == 0:
        application.status = GroupApplicationStatus.CLOSED
    elif proposed == 0 and waiting == 0:
        application.status = GroupApplicationStatus.CONFIRMED
    elif confirmed > 0:
        application.status = GroupApplicationStatus.PARTIALLY_CONFIRMED
    else:
        application.status = GroupApplicationStatus.PENDING
    db.flush()


def _check_eligibility(application: GroupApplication, session: SessionModel,
                       now: Optional[datetime] = None) -> Tuple[bool, str]:
    """递补时的当前资格校验，返回(是否合格, 原因说明)。"""
    now = now or _now()
    if application.status not in (GroupApplicationStatus.PENDING,
                                  GroupApplicationStatus.PARTIALLY_CONFIRMED):
        return False, f"申请状态为{application.status.value}，不再参与递补"
    if session.status not in (SessionStatus.DRAFT, SessionStatus.SCHEDULED):
        return False, f"场次状态为{session.status.value}，不可承接"
    if session.start_time <= now:
        return False, "场次已开始或已结束"
    if session.theme_id != application.theme_id:
        return False, "场次主题与申请主题不匹配"
    if session.audience_type != AudienceType.SCHOOL:
        return False, "场次不是学校团体场"
    if session.school_id is not None and session.school_id != application.school_id:
        return False, "场次已被其他学校专场预约"
    if application.preferred_start and session.start_time < application.preferred_start:
        return False, f"场次开始时间早于时间偏好窗口（{application.preferred_start}）"
    if application.preferred_end and session.start_time > application.preferred_end:
        return False, f"场次开始时间晚于时间偏好窗口（{application.preferred_end}）"
    return True, "符合承接资格"


def _candidate_sessions(db: Session, application: GroupApplication) -> List[SessionModel]:
    """符合基本条件的候选场次，按开始时间升序（时间偏好窗口在资格校验中过滤）。"""
    sessions = db.query(SessionModel).options(
        joinedload(SessionModel.venue)
    ).filter(
        SessionModel.theme_id == application.theme_id,
        SessionModel.status.in_([SessionStatus.DRAFT, SessionStatus.SCHEDULED]),
        SessionModel.audience_type == AudienceType.SCHOOL,
        SessionModel.start_time > _now()
    ).order_by(SessionModel.start_time, SessionModel.id).all()
    result = []
    for session in sessions:
        eligible, _ = _check_eligibility(application, session)
        if eligible:
            result.append(session)
    return result


def create_application_and_split(db: Session, app_in: schemas.GroupApplicationCreate
                                 ) -> Tuple[Optional[GroupApplication], List[str]]:
    """创建团体申请并按时间偏好/最小成团人数/同行约束自动拆分到多个场次。

    拆分结果仅为待确认方案（软占用），学校确认后才形成正式占用；
    未满足部分进入有顺序依据的候补队列。
    """
    errors = []
    school = db.query(School).filter(School.id == app_in.school_id).first()
    if not school:
        errors.append("学校不存在")
    theme = db.query(Theme).filter(Theme.id == app_in.theme_id).first()
    if not theme:
        errors.append("主题不存在")
    if app_in.min_group_size > app_in.total_count:
        errors.append(f"最小成团人数({app_in.min_group_size})不能超过报名总人数({app_in.total_count})")
    if app_in.preferred_start and app_in.preferred_end \
            and app_in.preferred_start >= app_in.preferred_end:
        errors.append("时间偏好窗口的开始时间必须早于结束时间")
    if errors:
        return None, errors

    confirm_deadline = app_in.confirm_deadline or \
        _now() + timedelta(hours=settings.GROUP_CONFIRM_TIMEOUT_HOURS)

    application = GroupApplication(
        school_id=app_in.school_id,
        theme_id=app_in.theme_id,
        original_total_count=app_in.total_count,
        total_count=app_in.total_count,
        released_count=0,
        min_group_size=app_in.min_group_size,
        co_travel_size=app_in.co_travel_size,
        preferred_start=app_in.preferred_start,
        preferred_end=app_in.preferred_end,
        confirm_deadline=confirm_deadline,
        status=GroupApplicationStatus.PENDING,
        note=app_in.note
    )
    db.add(application)
    db.flush()

    remaining = app_in.total_count
    split_seq = 0
    skip_notes = []
    for session in _candidate_sessions(db, application):
        if remaining == 0:
            break
        remaining_cap = _session_remaining(db, session)
        fit = _fit_amount(remaining, remaining_cap,
                          application.min_group_size, application.co_travel_size)
        if fit <= 0:
            skip_notes.append(
                f"场次「{session.title}」剩余容量{remaining_cap}人，"
                f"按同行单元{application.co_travel_size}人取整后不足最小成团"
                f"{application.min_group_size}人，未予拆分"
            )
            continue
        split_seq += 1
        reason = (f"拆分第{split_seq}顺位：场次「{session.title}」"
                  f"（{session.start_time}）在时间偏好窗口内，剩余容量{remaining_cap}人，"
                  f"按同行单元{application.co_travel_size}人取整，可安置{fit}人"
                  f"（满足最小成团{application.min_group_size}人）")
        allocation = GroupAllocation(
            application_id=application.id,
            session_id=session.id,
            split_seq=split_seq,
            allocated_count=fit,
            confirmed_count=0,
            status=AllocationStatus.PROPOSED,
            reason=reason,
            confirm_deadline=confirm_deadline
        )
        db.add(allocation)
        db.flush()
        _log_event(db, application.id, AllocationEventType.SPLIT, fit, reason,
                   allocation_id=allocation.id, session_id=session.id)
        remaining -= fit

    if remaining > 0:
        reason = f"总报名{app_in.total_count}人，候选场次共安置{app_in.total_count - remaining}人，" \
                 f"剩余{remaining}人暂无足够容量，进入候补队列"
        if skip_notes:
            reason += "；" + "；".join(skip_notes)
        _add_waitlist(db, application, remaining, reason)

    _refresh_status(db, application)
    db.commit()
    db.refresh(application)
    return application, []


def confirm_application(db: Session, application_id: int,
                        confirm_in: schemas.GroupApplicationConfirmRequest
                        ) -> Tuple[Optional[GroupApplication], List[str]]:
    """学校确认拆分方案：确认部分转为正式占用（计入场次audience_count），
    未接受部分回到候补队列，全程保持人数守恒。"""
    application = _get_application(db, application_id)
    if not application:
        return None, ["团体申请不存在"]
    if application.status == GroupApplicationStatus.CLOSED:
        return None, ["申请已关闭，无法确认"]

    _expire_overdue(db)

    errors = []
    allocations = {}
    for item in confirm_in.items:
        allocation = db.query(GroupAllocation).filter(
            GroupAllocation.id == item.allocation_id,
            GroupAllocation.application_id == application_id
        ).first()
        if not allocation:
            errors.append(f"分配记录{item.allocation_id}不存在或不属于该申请")
            continue
        if allocation.status != AllocationStatus.PROPOSED:
            errors.append(f"分配记录{item.allocation_id}当前状态为{allocation.status.value}，无法确认")
            continue
        if item.accepted_count > allocation.allocated_count:
            errors.append(f"分配记录{item.allocation_id}接受人数({item.accepted_count})"
                          f"超过分配人数({allocation.allocated_count})")
            continue
        session = db.query(SessionModel).filter(SessionModel.id == allocation.session_id).first()
        if session and session.status == SessionStatus.CANCELLED:
            errors.append(f"分配记录{item.allocation_id}所在场次已取消，无法确认")
            continue
        allocations[item.allocation_id] = (allocation, session, item.accepted_count)
    if errors:
        db.rollback()
        return None, errors

    affected_session_ids = set()
    for allocation_id, (allocation, session, accepted) in allocations.items():
        remainder = allocation.allocated_count - accepted
        affected_session_ids.add(allocation.session_id)
        if accepted == 0:
            allocation.status = AllocationStatus.REJECTED
            reason = (f"学校未接受场次「{session.title if session else allocation.session_id}」"
                      f"分配的{allocation.allocated_count}人，{remainder}人回到候补队列")
            _log_event(db, application_id, AllocationEventType.PARTIAL_CONFIRM, 0,
                       reason, operator=confirm_in.operator,
                       allocation_id=allocation.id, session_id=allocation.session_id)
            _add_waitlist(db, application, remainder, reason)
        else:
            allocation.status = AllocationStatus.CONFIRMED
            allocation.confirmed_count = accepted
            allocation.confirmed_at = _now()
            if session:
                session.audience_count = (session.audience_count or 0) + accepted
            if remainder > 0:
                reason = (f"学校部分接受：场次「{session.title if session else allocation.session_id}」"
                          f"分配{allocation.allocated_count}人，确认{accepted}人形成正式占用，"
                          f"剩余{remainder}人回到候补队列")
                _log_event(db, application_id, AllocationEventType.PARTIAL_CONFIRM, accepted,
                           reason, operator=confirm_in.operator,
                           allocation_id=allocation.id, session_id=allocation.session_id)
                _add_waitlist(db, application, remainder, reason)
            else:
                reason = (f"学校确认：场次「{session.title if session else allocation.session_id}」"
                          f"{accepted}人形成正式占用")
                _log_event(db, application_id, AllocationEventType.CONFIRM, accepted,
                           reason, operator=confirm_in.operator,
                           allocation_id=allocation.id, session_id=allocation.session_id)
        db.flush()

    _refresh_status(db, application)
    for session_id in affected_session_ids:
        _process_session_waitlist(db, session_id)
    db.commit()
    db.refresh(application)
    return application, []


def reduce_application(db: Session, application_id: int,
                       reduce_in: schemas.GroupApplicationReduceRequest
                       ) -> Tuple[Optional[GroupApplication], List[str]]:
    """学校缩减总人数：依次从候补、待确认、已确认中扣减，释放的正式占用
    会触发候补递补，保持人数守恒。"""
    application = _get_application(db, application_id)
    if not application:
        return None, ["团体申请不存在"]
    if application.status == GroupApplicationStatus.CLOSED:
        return None, ["申请已关闭，无法缩减"]
    if reduce_in.new_total_count >= application.total_count:
        return None, [f"新总人数({reduce_in.new_total_count})必须小于当前总人数({application.total_count})"]

    to_reduce = application.total_count - reduce_in.new_total_count
    reason_prefix = reduce_in.reason or "学校缩减报名人数"
    affected_session_ids = set()

    # 1. 先扣候补（最新进入的优先扣减，保留先排队者的优先权）
    entries = db.query(WaitlistEntry).filter(
        WaitlistEntry.application_id == application_id,
        WaitlistEntry.status == WaitlistStatus.WAITING
    ).order_by(WaitlistEntry.queue_seq.desc()).all()
    for entry in entries:
        if to_reduce == 0:
            break
        cut = min(entry.requested_count, to_reduce)
        entry.requested_count -= cut
        to_reduce -= cut
        if entry.requested_count == 0:
            entry.status = WaitlistStatus.CANCELLED
        reason = f"{reason_prefix}：从候补队列（序号{entry.queue_seq}）扣减{cut}人"
        _log_event(db, application_id, AllocationEventType.REDUCE, cut, reason,
                   operator=reduce_in.operator, waitlist_entry_id=entry.id,
                   session_id=entry.session_id)

    # 2. 再扣待确认分配（释放软占用）
    if to_reduce > 0:
        proposed = db.query(GroupAllocation).filter(
            GroupAllocation.application_id == application_id,
            GroupAllocation.status == AllocationStatus.PROPOSED
        ).order_by(GroupAllocation.split_seq.desc(), GroupAllocation.id.desc()).all()
        for allocation in proposed:
            if to_reduce == 0:
                break
            cut = min(allocation.allocated_count, to_reduce)
            allocation.allocated_count -= cut
            to_reduce -= cut
            affected_session_ids.add(allocation.session_id)
            if allocation.allocated_count == 0:
                allocation.status = AllocationStatus.CANCELLED
            reason = f"{reason_prefix}：待确认分配（第{allocation.split_seq}顺位）扣减{cut}人，释放场次软占用"
            _log_event(db, application_id, AllocationEventType.REDUCE, cut, reason,
                       operator=reduce_in.operator, allocation_id=allocation.id,
                       session_id=allocation.session_id)

    # 3. 最后扣已确认占用（释放正式占用，触发候补递补）
    if to_reduce > 0:
        confirmed = db.query(GroupAllocation).filter(
            GroupAllocation.application_id == application_id,
            GroupAllocation.status == AllocationStatus.CONFIRMED
        ).order_by(GroupAllocation.id.desc()).all()
        for allocation in confirmed:
            if to_reduce == 0:
                break
            cut = min(allocation.confirmed_count, to_reduce)
            allocation.confirmed_count -= cut
            to_reduce -= cut
            affected_session_ids.add(allocation.session_id)
            session = db.query(SessionModel).filter(SessionModel.id == allocation.session_id).first()
            if session:
                session.audience_count = max(0, (session.audience_count or 0) - cut)
            if allocation.confirmed_count == 0:
                allocation.status = AllocationStatus.CANCELLED
            reason = f"{reason_prefix}：已确认占用扣减{cut}人，释放场次正式占用"
            _log_event(db, application_id, AllocationEventType.REDUCE, cut, reason,
                       operator=reduce_in.operator, allocation_id=allocation.id,
                       session_id=allocation.session_id)

    application.released_count = (application.released_count or 0) + \
        (application.total_count - reduce_in.new_total_count)
    application.total_count = reduce_in.new_total_count
    db.flush()

    _refresh_status(db, application)
    for session_id in affected_session_ids:
        _process_session_waitlist(db, session_id)
    db.commit()
    db.refresh(application)
    return application, []


def cancel_application(db: Session, application_id: int,
                       cancel_in: schemas.GroupApplicationCancelRequest
                       ) -> Tuple[Optional[GroupApplication], List[str]]:
    """取消整个团体申请：候补、待确认、已确认全部释放，人数计入已释放。"""
    application = _get_application(db, application_id)
    if not application:
        return None, ["团体申请不存在"]
    if application.status == GroupApplicationStatus.CLOSED:
        return None, ["申请已关闭"]

    reason_prefix = cancel_in.reason or "学校取消团体申请"
    affected_session_ids = set()
    released = 0

    entries = db.query(WaitlistEntry).filter(
        WaitlistEntry.application_id == application_id,
        WaitlistEntry.status == WaitlistStatus.WAITING
    ).all()
    for entry in entries:
        entry.status = WaitlistStatus.CANCELLED
        released += entry.requested_count
        _log_event(db, application_id, AllocationEventType.CANCEL, entry.requested_count,
                   f"{reason_prefix}：候补{entry.requested_count}人退出队列",
                   operator=cancel_in.operator, waitlist_entry_id=entry.id,
                   session_id=entry.session_id)

    allocations = db.query(GroupAllocation).filter(
        GroupAllocation.application_id == application_id,
        GroupAllocation.status.in_([AllocationStatus.PROPOSED, AllocationStatus.CONFIRMED])
    ).all()
    for allocation in allocations:
        affected_session_ids.add(allocation.session_id)
        if allocation.status == AllocationStatus.PROPOSED:
            released += allocation.allocated_count
            _log_event(db, application_id, AllocationEventType.CANCEL, allocation.allocated_count,
                       f"{reason_prefix}：待确认分配{allocation.allocated_count}人释放软占用",
                       operator=cancel_in.operator, allocation_id=allocation.id,
                       session_id=allocation.session_id)
        else:
            released += allocation.confirmed_count
            session = db.query(SessionModel).filter(SessionModel.id == allocation.session_id).first()
            if session:
                session.audience_count = max(0, (session.audience_count or 0) - allocation.confirmed_count)
            _log_event(db, application_id, AllocationEventType.CANCEL, allocation.confirmed_count,
                       f"{reason_prefix}：已确认占用{allocation.confirmed_count}人释放正式占用",
                       operator=cancel_in.operator, allocation_id=allocation.id,
                       session_id=allocation.session_id)
        allocation.status = AllocationStatus.CANCELLED

    application.released_count = (application.released_count or 0) + released
    application.total_count = 0
    application.status = GroupApplicationStatus.CLOSED
    db.flush()

    for session_id in affected_session_ids:
        _process_session_waitlist(db, session_id)
    db.commit()
    db.refresh(application)
    return application, []


def _expire_overdue(db: Session, now: Optional[datetime] = None) -> List[int]:
    """超时未确认的待确认分配失效：软占用释放，人数回到候补队列队尾。"""
    now = now or _now()
    overdue = db.query(GroupAllocation).filter(
        GroupAllocation.status == AllocationStatus.PROPOSED,
        GroupAllocation.confirm_deadline.isnot(None),
        GroupAllocation.confirm_deadline < now
    ).all()

    expired_ids = []
    affected_session_ids = set()
    for allocation in overdue:
        application = db.query(GroupApplication).filter(
            GroupApplication.id == allocation.application_id).first()
        if not application or application.status == GroupApplicationStatus.CLOSED:
            continue
        allocation.status = AllocationStatus.EXPIRED
        expired_ids.append(allocation.id)
        affected_session_ids.add(allocation.session_id)
        reason = (f"超过确认截止时间{allocation.confirm_deadline}学校未确认，"
                  f"分配{allocation.allocated_count}人失效，释放场次软占用，人数回到候补队列队尾")
        _log_event(db, application.id, AllocationEventType.EXPIRE, allocation.allocated_count,
                   reason, allocation_id=allocation.id, session_id=allocation.session_id)
        _add_waitlist(db, application, allocation.allocated_count, reason)
        _refresh_status(db, application)

    for session_id in affected_session_ids:
        _process_session_waitlist(db, session_id)
    return expired_ids


def expire_overdue(db: Session) -> schemas.ExpireSweepResult:
    """公开的超时清理入口（单事务）。"""
    expired_ids = _expire_overdue(db)
    db.commit()
    return schemas.ExpireSweepResult(
        expired_count=len(expired_ids),
        expired_allocation_ids=expired_ids,
        message=f"共处理{len(expired_ids)}条超时未确认的分配" if expired_ids else "没有超时未确认的分配"
    )


def release_session_allocations(db: Session, session_id: int, operator: str = "系统",
                                reason: str = "场次取消", commit: bool = True) -> int:
    """场次取消（跨场取消）时释放其团体占用：待确认与已确认人数回到各自
    申请的候补队列，随后全局扫描候补，按当前资格原子递补到其他场次。"""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return 0

    allocations = db.query(GroupAllocation).filter(
        GroupAllocation.session_id == session_id,
        GroupAllocation.status.in_([AllocationStatus.PROPOSED, AllocationStatus.CONFIRMED])
    ).all()
    if not allocations:
        return 0

    released_people = 0
    application_ids = set()
    for allocation in allocations:
        application = db.query(GroupApplication).filter(
            GroupApplication.id == allocation.application_id).first()
        if not application or application.status == GroupApplicationStatus.CLOSED:
            continue
        application_ids.add(application.id)
        if allocation.status == AllocationStatus.PROPOSED:
            count = allocation.allocated_count
            detail = f"场次「{session.title}」{reason}，待确认分配{count}人释放软占用，回到候补队列"
        else:
            count = allocation.confirmed_count
            session.audience_count = max(0, (session.audience_count or 0) - count)
            detail = f"场次「{session.title}」{reason}，已确认占用{count}人释放正式占用，回到候补队列"
        allocation.status = AllocationStatus.CANCELLED
        released_people += count
        _log_event(db, application.id, AllocationEventType.SESSION_CANCEL, count, detail,
                   operator=operator, allocation_id=allocation.id, session_id=session_id)
        _add_waitlist(db, application, count, detail)
        _refresh_status(db, application)

    db.flush()
    _process_all_waitlists(db)
    for app_id in application_ids:
        application = db.query(GroupApplication).filter(GroupApplication.id == app_id).first()
        if application:
            _refresh_status(db, application)
    if commit:
        db.commit()
    return released_people


def _process_session_waitlist(db: Session, session_id: int) -> List[GroupAllocation]:
    """单场候补递补：按候补序号顺序，按当前资格校验后原子递补。

    每次递补在同一事务内完成：候补人数扣减 + 生成待确认分配 + 记录事件。
    """
    session = db.query(SessionModel).options(joinedload(SessionModel.venue)).filter(
        SessionModel.id == session_id).first()
    if not session or session.status not in (SessionStatus.DRAFT, SessionStatus.SCHEDULED):
        return []
    if session.start_time <= _now():
        return []

    promotions = []
    entries = db.query(WaitlistEntry).filter(
        WaitlistEntry.status == WaitlistStatus.WAITING,
        (WaitlistEntry.session_id.is_(None)) | (WaitlistEntry.session_id == session_id)
    ).order_by(WaitlistEntry.queue_seq).all()

    for entry in entries:
        application = db.query(GroupApplication).filter(
            GroupApplication.id == entry.application_id).first()
        if not application:
            continue
        eligible, _ = _check_eligibility(application, session)
        if not eligible:
            continue
        remaining_cap = _session_remaining(db, session)
        fit = _fit_amount(entry.requested_count, remaining_cap,
                          application.min_group_size, application.co_travel_size)
        if fit <= 0:
            continue

        split_seq = _next_split_seq(db, application.id)
        confirm_deadline = _now() + timedelta(hours=settings.GROUP_CONFIRM_TIMEOUT_HOURS)
        reason = (f"候补递补：场次「{session.title}」释放容量，当前剩余容量{remaining_cap}人，"
                  f"按候补顺序（序号{entry.queue_seq}，{entry.created_at}进入候补）"
                  f"递补{fit}人（满足最小成团{application.min_group_size}人、"
                  f"同行单元{application.co_travel_size}人约束），"
                  f"待学校在{confirm_deadline}前确认后形成正式占用")
        allocation = GroupAllocation(
            application_id=application.id,
            session_id=session.id,
            split_seq=split_seq,
            allocated_count=fit,
            confirmed_count=0,
            status=AllocationStatus.PROPOSED,
            reason=reason,
            confirm_deadline=confirm_deadline
        )
        db.add(allocation)
        db.flush()

        entry.requested_count -= fit
        if entry.requested_count == 0:
            entry.status = WaitlistStatus.PROMOTED
        _log_event(db, application.id, AllocationEventType.PROMOTE, fit, reason,
                   allocation_id=allocation.id, waitlist_entry_id=entry.id,
                   session_id=session.id)
        _refresh_status(db, application)
        promotions.append(allocation)
    return promotions


def _process_all_waitlists(db: Session) -> List[GroupAllocation]:
    """全局候补扫描：对所有可承接场次按开始时间顺序执行递补。"""
    sessions = db.query(SessionModel).options(joinedload(SessionModel.venue)).filter(
        SessionModel.status.in_([SessionStatus.DRAFT, SessionStatus.SCHEDULED]),
        SessionModel.audience_type == AudienceType.SCHOOL,
        SessionModel.start_time > _now()
    ).order_by(SessionModel.start_time, SessionModel.id).all()
    promotions = []
    for session in sessions:
        promotions.extend(_process_session_waitlist(db, session.id))
    return promotions


def process_waitlist(db: Session, session_id: Optional[int] = None) -> int:
    """公开的候补处理入口（单事务），返回递补人数总和。"""
    if session_id is not None:
        promotions = _process_session_waitlist(db, session_id)
    else:
        promotions = _process_all_waitlists(db)
    db.commit()
    return sum(p.allocated_count for p in promotions)


def get_conservation(db: Session, application_id: int) -> Optional[schemas.ConservationSummary]:
    application = db.query(GroupApplication).filter(GroupApplication.id == application_id).first()
    if not application:
        return None
    proposed, confirmed, waiting = _application_counts(db, application_id)
    released = application.released_count or 0
    conserved = (proposed + confirmed + waiting == application.total_count) and \
                (application.total_count + released == application.original_total_count)
    message = (f"待确认{proposed}人 + 已确认{confirmed}人 + 候补{waiting}人 = "
               f"{proposed + confirmed + waiting}人（当前总人数{application.total_count}人）；"
               f"当前总人数{application.total_count}人 + 已释放{released}人 = "
               f"{application.total_count + released}人（原始报名{application.original_total_count}人）")
    message += "；人数守恒" if conserved else "；人数不守恒，请核查"
    return schemas.ConservationSummary(
        application_id=application_id,
        original_total_count=application.original_total_count,
        total_count=application.total_count,
        released_count=released,
        proposed_count=proposed,
        confirmed_count=confirmed,
        waiting_count=waiting,
        conserved=conserved,
        message=message
    )


def _convert_allocation(allocation: GroupAllocation) -> schemas.GroupAllocationItem:
    session = allocation.session
    return schemas.GroupAllocationItem(
        id=allocation.id,
        session_id=allocation.session_id,
        session_title=session.title if session else "",
        session_start_time=session.start_time if session else None,
        split_seq=allocation.split_seq,
        allocated_count=allocation.allocated_count,
        confirmed_count=allocation.confirmed_count,
        status=allocation.status,
        reason=allocation.reason,
        confirm_deadline=allocation.confirm_deadline,
        created_at=allocation.created_at,
        confirmed_at=allocation.confirmed_at
    )


def _convert_waitlist_entry(entry: WaitlistEntry) -> schemas.WaitlistEntryItem:
    return schemas.WaitlistEntryItem(
        id=entry.id,
        session_id=entry.session_id,
        queue_seq=entry.queue_seq,
        requested_count=entry.requested_count,
        status=entry.status,
        reason=entry.reason,
        created_at=entry.created_at
    )


def _convert_event(event: AllocationEvent) -> schemas.AllocationEventItem:
    return schemas.AllocationEventItem(
        id=event.id,
        event_type=event.event_type,
        quantity=event.quantity,
        reason=event.reason,
        operator=event.operator,
        session_id=event.session_id,
        allocation_id=event.allocation_id,
        waitlist_entry_id=event.waitlist_entry_id,
        created_at=event.created_at
    )


def _convert_application_item(db: Session, application: GroupApplication
                              ) -> schemas.GroupApplicationItem:
    proposed, confirmed, waiting = _application_counts(db, application.id)
    return schemas.GroupApplicationItem(
        id=application.id,
        school_id=application.school_id,
        school_name=application.school.name if application.school else "",
        theme_id=application.theme_id,
        theme_name=application.theme.name if application.theme else "",
        total_count=application.total_count,
        original_total_count=application.original_total_count,
        released_count=application.released_count or 0,
        min_group_size=application.min_group_size,
        co_travel_size=application.co_travel_size,
        preferred_start=application.preferred_start,
        preferred_end=application.preferred_end,
        confirm_deadline=application.confirm_deadline,
        status=application.status,
        note=application.note,
        proposed_count=proposed,
        confirmed_count=confirmed,
        waiting_count=waiting,
        created_at=application.created_at
    )


def build_detail(db: Session, application: GroupApplication) -> schemas.GroupApplicationDetail:
    allocations = db.query(GroupAllocation).options(joinedload(GroupAllocation.session)).filter(
        GroupAllocation.application_id == application.id
    ).order_by(GroupAllocation.split_seq, GroupAllocation.id).all()
    entries = db.query(WaitlistEntry).filter(
        WaitlistEntry.application_id == application.id
    ).order_by(WaitlistEntry.queue_seq).all()
    events = db.query(AllocationEvent).filter(
        AllocationEvent.application_id == application.id
    ).order_by(AllocationEvent.created_at, AllocationEvent.id).all()

    item = _convert_application_item(db, application)
    return schemas.GroupApplicationDetail(
        **item.model_dump(),
        allocations=[_convert_allocation(a) for a in allocations],
        waitlist_entries=[_convert_waitlist_entry(e) for e in entries],
        events=[_convert_event(e) for e in events],
        conservation=get_conservation(db, application.id)
    )


def get_application_list(db: Session, school_id: Optional[int] = None,
                         status: Optional[GroupApplicationStatus] = None,
                         skip: int = 0, limit: int = 100) -> List[schemas.GroupApplicationItem]:
    query = db.query(GroupApplication).options(
        joinedload(GroupApplication.school),
        joinedload(GroupApplication.theme)
    )
    if school_id:
        query = query.filter(GroupApplication.school_id == school_id)
    if status:
        query = query.filter(GroupApplication.status == status)
    applications = query.order_by(GroupApplication.created_at.desc()).offset(skip).limit(limit).all()
    return [_convert_application_item(db, a) for a in applications]


def get_session_waitlist(db: Session, session_id: int) -> Optional[schemas.SessionWaitlistView]:
    """场次视角的候补队列：顺序依据 + 每个候补的当前资格说明。"""
    session = db.query(SessionModel).options(joinedload(SessionModel.venue)).filter(
        SessionModel.id == session_id).first()
    if not session:
        return None

    entries = db.query(WaitlistEntry).filter(
        WaitlistEntry.status == WaitlistStatus.WAITING,
        (WaitlistEntry.session_id.is_(None)) | (WaitlistEntry.session_id == session_id)
    ).order_by(WaitlistEntry.queue_seq).all()

    remaining_cap = _session_remaining(db, session)
    items = []
    position = 0
    for entry in entries:
        application = db.query(GroupApplication).options(
            joinedload(GroupApplication.school)
        ).filter(GroupApplication.id == entry.application_id).first()
        if not application:
            continue
        position += 1
        eligible, reason = _check_eligibility(application, session)
        if eligible:
            fit = _fit_amount(entry.requested_count, remaining_cap,
                              application.min_group_size, application.co_travel_size)
            if fit > 0:
                reason = f"符合当前资格，可递补{fit}人（当前剩余容量{remaining_cap}人）"
            else:
                eligible = False
                reason = (f"当前剩余容量{remaining_cap}人，按同行单元"
                          f"{application.co_travel_size}人取整后不足最小成团"
                          f"{application.min_group_size}人，暂不可递补")
        items.append(schemas.SessionWaitlistItem(
            waitlist_entry_id=entry.id,
            application_id=application.id,
            school_name=application.school.name if application.school else "",
            queue_position=position,
            queue_seq=entry.queue_seq,
            requested_count=entry.requested_count,
            eligible=eligible,
            eligibility_reason=reason,
            created_at=entry.created_at
        ))

    return schemas.SessionWaitlistView(
        session_id=session.id,
        session_title=session.title,
        remaining_capacity=remaining_cap,
        order_basis=ORDER_BASIS,
        entries=items
    )


def get_event_list(db: Session, application_id: int) -> List[schemas.AllocationEventItem]:
    events = db.query(AllocationEvent).filter(
        AllocationEvent.application_id == application_id
    ).order_by(AllocationEvent.created_at, AllocationEvent.id).all()
    return [_convert_event(e) for e in events]
