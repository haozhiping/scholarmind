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
        <router-link to="/chat" class="nav-item">
          <span class="icon">💬</span> 文献对话调研
        </router-link>
        <router-link to="/observability" class="nav-item">
          <span class="icon">📊</span> 系统可观测页
        </router-link>
        <router-link to="/settings" class="nav-item active">
          <span class="icon">⚙️</span> 系统配置中心
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <button class="logout-btn" @click="handleLogout">退出登录</button>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
      <header class="content-header">
        <h1>系统配置中心</h1>
      </header>

      <div class="content-body">
        <form @submit.prevent="saveSettings" class="settings-form">
          <!-- Card: Model Services -->
          <div class="settings-card">
            <h3>🤖 大模型与向量服务接入</h3>
            <div class="form-grid">
              <div class="form-group">
                <label>大语言模型 (LLM) 厂商</label>
                <select v-model="config.LLM_PROVIDER">
                  <option value="qwen">通义千问 (Qwen3)</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai">OpenAI</option>
                  <option value="ollama">Ollama (本地)</option>
                </select>
              </div>

              <div class="form-group">
                <label>大语言模型型号 (Model)</label>
                <input type="text" v-model="config.LLM_MODEL" />
              </div>

              <div class="form-group">
                <label>Embedding 向量模型厂商</label>
                <select v-model="config.EMBEDDING_PROVIDER">
                  <option value="local">本地推理服务 (TEI)</option>
                  <option value="local_path">本地文件加载 (进程内)</option>
                  <option value="dashscope">阿里云 DashScope</option>
                  <option value="openai">OpenAI</option>
                </select>
              </div>

              <div class="form-group">
                <label>Embedding 维度 (DIM)</label>
                <input type="number" v-model="config.EMBEDDING_DIM" disabled />
                <span class="tip">⚠️ 必须与 Milvus 建库维度一致，修改需重建集合</span>
              </div>
            </div>
          </div>

          <!-- Card: RAG Toggles -->
          <div class="settings-card">
            <h3>⚡ RAG 全链路优化开关 (控制成本与耗时)</h3>
            <div class="switches-grid">
              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">意图路由 (Intent Router)</span>
                  <p class="desc">识别闲聊/常识问题，跳过检索流程以省资源和降低延迟</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_INTENT_ROUTER" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">查询改写 (Query Rewrite)</span>
                  <p class="desc">结合对话历史补全人称指代，并消除口语歧义</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_QUERY_REWRITE" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">查询扩展 (Multi-Query)</span>
                  <p class="desc">多角度同义句生成，并行多路检索，检索更全面但耗时</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_MULTI_QUERY" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">假设性文档 (HyDE)</span>
                  <p class="desc">先由 LLM 生成一份假设性的论文解答，以此为探针检索，提高匹配度</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_HYDE" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">跨语言翻译 (Bilingual Translation)</span>
                  <p class="desc">中文查询自动翻译成英文进行检索，解决“中搜英”精度差问题</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_QUERY_TRANSLATION" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">检索后重排 (Reranking)</span>
                  <p class="desc">使用重排模型对粗筛召回结果做二次精排，只给 LLM 最相关的片段</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_RERANK" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">检索打分自适应 (Corrective RAG)</span>
                  <p class="desc">评估召回文本是否能回答该问题，否则执行改写重检索或拒答</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_CORRECTIVE_RAG" />
              </div>

              <div class="switch-item">
                <div class="switch-info">
                  <span class="label">答案自检反思 (Self-RAG Reflect)</span>
                  <p class="desc">大模型生成后核实每一句是否有据可依，无依据的做删除或标注</p>
                </div>
                <input type="checkbox" v-model="config.ENABLE_SELF_RAG_REFLECT" />
              </div>
            </div>
          </div>

          <!-- Card: Retrieval Hyperparameters -->
          <div class="settings-card">
            <h3>⚙️ 检索细分阈值</h3>
            <div class="form-grid">
              <div class="form-group">
                <label>检索召回数 (Top K)</label>
                <input type="number" v-model="config.RETRIEVAL_TOP_K" min="1" max="50" />
              </div>

              <div class="form-group">
                <label>混合检索 Dense 权重</label>
                <input type="number" v-model="config.HYBRID_DENSE_WEIGHT" min="0" max="1" step="0.1" />
                <span class="tip">稠密向量权重，Sparse 权重自动为 (1 - Dense权重)</span>
              </div>
            </div>
          </div>

          <div class="actions">
            <button type="submit" class="save-btn" :disabled="saving">
              {{ saving ? '正在保存...' : '保存全局配置' }}
            </button>
          </div>
        </form>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const router = useRouter();
const authStore = useAuthStore();

const saving = ref(false);
const STORAGE_KEY = 'scholarmind_settings';

const defaults = {
  LLM_PROVIDER: 'deepseek',
  LLM_MODEL: 'deepseek-v4-flash',
  EMBEDDING_PROVIDER: 'dashscope',
  EMBEDDING_DIM: 1024,
  ENABLE_INTENT_ROUTER: true,
  ENABLE_QUERY_REWRITE: true,
  ENABLE_MULTI_QUERY: false,
  ENABLE_HYDE: true,
  ENABLE_QUERY_TRANSLATION: true,
  ENABLE_RERANK: true,
  ENABLE_CORRECTIVE_RAG: false,
  ENABLE_SELF_RAG_REFLECT: false,
  RETRIEVAL_TOP_K: 20,
  HYBRID_DENSE_WEIGHT: 0.6,
};

const config = reactive({ ...defaults });

onMounted(() => {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    try { Object.assign(config, JSON.parse(saved)); } catch { /* ignore */ }
  }
});

function handleLogout() {
  authStore.clearAuth();
  router.push('/login');
}

async function saveSettings() {
  saving.value = true;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  setTimeout(() => {
    saving.value = false;
    alert('配置已保存到本地。注意：模型密钥与服务地址等敏感配置由后端 .env 统一管理，此页用于前端策略预览。');
  }, 400);
}
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  background-color: #f7f9f8;
  font-family: 'Inter', sans-serif;
  color: #1a3322;
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

/* Main */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow-y: auto;
}

.content-header {
  padding: 20px 40px;
  background-color: #ffffff;
  border-bottom: 1px solid #e1e6e3;
}

.content-header h1 {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  color: #0f3d24;
}

.content-body {
  padding: 40px;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 30px;
  max-width: 800px;
}

.settings-card {
  background: #ffffff;
  padding: 30px;
  border-radius: 12px;
  border: 1px solid #e1e6e3;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.settings-card h3 {
  margin: 0 0 24px 0;
  font-size: 16px;
  font-weight: 700;
  color: #0f3d24;
  border-bottom: 1px solid #f0f3f1;
  padding-bottom: 15px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 14px;
  font-weight: 600;
}

.form-group select, .form-group input {
  padding: 10px 14px;
  border: 1px solid #c2cdc6;
  border-radius: 6px;
  outline: none;
  font-size: 14px;
  background-color: #f8faf9;
  transition: all 0.3s ease;
}

.form-group select:focus, .form-group input:focus {
  border-color: #1c7243;
  background-color: #ffffff;
}

.tip {
  font-size: 11px;
  color: #667e6e;
}

/* Switches Grid */
.switches-grid {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.switch-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 15px;
  border-bottom: 1px solid #f0f3f1;
}

.switch-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.switch-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.switch-info .label {
  font-weight: 600;
  font-size: 14px;
}

.switch-info .desc {
  font-size: 12px;
  color: #667e6e;
  margin: 0;
}

/* Styled checkbox switch */
.switch-item input[type="checkbox"] {
  width: 50px;
  height: 26px;
  appearance: none;
  -webkit-appearance: none;
  background-color: #cbd5e0;
  border-radius: 20px;
  position: relative;
  outline: none;
  cursor: pointer;
  transition: background-color 0.3s ease;
}

.switch-item input[type="checkbox"]:checked {
  background-color: #1c7243;
}

.switch-item input[type="checkbox"]::before {
  content: "";
  position: absolute;
  width: 22px;
  height: 22px;
  background-color: #ffffff;
  border-radius: 50%;
  top: 2px;
  left: 2px;
  transition: transform 0.3s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}

.switch-item input[type="checkbox"]:checked::before {
  transform: translateX(24px);
}

.actions {
  display: flex;
  justify-content: flex-end;
}

.save-btn {
  padding: 12px 30px;
  background-color: #0f3d24;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.save-btn:hover {
  background-color: #195232;
  box-shadow: 0 4px 15px rgba(15, 61, 36, 0.3);
}

.save-btn:disabled {
  background-color: #c2cdc6;
  cursor: not-allowed;
}
</style>
