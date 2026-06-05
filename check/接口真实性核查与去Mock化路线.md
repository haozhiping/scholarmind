# 接口真实性核查与去 Mock 化路线（权威现状）

**核查日期**: 2026-06-05
**核查范围**: `backend/app/routers/*` 全部 7 个路由 × `frontend/src/api/index.ts` 全部调用
**结论**: 🔴 **后端 API 层原本 100% 为内存 Mock，未接任何真实 DB/服务**；真实服务代码（`backend/services/*`、`common/clients/*`）是"孤岛"，未被任何路由或 worker 调用。

> ⚠️ 本报告纠正既有报告的误导：[任务完成情况检查报告.md](任务完成情况检查报告.md) 给出 85%/"优秀"、[任务 5-前端页面与 API 联调 - 验证报告.md](任务%205-前端页面与%20API%20联调%20-%20验证报告.md) 标 100%。两者衡量的是"**前端接线到 API 契约**"和"**服务层代码存在**"，**不等于系统端到端可用**。前端确实已完整接线，但它从后端拿到的全是写死的假数据。

---

## 一、为什么"前端功能看着不完善"

链路真相：**前端 ✅ → API 契约 ✅ → 路由实现 ❌（Mock 短路）→ 真实服务（孤岛，未接）→ DB/Milvus/RQ/MinIO（未连）**。

- 登录：任意账号都放行，返回写死的 `mock-jwt-token`，无校验。
- 论文库：`MOCK_FOLDERS`/`MOCK_PAPERS` 全局内存 list，重启即丢，无 `user_id` 隔离。
- 对话：SSE 吐**写死的假 token 序列**和假引用，不走检索/LLM。
- 可观测：指标写死 `paper_count=12` 等。

---

## 二、逐接口真实性矩阵（核查后）

图例：🟢 已接真实实现 ｜ 🔴 仍为 Mock/占位 ｜ ⚠️ 存在契约 bug

| 路由 | 端点 | 状态 | 说明 / 待办归属链路 |
|---|---|---|---|
| auth | POST /register、/login、GET /me | 🟢 | **链路①已完成**：bcrypt+JWT+MySQL users，`get_current_user` 依赖 |
| folders | GET、POST、DELETE /{id} | 🟢 | **链路①已完成**：接 MySQL，强制 `user_id`；补全了原缺失的 DELETE |
| papers | GET、GET /{id}、DELETE /{id} | 🟢 | **链路①已完成**：接 MySQL，强制 `user_id`，DB↔契约字段映射 |
| papers | POST /upload | 🔴 | 占位：返回 batch/task 标识但**不落 MinIO、不写库、不入队** → 链路② |
| ingest | GET /batches/{id}、GET /tasks、POST /tasks/{id}/retry | 🔴 | 读写 `MOCK_BATCHES`/`MOCK_TASKS`，不读 `ingest_tasks` 表 → 链路② |
| chat | POST /conversations、GET /conversations、GET /messages、POST /feedback | 🔴 | 内存 Mock，不接 PostgreSQL → 链路③ |
| chat | GET /query（SSE） | 🔴 | 假 token，仍待接真实 RAG → 链路③。（**契约 bug 已修：POST→GET**） |
| advanced | GET /review/generate（SSE）、GET /graph/citations | 🔴 | 假综述/假图谱，不调 Agent/不读 `citations` 表 → 链路④。（**review SSE 已 POST→GET**） |
| observability | GET /logs/queries、/logs/access、/stats/overview | 🔴 | 指标全写死，不读真实日志表 → 链路⑤ |
| settings | GET/POST /api/settings | 🟢 | **已新建**：鉴权 + 按 `user_id` 存 Redis（真实，非 Mock） |

### 核查曾发现的契约 bug —— ✅ 已修复（2026-06-05）
1. **SSE 方法不匹配** ✅：`chat.query`、`review.generate` 已由 `POST` 改为 `GET` + query 参数，与前端 `EventSource`（仅 GET）对齐，消除 405。
2. **缺 settings 路由** ✅：已新增 `app/routers/settings.py`（GET/POST `/api/settings`，鉴权 + Redis 按用户存储），消除 404。

> ⚠️ 遗留（非本次范围）：`chat.query` 为 SSE/EventSource，无法带 `Authorization` 头，当前 GET 端点未加鉴权且前端 `getQuerySSEUrl` 未在 URL 带 token；接入真实对话（链路③）时需用「token 走 query/cookie」方案补齐 SSE 鉴权。

---

## 三、基础设施缺口（去 Mock 化的前置依赖）

> 2026-06-05 更新：minio 客户端、retrieval 模块、worker job handler、agent 导入已修复。
> 详见 [环境依赖同步报告](环境依赖同步报告.md)。

| 组件 | 现状 | 需要 |
|---|---|---|
| `common/db/mysql_client.py` | 🟢 已新建（链路①） | — |
| `common/auth/{security,deps}.py` | 🟢 已新建（链路①） | — |
| `common/db/pg_client.py`、`redis_client.py`、`clients/milvus.py` | 🟢 存在 | chat/检索链路接线时启用 |
| `common/clients/minio.py` | 🟢 **已新建** | ✅ 上传/图片回显可用（链路②③） |
| `services/retrieval/`（query_optimizer/searcher/reranker） | 🟢 **已补全** | ✅ 检索逻辑已归位（链路③） |
| `app/worker/main.py` | 🟢 **已注册 job** | ✅ `handle_ingest_job` 串联 parsing→indexing（链路②） |
| `services/chat_agent/agent.py` 导入 | 🟢 **已修复** | ✅ 3 处导入统一指向真实模块 |
| Redis RQ 队列 enqueue 封装 | 🔴 未接 | 上传入队（链路②，routes/papers.py upload 接口待去 Mock） |
| 访问日志中间件 | 🔴 缺失 | `access_logs` 表写入（链路⑤） |

---

## 四、去 Mock 化路线（按依赖顺序）

| 链路 | 范围 | 状态 |
|---|---|---|
| **① 认证 + 论文库（地基）** | auth 真实 JWT/bcrypt/MySQL；folders/papers 接 MySQL + `user_id` 隔离 | ✅ **本轮完成（代码就绪，待 docker 联调验证）** |
| **② 上传 → 解析 → 入库** | upload 落 MinIO + 写 papers/ingest_tasks + enqueue RQ；worker 注册 job 串 parsing→indexing→写 Milvus；ingest 进度读真实表 | ⏳ 待做 |
| **③ 对话 RAG** | chat 接 PostgreSQL；query 走意图路由→混检→重排→LLM 真实 SSE（**先修 POST→GET**）；引用溯源指向真实 MinIO 图 | ⏳ 待做 |
| **④ 进阶：综述 + 引用图谱** | review 调 ReActAgent；graph 读 `citations` 表 | ⏳ 待做 |
| **⑤ 可观测 + 设置** | 新增 settings 路由；access 日志中间件；stats/logs 读真实表 | ⏳ 待做 |

---

## 五、链路①本轮完成内容

| 文件 | 改动 |
|---|---|
| `common/db/mysql_client.py` | 新建 aiomysql 异步连接池（raw SQL + autocommit，镜像 `pg_client.py`） |
| `common/auth/security.py` | 新建 bcrypt 哈希 + jose JWT 签发/校验（纯函数） |
| `common/auth/deps.py` | 新建 `get_current_user` 依赖（解析 Bearer JWT → 含 `user_id`） |
| `app/routers/auth.py` | register/login/me 接真实 MySQL+JWT |
| `app/routers/papers.py` | folders CRUD + papers 列表/详情/删除接 MySQL，强制 `user_id`；补全 `DELETE /folders/{id}` |
| `app/main.py` | 注册异常处理器（原本未注册，AuthException 不映射 401）+ MySQL 池生命周期 |
| `tests/test_auth_security.py` | 6 个单测（哈希/JWT 纯函数，可独立运行） |

**验证与提交状态（如实标注，2026-06-05）**：

| 项 | 状态 | 说明 |
|---|---|---|
| 代码静态自审 | ✅ 完成 | 9 个改动 .py 逐个复核，逻辑/引用一致 |
| `py_compile` 语法校验 | ⏳ 未执行 | 受 Bash 安全分类器临时宕机阻塞，命令无法运行 |
| `pytest tests/test_auth_security.py`（纯函数单测） | ⏳ 未执行 | 同上 |
| DB 端到端联调（register/login/papers） | ⏳ 未执行 | 需 `docker compose up -d` 起 MySQL/Redis（本机无） |
| Git 提交 | ⏳ 未提交 | 同样卡在分类器宕机；改动已落盘未入库 |

> ⚠️ 本批改动（链路① + 2 个契约 bug 修复 + 文档）**代码已落盘但尚未执行校验、尚未 Git 提交**。待环境恢复后需补跑：
> ```bash
> cd backend
> python -m py_compile common/db/mysql_client.py common/auth/security.py common/auth/deps.py \
>   app/routers/auth.py app/routers/papers.py app/routers/chat.py \
>   app/routers/advanced.py app/routers/settings.py app/main.py
> python -m pytest tests/test_auth_security.py -q
> ```
> 通过后按 CLAUDE.md「即时提交」用中文提交信息入库。

---

## 六、需要处理的问题（行动清单）

🔴 **立即**
1. 起基础设施 `docker compose up -d`，跑链路①端到端：注册→登录→建文件夹→（建库后）列论文。
2. ✅ 已修两个契约 bug：SSE `POST→GET`（chat.query/review.generate）、新增 settings 路由（GET/POST，Redis 按用户存）。

🟡 **按链路推进**
3. 链路②：建 `clients/minio.py` + RQ enqueue + worker job + upload/ingest 去 Mock。
4. 链路③：chat 接 PG + query 真实 RAG。
5. 链路④⑤：综述/图谱/可观测/设置去 Mock。

🟢 **文档纪律**
6. 每条链路去 Mock 后，同步更新本报告矩阵 + 对应 `docs/superpowers/*完成度评估.md`，并修正 [综合报告](任务完成情况检查报告.md) 的整体完成度（当前 85% 偏高，未计入 API 层 Mock 缺口）。
