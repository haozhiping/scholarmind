import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8008',
        changeOrigin: true,
        proxyTimeout: 300000, // 5 min proxy→target wait (large PDF uploads)
        timeout: 600000,     // 10 min incoming socket timeout
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            console.error('[vite proxy error]', err.message);
            if (res && 'writeHead' in res) {
              (res as any).writeHead(500, { 'Content-Type': 'application/json' });
              (res as any).end(JSON.stringify({ detail: `Proxy error: ${err.message}` }));
            }
          });
        },
      },
    },
  },
})
