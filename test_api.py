#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8080"

def p(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))

print("=" * 60)
print("1. 测试排班校验（验证人员1是否可安排到场次1）")
print("=" * 60)
response = requests.get(f"{BASE_URL}/api/sessions/1/validate", 
                       params={"staff_id": 1, "role": "讲解员"})
print(f"状态码: {response.status_code}")
p(response.json())

print("\n" + "=" * 60)
print("2. 测试人员推荐（获取场次1的讲解员推荐）")
print("=" * 60)
response = requests.get(f"{BASE_URL}/api/staff/1/recommend", 
                       params={"role": "讲解员", "limit": 5})
print(f"状态码: {response.status_code}")
p(response.json())

print("\n" + "=" * 60)
print("3. 测试人员饱和度统计")
print("=" * 60)
response = requests.get(f"{BASE_URL}/api/statistics/staff-saturation")
print(f"状态码: {response.status_code}")
data = response.json()
p(data[:5] if len(data) > 5 else data)

print("\n" + "=" * 60)
print("4. 测试新增评价（自动更新星级）")
print("=" * 60)
review_data = {
    "session_id": 22,
    "reviewer_name": "测试家长",
    "reviewer_type": "家长",
    "rating": 5,
    "comment": "非常棒的讲解，孩子学到了很多！"
}
response = requests.post(f"{BASE_URL}/api/reviews", json=review_data)
print(f"状态码: {response.status_code}")
p(response.json())

print("\n" + "=" * 60)
print("5. 测试评价后查看人员6的星级变化")
print("=" * 60)
response = requests.get(f"{BASE_URL}/api/staff/6")
print(f"状态码: {response.status_code}")
staff_data = response.json()
print(f"人员: {staff_data['name']}")
print(f"星级: {staff_data['star_rating']}")
print(f"服务时长: {staff_data['total_service_hours']}")
print(f"评价次数: {staff_data['review_count']}")

print("\n" + "=" * 60)
print("6. 测试自动排班（为场次1自动分配人员）")
print("=" * 60)
response = requests.post(f"{BASE_URL}/api/sessions/1/auto-assign")
print(f"状态码: {response.status_code}")
p(response.json())

print("\n" + "=" * 60)
print("7. 查看自动分配后的场次1详情")
print("=" * 60)
response = requests.get(f"{BASE_URL}/api/sessions/1")
print(f"状态码: {response.status_code}")
session_data = response.json()
print(f"场次: {session_data['title']}")
print(f"状态: {session_data['status']}")
print(f"人员配齐: {session_data['is_fully_staffed']}")
print(f"已分配人员: {[a['staff_name'] for a in session_data['assignments']]}")
