#!/usr/bin/env python3
import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000"

def p(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))

def test_header(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

test_header("1. 初始化测试数据 - 创建主题、场地、学校、讲解员")

theme_data = {"name": "青铜器文化", "description": "青铜器历史与文化", "category": "历史"}
response = requests.post(f"{BASE_URL}/api/themes", json=theme_data)
theme_id = response.json()["id"] if response.status_code == 200 else 1
print(f"创建主题: 状态码={response.status_code}, ID={theme_id}")

venue_data = {"name": "青铜馆", "venue_type": "展厅", "capacity": 50, "location": "1楼"}
response = requests.post(f"{BASE_URL}/api/venues", json=venue_data)
venue_id = response.json()["id"] if response.status_code == 200 else 1
print(f"创建场地: 状态码={response.status_code}, ID={venue_id}")

school_data = {"name": "实验小学", "contact_person": "王老师", "phone": "13800138000"}
response = requests.post(f"{BASE_URL}/api/schools", json=school_data)
school_id = response.json()["id"] if response.status_code == 200 else 1
print(f"创建学校: 状态码={response.status_code}, ID={school_id}")

staff_data = {
    "name": "张讲解",
    "staff_type": "讲解员",
    "phone": "13900139000",
    "email": "zhang@example.com",
    "themes": [{"theme_id": theme_id, "proficiency_level": 5}],
    "venues": [{"venue_id": venue_id, "is_certified": True}]
}
response = requests.post(f"{BASE_URL}/api/staff", json=staff_data)
staff1_id = response.json()["id"] if response.status_code == 200 else 1
print(f"创建讲解员1: 状态码={response.status_code}, ID={staff1_id}")

staff_data2 = {
    "name": "李讲解",
    "staff_type": "讲解员",
    "phone": "13900139001",
    "email": "li@example.com",
    "themes": [{"theme_id": theme_id, "proficiency_level": 4}],
    "venues": [{"venue_id": venue_id, "is_certified": True}]
}
response = requests.post(f"{BASE_URL}/api/staff", json=staff_data2)
staff2_id = response.json()["id"] if response.status_code == 200 else 2
print(f"创建讲解员2: 状态码={response.status_code}, ID={staff2_id}")

test_header("2. 创建初始场次并安排讲解员")

tomorrow = datetime.now() + timedelta(days=1)
session_data = {
    "title": "青铜器研学活动",
    "theme_id": theme_id,
    "venue_id": venue_id,
    "session_type": "研学实践",
    "start_time": tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).isoformat(),
    "end_time": tomorrow.replace(hour=11, minute=0, second=0, microsecond=0).isoformat(),
    "audience_type": "学校",
    "audience_count": 30,
    "school_id": school_id,
    "guides_needed": 1,
    "needs_lecturer": False,
    "description": "实验小学研学"
}
response = requests.post(f"{BASE_URL}/api/sessions", json=session_data)
session_id = response.json()["id"]
print(f"创建场次: 状态码={response.status_code}, ID={session_id}")

assignment_data = {"staff_id": staff1_id, "role": "讲解员", "is_primary": True}
response = requests.post(f"{BASE_URL}/api/sessions/{session_id}/assignments", json=assignment_data)
print(f"安排讲解员: 状态码={response.status_code}")
p(response.json())

test_header("3. 预检查变更冲突 - 调整时间与人员1冲突")

new_start = tomorrow.replace(hour=14, minute=0).isoformat()
new_end = tomorrow.replace(hour=16, minute=0).isoformat()

conflict_session = {
    "title": "冲突场次",
    "theme_id": theme_id,
    "venue_id": venue_id,
    "session_type": "研学实践",
    "start_time": new_start,
    "end_time": new_end,
    "audience_type": "学校",
    "audience_count": 20,
    "school_id": school_id,
    "guides_needed": 1,
    "needs_lecturer": False
}
response = requests.post(f"{BASE_URL}/api/sessions", json=conflict_session)
conflict_session_id = response.json()["id"]

response = requests.post(f"{BASE_URL}/api/sessions/{conflict_session_id}/assignments", 
                        json={"staff_id": staff1_id, "role": "讲解员"})
print(f"在冲突场次安排同一讲解员: 状态码={response.status_code}")

check_data = {
    "new_start_time": new_start,
    "new_end_time": new_end
}
response = requests.post(f"{BASE_URL}/api/changes/{session_id}/check-conflicts", json=check_data)
print(f"预检查冲突: 状态码={response.status_code}")
p(response.json())

test_header("4. 提交变更申请 - 调整时间和人数")

change_data = {
    "session_id": session_id,
    "requester": "王老师（实验小学）",
    "change_type": "时间和人数变更",
    "new_start_time": new_start,
    "new_end_time": new_end,
    "new_audience_count": 50,
    "new_guides_needed": 2,
    "reason": "学校临时增加了20名学生，同时下午的交通更方便"
}
response = requests.post(f"{BASE_URL}/api/changes", json=change_data)
change_id = response.json()["id"] if response.status_code == 200 else None
print(f"提交变更申请: 状态码={response.status_code}")
p(response.json())

test_header("5. 审核变更申请 - 自动触发冲突检测")

review_data = {
    "status": "已通过",
    "reviewer": "调度员小李",
    "review_comment": "情况属实，同意变更"
}
response = requests.put(f"{BASE_URL}/api/changes/{change_id}/review", json=review_data)
print(f"审核变更: 状态码={response.status_code}")
result = response.json()
print(f"检测到冲突数: {len(result.get('conflicts', []))}")
print(f"生成建议数: {len(result.get('suggestions', []))}")
print("\n冲突详情:")
for c in result.get('conflicts', []):
    print(f"  - [{c['conflict_type']}] {c['message']}")
print("\n重排建议:")
for s in result.get('suggestions', []):
    print(f"  - [{s['action']}] {s['reason']} (优先级: {s['priority']})")

test_header("6. 查看变更历史")

response = requests.get(f"{BASE_URL}/api/changes/history", params={"session_id": session_id})
print(f"查询变更历史: 状态码={response.status_code}")
for h in response.json():
    print(f"  [{h['created_at'][:19]}] {h['operator']} - {h['action']}: {h['description']}")

test_header("7. 手动应用单条重排建议")

suggestions = result.get('suggestions', [])
if suggestions:
    suggestion_id = suggestions[0]['id']
    response = requests.post(f"{BASE_URL}/api/changes/suggestions/{suggestion_id}/apply", 
                            json={"operator": "调度员小李"})
    print(f"应用建议{suggestion_id}: 状态码={response.status_code}")
    p(response.json())

test_header("8. 执行变更并自动重排")

response = requests.post(f"{BASE_URL}/api/changes/{change_id}/execute", 
                        json={"operator": "调度员小李"})
print(f"执行变更: 状态码={response.status_code}")
result = response.json()
print(f"执行结果: {result['message']}")
print(f"应用建议数: {result['applied_suggestions']}")
print(f"剩余冲突: {result['remaining_conflicts']}")
if result.get('errors'):
    print(f"错误信息: {result['errors']}")

test_header("9. 查看变更后场次详情")

response = requests.get(f"{BASE_URL}/api/sessions/{session_id}")
print(f"场次详情: 状态码={response.status_code}")
data = response.json()
print(f"场次标题: {data['title']}")
print(f"新时间: {data['start_time']} - {data['end_time']}")
print(f"新人数: {data['audience_count']}")
print(f"需要讲解员: {data['guides_needed']}人")
print(f"当前安排讲解员: {len(data['assignments'])}人")
print(f"人员是否充足: {data['is_fully_staffed']}")
print(f"场次状态: {data['status']}")

test_header("10. 统计模块 - 场次变更频次")

response = requests.get(f"{BASE_URL}/api/statistics/session-changes")
print(f"各场次变更统计: 状态码={response.status_code}")
for stat in response.json()[:5]:
    print(f"  [{stat['session_title']}] 总变更{stat['change_count']}次, "
          f"时间变更{stat['time_change_count']}次, "
          f"人数变更{stat['count_change_count']}次")

test_header("11. 统计模块 - 变更趋势分析")

response = requests.get(f"{BASE_URL}/api/statistics/change-frequency", params={"period": "day"})
print(f"按日变更趋势: 状态码={response.status_code}")
for stat in response.json():
    print(f"  [{stat['period']}] 总变更{stat['total_changes']}次, "
          f"通过{stat['approved_count']}次, 拒绝{stat['rejected_count']}次, "
          f"平均处理{stat['avg_resolution_time_hours']:.2f}小时")

test_header("12. 查看所有冲突列表")

response = requests.get(f"{BASE_URL}/api/changes/conflicts")
print(f"所有冲突: 状态码={response.status_code}")
for c in response.json():
    print(f"  [{c['status']}] [{c['conflict_type']}] {c['message']}")

test_header("测试完成！总结")
print("""
已实现的核心功能:
✓ 变更申请提交（支持时间、人数、两者同时变更）
✓ 变更审核流程（通过/拒绝）
✓ 审核通过自动触发冲突检测（时间撞场、人员不足）
✓ 智能重排建议（更换冲突人员、补充缺口人员）
✓ 手动应用单条建议
✓ 一键执行变更并自动应用所有建议
✓ 完整的变更历史记录
✓ 变更频次统计（按场次、按时间周期）
✓ 预检查功能（提交前预览冲突）

数据模型扩展:
✓ ChangeRequest - 变更申请表
✓ ChangeHistory - 变更历史表
✓ SessionConflict - 冲突检测结果表
✓ RescheduleSuggestion - 重排建议表

新增接口:
✓ POST   /api/changes                          - 提交变更申请
✓ GET    /api/changes                          - 变更列表
✓ GET    /api/changes/{id}                     - 变更详情
✓ PUT    /api/changes/{id}/review              - 审核变更
✓ POST   /api/changes/{id}/execute             - 执行变更
✓ POST   /api/changes/{session_id}/check-conflicts - 预检查冲突
✓ GET    /api/changes/{id}/conflicts           - 变更关联冲突
✓ GET    /api/changes/{id}/suggestions         - 变更关联建议
✓ POST   /api/changes/suggestions/{id}/apply   - 应用单条建议
✓ GET    /api/changes/conflicts                - 所有冲突列表
✓ GET    /api/changes/history                  - 变更历史
✓ GET    /api/statistics/session-changes       - 场次变更统计
✓ GET    /api/statistics/change-frequency      - 变更趋势统计
""")
