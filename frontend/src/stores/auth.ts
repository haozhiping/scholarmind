import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import api from '../api';

interface UserInfo {
  id: number;
  username: string;
  email: string;
  role: string;
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('token'));
  const user = ref<UserInfo | null>(null);

  const isAuthenticated = computed(() => !!token.value);

  function setToken(newToken: string) {
    token.value = newToken;
    localStorage.setItem('token', newToken);
  }

  function clearAuth() {
    token.value = null;
    user.value = null;
    localStorage.removeItem('token');
  }

  async function login(username: string, password: string) {
    const res = await api.post('/auth/login', { username, password });
    setToken(res.data.access_token);
    await fetchMe();
  }

  async function register(username: string, email: string, password: string) {
    await api.post('/auth/register', { username, email, password });
  }

  async function fetchMe() {
    try {
      const res = await api.get('/auth/me');
      user.value = res.data;
    } catch {
      user.value = null;
    }
  }

  return { token, user, isAuthenticated, setToken, clearAuth, login, register, fetchMe };
});
