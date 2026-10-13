import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/client'
import { applyPwaManifest } from './utils/pwaManifest'
import { loginRedirectTarget } from './utils/loginNext'
import './styles/theme.css'
// 工作台主题（批次 ③-1a）：令牌上移到 html 级，只对带 data-workbench 的文档生效 ——
// 所以这里必须**自己把开关打开**（后台页面不打，继续吃上面的 theme.css :root）。
import './styles/theme.workbench.css'
// JSON Forms 的参数表单（企微推送的推送任务）用 vanilla 渲染器：它不带样式，这里把
// 它的结构与控件类映射到 theme.css 的既有样式上（ADR 0097）。
import './styles/jsonforms.css'
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

// 工作台的文档级契约（`<html data-workbench>` + 共享样式表那一份 `<link>`）不在这里：
// 票 06 起由 `router/index.js` 的 `installWorkbenchDocument(router)` 装（判据是路由 meta
// `workbenchTheme`，由页面清单派生；实现与理由见 `utils/workbenchDocument.js`）。
// 它注册在 `afterEach` 上，而下面等 `isReady()` 之后才挂载 —— 所以首次导航落定时属性与
// 样式表都已经就位，首屏不吃「无样式再跳一下」。

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
