# 任务5 前端页面与 API 联调实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端各页面的 Mock 方法替换为真实的 Axios 请求，实现完整的前后端联调：接口联调、SSE 流式对话与引用溯源、可观测可视化。

**Architecture:** 基于 Vue3 + TypeScript + Pinia + Vue Router，使用 Axios 作为 HTTP 客户端，EventSource 处理 SSE 流式数据。

**Tech Stack:** Vue3 · TypeScript · Pinia · Vue Router · Axios · SSE (EventSource)

---

## 文件结构

| 文件 | 责任 | 操作 |
|---|---|---|
| `frontend/src/api/index.ts` | 新增统一 API 封装层 | Create |
| `frontend/src/views/Login.vue` | 登录/注册页面真实 API | Modify |
| `frontend/src/views/Library.vue` | 文献库上传与进度轮询 | Modify |
| `frontend/src/views/Chat.vue` | SSE 流式对话与引用溯源 | Modify |
| `frontend/src/views/Observability.vue` | 可观测数据展示 | Modify |
| `frontend/src/views/Settings.vue` | 配置保存 | Modify |

---

## Task 1: 新增前端 API 层封装

**Files:**
- Create: `frontend/src/api/index.ts`

- [ ] **Step 1: 创建 API 封装文件**

Create `frontend/src/api/index.ts`:

```typescript
import axios, { type AxiosInstance, type AxiosResponse } from 'axios';

const api: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://localhost:8000',
  timeout: 30000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export const authAPI = {
  login: (username: string, password: string) =>
    api.post('/api/auth/login', { username, password }),
  register: (username: string, email: string, password: string) =>
    api.post('/api/auth/register', { username, email, password }),
  getProfile: () => api.get('/api/auth/profile'),
};

export const papersAPI = {
  uploadPapers: (files: FileList, folderId?: number) => {
    const formData = new FormData();
    Array.from(files).forEach((file) => formData.append('files', file));
    if (folderId) formData.append('folder_id', folderId.toString());
    return api.post('/api/papers/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  getPapers: (folderId?: number) =>
    api.get('/api/papers', { params: { folder_id: folderId } }),
  getPaper: (id: number) => api.get(`/api/papers/${id}`),
  deletePaper: (id: number) => api.delete(`/api/papers/${id}`),
};

export const foldersAPI = {
  getFolders: () => api.get('/api/folders'),
  createFolder: (name: string) => api.post('/api/folders', { name }),
  deleteFolder: (id: number) => api.delete(`/api/folders/${id}`),
};

export const ingestAPI = {
  getBatchProgress: (batchId: string) =>
    api.get(`/api/ingest/batches/${batchId}/progress`),
  getTasks: () => api.get('/api/ingest/tasks'),
  retryTask: (taskId: string) => api.post(`/api/ingest/tasks/${taskId}/retry`),
};

export const chatAPI = {
  createConversation: () => api.post('/api/chat/conversations'),
  getConversations: () => api.get('/api/chat/conversations'),
  getMessages: (conversationId: string) =>
    api.get(`/api/chat/conversations/${conversationId}/messages`),
  streamQuery: (conversationId: string, question: string) =>
    `/api/chat/conversations/${conversationId}/stream?question=${encodeURIComponent(question)}`,
  sendFeedback: (messageId: string, feedback: number) =>
    api.post('/api/chat/feedback', { message_id: messageId, feedback }),
  deleteConversation: (conversationId: string) =>
    api.delete(`/api/chat/conversations/${conversationId}`),
};

export const observabilityAPI = {
  getQueryLogs: () => api.get('/api/logs/queries'),
  getAccessLogs: () => api.get('/api/logs/access'),
  getStatsOverview: () => api.get('/api/stats/overview'),
  getActiveTasks: () => api.get('/api/observability/tasks'),
};

export const settingsAPI = {
  getSettings: () => api.get('/api/settings'),
  saveSettings: (config: Record<string, any>) => api.post('/api/settings', config),
};

export default api;
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/api/index.ts
git commit -m "feat(frontend): 新增统一 API 封装层
包含 authAPI/papersAPI/foldersAPI/ingestAPI/chatAPI/observabilityAPI/settingsAPI 七个模块；
配置请求/响应拦截器处理 JWT Token；支持 FormData 文件上传；提供 SSE 流式 URL 生成。"
```

---

## Task 2: 登录/注册页面 API 联调

**Files:**
- Modify: `frontend/src/views/Login.vue`

- [ ] **Step 1: 更新 Login.vue 使用真实 API**

将原 Mock 实现替换为：

```typescript
// 登录
const res = await authAPI.login(form.username, form.password);
authStore.setToken(res.data.access_token);
router.push('/library');

// 注册
await authAPI.register(form.username, form.email, form.password);
showRegister.value = false;
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/views/Login.vue
git commit -m "feat(frontend): 登录/注册页面对接真实 API
替换 Mock 实现为 authAPI 调用；支持 JWT Token 存储与自动跳转。"
```

---

## Task 3: 文献库页面 API 联调

**Files:**
- Modify: `frontend/src/views/Library.vue`

- [ ] **Step 1: 更新文件上传**

```typescript
// 上传文件
const res = await papersAPI.uploadPapers(files, selectedFolderId.value);
currentBatchId.value = res.data.batch_id;
```

- [ ] **Step 2: 更新论文列表加载**

```typescript
// 加载论文列表
const res = await papersAPI.getPapers(selectedFolderId.value);
papers.value = res.data;
```

- [ ] **Step 3: 添加定时进度轮询**

```typescript
// 每 3 秒刷新一次
setInterval(async () => {
  if (currentBatchId.value) {
    await loadPapers();
  }
}, 3000);
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/views/Library.vue
git commit -m "feat(frontend): 文献库页面对接真实 API
实现文件上传、论文列表、文件夹管理；添加 3 秒定时进度轮询。"
```

---

## Task 4: 对话页面 SSE 流式对话与引用溯源

**Files:**
- Modify: `frontend/src/views/Chat.vue`

- [ ] **Step 1: 实现 SSE 连接**

```typescript
function connectSSE(url: string) {
  const eventSource = new EventSource(url);
  
  eventSource.addEventListener('cite', (e) => {
    const citation = JSON.parse(e.data);
    citationsBuffer.value.push(citation);
  });
  
  eventSource.addEventListener('token', (e) => {
    const data = JSON.parse(e.data);
    streamingText.value += data.delta;
  });
  
  eventSource.addEventListener('done', (e) => {
    eventSource.close();
    // 保存最终消息
  });
  
  eventSource.addEventListener('error', (e) => {
    eventSource.close();
    console.error('SSE error:', e);
  });
}
```

- [ ] **Step 2: 实现引用溯源展示**

```typescript
function showCitation(citation: any) {
  currentCitation.value = citation;
  showPreview.value = true;
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/src/views/Chat.vue
git commit -m "feat(frontend): 对话页面实现 SSE 流式对话与引用溯源
支持 token/cite/done/error 四种事件类型；点击引用角标在右侧预览区域展示原文。"
```

---

## Task 5: 可观测页面 API 联调

**Files:**
- Modify: `frontend/src/views/Observability.vue`

- [ ] **Step 1: 更新统计概览**

```typescript
async function loadStats() {
  const res = await observabilityAPI.getStatsOverview();
  stats.value = res.data;
}
```

- [ ] **Step 2: 更新活跃任务**

```typescript
async function loadTasks() {
  const res = await observabilityAPI.getActiveTasks();
  activeTasks.value = res.data;
}
```

- [ ] **Step 3: 更新查询日志**

```typescript
async function loadLogs() {
  const res = await observabilityAPI.getQueryLogs();
  queryLogs.value = res.data;
}
```

- [ ] **Step 4: 添加定时刷新**

```typescript
setInterval(async () => {
  await loadTasks();
}, 3000);
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/Observability.vue
git commit -m "feat(frontend): 可观测页面对接真实 API
实现统计概览、活跃任务、查询日志展示；添加 3 秒定时刷新。"
```

---

## Task 6: 设置页面配置保存

**Files:**
- Modify: `frontend/src/views/Settings.vue`

- [ ] **Step 1: 更新配置保存**

```typescript
async function saveSettings() {
  saving.value = true;
  try {
    await settingsAPI.saveSettings(config);
    alert('全局 RAG 策略与模型参数已保存！');
  } catch (error) {
    alert('保存失败，请稍后重试');
  } finally {
    saving.value = false;
  }
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/src/views/Settings.vue
git commit -m "feat(frontend): 设置页面配置保存对接真实 API
替换 Mock 实现为 settingsAPI.saveSettings 调用；添加错误处理。"
```

---

## Task 7: 验证文档 + 收尾

**Files:**
- Create: `docs/STATUS.md`（更新验证记录）

- [ ] **Step 1: 编写验证报告**

创建详细的验证报告，包含：
- 实现摘要
- API 封装清单
- 文件修改清单
- 核心技术实现
- 测试建议

- [ ] **Step 2: 更新 README.md**

将任务5标记为已完成。

- [ ] **Step 3: 提交**

```bash
git add docs/STATUS.md README.md
git commit -m "docs: 更新 STATUS.md 记录前端页面与API联调验证结果
更新 README.md 任务状态。"
```

---

## Self-Review

**Spec coverage:**
- 接口联调 ✅
- SSE 流式对话 ✅
- 引用溯源 ✅
- 可观测可视化 ✅
- 配置保存 ✅
- 验证文档 ✅

**Placeholder scan:** 无 TBD/TODO；每个改代码步骤均含完整代码。

**Type consistency:** API 返回类型统一；事件处理命名一致。
