# 对话与Agent综述服务完成度评估

**评估日期**: 2026-06-05  
**评估范围**: `backend/services/chat_agent/`

---

## 一、任务概述

任务4聚焦于构建对话与Agent综述服务，包含四大核心功能模块：

| # | 任务 | 状态 | 完成度 | 关键产出 |
|---|------|------|--------|----------|
| 1 | 意图路由 | 📋 设计中 | 20% | `intent_router.py` |
| 2 | 多轮记忆 | 📋 设计中 | 20% | `chat_service.py` |
| 3 | Agent综述生成 | 📋 设计中 | 20% | `agent.py` |
| 4 | SSE流式输出 | 📋 设计中 | 20% | `chat_service.py` |

---

## 二、详细评估

### 2.1 意图路由

| 指标 | 当前状态 | 评估说明 |
|-----|---------|---------|
| 意图分类 | 📋 设计 | chitchat/knowledge/complex/followup |
| 提示词定义 | ❌ 未开始 | `intent_router`提示词待定义 |
| LLM集成 | ❌ 未开始 | 调用LLM进行分类 |
| 置信度判断 | ❌ 未开始 | 根据置信度决定处理路径 |

### 2.2 多轮记忆

| 指标 | 当前状态 | 评估说明 |
|-----|---------|---------|
| PostgreSQL连接 | ❌ 未开始 | `scholarmind_memory`库配置 |
| 会话表设计 | 📋 设计 | conversations/messages表结构 |
| 历史截取策略 | 📋 设计 | 窗口大小控制 |
| Redis缓存 | ❌ 未开始 | 短期会话缓存 |

### 2.3 Agent综述生成

| 指标 | 当前状态 | 评估说明 |
|-----|---------|---------|
| LlamaIndex集成 | ❌ 未开始 | Agent框架集成 |
| 子查询分解 | 📋 设计 | 复杂问题拆解逻辑 |
| 多篇检索 | ❌ 未开始 | 调用Retrieval服务 |
| 综述提示词 | ❌ 未开始 | `review_generation`提示词 |

### 2.4 SSE流式输出

| 指标 | 当前状态 | 评估说明 |
|-----|---------|---------|
| EventSourceResponse | 📋 设计 | FastAPI流式响应 |
| token事件 | 📋 设计 | 文本片段推送 |
| cite事件 | 📋 设计 | 引用信息推送 |
| done/error事件 | 📋 设计 | 完成/错误信号 |

---

## 三、优先级待办事项

### 🔴 高优先级（立即执行）

| # | 任务 | 负责人 | 预计时间 |
|---|------|--------|---------|
| 1 | 定义`intent_router`提示词模板 | - | 1天 |
| 2 | 定义`review_generation`提示词模板 | - | 1天 |
| 3 | 配置PostgreSQL连接 | - | 0.5天 |
| 4 | 创建核心代码文件结构 | - | 2天 |

### 🟡 中优先级（1-2周）

| # | 任务 | 负责人 | 预计时间 |
|---|------|--------|---------|
| 1 | 实现意图路由逻辑 | - | 2天 |
| 2 | 实现会话记忆CRUD | - | 2天 |
| 3 | 实现SSE流式输出 | - | 2天 |
| 4 | 集成LlamaIndex Agent | - | 3天 |

### 🟢 低优先级（1个月+）

| # | 任务 | 负责人 | 预计时间 |
|---|------|--------|---------|
| 1 | 添加单元测试 | - | 2天 |
| 2 | 性能优化 | - | 3天 |
| 3 | 监控指标集成 | - | 2天 |

---

## 四、代码文件清单

| 文件路径 | 状态 | 说明 |
|---------|------|------|
| `backend/services/chat_agent/__init__.py` | ❌ | 模块入口 |
| `backend/services/chat_agent/intent_router.py` | ❌ | 意图路由实现 |
| `backend/services/chat_agent/chat_service.py` | ❌ | 对话服务与SSE |
| `backend/services/chat_agent/agent.py` | ❌ | LlamaIndex Agent |
| `backend/services/chat_agent/schemas.py` | ❌ | Pydantic模型 |
| `backend/services/chat_agent/prompts.py` | ❌ | 提示词模板 |

---

## 五、依赖清单

| 依赖 | 版本 | 用途 |
|-----|------|-----|
| `llama-index-core` | ^0.10.0 | Agent框架 |
| `llama-index-llms-qwen` | ^0.1.0 | Qwen LLM集成 |
| `asyncpg` | ^0.29.0 | PostgreSQL异步驱动 |
| `redis` | ^5.0.0 | Redis缓存 |

---

## 六、结论

**当前状态**: 📋 **方案设计阶段**

任务4的架构设计已完成，核心模块的功能定位和接口设计清晰。下一步需要：

1. **立即**: 配置数据库连接和定义提示词模板
2. **短期**: 实现核心代码文件
3. **长期**: 完善测试和监控

**建议**: 先完成数据库配置和提示词定义，再逐步实现各个核心模块。