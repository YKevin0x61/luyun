import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地开发：5173 路由对齐直连 :8000（见 main.py 静态挂载与 deploy/nginx.conf）。
// - admin SPA 路由由 Vite 自己 serve（history fallback）
// - /assets 由 Vite HMR 提供，不反代 dist
// - 其余仍由 FastAPI 提供的路径统一反代到后端
const backendTarget = process.env.LUYUN_API_PROXY || 'http://localhost:8000'

const backendProxy = { target: backendTarget, changeOrigin: true }

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': backendProxy,
      '/ws': { ...backendProxy, ws: true },
      '/kds': backendProxy,
      '/vendor': backendProxy,
      '/recipe.css': backendProxy,
      '/docs': backendProxy,
      '/openapi.json': backendProxy,
      '/redoc': backendProxy,
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: 'node',
    include: ['src/**/__tests__/**/*.test.js'],
  },
})
