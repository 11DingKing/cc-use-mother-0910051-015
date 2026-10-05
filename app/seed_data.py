from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_
import random

from app.database import engine, SessionLocal, Base
from app.models import (
    Staff, Theme, Venue, StaffTheme, StaffVenue, School,
    Session, Assignment, Review, LevelBadge,
    StaffType, SessionType, SessionStatus, AssignmentRole, AudienceType
)
from app.crud import is_session_fully_staffed


def seed_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Theme).count() > 0:
            print("数据库已有数据，跳过初始化")
            return

        themes_data = [
            {"name": "青铜器文化", "category": "历史文物", "description": "商周时期青铜器的历史、工艺与文化价值"},
            {"name": "楚汉文化", "category": "历史文化", "description": "湖北地区楚文化与汉文化的传承与发展"},
            {"name": "非遗传承", "category": "传统文化", "description": "湖北省非物质文化遗产展示与体验"},
            {"name": "红色文化", "category": "革命历史", "description": "湖北地区革命历史与红色教育"},
            {"name": "古建艺术", "category": "建筑艺术", "description": "湖北传统建筑艺术与美学"},
            {"name": "水利文化", "category": "科技文明", "description": "都江堰等水利工程的历史与科技价值"},
            {"name": "茶文化", "category": "传统文化", "description": "湖北茶史、茶艺与茶文化体验"},
            {"name": "陶瓷文化", "category": "传统工艺", "description": "湖北陶瓷工艺的历史与传承"},
        ]
        themes = []
        for t in themes_data:
            theme = Theme(**t)
            db.add(theme)
            themes.append(theme)
        db.flush()

        venues_data = [
            {"name": "一号展厅", "venue_type": "展厅", "capacity": 100, "location": "A馆一层"},
            {"name": "二号展厅", "venue_type": "展厅", "capacity": 80, "location": "A馆二层"},
            {"name": "三号展厅", "venue_type": "展厅", "capacity": 120, "location": "B馆一层"},
            {"name": "四号展厅", "venue_type": "展厅", "capacity": 60, "location": "B馆二层"},
            {"name": "互动体验厅", "venue_type": "展厅", "capacity": 50, "location": "C馆一层"},
            {"name": "学术报告厅", "venue_type": "讲座厅", "capacity": 200, "location": "D馆一层"},
            {"name": "多功能厅", "venue_type": "讲座厅", "capacity": 150, "location": "D馆二层"},
            {"name": "小型会议室", "venue_type": "讲座厅", "capacity": 30, "location": "D馆三层"},
        ]
        venues = []
        for v in venues_data:
            venue = Venue(**v)
            db.add(venue)
            venues.append(venue)
        db.flush()

        if db.query(LevelBadge).count() == 0:
            level_badges_data = [
                {"level": 1, "name": "初级讲解员", "badge_name": "萌新讲解", "min_points": 0, "max_points": 99,
                 "description": "初入讲解岗位，正在学习成长中", "icon": "🌱"},
                {"level": 2, "name": "中级讲解员", "badge_name": "成长之星", "min_points": 100, "max_points": 299,
                 "description": "积累了一定经验，能够独立完成讲解任务", "icon": "⭐"},
                {"level": 3, "name": "高级讲解员", "badge_name": "讲解能手", "min_points": 300, "max_points": 599,
                 "description": "讲解经验丰富，深受观众好评", "icon": "🌟"},
                {"level": 4, "name": "资深讲解员", "badge_name": "金牌讲解", "min_points": 600, "max_points": 999,
                 "description": "资深讲解员，专业水平高，口碑极佳", "icon": "🏅"},
                {"level": 5, "name": "专家讲解员", "badge_name": "讲解大师", "min_points": 1000, "max_points": None,
                 "description": "殿堂级讲解员，行业标杆，拥有极高的知名度和影响力", "icon": "👑"},
            ]
            for lb in level_badges_data:
                level_badge = LevelBadge(**lb)
                db.add(level_badge)
            db.flush()
            print(f"  等级勋章: {len(level_badges_data)} 个")

        guide_names = [
            "张明", "李华", "王芳", "刘伟", "陈静",
            "杨勇", "赵敏", "周杰", "吴涛", "郑琳",
            "孙明", "马超", "朱婷", "胡军", "林雪"
        ]
        lecturer_names = [
            "王国维", "陈寅恪", "钱穆", "吕思勉", "翦伯赞",
            "范文澜", "郭沫若", "侯外庐"
        ]

        staff_list = []
        for i, name in enumerate(guide_names):
            staff = Staff(
                name=name,
                staff_type=StaffType.GUIDE,
                phone=f"138{random.randint(10000000, 99999999)}",
                email=f"{name.lower()}@museum.com",
                total_service_hours=round(random.uniform(50, 300), 1),
                star_rating=round(random.uniform(3.5, 5.0), 1),
                review_count=random.randint(10, 80)
            )
            db.add(staff)
            staff_list.append(staff)

        for i, name in enumerate(lecturer_names):
            staff = Staff(
                name=name,
                staff_type=StaffType.LECTURER,
                phone=f"139{random.randint(10000000, 99999999)}",
                email=f"{name.lower()}@university.edu",
                total_service_hours=round(random.uniform(30, 150), 1),
                star_rating=round(random.uniform(4.0, 5.0), 1),
                review_count=random.randint(5, 40)
            )
            db.add(staff)
            staff_list.append(staff)
        db.flush()

        for staff in staff_list:
            num_themes = random.randint(2, 5)
            selected_themes = random.sample(themes, num_themes)
            for theme in selected_themes:
                st = StaffTheme(
                    staff_id=staff.id,
                    theme_id=theme.id,
                    proficiency_level=random.randint(3, 5)
                )
                db.add(st)

            if staff.staff_type == StaffType.GUIDE:
                exhibit_venues = [v for v in venues if v.venue_type == "展厅"]
                num_venues = random.randint(3, min(5, len(exhibit_venues)))
                selected_venues = random.sample(exhibit_venues, num_venues)
                for venue in selected_venues:
                    sv = StaffVenue(
                        staff_id=staff.id,
                        venue_id=venue.id,
                        is_certified=True
                    )
                    db.add(sv)
            else:
                selected_venues = [v for v in venues if v.venue_type == "讲座厅"]
                for venue in selected_venues:
                    sv = StaffVenue(
                        staff_id=staff.id,
                        venue_id=venue.id,
                        is_certified=True
                    )
                    db.add(sv)
        db.flush()

        schools_data = [
            {"name": "武汉市第一中学", "contact_person": "王老师", "phone": "027-81234567"},
            {"name": "武汉市实验中学", "contact_person": "李老师", "phone": "027-82345678"},
            {"name": "华中师范大学附属小学", "contact_person": "张老师", "phone": "027-83456789"},
            {"name": "武汉市外国语学校", "contact_person": "刘老师", "phone": "027-84567890"},
            {"name": "湖北省武昌实验中学", "contact_person": "陈老师", "phone": "027-85678901"},
            {"name": "武汉市第十四中学", "contact_person": "赵老师", "phone": "027-86789012"},
            {"name": "湖北大学附属中学", "contact_person": "周老师", "phone": "027-87890123"},
            {"name": "武汉市第四十九中学", "contact_person": "吴老师", "phone": "027-88901234"},
        ]
        schools = []
        for s in schools_data:
            school = School(**s)
            db.add(school)
            schools.append(school)
        db.flush()

        base_date = datetime(2026, 6, 13)
        sessions_data = []

        for day_offset in range(3):
            current_date = base_date + timedelta(days=day_offset)

            sessions_data.extend([
                {
                    "title": "青铜器专题讲解",
                    "theme": themes[0],
                    "venue": venues[0],
                    "type": SessionType.RESEARCH,
                    "start": current_date.replace(hour=9, minute=0),
                    "end": current_date.replace(hour=10, minute=30),
                    "audience": AudienceType.SCHOOL,
                    "count": 50,
                    "school": schools[0],
                    "guides": 2,
                    "need_lecturer": False
                },
                {
                    "title": "楚汉文化讲座",
                    "theme": themes[1],
                    "venue": venues[5],
                    "type": SessionType.LECTURE,
                    "start": current_date.replace(hour=10, minute=0),
                    "end": current_date.replace(hour=12, minute=0),
                    "audience": AudienceType.PUBLIC,
                    "count": 150,
                    "school": None,
                    "guides": 0,
                    "need_lecturer": True
                },
                {
                    "title": "非遗技艺体验",
                    "theme": themes[2],
                    "venue": venues[4],
                    "type": SessionType.RESEARCH,
                    "start": current_date.replace(hour=14, minute=0),
                    "end": current_date.replace(hour=16, minute=0),
                    "audience": AudienceType.SCHOOL,
                    "count": 30,
                    "school": schools[1],
                    "guides": 3,
                    "need_lecturer": False
                },
                {
                    "title": "红色历史研学",
                    "theme": themes[3],
                    "venue": venues[1],
                    "type": SessionType.RESEARCH,
                    "start": current_date.replace(hour=9, minute=30),
                    "end": current_date.replace(hour=11, minute=0),
                    "audience": AudienceType.SCHOOL,
                    "count": 60,
                    "school": schools[2],
                    "guides": 2,
                    "need_lecturer": False
                },
                {
                    "title": "古建艺术赏析",
                    "theme": themes[4],
                    "venue": venues[2],
                    "type": SessionType.LECTURE,
                    "start": current_date.replace(hour=14, minute=30),
                    "end": current_date.replace(hour=16, minute=30),
                    "audience": AudienceType.PUBLIC,
                    "count": 100,
                    "school": None,
                    "guides": 0,
                    "need_lecturer": True
                },
                {
                    "title": "水利科技探索",
                    "theme": themes[5],
                    "venue": venues[3],
                    "type": SessionType.RESEARCH,
                    "start": current_date.replace(hour=15, minute=0),
                    "end": current_date.replace(hour=17, minute=0),
                    "audience": AudienceType.SCHOOL,
                    "count": 40,
                    "school": schools[3],
                    "guides": 2,
                    "need_lecturer": False
                },
                {
                    "title": "茶文化讲座",
                    "theme": themes[6],
                    "venue": venues[6],
                    "type": SessionType.LECTURE,
                    "start": current_date.replace(hour=10, minute=30),
                    "end": current_date.replace(hour=12, minute=0),
                    "audience": AudienceType.PUBLIC,
                    "count": 80,
                    "school": None,
                    "guides": 0,
                    "need_lecturer": True
                },
            ])

        sessions = []
        for s_data in sessions_data:
            session = Session(
                title=s_data["title"],
                theme_id=s_data["theme"].id,
                venue_id=s_data["venue"].id,
                session_type=s_data["type"],
                start_time=s_data["start"],
                end_time=s_data["end"],
                audience_type=s_data["audience"],
                audience_count=s_data["count"],
                school_id=s_data["school"].id if s_data["school"] else None,
                guides_needed=s_data["guides"],
                needs_lecturer=s_data["need_lecturer"],
                status=SessionStatus.DRAFT,
                description=f"遗产日主题活动：{s_data['title']}"
            )
            db.add(session)
            sessions.append(session)
        db.flush()

        past_date = base_date - timedelta(days=7)
        past_sessions = []
        for day_offset in range(2):
            current_date = past_date + timedelta(days=day_offset)

            past_sessions.extend([
                {
                    "title": "往日青铜器讲解",
                    "theme": themes[0],
                    "venue": venues[0],
                    "type": SessionType.RESEARCH,
                    "start": current_date.replace(hour=9, minute=0),
                    "end": current_date.replace(hour=10, minute=30),
                    "audience": AudienceType.SCHOOL,
                    "count": 45,
                    "school": schools[4],
                    "guides": 2,
                    "need_lecturer": False
                },
                {
                    "title": "往日楚汉文化讲座",
                    "theme": themes[1],
                    "venue": venues[5],
                    "type": SessionType.LECTURE,
                    "start": current_date.replace(hour=14, minute=0),
                    "end": current_date.replace(hour=16, minute=0),
                    "audience": AudienceType.PUBLIC,
                    "count": 120,
                    "school": None,
                    "guides": 0,
                    "need_lecturer": True
                },
            ])

        completed_sessions = []
        for s_data in past_sessions:
            session = Session(
                title=s_data["title"],
                theme_id=s_data["theme"].id,
                venue_id=s_data["venue"].id,
                session_type=s_data["type"],
                start_time=s_data["start"],
                end_time=s_data["end"],
                audience_type=s_data["audience"],
                audience_count=s_data["count"],
                school_id=s_data["school"].id if s_data["school"] else None,
                guides_needed=s_data["guides"],
                needs_lecturer=s_data["need_lecturer"],
                status=SessionStatus.COMPLETED,
                description=f"已完成活动：{s_data['title']}"
            )
            db.add(session)
            completed_sessions.append(session)
        db.flush()

        def find_qualified_staff(db_session, theme_id, venue_id, staff_type_role, used_staff, start, end):
            from app.crud import is_staff_available, is_staff_qualified
            role = AssignmentRole.LECTURER if staff_type_role == StaffType.LECTURER else AssignmentRole.GUIDE

            staff_candidates = db_session.query(Staff).filter(
                Staff.staff_type == staff_type_role,
                Staff.is_active == True,
                Staff.themes.any(StaffTheme.theme_id == theme_id),
                Staff.venues.any(and_(
                    StaffVenue.venue_id == venue_id,
                    StaffVenue.is_certified == True
                ))
            ).order_by(Staff.star_rating.desc()).all()

            for candidate in staff_candidates:
                if candidate.id not in used_staff and is_staff_available(db_session, candidate.id, start, end):
                    qualified, _ = is_staff_qualified(db_session, candidate.id, theme_id, venue_id, role)
                    if qualified:
                        return candidate
            return None

        for session in sessions:
            used_staff = set()

            if session.needs_lecturer:
                lecturer = find_qualified_staff(
                    db, session.theme_id, session.venue_id,
                    StaffType.LECTURER, used_staff,
                    session.start_time, session.end_time
                )
                if lecturer:
                    assignment = Assignment(
                        session_id=session.id,
                        staff_id=lecturer.id,
                        role=AssignmentRole.LECTURER,
                        is_primary=True
                    )
                    db.add(assignment)
                    used_staff.add(lecturer.id)

            for _ in range(session.guides_needed):
                guide = find_qualified_staff(
                    db, session.theme_id, session.venue_id,
                    StaffType.GUIDE, used_staff,
                    session.start_time, session.end_time
                )
                if guide:
                    assignment = Assignment(
                        session_id=session.id,
                        staff_id=guide.id,
                        role=AssignmentRole.GUIDE,
                        is_primary=(len([a for a in used_staff]) == 0)
                    )
                    db.add(assignment)
                    used_staff.add(guide.id)
            db.flush()

            if is_session_fully_staffed(db, session.id):
                session.status = SessionStatus.SCHEDULED

        for session in completed_sessions:
            used_staff = set()

            if session.needs_lecturer:
                lecturer = find_qualified_staff(
                    db, session.theme_id, session.venue_id,
                    StaffType.LECTURER, used_staff,
                    session.start_time, session.end_time
                )
                if lecturer:
                    assignment = Assignment(
                        session_id=session.id,
                        staff_id=lecturer.id,
                        role=AssignmentRole.LECTURER,
                        is_primary=True
                    )
                    db.add(assignment)
                    used_staff.add(lecturer.id)

            for _ in range(session.guides_needed):
                guide = find_qualified_staff(
                    db, session.theme_id, session.venue_id,
                    StaffType.GUIDE, used_staff,
                    session.start_time, session.end_time
                )
                if guide:
                    assignment = Assignment(
                        session_id=session.id,
                        staff_id=guide.id,
                        role=AssignmentRole.GUIDE,
                        is_primary=(len([a for a in used_staff]) == 0)
                    )
                    db.add(assignment)
                    used_staff.add(guide.id)

            reviewer_names = ["观众张三", "李四老师", "王同学", "赵家长", "刘老师"]
            reviewer_types = ["观众", "带队老师", "学生", "家长", "带队老师"]
            for i in range(random.randint(2, 4)):
                review = Review(
                    session_id=session.id,
                    reviewer_name=reviewer_names[i],
                    reviewer_type=reviewer_types[i],
                    rating=random.randint(4, 5),
                    comment=f"{session.title}活动非常精彩，讲解员专业耐心，收获很大！"
                )
                db.add(review)

        db.commit()

        print("数据初始化完成！")
        print(f"  主题: {len(themes)} 个")
        print(f"  场地: {len(venues)} 个")
        print(f"  人员: {len(staff_list)} 人 (讲解员{len([s for s in staff_list if s.staff_type == StaffType.GUIDE])}人, 讲师{len([s for s in staff_list if s.staff_type == StaffType.LECTURER])}人)")
        print(f"  学校: {len(schools)} 所")
        print(f"  场次: {len(sessions) + len(completed_sessions)} 场 (已排定{len([s for s in sessions if s.status == SessionStatus.SCHEDULED])}场, 已完成{len(completed_sessions)}场)")

    except Exception as e:
        db.rollback()
        print(f"初始化失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
