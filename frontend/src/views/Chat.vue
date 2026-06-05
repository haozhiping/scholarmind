<template>
  <div class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>文渊 · ScholarMind</h2>
      </div>
      <nav class="nav-menu">
        <router-link to="/library" class="nav-item">
          <span class="icon">📚</span> 论文文献库
        </router-link>
        <router-link to="/chat" class="nav-item active">
          <span class="icon">💬</span> 文献对话调研
        </router-link>
        <router-link to="/observability" class="nav-item">
          <span class="icon">📊</span> 系统可观测页
        </router-link>
        <router-link to="/settings" class="nav-item">
          <span class="icon">⚙️</span> 系统配置中心
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <button class="logout-btn" @click="handleLogout">退出登录</button>
      </div>
    </aside>

    <!-- Chat & Preview Panel Layout -->
    <div class="chat-container-wrapper">
      <!-- Main Chat Area -->
      <div class="chat-panel">
        <header class="panel-header">
          <div class="header-info">
            <h1>文献智能调研对话</h1>
            <span class="scope-badge">📖 检索范围: 当前知识库 (全部论文)</span>
          </div>
        </header>

        <!-- Message List -->
        <div class="message-list" ref="messageListRef">
          <div 
            v-for="msg in messages" 
            :key="msg.id" 
            :class="['message-item', msg.role]"
          >
            <div class="avatar-box">
              {{ msg.role === 'user' ? '👤' : '🤖' }}
            </div>
            <div class="message-bubble">
              <div class="message-sender">
                {{ msg.role === 'user' ? '提问者' : '文渊 AI 助手' }}
              </div>
              <div class="message-content" v-html="msg.content"></div>
              
              <!-- Citations List at bottom of assistant message -->
              <div v-if="msg.role === 'assistant' && msg.citations?.length" class="citations-footer">
                <span class="cite-label">📍 引用出处:</span>
                <button 
                  v-for="(cite, idx) in msg.citations" 
                  :key="idx" 
                  class="cite-badge-btn"
                  @click="showCitationDetail(cite)"
                >
                  [{{ idx + 1 }}] {{ cite.paper_title }} (P.{{ cite.page_num }})
                </button>
              </div>
            </div>
          </div>

          <div v-if="streaming" class="message-item assistant typing">
            <div class="avatar-box">🤖</div>
            <div class="message-bubble">
              <div class="message-sender">文渊 AI 助手</div>
              <div class="message-content">
                {{ streamingText }}<span class="cursor">|</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Input Area -->
        <footer class="input-panel">
          <form @submit.prevent="sendMessage" class="input-form">
            <input 
              type="text" 
              v-model="inputQuery" 
              placeholder="请输入您的学术调研问题..." 
              :disabled="streaming"
            />
            <button type="submit" class="send-btn" :disabled="streaming || !inputQuery.trim()">
              发送
            </button>
          </form>
        </footer>
      </div>

      <!-- Right Column: Document Source Viewer (Citations/Figures Details) -->
      <div class="preview-panel">
        <header class="panel-header">
          <h2>🔍 引用溯源与图表回显</h2>
        </header>

        <div class="preview-body">
          <div v-if="activeCitation" class="citation-detail-card">
            <div class="citation-meta">
              <span class="tag">PDF 原文引用</span>
              <h3>{{ activeCitation.paper_title }}</h3>
              <p class="page-info">第 {{ activeCitation.page_num }} 页 (位置框: {{ activeCitation.bbox }})</p>
            </div>

            <!-- Block Content -->
            <div class="block-content-box">
              <div class="box-title">📄 召回块内容 (类型: {{ blockTypeMap[activeCitation.chunk_type] }})</div>
              
              <!-- Text Block -->
              <p v-if="activeCitation.chunk_type === 'text'">
                {{ activeCitation.content }}
              </p>

              <!-- Table Block -->
              <div 
                v-else-if="activeCitation.chunk_type === 'table'"
                class="html-table-viewer" 
                v-html="activeCitation.content"
              ></div>

              <!-- Figure Block -->
              <div v-else-if="activeCitation.chunk_type === 'figure'" class="image-viewer">
                <div class="mock-image">
                  🖼️ [图表 Key: {{ activeCitation.image_key }}]
                  <p class="img-caption">{{ activeCitation.content }}</p>
                </div>
              </div>

              <!-- Formula Block -->
              <div v-else-if="activeCitation.chunk_type === 'formula'" class="formula-viewer">
                <code>{{ activeCitation.content }}</code>
              </div>
            </div>

            <button class="clear-cite-btn" @click="activeCitation = null">关闭预览</button>
          </div>

          <div v-else class="preview-empty">
            <span class="icon">📍</span>
            <p>在左侧对话中，点击 AI 回答底部的引用来源或文本中的引用序号，可在此处查看对应论文的段落原文、HTML表格、公式或者插图。</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { chatAPI } from '../api';

interface Citation {
  paper_id: number;
  paper_title: string;
  page_num: number;
  bbox: string;
  chunk_type: string;
  content: string;
  image_key?: string;
}

interface Message {
  id: number;
  role: string;
  content: string;
  citations: Citation[];
}

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();

const inputQuery = ref('');
const streaming = ref(false);
const streamingText = ref('');
const messageListRef = ref<HTMLDivElement | null>(null);

const activeCitation = ref<Citation | null>(null);
const currentConversationId = ref<number | null>(null);
const citationsBuffer = ref<Citation[]>([]);

const blockTypeMap: Record<string, string> = {
  text: '段落文本',
  table: '表格数据',
  figure: '插图/图表',
  formula: '学术公式',
};

const messages = ref<Message[]>([
  {
    id: 1,
    role: 'assistant',
    content: '您好！我是您的跨语言文献调研助手"文渊"。请问有什么关于论文、公式或图表的问题我可以帮您解答？',
    citations: [],
  },
]);

onMounted(async () => {
  // Check if coming from paper selection
  if (route.query.paperId) {
    // Could auto-create conversation with this paper
    console.log('Paper selected:', route.query.paperId);
  }
  
  // Try to create or load conversation
  await initConversation();
});

async function initConversation() {
  try {
    // Create a new conversation
    const res = await chatAPI.createConversation('新会话');
    currentConversationId.value = res.data.id;
  } catch (error: any) {
    console.error('Failed to create conversation:', error);
  }
}

function handleLogout() {
  authStore.clearAuth();
  router.push('/login');
}

function showCitationDetail(cite: Citation) {
  activeCitation.value = cite;
}

async function sendMessage() {
  if (!inputQuery.value.trim() || streaming.value) return;
  
  if (!currentConversationId.value) {
    alert('请先创建会话');
    return;
  }

  const userText = inputQuery.value;
  inputQuery.value = '';
  
  // Reset citations buffer
  citationsBuffer.value = [];

  // 1. Add User Message
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: userText,
    citations: [],
  });

  await scrollToBottom();

  // 2. Connect to SSE stream
  streaming.value = true;
  streamingText.value = '';
  
  const sseUrl = chatAPI.getQuerySSEUrl(currentConversationId.value, userText);
  connectSSE(sseUrl);
}

function connectSSE(url: string) {
  const eventSource = new EventSource(url);
  
  eventSource.onmessage = (e) => {
    // Default message handler (shouldn't happen with proper SSE)
    console.log('SSE message:', e.data);
  };
  
  eventSource.addEventListener('cite', (e) => {
    try {
      const citation: Citation = JSON.parse(e.data);
      citationsBuffer.value.push(citation);
      console.log('Received citation:', citation);
    } catch (error) {
      console.error('Failed to parse citation:', error);
    }
  });
  
  eventSource.addEventListener('token', (e) => {
    try {
      const data = JSON.parse(e.data);
      const delta = data.delta || '';
      streamingText.value += delta;
      scrollToBottom();
    } catch (error) {
      console.error('Failed to parse token:', error);
    }
  });
  
  eventSource.addEventListener('done', (e) => {
    try {
      const data = JSON.parse(e.data);
      console.log('Stream completed:', data);
      
      // Close SSE connection
      eventSource.close();
      
      // Save final message with citations
      const finalContent = processCitationsInText(streamingText.value, citationsBuffer.value);
      
      messages.value.push({
        id: Date.now() + 1,
        role: 'assistant',
        content: finalContent,
        citations: [...citationsBuffer.value],
      });
      
      streamingText.value = '';
      streaming.value = false;
      scrollToBottom();
    } catch (error) {
      console.error('Failed to parse done event:', error);
      eventSource.close();
      streaming.value = false;
    }
  });
  
  eventSource.addEventListener('error', (e) => {
    console.error('SSE error:', e);
    eventSource.close();
    streaming.value = false;
    alert('连接中断，请稍后重试');
  });
}

// Process text to add clickable citation markers
function processCitationsInText(text: string, citations: Citation[]): string {
  // Simple version: just return the text as-is
  // Citations are shown in the footer already
  // Advanced version could parse [1], [2] markers and make them clickable
  return text;
}

async function scrollToBottom() {
  await nextTick();
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight;
  }
}
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  background-color: #f7f9f8;
  font-family: 'Inter', sans-serif;
  color: #1a3322;
  overflow: hidden;
}

/* Sidebar */
.sidebar {
  width: 260px;
  background-color: #0f3d24;
  color: #ffffff;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 30px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.sidebar-header h2 {
  font-size: 20px;
  font-weight: 700;
  margin: 0;
  background: linear-gradient(135deg, #a2f26d 0%, #ffffff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.nav-menu {
  flex: 1;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  color: rgba(255, 255, 255, 0.75);
  text-decoration: none;
  font-weight: 600;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.nav-item:hover, .nav-item.active {
  color: #ffffff;
  background-color: rgba(255, 255, 255, 0.12);
}

.sidebar-footer {
  padding: 24px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.logout-btn {
  width: 100%;
  padding: 10px;
  background-color: transparent;
  color: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.logout-btn:hover {
  color: #ffffff;
  border-color: #ffffff;
  background-color: rgba(255, 255, 255, 0.05);
}

/* Chat & Preview Split Layout */
.chat-container-wrapper {
  flex: 1;
  display: flex;
  height: 100%;
}

/* Chat Panel */
.chat-panel {
  flex: 3;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border-right: 1px solid #e1e6e3;
  height: 100%;
}

.panel-header {
  padding: 20px 30px;
  border-bottom: 1px solid #e1e6e3;
  background-color: #ffffff;
}

.panel-header h1 {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 5px 0;
  color: #0f3d24;
}

.scope-badge {
  font-size: 12px;
  color: #667e6e;
  font-weight: 500;
}

.message-list {
  flex: 1;
  padding: 30px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.message-item {
  display: flex;
  gap: 16px;
  max-width: 85%;
}

.message-item.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-item.assistant {
  align-self: flex-start;
}

.avatar-box {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background-color: #e2ece7;
  display: flex;
  justify-content: center;
  align-items: center;
  font-size: 20px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
}

.message-item.user .avatar-box {
  background-color: #a2f26d;
}

.message-bubble {
  padding: 16px 20px;
  border-radius: 12px;
  background-color: #f3f6f4;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02);
}

.message-item.user .message-bubble {
  background-color: #0f3d24;
  color: #ffffff;
}

.message-sender {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 8px;
  color: #667e6e;
}

.message-item.user .message-sender {
  color: #a2f26d;
  text-align: right;
}

.message-content {
  font-size: 14px;
  line-height: 1.6;
}

.message-content :deep(strong) {
  font-weight: 700;
}

.citations-footer {
  margin-top: 15px;
  padding-top: 12px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.cite-label {
  font-size: 12px;
  font-weight: 700;
  color: #556c5c;
}

.cite-badge-btn {
  padding: 4px 10px;
  background-color: #e2ece7;
  border: 1px solid #c2cdc6;
  border-radius: 20px;
  color: #0f3d24;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.cite-badge-btn:hover {
  background-color: #0f3d24;
  color: #ffffff;
}

.cursor {
  font-weight: bold;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  from, to { color: transparent }
  50% { color: #000 }
}

.input-panel {
  padding: 20px 30px;
  border-top: 1px solid #e1e6e3;
  background-color: #ffffff;
}

.input-form {
  display: flex;
  gap: 12px;
}

.input-form input {
  flex: 1;
  padding: 14px 20px;
  border: 1px solid #c2cdc6;
  border-radius: 8px;
  outline: none;
  font-size: 14px;
  background-color: #f7f9f8;
  transition: all 0.3s ease;
}

.input-form input:focus {
  border-color: #1c7243;
  background-color: #ffffff;
}

.send-btn {
  padding: 0 24px;
  background-color: #0f3d24;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.send-btn:hover {
  background-color: #195232;
}

.send-btn:disabled {
  background-color: #c2cdc6;
  cursor: not-allowed;
}

/* Preview Panel */
.preview-panel {
  flex: 2;
  display: flex;
  flex-direction: column;
  background-color: #f7f9f8;
  height: 100%;
}

.preview-panel h2 {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
  color: #0f3d24;
}

.preview-body {
  flex: 1;
  padding: 30px;
  overflow-y: auto;
}

.preview-empty {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 80%;
  text-align: center;
  color: #667e6e;
}

.preview-empty .icon {
  font-size: 40px;
  margin-bottom: 20px;
}

.preview-empty p {
  font-size: 13px;
  line-height: 1.6;
}

.citation-detail-card {
  background: #ffffff;
  padding: 24px;
  border-radius: 12px;
  border: 1px solid #e1e6e3;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.citation-meta h3 {
  margin: 8px 0 4px 0;
  font-size: 16px;
  color: #0f3d24;
}

.citation-meta .tag {
  display: inline-block;
  padding: 2px 8px;
  background-color: #e2ece7;
  color: #0f6c2c;
  font-size: 11px;
  font-weight: 600;
  border-radius: 4px;
}

.page-info {
  font-size: 12px;
  color: #667e6e;
  margin: 0;
}

.block-content-box {
  padding: 16px;
  background-color: #f8faf9;
  border-radius: 8px;
  border-left: 4px solid #1c7243;
}

.box-title {
  font-size: 12px;
  font-weight: 700;
  color: #556c5c;
  margin-bottom: 10px;
}

.block-content-box p {
  font-size: 14px;
  line-height: 1.6;
  margin: 0;
}

.html-table-viewer {
  font-size: 13px;
  overflow-x: auto;
}

.html-table-viewer :deep(.mock-table) {
  width: 100%;
  border-collapse: collapse;
}

.html-table-viewer :deep(.mock-table th), .html-table-viewer :deep(.mock-table td) {
  border: 1px solid #c2cdc6;
  padding: 8px;
  text-align: left;
}

.image-viewer .mock-image {
  background-color: #edf2f0;
  border: 1px dashed #b8c7be;
  padding: 30px 10px;
  text-align: center;
  border-radius: 6px;
  font-weight: 600;
}

.img-caption {
  font-size: 12px;
  color: #667e6e;
  font-weight: normal;
  margin-top: 15px;
}

.clear-cite-btn {
  padding: 10px;
  background-color: #f0f3f1;
  color: #1a3322;
  border: 1px solid #c2cdc6;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.clear-cite-btn:hover {
  background-color: #e2ece7;
}
</style>
