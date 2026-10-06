# 文化遗产开放日排班服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖活动、场次、讲解员、主题、排班、评价、变更、预警和统计。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 团体申请容量分配

针对热门研学日单场容量不足的问题，系统为团体（学校）申请提供可追踪的容量分配：

- **自动拆分**：一个申请按时间偏好窗口、最小成团人数和同行约束（不可拆分的同行单元人数）拆到多个场次，拆分结果为待确认软占用并记录拆分原因。
- **确认占用**：学校确认（支持部分接受）后才形成正式占用（计入场次 `audience_count`），未接受部分回到候补队列。
- **有序候补**：未满足部分进入按候补序号排序的候补队列；容量释放（缩减、超时未确认、场次取消）时按当前资格原子递补，并记录递补原因。
- **人数守恒**：缩减人数、部分接受、超时未确认、跨场取消均保持「待确认 + 已确认 + 候补 = 当前总人数」「当前总人数 + 已释放 = 原始报名」守恒，可随时通过守恒接口校验。

主要接口（前缀 `/api/group-applications`）：`POST ""` 提交并拆分、`POST /{id}/confirm` 学校确认、`POST /{id}/reduce` 缩减、`POST /{id}/cancel` 取消、`POST /expire` 超时清理、`GET /{id}` 详情（含事件流水与守恒校验）、`GET /{id}/conservation` 守恒校验、`GET /session-waitlist/{session_id}` 场次候补队列（含顺序依据与资格说明）。场次通过既有接口取消时，其团体占用自动释放并触发全局候补递补。

功能验证：`python3 test_group_allocation.py`（已纳入 `pytest` 基线）。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 -m pytest -q
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from app.main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
