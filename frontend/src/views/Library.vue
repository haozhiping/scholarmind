<template>
  <div class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>文渊 · ScholarMind</h2>
      </div>
      <nav class="nav-menu">
        <router-link to="/library" class="nav-item active">
          <span class="icon">📚</span> 论文文献库
        </router-link>
        <router-link to="/chat" class="nav-item">
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

    <!-- Main Content Area -->
    <main class="main-content">
      <header class="content-header">
        <h1>论文文献库</h1>
        <div class="user-profile">
          <span class="avatar">👤</span>
          <span class="username">项目成员</span>
        </div>
      </header>

      <div class="content-body">
        <!-- Folders & Upload Control Pane -->
        <div class="control-panel">
          <!-- Folders list -->
          <div class="folders-section">
            <div class="section-title">
              <h3>📂 文献文件夹</h3>
              <button @click="createFolder" class="add-folder-btn">+ 新建</button>
            </div>
            <ul class="folder-list">
              <li 
                v-for="folder in folders" 
                :key="folder.id" 
                :class="{ active: selectedFolderId === folder.id }"
                @click="selectedFolderId = folder.id"
                class="folder-item"
              >
                <span>📁 {{ folder.name }}</span>
                <button 
                  class="delete-folder-btn" 
                  @click.stop="confirmDeleteFolder(folder)"
                  title="删除文件夹"
                >
                  🗑️
                </button>
              </li>
            </ul>
          </div>

          <!-- Drag and Drop Upload -->
          <div 
            class="upload-section"
            :class="{ dragging: isDragging }"
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            @click="triggerFileSelect"
          >
            <div class="upload-box">
              <span class="upload-icon">📤</span>
              <p>将 PDF 论文拖拽至此上传</p>
              <span class="upload-sub">或点击此处选择文件</span>
              <input type="file" ref="fileInput" @change="handleFileSelect" multiple accept=".pdf" style="display:none" />
            </div>
          </div>
        </div>

        <!-- Papers List Table -->
        <div class="papers-section">
          <div class="table-header">
            <h3>📑 论文列表</h3>
            <div class="search-box">
              <input type="text" v-model="searchQuery" placeholder="搜索论文标题/作者..." />
            </div>
          </div>

          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>论文标题</th>
                  <th>作者</th>
                  <th>年份</th>
                  <th>解析状态</th>
                  <th>入库时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="paper in filteredPapers" :key="paper.id">
                  <td class="paper-title" @click="openPaper(paper)">{{ paper.title }}</td>
                  <td>{{ paper.authors?.join(', ') || '未知' }}</td>
                  <td>{{ paper.year || '-' }}</td>
                  <td>
                    <span :class="['status-badge', paper.status]">
                      {{ statusMap[paper.status] }}
                    </span>
                  </td>
                  <td>{{ paper.created_at }}</td>
                  <td>
                    <button class="delete-btn" @click="deletePaper(paper.id)">删除</button>
                  </td>
                </tr>
                <tr v-if="filteredPapers.length === 0">
                  <td colspan="6" class="empty-row">暂无文献数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </main>
  </div>

  <!-- Create Folder Custom Modal -->
  <div v-if="showCreateFolderModal" class="modal-overlay" @click.self="showCreateFolderModal = false">
    <div class="modal-card">
      <div class="modal-header">
        <h3>📂 新建文献文件夹</h3>
        <button @click="showCreateFolderModal = false" class="close-modal-btn">×</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label>文件夹名称</label>
          <input 
            type="text" 
            v-model="newFolderName" 
            placeholder="请输入文件夹名称..." 
            ref="newFolderInputRef"
            @keyup.enter="submitCreateFolder"
            autofocus
          />
        </div>
      </div>
      <div class="modal-footer">
        <button @click="showCreateFolderModal = false" class="btn-secondary">取消</button>
        <button @click="submitCreateFolder" class="btn-primary" :disabled="!newFolderName.trim()">确定</button>
      </div>
    </div>
  </div>

  <!-- Delete Confirm Custom Modal -->
  <div v-if="confirmModal.show" class="modal-overlay" @click.self="closeConfirmModal(false)">
    <div class="modal-card">
      <div class="modal-header">
        <h3>{{ confirmModal.title }}</h3>
        <button @click="closeConfirmModal(false)" class="close-modal-btn">×</button>
      </div>
      <div class="modal-body">
        <p>{{ confirmModal.message }}</p>
      </div>
      <div class="modal-footer">
        <button @click="closeConfirmModal(false)" class="btn-secondary">取消</button>
        <button @click="closeConfirmModal(true)" class="btn-danger">确定</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { papersAPI, foldersAPI, ingestAPI } from '../api';

const router = useRouter();
const authStore = useAuthStore();

interface Paper {
  id: number;
  title: string;
  authors: string[];
  year?: number;
  status: string;
  created_at: string;
  batch_id?: string;
}

interface Folder {
  id: number;
  name: string;
  paper_count?: number;
}

const isDragging = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const searchQuery = ref('');
const selectedFolderId = ref<number | null>(null);

const showCreateFolderModal = ref(false);
const newFolderName = ref('');
const newFolderInputRef = ref<HTMLInputElement | null>(null);

const confirmModal = ref({
  show: false,
  title: '',
  message: '',
  onConfirm: null as (() => void) | null
});

const loading = ref(false);
const uploadProgress = ref<Record<number, number>>({});

const statusMap: Record<string, string> = {
  pending: '排队中',
  parsing: '解析中',
  indexing: '索引中',
  completed: '就绪',
  done: '就绪',
  failed: '失败',
};

const folders = ref<Folder[]>([]);
const papers = ref<Paper[]>([]);
const currentBatchId = ref<string | null>(null);

const filteredPapers = computed(() => {
  return papers.value.filter(paper => {
    const matchesSearch = paper.title.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      paper.authors.some((auth: string) => auth.toLowerCase().includes(searchQuery.value.toLowerCase()));
    return matchesSearch;
  });
});

function triggerFileSelect() {
  fileInput.value?.click();
}

// Load folders and papers on mount
onMounted(async () => {
  await loadFolders();
  await loadPapers();
  
  // Poll for upload progress every 3 seconds
  setInterval(async () => {
    if (currentBatchId.value) {
      await loadPapers();
    }
  }, 3000);
});

// Reload papers when folder selection changes
watch(selectedFolderId, () => {
  loadPapers();
});

async function loadFolders() {
  try {
    const res = await foldersAPI.listFolders();
    folders.value = res.data;
  } catch (error: any) {
    console.error('Failed to load folders:', error);
  }
}

async function loadPapers() {
  try {
    loading.value = true;
    const res = await papersAPI.listPapers(
      selectedFolderId.value ?? undefined
    );
    papers.value = res.data.map((paper: any) => ({
      id: paper.id,
      title: paper.title,
      authors: paper.authors ? (typeof paper.authors === 'string' ? paper.authors.split(', ') : paper.authors) : ['未知'],
      year: paper.year,
      status: paper.status,
      created_at: new Date(paper.created_at).toLocaleString('zh-CN'),
      batch_id: paper.batch_id,
    }));
    
    // Update batch_id for polling
    if (papers.value.length > 0 && papers.value[0].batch_id) {
      currentBatchId.value = papers.value[0].batch_id;
    }
  } catch (error: any) {
    console.error('Failed to load papers:', error);
  } finally {
    loading.value = false;
  }
}

function handleLogout() {
  authStore.clearAuth();
  router.push('/login');
}

async function createFolder() {
  newFolderName.value = '';
  showCreateFolderModal.value = true;
  await nextTick();
  newFolderInputRef.value?.focus();
}

async function submitCreateFolder() {
  const name = newFolderName.value.trim();
  if (name) {
    try {
      const res = await foldersAPI.createFolder(name);
      folders.value.push({
        id: res.data.id,
        name: res.data.name,
      });
      showCreateFolderModal.value = false;
    } catch (error: any) {
      console.error('Failed to create folder:', error);
    }
  }
}

function confirmDeleteFolder(folder: { id: number; name: string }) {
  confirmModal.value = {
    show: true,
    title: "🗑️ 删除文献文件夹",
    message: `确定要删除文件夹 "${folder.name}" 吗？删除该文件夹不会删除其中的文献，文献将被归类到未分类中。`,
    onConfirm: async () => {
      try {
        await foldersAPI.deleteFolder(folder.id);
        folders.value = folders.value.filter(f => f.id !== folder.id);
        if (selectedFolderId.value === folder.id) {
          selectedFolderId.value = null;
        }
        await loadFolders();
      } catch (error: any) {
        console.error('Failed to delete folder:', error);
      }
    }
  };
}

function handleDrop(e: DragEvent) {
  isDragging.value = false;
  const files = e.dataTransfer?.files;
  if (files) {
    uploadFiles(files);
  }
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement;
  if (target.files) {
    uploadFiles(target.files);
  }
}

async function uploadFiles(files: FileList) {
  try {
    loading.value = true;
    const res = await papersAPI.uploadPapers(files, selectedFolderId.value ?? undefined);
    
    // Store batch_id for polling
    currentBatchId.value = res.data.batch_id;
    
    // Show upload success
    alert(`已上传 ${files.length} 个文件，正在后台解析中...`);
    
    // Reload papers to show new uploads
    await loadPapers();
  } catch (error: any) {
    console.error('Failed to upload papers:', error);
    alert('上传失败，请稍后重试');
  } finally {
    loading.value = false;
  }
}

// Use custom modal for delete confirmation
function deletePaper(id: number) {
  confirmModal.value = {
    show: true,
    title: "🗑️ 删除文献",
    message: "确认删除此文献吗？其对应的向量与解析内容均将被彻底清理，不可恢复。",
    onConfirm: async () => {
      try {
        await papersAPI.deletePaper(id);
        papers.value = papers.value.filter(p => p.id !== id);
      } catch (error: any) {
        console.error('Failed to delete paper:', error);
        alert('删除失败，请稍后重试');
      }
    }
  };
}

function closeConfirmModal(isConfirmed: boolean) {
  if (isConfirmed && confirmModal.value.onConfirm) {
    confirmModal.value.onConfirm();
  }
  confirmModal.value.show = false;
}

function openPaper(paper: any) {
  router.push({ path: '/chat', query: { paperId: paper.id } });
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

/* Main Content */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow-y: auto;
}

.content-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.user-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}

.content-body {
  padding: 40px;
  display: flex;
  flex-direction: column;
  gap: 30px;
}

/* Control Panel */
.control-panel {
  display: flex;
  gap: 30px;
}

.folders-section {
  flex: 1;
  background: #ffffff;
  padding: 20px;
  border-radius: 12px;
  border: 1px solid #e1e6e3;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.section-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #0f3d24;
}

.add-folder-btn {
  background: none;
  border: none;
  color: #1c7243;
  font-weight: 600;
  cursor: pointer;
}

.folder-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.folder-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
  font-weight: 500;
}

.folder-item:hover {
  background-color: #f0f4f1;
}

.folder-item.active {
  background-color: #e3ece6;
  color: #0f3d24;
  font-weight: 600;
}

.delete-folder-btn {
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s ease;
  padding: 2px 6px;
  font-size: 14px;
  border-radius: 4px;
}

.folder-item:hover .delete-folder-btn {
  opacity: 0.6;
}

.delete-folder-btn:hover {
  opacity: 1 !important;
  background-color: rgba(197, 48, 48, 0.1);
}

.upload-section {
  flex: 2;
  background: #ffffff;
  border: 2px dashed #b8c7be;
  border-radius: 12px;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 20px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.upload-section.dragging, .upload-section:hover {
  border-color: #1c7243;
  background-color: #f0f7f3;
}

.upload-box {
  text-align: center;
}

.upload-icon {
  font-size: 32px;
  display: block;
  margin-bottom: 10px;
}

.upload-box p {
  margin: 0 0 5px 0;
  font-weight: 600;
}

.upload-sub {
  font-size: 12px;
  color: #667e6e;
}

/* Papers List */
.papers-section {
  background: #ffffff;
  padding: 24px;
  border-radius: 12px;
  border: 1px solid #e1e6e3;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.table-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.table-header h3 {
  margin: 0;
  font-size: 18px;
  color: #0f3d24;
}

.search-box input {
  padding: 8px 16px;
  border: 1px solid #c2cdc6;
  border-radius: 20px;
  outline: none;
  font-size: 14px;
  width: 250px;
  transition: all 0.3s ease;
}

.search-box input:focus {
  border-color: #1c7243;
  width: 300px;
}

.table-container {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

th {
  padding: 12px 16px;
  border-bottom: 2px solid #e1e6e3;
  color: #556c5c;
  font-weight: 600;
  font-size: 14px;
}

td {
  padding: 16px;
  border-bottom: 1px solid #f0f3f1;
  font-size: 14px;
}

.paper-title {
  font-weight: 600;
  color: #1c7243;
  cursor: pointer;
}

.paper-title:hover {
  text-decoration: underline;
}

.status-badge {
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}

.status-badge.done {
  background-color: #d1f2d9;
  color: #0f6c2c;
}

.status-badge.parsing, .status-badge.indexing {
  background-color: #ffebd1;
  color: #b35900;
}

.status-badge.pending {
  background-color: #e2e8f0;
  color: #4a5568;
}

.status-badge.failed {
  background-color: #fed7d7;
  color: #9b2c2c;
}

.delete-btn {
  background: none;
  border: none;
  color: #c53030;
  cursor: pointer;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
}

.delete-btn:hover {
  background-color: #fff5f5;
}

.empty-row {
  text-align: center;
  color: #667e6e;
  padding: 30px;
}

/* Modal Overlays */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background-color: rgba(15, 61, 36, 0.4);
  backdrop-filter: blur(8px);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
  animation: fadeIn 0.2s ease-out;
}

.modal-card {
  background-color: #ffffff;
  width: 440px;
  max-width: 90%;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow: 0 12px 40px rgba(15, 61, 36, 0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: scaleUp 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.modal-header {
  padding: 20px 24px;
  border-bottom: 1px solid #e1e6e3;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #0f3d24;
}

.close-modal-btn {
  background: none;
  border: none;
  font-size: 24px;
  color: #667e6e;
  cursor: pointer;
  line-height: 1;
  padding: 4px;
}

.close-modal-btn:hover {
  color: #0f3d24;
}

.modal-body {
  padding: 24px;
}

.modal-body p {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: #2c4d37;
}

.modal-body .form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.modal-body .form-group label {
  font-size: 13px;
  font-weight: 700;
  color: #0f3d24;
}

.modal-body .form-group input {
  padding: 12px 16px;
  border: 1px solid #c2cdc6;
  border-radius: 8px;
  font-size: 14px;
  background-color: #f7f9f8;
  outline: none;
  transition: all 0.2s ease;
}

.modal-body .form-group input:focus {
  border-color: #1c7243;
  background-color: #ffffff;
  box-shadow: 0 0 0 3px rgba(28, 114, 67, 0.1);
}

.modal-footer {
  padding: 16px 24px;
  background-color: #f8faf9;
  border-top: 1px solid #e1e6e3;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.btn-secondary {
  padding: 10px 16px;
  background: none;
  border: 1px solid #c2cdc6;
  border-radius: 8px;
  color: #1a3322;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-secondary:hover {
  background-color: #f0f3f1;
}

.btn-primary {
  padding: 10px 20px;
  background-color: #0f3d24;
  border: none;
  border-radius: 8px;
  color: #ffffff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-primary:hover {
  background-color: #195232;
}

.btn-primary:disabled {
  background-color: #c2cdc6;
  cursor: not-allowed;
}

.btn-danger {
  padding: 10px 20px;
  background-color: #c53030;
  border: none;
  border-radius: 8px;
  color: #ffffff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-danger:hover {
  background-color: #9b2c2c;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes scaleUp {
  from { transform: scale(0.95); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}
</style>
