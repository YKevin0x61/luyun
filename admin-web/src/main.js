import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/client'
import { applyPwaManifest } from './utils/pwaManifest'
import { loginRedirectTarget } from './utils/loginNext'
import './styles/theme.css'
import '@vuepic/vue-datepicker/dist/main.css'

applyPwaManifest(window.location.pathname)

// 401 兜底的落点注入（票 10）：`api/client.js` 是非组件模块、拿不到 `useRouter()`，
// 由这里把 SPA 的 router 交给它 —— 会话没了就走**客户端导航**回登录页并带上原目标
// （`?next=`），不再整页重载。豁免哪些页（登录页 / 配置页 / 员工页 / 配方阅读面）
// 由 client 按页面清单判定，这里只管怎么跳。落点本身（含「已经在登录页上就不再跳」，
// 票 12 收的 O2）在 `utils/loginNext.js` 的 `loginRedirectTarget` 一处算，员工三页共用它。
setUnauthorizedHandler(() => {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) void router.replace(target)
})

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 首屏**等路由定下来再挂载**（票 12 收的 O1）：早于首次导航落定时 `route` 还是
// START_LOCATION（默认 `/`），而 `App.vue` 那个 `watch(isStandalone, {immediate: true})`
// 在挂载那一刻就会按「非独立页」去拉 `/api/stations` —— 员工 cookie 拿到 401 之后，
// 兜底（上面注入的那个 handler）拿着 `next=/` 当场 `router.replace('/login')`，
// **抢在路由守卫前面**把这次导航劫走：员工硬导航店长专属页本该落 `/workbench/forbidden`，
// 实测却落在员工首页（`.scratch/workbench-subapp/shots/08-staff-typed-manager-url.png`）。
// 等 `isReady()` 之后再挂载，首次导航（含守卫里的重定向）已经落定，兜底读到的才是真正
// 当前那条地址。`catch` 只是不让守卫里的意外异常把整页卡成白屏：出错时照样挂载，
// 错误本身已经由 vue-router 打到控制台了。
router.isReady().catch(() => {}).then(() => app.mount('#app'))
