import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://localhost:8000',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor to attach JWT token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token');
      // Redirect to login page
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// ==================== Auth APIs ====================
export const authAPI = {
  login: (username: string, password: string) => 
    api.post('/api/auth/login', { username, password }),
  
  register: (username: string, email: string, password: string) => 
    api.post('/api/auth/register', { username, email, password }),
  
  getMe: () => 
    api.get('/api/auth/me'),
};

// ==================== Papers APIs ====================
export const papersAPI = {
  // Upload papers (returns batch_id and task_ids)
  uploadPapers: (files: FileList, folderId?: number) => {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }
    if (folderId) {
      formData.append('folder_id', folderId.toString());
    }
    return api.post('/api/papers/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  
  // Get papers list
  listPapers: (folderId?: number, status?: string) => {
    const params: Record<string, any> = {};
    if (folderId !== undefined) params.folder_id = folderId;
    if (status) params.status = status;
    return api.get('/api/papers', { params });
  },
  
  // Get paper detail
  getPaper: (id: number) => 
    api.get(`/api/papers/${id}`),
  
  // Delete paper
  deletePaper: (id: number) => 
    api.delete(`/api/papers/${id}`),
};

// ==================== Folders APIs ====================
export const foldersAPI = {
  listFolders: () => 
    api.get('/api/folders'),
  
  createFolder: (name: string, parentId?: number) => 
    api.post('/api/folders', { name, parent_id: parentId }),
  
  deleteFolder: (id: number) => 
    api.delete(`/api/folders/${id}`),
};

// ==================== Ingest APIs ====================
export const ingestAPI = {
  // Get batch progress
  getBatchProgress: (batchId: string) => 
    api.get(`/api/ingest/batches/${batchId}`),
  
  // Get tasks list
  listTasks: (batchId?: string) => {
    const params: Record<string, any> = {};
    if (batchId) params.batch_id = batchId;
    return api.get('/api/ingest/tasks', { params });
  },
  
  // Retry failed task
  retryTask: (id: string) => 
    api.post(`/api/ingest/tasks/${id}/retry`),
};

// ==================== Chat APIs ====================
export const chatAPI = {
  // Create conversation
  createConversation: (title?: string, folderId?: number, paperIds?: number[]) => 
    api.post('/api/chat/conversations', { title, folder_id: folderId, paper_ids: paperIds }),
  
  // Get conversations list
  listConversations: () => 
    api.get('/api/chat/conversations'),
  
  // Get conversation messages
  getMessages: (conversationId: number) => 
    api.get(`/api/chat/conversations/${conversationId}/messages`),
  
  // SSE chat query (returns EventSource URL)
  getQuerySSEUrl: (conversationId: number, question: string, scopeType: string = 'all', scopeIds?: number[]) => {
    const params = new URLSearchParams();
    params.append('conversation_id', conversationId.toString());
    params.append('question', question);
    params.append('scope_type', scopeType);
    if (scopeIds) {
      params.append('scope_ids', scopeIds.join(','));
    }
    return `${api.defaults.baseURL}/api/chat/query?${params.toString()}`;
  },
  
  // Send feedback
  sendFeedback: (messageId: number, isPositive: boolean, reason?: string) => 
    api.post('/api/chat/feedback', { message_id: messageId, is_positive: isPositive, reason }),
};

// ==================== Observability APIs ====================
export const observabilityAPI = {
  // Get query logs
  getQueryLogs: (limit: number = 10, offset: number = 0) => 
    api.get('/api/logs/queries', { params: { limit, offset } }),
  
  // Get access logs
  getAccessLogs: (limit: number = 10, offset: number = 0) => 
    api.get('/api/logs/access', { params: { limit, offset } }),
  
  // Get stats overview
  getStatsOverview: () => 
    api.get('/api/stats/overview'),
  
  // Get active tasks (for ingestion progress)
  getActiveTasks: () => 
    api.get('/api/ingest/tasks'),
};

// ==================== Settings APIs ====================
export const settingsAPI = {
  // Save global settings
  saveSettings: (config: Record<string, any>) => 
    api.post('/api/settings', config),
};

export default api;
