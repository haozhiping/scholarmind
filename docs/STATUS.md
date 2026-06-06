# ScholarMind 项目现状（STATUS）

> 本文件是**唯一的现状真相源**。`check/` 目录已废弃，所有状态收敛到本文档。
> 最后更新：2026-06-05

## 文档地图

| 类型 | 位置 | 说明 |
|---|---|---|
| **现状（本文件）** | `docs/STATUS.md` | 系统真实状态、已完成修复、待办、不确定点 |
| 架构 | `docs/architecture.md` | 服务边界与总体架构 |
| 数据契约 | `docs/data-contracts.md` | 表 / Milvus / Redis schema，**唯一真相源** |
| RAG 链路 | `docs/rag-pipeline.md` | 检索/意图路由设计 |
| API | `docs/api.md` | 接口契约 |
| 部署 | `docs/deploy.md` | 运行与部署 |
| 设计档案 | `docs/superpowers/plans/` + `specs/` | 各模块实现计划与技术设计文档（历史参考） |

## 链路状态总览

> ⚠️ 完成度口径：以下「代码就绪」指实现已落盘并经代码审阅，**不等于端到端运行验证**。

| 链路 | 文件/模块 | 状态 | 说明 |
|---|---|---|---|
| 认证 + 论文库 | `routers/auth.py`, `routers/papers.py` | ✅ 已去 Mock | 强制 `user_id` 过滤，接真实 MySQL |
| 上传入队 | `routers/papers.py`, `routers/ingest.py` | ✅ 代码就绪 | MinIO 落 PDF + 写 `ingest_tasks` + RQ enqueue |
| 解析 | `services/parsing/parser.py` | ✅ 代码就绪 | MinerU Agent API + KIE SDK 双通路；VLM 图描述；引用提取 |
| 索引 | `services/indexing/indexer.py` | 🟡 代码就绪 | 切分/双语/Milvus 入库，未端到端验证 |
| 检索 | `services/retrieval/` | 🟡 代码就绪 | 混检/RRF/重排，依赖外部模型服务 |
| 对话/Agent | `services/chat_agent/` | 🟡 代码就绪 | SSE 流式 + query_logs，未端到端验证 |
| 可观测 | `routers/observability.py` | ✅ 代码就绪 | query/access 日志接口已修复 |

## 已完成修复（累积 changelog）

| 日期 | 问题 | 根因 | 改动 |
|---|---|---|---|
| 06-05 | VLM 图描述生成即丢弃 | `_write_blocks` 缺 `content_zh` 列 + VLM 后无回写 | 表加列 + INSERT 补齐 + `_describe_figures` 后 UPDATE |
| 06-05 | KIE SDK `get_result` 缺 `file_ids` | `upload_file` 返回值被丢弃 | 捕获 file_ids → 显式传入 get_result + 空值防御 |
| 06-05 | `check/` 目录状态分散 | 验证报告独立于 STATUS.md | 删除 `check/`，引用统一收敛到本文档 |
| 06-05 | 传入文件解析失败 | MinerU Agent API 免登录，代码却发 `Authorization` 头 | `parser.py` 去掉鉴权头 |
| 06-05 | query logs 假数据 | `save_feedback` 每次插入伪条目 | 改为 UPDATE 最近真实日志的 `feedback` 列 |
| 06-05 | 查询/访问日志接口 500 | `observability.py` 调用不存在的 `mysql.fetch` | 改为 `mysql.fetchall` |
| 06-05 | worker 多任务崩溃（隐患） | `asyncio.run()` 每任务新循环 + aiomysql 单例池绑旧循环 | 每个 job 末尾 `mysql.disconnect()` |
| 06-05 | 错误展示一大坨栈 | error_msg 存了完整 traceback | 存简洁消息，完整栈进日志 |
| 06-05 | 重试接口无效 | `error=NULL` 列名错 + 未重新入队 | 修列名 + 补 RQ 重新入队 |
| 06-05 | MinIO 下载失败状态机不一致 | 只改 `stage` 未改 `status` | 补 `status='failed'` |
| 06-05 | 环境依赖缺失 | `requirements.txt` 缺 openai/llama-index；Dockerfile 覆写 pymilvus 版本；.env 密码不对 | 补齐依赖，统一版本，修密码 |
| 06-05 | `common/clients/minio.py` 缺失 | 未创建 | 新建，含 bucket 初始化 + 上传/下载 |

## 已知降级 / 限制

- **MinerU Agent 轻量 API**：仅输出 markdown → 解析块**无 `page_num` / `bbox`**，图片是 CDN URL 不落 MinIO，限 **20 页 / 10MB**。
  - 后果：溯源降级到 chunk/论文级，点不到具体页码/原图位置（与 CLAUDE.md「每块带页码/坐标」硬规则冲突，属当前接口固有取舍）。

- **MinerU KIE SDK**：同步阻塞（`requests` 库），解析返回结构未完全文档化，`_mineru_to_blocks` 使用容错适配层（字段映射假设见 `_find_block_list`），真实联调需核对。

## 待办（P0 / P1 / P2）

### P0 — 阻塞 / 必须验证

- [ ] 端到端跑通一篇 PDF：upload → worker 解析 → 索引 → 对话溯源
- [ ] 所有改动 `git commit` + `py_compile` 验证

### P1 — 功能补全

- [ ] 找回溯源：下载 MinerU Agent CDN 图 → 入 MinIO + 估算/回填页码
- [ ] MinerU KIE 返回结构实测核对（`_mineru_to_blocks` 字段映射假设验证）
- [ ] Sparse 检索实现（当前仅 dense）
- [ ] 解析幂等：按 `file_hash` 去重，避免重复入库
- [ ] 解析/索引步骤的事务回滚与失败重试
- [ ] Agent API markdown parser（`_markdown_to_blocks`）页级分块增强

### P2 — 优化

- [ ] 库内历史伪条目清理：`DELETE FROM query_logs WHERE question LIKE 'Feedback for msg %';`
- [ ] 监控指标接入
- [ ] KIE SDK → Agent API 迁移策略（KIE 为 legacy 通路，确认 Agent 稳定后可删除）

## 遗留不确定点

> 以下需真实环境运行后方可确认，当前为推断/假设。

| 编号 | 不确定项 | 当前假设 | 确认方式 |
|---|---|---|---|
| U1 | MinerU KIE SDK `results["parse"]` 确切的字段结构 | 见 `_find_block_list()` 容错搜索 + `_pick()` 多 key 兜底 | 真实调用后打印 `parse_result` |
| U2 | MinerU Agent markdown 输出的页级信息 | 无页码/bbox，当前 `_markdown_to_blocks` 不填 page_num | 多页 PDF 解析后检查 markdown 内容 |
| U3 | `_describe_figures` VLM 调用对 MinerU Agent CDN URL 的兼容性 | CDN URL 可直接作为 VLM 图片输入 | 真实调用一次观察日志 |
| U4 | RQ worker 新 job 工厂模式（每个 job 独立 asyncio 循环）的内存/连接泄漏 | `disconnect()` 已处理池释放 | 连续跑 10+ job 后检查 MySQL `SHOW PROCESSLIST` |
| U5 | `doc_blocks.content_zh` 入库后检索/对话链路是否能消费此字段 | 暂未消费，待检索链路接入 | 检索服务中查询 `content_zh` 是否入索引 |

## 关键陷阱

跨会话陷阱与设计决策见根目录 `MEMORY.md`（每次任务前必读）。
