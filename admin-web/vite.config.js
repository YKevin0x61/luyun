import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// 本地开发：5173 路由对齐直连 :8000（见 main.py 静态挂载与 deploy/nginx.conf）。
// - admin SPA 路由由 Vite 自己 serve（history fallback）
// - /assets 由 Vite HMR 提供，不反代 dist
// - 其余仍由 FastAPI 提供的路径统一反代到后端
const backendTarget = process.env.LUYUN_API_PROXY || 'http://localhost:8000'

const backendProxy = { target: backendTarget, changeOrigin: true }

// 两份 worker 共用的 workbox 口径：只预缓存前端程序资源，`/api`、`/ws`、上传与
// 受保护图片一律走网络。
const workboxBase = {
  globPatterns: ['**/*.{html,js,css,png,svg,ico,webmanifest}'],
  maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
  navigateFallback: '/index.html',
  navigateFallbackDenylist: [
    /^\/kds(?:\/|$)/,
    /^\/api(?:\/|$)/,
    /^\/ws(?:\/|$)/,
    /^\/docs(?:\/|$)/,
    /^\/openapi\.json$/,
    /^\/redoc(?:\/|$)/,
    /^\/README\.md$/,
  ],
  cleanupOutdatedCaches: true,
  clientsClaim: true,
  skipWaiting: false,
  inlineWorkboxRuntime: true,
}

export default defineConfig({
  plugins: [
    vue(),
    // 全站那一份（scope `/`）：管理面页面用它。**工作台页面不再用它** —— 更具体的
    // `/workbench` scope 会接管那些页面（票 09 实测见
    // `.scratch/workbench-subapp/pwa-lab/exp-b-sw-scope.json`）。
    VitePWA({
      registerType: 'prompt',
      injectRegister: false,
      manifest: false,
      devOptions: { enabled: false },
      workbox: {
        ...workboxBase,
        globIgnores: ['pwa/icons/*.svg', 'workbench/sw.js'],
      },
    }),
    // 工作台自己那一份（票 09）：从 `/workbench/sw.js` 提供、scope 是 `/workbench`，
    // 只被工作台页面注册（`utils/pwaManifest.js` 的归属判据给出 swUrl/scope）。
    // 三处非默认值都有原因：
    // - `filename: 'workbench/sw.js'`：脚本必须落在工作台路径下（票面要求），也才够得着
    //   `/workbench` 这个 scope（服务端配发 `Service-Worker-Allowed: /workbench`）。
    // - `globIgnores: [... 'sw.js']`：别把全站那份 worker 自己打进工作台的预缓存。
    // - `modifyURLPrefix: { '': '/' }`：workbox 生成的预缓存 URL 默认是**相对**的，运行时
    //   按 worker 自己的位置解析 —— 全站那份恰好在 `/sw.js`（相对即根相对）所以一直没事；
    //   工作台这份在 `/workbench/sw.js`，相对的 `assets/xxx.js` 会被解析成
    //   `/workbench/assets/xxx.js`（404，install 直接失败），而且相对形式的
    //   `navigateFallback: '/index.html'` 在清单里找不到对应条目，脚本求值时就抛
    //   `non-precached-url`（实测：注册报 "ServiceWorker script evaluation failed"）。
    //   加上前缀映射后清单变成根相对的，两个问题一起消失。
    VitePWA({
      registerType: 'prompt',
      injectRegister: false,
      filename: 'workbench/sw.js',
      manifest: false,
      devOptions: { enabled: false },
      workbox: {
        ...workboxBase,
        globIgnores: ['pwa/icons/*.svg', 'sw.js'],
        modifyURLPrefix: { '': '/' },
      },
    }),
  ],
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
