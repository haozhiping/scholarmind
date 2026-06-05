# API 接口约定

Base: `http://localhost:8000`  ｜  鉴权: `Authorization: Bearer <jwt>`（除 auth 外全部需要）
所有业务接口由 user_id 隔离中间件自动注入并强制过滤。

## 鉴权 auth
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册 (username/email/password) |
| POST | `/api/auth/login` | 登录 → 返回 JWT |
| GET  | `/api/auth/me` | 当前用户信息 |

## 论文 / 文件夹 papers
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/papers/upload` | 批量上传 PDF → 返回 `batch_id` + tasks (202, 异步) |
| GET  | `/api/papers` | 论文列表 (按 folder/状态过滤) |
| GET  | `/api/papers/{id}` | 论文详情 + 元数据 |
| DELETE | `/api/papers/{id}` | 删除 (连带 Milvus/MinIO 清理) |
| GET  | `/api/folders` / POST `/api/folders` | 文件夹 CRUD |

## 入库任务 ingest
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ingest/batches/{batch_id}` | 批次进度 (done/total/failed) |
| GET | `/api/ingest/tasks?batch_id=` | 任务列表 + stage/progress/error |
| POST | `/api/ingest/tasks/{id}/retry` | 失败重试 |

## 对话 chat
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat/conversations` | 新建会话 |
| GET  | `/api/chat/conversations` | 会话列表 |
| GET  | `/api/chat/conversations/{id}/messages` | 历史消息 |
| GET  | `/api/chat/query` | **提问 (SSE 流式)**，query: `conversation_id`/`question`/`scope_type`/`scope_ids`。必须 GET：前端用 EventSource(仅支持 GET) |
| POST | `/api/chat/feedback` | 答案点赞/踩 |

## 进阶 advanced
| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/review/generate` | Agentic 文献综述 (SSE)，query: `topic`/`scope_type`/`scope_ids`。同 chat/query，SSE 走 GET |
| GET  | `/api/graph/citations?paper_id=` | 引用图谱 (节点+边) |

## 可观测 observability
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/logs/queries` | 查询日志 (分页) |
| GET | `/api/logs/access` | 访问日志 |
| GET | `/api/stats/overview` | 论文数/chunk数/查询量/平均延迟 |

## 设置 settings
| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/settings` | 读取当前用户全局配置 (未保存返回 `{}`) |
| POST | `/api/settings` | 保存 RAG 开关/模型/检索超参 (按 user_id 存 Redis) |

## SSE 事件格式
```
event: token   data: {"delta":"..."}              # 流式 token
event: cite    data: {"paper_id":1,"page":2,"chunk_id":"...","image_key":"..."}
event: done    data: {"latency_ms":1234}
event: error   data: {"msg":"..."}
```
