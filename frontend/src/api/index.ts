import axios from 'axios';

// VITE_API_BASE points at the backend origin (e.g. http://localhost:8008).
const ORIGIN = (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8008';
export const API_BASE = `${ORIGIN}/api`;

// MinIO origin for figure images. Backend MinIO is published on :9000.
export const MINIO_BASE = ORIGIN.replace(/:\d+$/, ':9000');

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

// Attach JWT
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// Handle 401 globally
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  },
);

// Build a full figure URL from a MinIO object key.
export function figureUrl(imageKey: string): string {
  return `${MINIO_BASE}/figures/${imageKey}`;
}

export default api;
