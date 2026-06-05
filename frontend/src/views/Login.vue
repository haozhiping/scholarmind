<template>
  <div class="login-container">
    <div class="glass-card">
      <div class="logo-area">
        <h1>文渊 · ScholarMind</h1>
        <p>跨语言学术文献智能调研系统</p>
      </div>

      <div class="tabs">
        <button 
          :class="{ active: isLogin }" 
          @click="isLogin = true"
        >登录</button>
        <button 
          :class="{ active: !isLogin }" 
          @click="isLogin = false"
        >注册</button>
      </div>

      <form @submit.prevent="handleSubmit" class="auth-form">
        <div class="form-group">
          <label for="username">用户名</label>
          <input 
            type="text" 
            id="username" 
            v-model="form.username" 
            required 
            placeholder="请输入用户名"
          />
        </div>

        <div v-if="!isLogin" class="form-group">
          <label for="email">邮箱</label>
          <input 
            type="email" 
            id="email" 
            v-model="form.email" 
            required 
            placeholder="请输入邮箱"
          />
        </div>

        <div class="form-group">
          <label for="password">密码</label>
          <input 
            type="password" 
            id="password" 
            v-model="form.password" 
            required 
            placeholder="请输入密码"
          />
        </div>

        <div v-if="errorMsg" class="error-msg">
          {{ errorMsg }}
        </div>

        <button type="submit" class="submit-btn" :disabled="loading">
          {{ loading ? '处理中...' : (isLogin ? '立即登录' : '立即注册') }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { authAPI } from '../api';

const router = useRouter();
const authStore = useAuthStore();

const isLogin = ref(true);
const loading = ref(false);
const errorMsg = ref('');

const form = reactive({
  username: '',
  email: '',
  password: '',
});

async function handleSubmit() {
  loading.value = true;
  errorMsg.value = '';
  
  try {
    if (isLogin.value) {
      // Login API call
      const res = await authAPI.login(form.username, form.password);
      authStore.setToken(res.data.access_token);
      router.push('/library');
    } else {
      // Register API call
      await authAPI.register(form.username, form.email, form.password);
      isLogin.value = true;
      errorMsg.value = '注册成功，请使用新账号登录！';
    }
  } catch (error: any) {
    errorMsg.value = error.response?.data?.detail || error.message || '操作失败，请稍后重试';
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: radial-gradient(circle at 10% 20%, rgb(4, 159, 108) 0%, rgb(194, 254, 113) 90.1%);
  font-family: 'Inter', sans-serif;
}

.glass-card {
  width: 400px;
  padding: 40px;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
  color: #1a3322;
}

.logo-area {
  text-align: center;
  margin-bottom: 30px;
}

.logo-area h1 {
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 10px 0;
  background: linear-gradient(135deg, #0f3d24 0%, #1e5a38 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.logo-area p {
  font-size: 14px;
  color: #2c4d37;
  margin: 0;
}

.tabs {
  display: flex;
  margin-bottom: 25px;
  border-bottom: 2px solid rgba(255, 255, 255, 0.2);
}

.tabs button {
  flex: 1;
  padding: 10px;
  background: none;
  border: none;
  font-size: 16px;
  font-weight: 600;
  color: rgba(26, 51, 34, 0.6);
  cursor: pointer;
  transition: all 0.3s ease;
}

.tabs button.active {
  color: #0f3d24;
  border-bottom: 2px solid #0f3d24;
  margin-bottom: -2px;
}

.auth-form {
  display: flex;
  flex-direction: column;
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
  color: #1a3322;
}

.form-group input {
  padding: 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.2);
  color: #1a3322;
  font-size: 14px;
  outline: none;
  transition: all 0.3s ease;
}

.form-group input::placeholder {
  color: rgba(26, 51, 34, 0.5);
}

.form-group input:focus {
  border-color: #0f3d24;
  background: rgba(255, 255, 255, 0.4);
}

.error-msg {
  color: #a71d24;
  font-size: 13px;
  background: rgba(255, 0, 0, 0.1);
  padding: 10px;
  border-radius: 6px;
  border-left: 3px solid #a71d24;
}

.submit-btn {
  padding: 12px;
  background: #0f3d24;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  margin-top: 10px;
}

.submit-btn:hover {
  background: #195232;
  box-shadow: 0 4px 15px rgba(15, 61, 36, 0.3);
}

.submit-btn:disabled {
  background: #476a54;
  cursor: not-allowed;
}
</style>
