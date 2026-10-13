/** 工作台页面的**文档级契约**（ADR 0106 决策 7）：一个属性 + 一份样式表。
 *
 *  `<html data-workbench>` 是主题层的开关（颜色 / 字体 / 层级那一套令牌只在它存在时生效），
 *  共享样式表 `hygiene-admin.css` 是页面那套类名（`.btn` / `.card` / `.hy-*`）的来源。
 *  两件事**同一个判据、同一处落点**：分开写就会出现「令牌开着、样式表没挂」这种半截状态
 *  —— 那是整页掉色与整页没样式两种事故的同一个根。
 *
 *  判据是**路由 meta**（`meta.workbenchTheme`，由 `router/pageRoutes.js` 从页面清单派生）：
 *  `/workbench` 前缀下的页一律算开，前缀之外只有清单里显式标了 `workbenchTheme: true`
 *  的页才开 —— 今天只有 `/register`（它套员工鉴权壳 `.hygiene-staff`，却住在顶层路径）。
 *  真值来自 `router.resolve()` / 当前路由这一份**已经合并过父子记录**的 meta。
 *  这里**不写第二份路径前缀**：那正是它当初写错的地方（注册页整页吃不到 `--hy-*`，
 *  输入框无边框无底色、主按钮退化成一行裸文字，见 `pageRoutes.js` 的注释）。
 *
 *  挂载是**幂等**的：工作台页之间跳转时那一份 `<link>` 原地留着（摘了再挂，两次 DOM 变更
 *  之间那一帧就是「首屏无样式跳一下」）；非工作台页上一条都不留（`.btn` / `.card` 那类
 *  类名不常驻全局主题 —— 这也正是 `composables/useScopedStylesheet.js` 存在的理由）。
 *
 *  调用点只有一处：`router/index.js` 的 `installWorkbenchDocument(router)`。它跑在
 *  `afterEach` 里，也就是**首次导航落定之后、`app.mount()` 之前**（`main.js` 等
 *  `router.isReady()`），所以样式表比改动前（壳的 `onMounted`）更早进文档，首次绘制就有。
 *
 *  护栏：`src/__tests__/workbenchStylesheet.test.js`（真实路由 → 数 `<link>` 条数）。
 */

/** 共享样式表的地址（同一份在 `public/hygiene-admin.css`，由后端静态挂载）。 */
export const WORKBENCH_STYLESHEET_HREF = '/hygiene-admin.css'

/** 文档里今天挂着的那一份（只认自己挂的那条：`data-workbench-stylesheet`）。
 *  用标记属性而不是 `href` 去重：`href` 的写法（尾斜杠 / 绝对地址）会漂，标记不会。 */
function mountedStylesheet() {
  return document.head.querySelector('link[data-workbench-stylesheet]')
}

function mountStylesheet() {
  if (mountedStylesheet()) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = WORKBENCH_STYLESHEET_HREF
  // 与 `useScopedStylesheet` 挂出来的那份同形（配方页的打印逻辑按这个标记找自己的表），
  // 另加一条只属于工作台的标记供上面去重。
  link.dataset.scoped = 'true'
  link.dataset.workbenchStylesheet = ''
  document.head.appendChild(link)
}

function unmountStylesheet() {
  mountedStylesheet()?.remove()
}

/** 按一条路由（或任何带 `meta` 的路由对象）同步文档：属性与样式表一起开、一起关。 */
export function applyWorkbenchDocument(route) {
  const enabled = !!(route && route.meta && route.meta.workbenchTheme === true)
  const html = document.documentElement

  if (enabled) html.setAttribute('data-workbench', '')
  else html.removeAttribute('data-workbench')

  if (enabled) mountStylesheet()
  else unmountStylesheet()
}

/** 把这份契约装到 router 上：先按当前路由同步一次（HMR / 已有路由的场合），
 *  之后每次导航落定都跟着走 —— 导航是它唯一的输入。 */
export function installWorkbenchDocument(router) {
  applyWorkbenchDocument(router.currentRoute.value)
  router.afterEach((to) => applyWorkbenchDocument(to))
}
