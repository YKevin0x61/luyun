import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/client'
import { applyPwaManifest } from './utils/pwaManifest'
import { buildLoginNextFromRoute } from './utils/loginNext'
import './styles/theme.css'
import '@vuepic/vue-datepicker/dist/main.css'

applyPwaManifest(window.location.pathname)

// 401 兜底的落点注入（票 10）：`api/client.js` 是非组件模块、拿不到 `useRouter()`，
// 由这里把 SPA 的 router 交给它 —— 会话没了就走**客户端导航**回登录页并带上原目标
// （`?next=`），不再整页重载。豁免哪些页（登录页 / 配置页 / 员工页 / 配方阅读面）
// 由 client 按页面清单判定，这里只管怎么跳。
setUnauthorizedHandler(() => {
  const current = router.currentRoute.value
  void router.replace({ path: '/login', query: { next: buildLoginNextFromRoute(current) } })
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
