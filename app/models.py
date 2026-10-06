from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Enum, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


class StaffType(str, enum.Enum):
    GUIDE = "讲解员"
    LECTURER = "讲师"


class SessionType(str, enum.Enum):
    RESEARCH = "研学实践"
    LECTURE = "专题讲座"


class SessionStatus(str, enum.Enum):
    DRAFT = "草稿"
    SCHEDULED = "已排定"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class AssignmentRole(str, enum.Enum):
    GUIDE = "讲解员"
    LECTURER = "主讲"


class AudienceType(str, enum.Enum):
    SCHOOL = "学校"
    PUBLIC = "公众"


class WarningType(str, enum.Enum):
    STAFF_SHORTAGE = "人员不足"
    OVERLOAD = "人员过载"


class ChangeType(str, enum.Enum):
    TIME = "时间变更"
    COUNT = "人数变更"
    BOTH = "时间和人数变更"
    OTHER = "其他变更"


class ChangeStatus(str, enum.Enum):
    PENDING = "待审核"
    APPROVED = "已通过"
    REJECTED = "已拒绝"
    EXECUTED = "已执行"
    CANCELLED = "已取消"


class ConflictType(str, enum.Enum):
    TIME_OVERLAP = "时间冲突"
    STAFF_SHORTAGE = "人员不足"
    QUALIFICATION = "资质不符"


class RescheduleStatus(str, enum.Enum):
    PENDING = "待处理"
    IN_PROGRESS = "处理中"
    RESOLVED = "已解决"
    UNRESOLVED = "未解决"


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    staff_type = Column(Enum(StaffType), nullable=False)
    phone = Column(String(20))
    email = Column(String(100))
    total_service_hours = Column(Float, default=0.0)
    star_rating = Column(Float, default=3.0)
    review_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    total_points = Column(Integer, default=0)
    current_level = Column(Integer, default=1)
    current_badge_id = Column(Integer, ForeignKey("level_badges.id"))
    is_excellent = Column(Boolean, default=False)
    excellent_until = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    themes = relationship("StaffTheme", back_populates="staff", cascade="all, delete-orphan")
    venues = relationship("StaffVenue", back_populates="staff", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="staff")
    point_records = relationship("PointRecord", back_populates="staff", cascade="all, delete-orphan")
    monthly_rankings = relationship("MonthlyRanking", back_populates="staff", cascade="all, delete-orphan")
    badges = relationship("StaffBadge", back_populates="staff", cascade="all, delete-orphan")
    current_badge = relationship("LevelBadge", foreign_keys=[current_badge_id])


class Theme(Base):
    __tablename__ = "themes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    category = Column(String(50))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff_themes = relationship("StaffTheme", back_populates="theme")
    sessions = relationship("Session", back_populates="theme")
    warnings = relationship("Warning", back_populates="theme")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    venue_type = Column(String(50), nullable=False)
    capacity = Column(Integer)
    location = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff_venues = relationship("StaffVenue", back_populates="venue")
    sessions = relationship("Session", back_populates="venue")


class StaffTheme(Base):
    __tablename__ = "staff_themes"

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    theme_id = Column(Integer, ForeignKey("themes.id"), nullable=False)
    proficiency_level = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="themes")
    theme = relationship("Theme", back_populates="staff_themes")


class StaffVenue(Base):
    __tablename__ = "staff_venues"

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    is_certified = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="venues")
    venue = relationship("Venue", back_populates="staff_venues")


class School(Base):
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True)
    contact_person = Column(String(50))
    phone = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sessions = relationship("Session", back_populates="school")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    theme_id = Column(Integer, ForeignKey("themes.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    session_type = Column(Enum(SessionType), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    audience_type = Column(Enum(AudienceType), nullable=False)
    audience_count = Column(Integer, default=0)
    school_id = Column(Integer, ForeignKey("schools.id"))
    guides_needed = Column(Integer, default=0)
    needs_lecturer = Column(Boolean, default=False)
    status = Column(Enum(SessionStatus), default=SessionStatus.DRAFT)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    theme = relationship("Theme", back_populates="sessions")
    venue = relationship("Venue", back_populates="sessions")
    school = relationship("School", back_populates="sessions")
    assignments = relationship("Assignment", back_populates="session", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="session", cascade="all, delete-orphan")
    change_requests = relationship("ChangeRequest", back_populates="session", cascade="all, delete-orphan")
    change_histories = relationship("ChangeHistory", back_populates="session", cascade="all, delete-orphan")


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    role = Column(Enum(AssignmentRole), nullable=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="assignments")
    staff = relationship("Staff", back_populates="assignments")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    reviewer_name = Column(String(100))
    reviewer_type = Column(String(50))
    rating = Column(Integer, nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="reviews")


class Warning(Base):
    __tablename__ = "warnings"

    id = Column(Integer, primary_key=True, index=True)
    theme_id = Column(Integer, ForeignKey("themes.id"))
    warning_type = Column(Enum(WarningType), nullable=False)
    message = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    theme = relationship("Theme", back_populates="warnings")


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    requester = Column(String(100), nullable=False)
    change_type = Column(Enum(ChangeType), nullable=False)
    old_start_time = Column(DateTime)
    old_end_time = Column(DateTime)
    old_audience_count = Column(Integer)
    old_guides_needed = Column(Integer)
    new_start_time = Column(DateTime)
    new_end_time = Column(DateTime)
    new_audience_count = Column(Integer)
    new_guides_needed = Column(Integer)
    reason = Column(Text)
    status = Column(Enum(ChangeStatus), default=ChangeStatus.PENDING)
    reviewer = Column(String(100))
    review_comment = Column(Text)
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    session = relationship("Session", back_populates="change_requests")
    conflicts = relationship("SessionConflict", back_populates="change_request", cascade="all, delete-orphan")
    reschedule_suggestions = relationship("RescheduleSuggestion", back_populates="change_request", cascade="all, delete-orphan")
    change_histories = relationship("ChangeHistory", back_populates="change_request", cascade="all, delete-orphan")


class ChangeHistory(Base):
    __tablename__ = "change_histories"

    id = Column(Integer, primary_key=True, index=True)
    change_request_id = Column(Integer, ForeignKey("change_requests.id"))
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    operator = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)
    old_values = Column(Text)
    new_values = Column(Text)
    change_type = Column(Enum(ChangeType))
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="change_histories")
    change_request = relationship("ChangeRequest", back_populates="change_histories")


class SessionConflict(Base):
    __tablename__ = "session_conflicts"

    id = Column(Integer, primary_key=True, index=True)
    change_request_id = Column(Integer, ForeignKey("change_requests.id"), nullable=False)
    conflict_type = Column(Enum(ConflictType), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"))
    assignment_id = Column(Integer, ForeignKey("assignments.id"))
    message = Column(Text, nullable=False)
    detail = Column(Text)
    status = Column(Enum(RescheduleStatus), default=RescheduleStatus.PENDING)
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    change_request = relationship("ChangeRequest", back_populates="conflicts")
    staff = relationship("Staff")
    assignment = relationship("Assignment")


class RescheduleSuggestion(Base):
    __tablename__ = "reschedule_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    change_request_id = Column(Integer, ForeignKey("change_requests.id"), nullable=False)
    conflict_id = Column(Integer, ForeignKey("session_conflicts.id"))
    staff_id = Column(Integer, ForeignKey("staff.id"))
    suggested_staff_id = Column(Integer, ForeignKey("staff.id"))
    action = Column(String(50), nullable=False)
    priority = Column(Integer, default=5)
    reason = Column(Text)
    is_applied = Column(Boolean, default=False)
    applied_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    change_request = relationship("ChangeRequest", back_populates="reschedule_suggestions")
    conflict = relationship("SessionConflict")
    staff = relationship("Staff", foreign_keys=[staff_id])
    suggested_staff = relationship("Staff", foreign_keys=[suggested_staff_id])


class PointSourceType(str, enum.Enum):
    SERVICE = "服务积分"
    RATING = "评价奖励"
    BONUS = "额外奖励"
    DEDUCTION = "积分扣除"


class LevelBadge(Base):
    __tablename__ = "level_badges"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(Integer, unique=True, nullable=False)
    name = Column(String(50), nullable=False)
    badge_name = Column(String(100), nullable=False)
    min_points = Column(Integer, nullable=False)
    max_points = Column(Integer)
    description = Column(Text)
    icon = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    point_records = relationship("PointRecord", back_populates="level_badge")
    monthly_rankings = relationship("MonthlyRanking", back_populates="level_badge")


class PointRecord(Base):
    __tablename__ = "point_records"

    id = Column(Integer, primary_key=True, index=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    review_id = Column(Integer, ForeignKey("reviews.id"))
    level_badge_id = Column(Integer, ForeignKey("level_badges.id"))
    source_type = Column(Enum(PointSourceType), nullable=False)
    points = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="point_records")
    session = relationship("Session")
    review = relationship("Review")
    level_badge = relationship("LevelBadge", back_populates="point_records")


class MonthlyRanking(Base):
    __tablename__ = "monthly_rankings"

    id = Column(Integer, primary_key=True, index=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)
    total_points = Column(Integer, nullable=False)
    positive_review_rate = Column(Float, nullable=False)
    session_count = Column(Integer, nullable=False)
    is_excellent = Column(Boolean, default=False)
    level_badge_id = Column(Integer, ForeignKey("level_badges.id"))
    settled_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="monthly_rankings")
    level_badge = relationship("LevelBadge", back_populates="monthly_rankings")


class StaffBadge(Base):
    __tablename__ = "staff_badges"

    id = Column(Integer, primary_key=True, index=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    level_badge_id = Column(Integer, ForeignKey("level_badges.id"), nullable=False)
    earned_at = Column(DateTime(timezone=True), server_default=func.now())
    is_current = Column(Boolean, default=True)

    staff = relationship("Staff", back_populates="badges")
    level_badge = relationship("LevelBadge")


class GroupApplicationStatus(str, enum.Enum):
    PENDING = "待确认"
    PARTIALLY_CONFIRMED = "部分确认"
    CONFIRMED = "已全部确认"
    CLOSED = "已关闭"


class AllocationStatus(str, enum.Enum):
    PROPOSED = "待学校确认"
    CONFIRMED = "已确认占用"
    REJECTED = "学校未接受"
    EXPIRED = "超时未确认"
    CANCELLED = "已取消"


class WaitlistStatus(str, enum.Enum):
    WAITING = "候补中"
    PROMOTED = "已递补"
    CANCELLED = "已取消"


class AllocationEventType(str, enum.Enum):
    SPLIT = "拆分"
    WAITLIST = "进入候补"
    CONFIRM = "确认占用"
    PARTIAL_CONFIRM = "部分确认"
    REDUCE = "缩减人数"
    EXPIRE = "超时释放"
    PROMOTE = "候补递补"
    SESSION_CANCEL = "场次取消释放"
    CANCEL = "申请取消"


class GroupApplication(Base):
    __tablename__ = "group_applications"

    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    theme_id = Column(Integer, ForeignKey("themes.id"), nullable=False)
    original_total_count = Column(Integer, nullable=False)
    total_count = Column(Integer, nullable=False)
    released_count = Column(Integer, default=0)
    min_group_size = Column(Integer, nullable=False, default=1)
    co_travel_size = Column(Integer, nullable=False, default=1)
    preferred_start = Column(DateTime)
    preferred_end = Column(DateTime)
    confirm_deadline = Column(DateTime)
    status = Column(Enum(GroupApplicationStatus), default=GroupApplicationStatus.PENDING)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    school = relationship("School")
    theme = relationship("Theme")
    allocations = relationship("GroupAllocation", back_populates="application", cascade="all, delete-orphan")
    waitlist_entries = relationship("WaitlistEntry", back_populates="application", cascade="all, delete-orphan")
    events = relationship("AllocationEvent", back_populates="application", cascade="all, delete-orphan")


class GroupAllocation(Base):
    __tablename__ = "group_allocations"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("group_applications.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    split_seq = Column(Integer, nullable=False, default=1)
    allocated_count = Column(Integer, nullable=False)
    confirmed_count = Column(Integer, default=0)
    status = Column(Enum(AllocationStatus), default=AllocationStatus.PROPOSED)
    reason = Column(Text)
    confirm_deadline = Column(DateTime)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_at = Column(DateTime(timezone=True))

    application = relationship("GroupApplication", back_populates="allocations")
    session = relationship("Session")


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("group_applications.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    queue_seq = Column(Integer, nullable=False)
    requested_count = Column(Integer, nullable=False)
    status = Column(Enum(WaitlistStatus), default=WaitlistStatus.WAITING)
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    application = relationship("GroupApplication", back_populates="waitlist_entries")
    session = relationship("Session")


class AllocationEvent(Base):
    __tablename__ = "allocation_events"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("group_applications.id"), nullable=False)
    allocation_id = Column(Integer, ForeignKey("group_allocations.id"))
    waitlist_entry_id = Column(Integer, ForeignKey("waitlist_entries.id"))
    session_id = Column(Integer, ForeignKey("sessions.id"))
    event_type = Column(Enum(AllocationEventType), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    reason = Column(Text)
    operator = Column(String(100), default="系统")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    application = relationship("GroupApplication", back_populates="events")
    allocation = relationship("GroupAllocation")
    waitlist_entry = relationship("WaitlistEntry")
    session = relationship("Session")
