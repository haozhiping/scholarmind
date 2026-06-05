# 任务4：对话与Agent综述 - 验证报告

**报告日期**: 2026-06-05  
**检查范围**: `backend/services/chat_agent/`  
**状态**: ✅ 实现完成（80%）

---

## 一、任务概述

任务4聚焦于构建对话与Agent综述服务，实现意图路由、多轮记忆、Agent综述生成和SSE流式输出四大核心功能。

### 1.1 任务目标

| 子任务 | 目标描述 | 关联模块 |
|-------|---------|---------|
| 意图路由 | 使用`intent_router`提示词进行分类分流，过滤闲聊 | `intent_router.py` |
| 多轮记忆 | 连接PostgreSQL，读取/保存会话历史 | `chat_service.py` |
| Agent综述生成 | 使用LlamaIndex Agent进行复杂综述/对比 | `agent.py` |
| SSE流式输出 | 通过FastAPI EventSourceResponse流式推送 | `chat_service.py` |

---

## 二、实现架构

```
用户请求 → IntentRouter → 分类决策
                              ↓
            ┌───────────────┴───────────────┐
            ▼                               ▼
      闲聊/直答                        知识检索/Agent
            ↓                               ↓
      LLM直接响应              HybridRetriever → Agent
                                          ↓
                               SSE流式生成 + 引用溯源
                                          ↓
                               PostgreSQL会话记忆 + Redis缓存
```

### 2.1 架构层次

| 层级 | 模块 | 职责 |
|-----|------|-----|
| **入口层** | `chat_service.py` | API接口、SSE流管理 |
| **路由层** | `intent_router.py` | 意图分类、请求分发 |
| **业务层** | `agent.py` | LlamaIndex Agent综述生成 |
| **数据层** | PostgreSQL | 会话历史存储 |
| **缓存层** | Redis | 短期会话缓存 |

---

## 三、核心模块设计

### 3.1 意图路由 (IntentRouter)

**功能定位**: 对用户输入进行意图分类，决定处理路径。

**意图类型**:

| 意图类型 | 描述 | 处理方式 |
|---------|------|---------|
| `chitchat` | 闲聊对话 | 直接调用LLM响应，不检索 |
| `knowledge` | 知识问答 | 调用Retrieval服务检索后生成 |
| `complex` | 复杂综述/对比 | 调用LlamaIndex Agent分解处理 |
| `followup` | 追问/上下文相关 | 携带历史会话进行检索 |

**核心逻辑**:
```python
async def route_intent(query: str, history: List[dict]) -> IntentResult:
    # 调用LLM进行意图分类
    # 返回意图类型和置信度
```

### 3.2 多轮记忆 (ConversationMemory)

**功能定位**: 管理会话生命周期，支持上下文感知对话。

**数据模型**:

| 表名 | 用途 | 关键字段 |
|-----|------|---------|
| `conversations` | 会话元数据 | `conv_id`, `user_id`, `created_at`, `updated_at` |
| `messages` | 消息历史 | `msg_id`, `conv_id`, `role`, `content`, `timestamp` |

**核心操作**:

| 操作 | 说明 |
|-----|------|
| `create_conversation()` | 创建新会话 |
| `add_message()` | 添加消息记录 |
| `get_history()` | 获取会话历史（按窗口大小截取） |
| `delete_conversation()` | 删除会话及所有消息 |

### 3.3 Agent综述生成 (ReviewAgent)

**功能定位**: 处理复杂综述请求，分解子查询并综合多篇论文。

**工作流程**:
1. **分析请求**: 理解用户的综述/对比需求
2. **子查询分解**: 将复杂问题拆分为多个子问题
3. **多篇检索**: 对每个子问题进行文献检索
4. **综合生成**: 使用`review_generation`提示词生成带引用的综述

**输出格式**:
- 结构化文本输出
- 引用角标`[n]`标记
- 参考文献列表

### 3.4 SSE流式输出 (SSEStream)

**功能定位**: 实现实时流式响应，支持引用事件推送。

**事件类型**:

| 事件类型 | 数据结构 | 说明 |
|---------|---------|------|
| `token` | `{"content": "..."})` | 文本片段 |
| `cite` | `{"paper_id": "...", "page": N, "chunk_id": "...", "image_key": "..."}` | 引用信息 |
| `done` | `{"finish_reason": "..."}` | 完成信号 |
| `error` | `{"error": "..."}` | 错误信息 |

---

## 四、API接口设计

### 4.1 对话接口

| 接口 | 方法 | 路径 | 说明 |
|-----|------|-----|------|
| 发起对话 | POST | `/api/chat/query` | SSE流式响应 |
| 创建会话 | POST | `/api/chat/conversations` | 创建新会话 |
| 获取会话列表 | GET | `/api/chat/conversations` | 获取用户会话列表 |
| 获取会话详情 | GET | `/api/chat/conversations/{conv_id}` | 获取单会话消息 |
| 删除会话 | DELETE | `/api/chat/conversations/{conv_id}` | 删除会话 |

### 4.2 综述接口

| 接口 | 方法 | 路径 | 说明 |
|-----|------|-----|------|
| 生成综述 | POST | `/api/review/generate` | SSE流式响应 |

### 4.3 请求/响应示例

**POST /api/chat/query**

请求体:
```json
{
  "conv_id": "uuid-string",
  "query": "什么是深度学习？",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "您好！请问有什么可以帮助您的？"}
  ]
}
```

SSE响应流:
```
event: token
data: {"content": "深"}

event: token
data: {"content": "度学习是"}

event: cite
data: {"paper_id": "abc123", "page": 5, "chunk_id": "chunk_001"}

event: token
data: {"content": "一种机器学习方法"}

event: done
data: {"finish_reason": "stop"}
```

---

## 五、文件清单

| 文件路径 | 说明 | 状态 |
|---------|------|------|
| `backend/services/chat_agent/__init__.py` | 模块入口 | ✅ 已创建 |
| `backend/services/chat_agent/intent_router.py` | 意图路由实现 | ✅ 已创建 |
| `backend/services/chat_agent/chat_service.py` | 对话服务与SSE | ✅ 已创建 |
| `backend/services/chat_agent/agent.py` | LlamaIndex Agent | ✅ 已创建 |
| `backend/services/chat_agent/schemas.py` | Pydantic模型 | ✅ 已创建 |
| `backend/services/chat_agent/prompts.py` | 提示词模板 | ✅ 已创建 |
| `backend/common/exceptions.py` | 统一异常处理 | ✅ 已创建 |
| `backend/common/db/redis_client.py` | Redis异步客户端 | ✅ 已创建 |
| `backend/prompts/*.md` | 提示词文件 | ✅ 已创建（6个） |

---

## 六、待完善项

| 优先级 | 问题 | 说明 | 状态 |
|-------|------|------|------|
| 🔴 高 | PostgreSQL连接配置 | 需配置`scholarmind_memory`库连接 | ✅ 已完成 |
| 🔴 高 | 意图路由提示词 | 需要定义`intent_router`提示词 | ✅ 已完成 |
| 🔴 高 | 综述生成提示词 | 需要定义`review_generation`提示词 | ✅ 已完成 |
| 🟡 中 | Redis缓存集成 | 短期会话窗口缓存 | ✅ 已完成 |
| 🟡 中 | 错误处理 | SSE异常处理和错误事件推送 | ✅ 已完成 |
| 🟢 低 | 单元测试 | 核心模块测试覆盖 | ⏳ 待完成 |
| 🟢 低 | 限流功能 | 请求限流保护 | ⏳ 待完成 |

---

## 七、错误处理完善情况

### 7.1 异常类型定义

| 异常类型 | HTTP状态码 | 用途 |
|---------|-----------|------|
| `DatabaseException` | 500 | 数据库操作失败 |
| `LLMException` | 503 | LLM服务不可用 |
| `RedisException` | 503 | Redis缓存失败 |
| `ValidationException` | 400 | 请求参数验证失败 |
| `NotFoundException` | 404 | 资源未找到 |
| `AuthException` | 401 | 认证失败 |
| `RateLimitException` | 429 | 请求超限 |

### 7.2 日志记录策略

| 日志级别 | 使用场景 | 示例 |
|---------|---------|------|
| `INFO` | 业务关键节点 | 会话创建、查询完成 |
| `DEBUG` | 详细调试信息 | 缓存命中、消息添加 |
| `WARNING` | 非致命异常 | 缓存失败回退 |
| `ERROR` | 致命错误 | 数据库操作失败 |

### 7.3 SSE错误事件

错误处理已集成到`chat_service.py`的流式输出中，通过`error`事件类型推送错误信息：

```json
{
  "event": "error",
  "data": {"message": "具体错误信息", "conv_id": "会话ID"}
}
```

---

## 八、结论

**状态**: ✅ **实现完成（80%）**

任务4的四大核心模块已全部实现：

1. ✅ **意图路由** - 支持chitchat/knowledge/complex/followup四类意图
2. ✅ **多轮记忆** - PostgreSQL存储会话历史，Redis缓存短期会话
3. ✅ **Agent综述生成** - LlamaIndex Agent支持复杂综述请求
4. ✅ **SSE流式输出** - 支持token/cite/done/error四种事件类型

**待完善项**:
- 单元测试覆盖
- 请求限流功能

**总体完成度**: 80%