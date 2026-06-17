# 任务5：前端页面与 API 联调设计

> 日期：2026-06-05 ｜ 目标文件：`frontend/src/api/index.ts` + 各视图组件
> 范围决策：将所有 Mock 数据替换为真实 API 调用；实现 SSE 流式对话；实现引用溯源；实现可观测可视化。

## 1. 背景与现状

- 前端页面已完成 UI 开发，使用 Mock 数据展示
- 需要对接后端 API 实现完整功能
- 后端 API 已完成开发，提供 RESTful 接口和 SSE 流式接口

## 2. 范围

### 做
1. **API 封装层**：创建统一的 Axios 封装，包含请求/响应拦截器
2. **认证接口**：登录/注册/获取用户信息
3. **文献管理**：文件上传、列表、删除、文件夹管理
4. **流式对话**：SSE 连接、事件解析、实时渲染
5. **引用溯源**：引用数据解析、预览展示
6. **可观测数据**：统计概览、活跃任务、查询日志
7. **配置管理**：全局设置保存

### 不做（YAGNI）
- 不修改路由配置
- 不修改 Pinia store 核心逻辑
- 不添加新页面

## 3. 架构设计

### 3.1 API 封装层

```
┌─────────────────────────────────────────────────────────────┐
│                     API 封装层                             │
├─────────────┬─────────────┬────────────────────────────────┤
│  authAPI    │ papersAPI   │ foldersAPI / ingestAPI         │
├─────────────┼─────────────┼────────────────────────────────┤
│  chatAPI    │ observAPI   │ settingsAPI                    │
└─────────────┴─────────────┴────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      Axios 实例                            │
│  ┌──────────────────┐  ┌──────────────────┐               │
│  │ 请求拦截器       │  │ 响应拦截器       │               │
│  │ 添加 JWT Token   │  │ 401 处理         │               │
│  └──────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 SSE 流式对话架构

```
┌──────────────────┐         ┌──────────────────┐
│   Chat.vue       │────────▶│    EventSource   │
│                  │         │                  │
│  • 发送问题      │◀────────│  • token 事件    │
│  • 渲染文本      │         │  • cite 事件     │
│  • 展示引用      │         │  • done 事件     │
│                  │         │  • error 事件    │
└──────────────────┘         └──────────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │   Backend SSE    │
                          │   /api/chat/stream│
                          └──────────────────┘
```

### 3.3 引用溯源机制

| 事件类型 | 数据结构 | 说明 |
|---|---|---|
| `cite` | `{paper_id, page_num, bbox, content, block_type}` | 引用块数据 |
| `token` | `{delta: string}` | 流式文本片段 |
| `done` | `{finish_reason}` | 完成信号 |
| `error` | `{message}` | 错误信息 |

## 4. API 接口清单

### 4.1 认证模块 (authAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| login | POST | `/api/auth/login` | 用户登录 |
| register | POST | `/api/auth/register` | 用户注册 |
| getProfile | GET | `/api/auth/profile` | 获取用户信息 |

### 4.2 论文管理 (papersAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| uploadPapers | POST | `/api/papers/upload` | 上传论文 |
| getPapers | GET | `/api/papers` | 获取论文列表 |
| getPaper | GET | `/api/papers/{id}` | 获取论文详情 |
| deletePaper | DELETE | `/api/papers/{id}` | 删除论文 |

### 4.3 文件夹管理 (foldersAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| getFolders | GET | `/api/folders` | 获取文件夹列表 |
| createFolder | POST | `/api/folders` | 创建文件夹 |
| deleteFolder | DELETE | `/api/folders/{id}` | 删除文件夹 |

### 4.4 解析进度 (ingestAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| getBatchProgress | GET | `/api/ingest/batches/{id}/progress` | 获取批次进度 |
| getTasks | GET | `/api/ingest/tasks` | 获取任务列表 |
| retryTask | POST | `/api/ingest/tasks/{id}/retry` | 重试任务 |

### 4.5 对话模块 (chatAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| createConversation | POST | `/api/chat/conversations` | 创建会话 |
| getConversations | GET | `/api/chat/conversations` | 获取会话列表 |
| getMessages | GET | `/api/chat/conversations/{id}/messages` | 获取消息历史 |
| streamQuery | GET (SSE) | `/api/chat/conversations/{id}/stream` | 流式查询 |
| sendFeedback | POST | `/api/chat/feedback` | 发送反馈 |

### 4.6 可观测模块 (observabilityAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| getQueryLogs | GET | `/api/logs/queries` | 获取查询日志 |
| getAccessLogs | GET | `/api/logs/access` | 获取访问日志 |
| getStatsOverview | GET | `/api/stats/overview` | 获取统计概览 |
| getActiveTasks | GET | `/api/observability/tasks` | 获取活跃任务 |

### 4.7 设置模块 (settingsAPI)

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| getSettings | GET | `/api/settings` | 获取设置 |
| saveSettings | POST | `/api/settings` | 保存设置 |

## 5. 数据流设计

### 5.1 登录流程

```
用户输入 → authAPI.login() → 后端验证 → 返回 Token → localStorage 存储 → 跳转首页
```

### 5.2 文件上传流程

```
选择文件 → papersAPI.uploadPapers() → 获取 batch_id → 定时轮询进度 → 更新 UI
```

### 5.3 流式对话流程

```
用户提问 → 创建会话 → 建立 SSE 连接 → 接收 token/cite 事件 → 实时渲染 → done 关闭连接
```

## 6. 错误处理矩阵

| 失败点 | 处理方式 |
|---|---|
| 401 未授权 | 清除 Token，跳转登录页 |
| 网络错误 | 显示错误提示 |
| API 超时 | 显示超时提示 |
| SSE 连接失败 | 重试连接，显示错误 |

## 7. 验收标准

- [ ] 所有页面 Mock 数据替换为真实 API 调用
- [ ] 登录/注册功能正常工作
- [ ] 文件上传与进度轮询正常工作
- [ ] SSE 流式对话正常工作
- [ ] 引用溯源功能正常（点击角标展示原文）
- [ ] 可观测数据实时更新
- [ ] 配置保存功能正常
- [ ] 401 错误自动跳转登录

## 8. 测试策略

### 单元测试
- API 封装层测试
- 响应拦截器测试

### 集成测试
- 完整登录流程测试
- 文件上传测试
- 对话功能测试

### 端到端测试
- 完整用户流程：登录 → 上传文献 → 对话 → 查看监控
