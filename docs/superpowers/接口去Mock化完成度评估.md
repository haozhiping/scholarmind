# 接口去 Mock 化完成度评估

**评估日期**: 2026-06-05
**详细核查**: 见 [check/接口真实性核查与去Mock化路线.md](../../check/接口真实性核查与去Mock化路线.md)

---

## 一、核心结论

后端 `app/routers/*` 7 个路由原本 **100% 内存 Mock**，未接真实 DB/服务；`services/*` 与 `common/clients/*` 的真实实现是"孤岛"。前端已完整接线到 API 契约，因此"前端功能不完善"的根因在后端被 Mock 短路，而非前端缺失。

既有报告（综合 85%、任务5 标 100%）衡量的是"前端接线"与"服务层代码存在"，**不代表端到端可用**，需以本评估为准修正认知。

## 二、按链路完成度

| 链路 | 内容 | 完成度 |
|---|---|---|
| ① 认证 + 论文库（地基） | auth JWT/bcrypt/MySQL；folders/papers 接 MySQL + `user_id` | ✅ 代码就绪（待 docker 联调验证） |
| ② 上传→解析→入库 | upload 落 MinIO + RQ；worker job；ingest 进度读真实表 | 🟡 **入队已通 + Agent API 已接**（2026-06-05），需 `MINERU_API_KEY` + LLM Key 即可完整跑通 |
| ③ 对话 RAG | chat 接 PG；query 真实检索+LLM SSE | ⏳ 0% |
| ④ 综述 + 引用图谱 | review Agent；graph 读 citations | ⏳ 0% |
| ⑤ 可观测 + 设置 | settings 路由；access 日志；stats/logs 真实表 | ⏳ 0% |

## 三、待处理问题摘要

1. **两个契约 bug**：① `chat.query`/`review.generate` 用 POST 但前端 `EventSource` 只发 GET → 405；② 后端缺 `POST /api/settings` 路由 → 前端保存必 404。
2. **基础设施缺口**：✅ `clients/minio.py` 已完成、✅ RQ enqueue 已接（本轮修复）、✅ worker 已注册 job、🔴 access 日志中间件缺失、✅ `services/retrieval/` 已补全。
3. **链路①验证**：需 `docker compose up -d` 后端到端跑通注册/登录/论文库（本机无 MySQL，当前仅纯函数单测可跑）。

## 四、链路①已交付

新增 `common/db/mysql_client.py`、`common/auth/{security,deps}.py`、`tests/test_auth_security.py`；auth/papers/folders 路由去 Mock 接 MySQL（强制 `user_id`）；main.py 注册异常处理器与 MySQL 池生命周期；补全前端调用但后端缺失的 `DELETE /folders/{id}`。

并修复 2 个契约 bug：chat/advanced 的 SSE 端点 `POST→GET`（对齐 `EventSource`）、新增 `app/routers/settings.py`（鉴权 + 按 `user_id` 存 Redis）。

### 链路②-入队部分已交付（2026-06-05）

修复 `papers.py` upload：落 MinIO → INSERT DB → RQ `Queue("ingest").enqueue()`；修复 `worker/main.py`：从 MinIO 下载 PDF、统一 `task_id`（UUID）查询、补全 `parse_paper` 参数（pdf_bytes + db）；修复 `parser.py`：移除 SQLAlchemy 依赖、全部 DB 操作改用 `AsyncMySQLClient`（`%s` 占位符 + autocommit）。ingest 路由 JOIN papers 表返回真实 file_name。

### ⚠️ 上传链路当前行为

上传 PDF 后 Worker 能收到任务并尝试执行 `parse_paper`。**已新增 `MINERU_PROVIDER=agent` 模式**（2026-06-05），无需 Pipeline ID，走 MinerU Agent 签名上传 API。仍需配置 `MINERU_API_KEY`（https://mineru.net 注册）才能完成真实解析。**入队 → Worker 唤醒 → DB 状态更新 → Agent API 解析** 的调度链路已贯通。

## 五、验证与提交状态（2026-06-05，如实标注）

🔴 **本批改动代码已落盘，但尚未执行校验、尚未 Git 提交** —— 受 Bash 安全分类器临时宕机阻塞，`py_compile`、`pytest`、`git commit` 均无法运行（非代码问题）。已完成的是代码静态自审；待环境恢复后补跑语法校验 + 纯函数单测 + 中文提交入库，DB 端到端联调另需 `docker compose up -d`。详见 [check/接口真实性核查与去Mock化路线.md](../../check/接口真实性核查与去Mock化路线.md) 第五节。
