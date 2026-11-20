/** 工作台主题（浅色 / 深色）的**唯一落点**（2026-10-17 双主题）。
 *
 *  两套主题在 `styles/theme.workbench.css` 里：`html[data-workbench]`（不挂属性）是
 *  **浅色贴纸风主皮**，`html[data-workbench][data-wb-theme="dark"]` 是同族深墨档
 *  （2026-11-20 换皮；此前默认是深色、浅色挂 `light`，方向相反）。
 *  这里只做三件事：**存偏好、算最终档、把 `data-wb-theme` 挂到 `<html>` 上**。
 *
 *  三档语义：`light` / `dark` 是用户明确选的；`system` 跟随 `prefers-color-scheme`。
 *  默认 `system` —— 后厨手机与店长电脑未必同档，跟着系统走不用每人配一次。
 *
 *  **不在组件里各写一份**：壳的切换按钮、启动时的首帧、系统主题变化的监听，都读这里。
 *  属性挂在 `<html>` 上（与 `data-workbench` 同一个宿主）：令牌是文档级契约，页面里套了
 *  哪层壳都继承得到；挂在壳的根节点上，弹层与 portal 出去的那部分就掉回浅色。
 *
 *  jsdom / SSR 安全：没有 `window.matchMedia` 时按**深色**算（与浏览器「没有 prefers-color-scheme
 *  即视为浅色」相反，这条是给 jsdom 兜底的：测试里关心的是「两档都能挂上属性」，不是首屏肤色）。
 */

export const WORKBENCH_THEME_KEY = 'luyun.workbench.theme'
export const THEME_LIGHT = 'light'
export const THEME_DARK = 'dark'
export const THEME_SYSTEM = 'system'
const VALID = [THEME_LIGHT, THEME_DARK, THEME_SYSTEM]

/** 读用户选的档（非法值 / 未设置 → `system`）。localStorage 在隐私模式下会抛，吞掉。 */
export function readWorkbenchTheme() {
  try {
    const raw = window.localStorage.getItem(WORKBENCH_THEME_KEY)
    return VALID.includes(raw) ? raw : THEME_SYSTEM
  } catch {
    return THEME_SYSTEM
  }
}

/** 系统现在是不是深色（没有 API 时 true —— 这是 jsdom 的兜底；真实浏览器缺 API 时按浅色，
 *  那样首屏就是暖纸底，不会白屏闪一帧深色）。 */
export function systemPrefersDark() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** 把用户选的档 + 系统现状算成最终生效的一档（只有 light / dark）。 */
export function resolveWorkbenchTheme(mode = readWorkbenchTheme(), systemDark = systemPrefersDark()) {
  if (mode === THEME_LIGHT || mode === THEME_DARK) return mode
  return systemDark ? THEME_DARK : THEME_LIGHT
}

/** 把最终档写到 `<html data-wb-theme>` 上（浅色是默认，不挂属性；深色挂 `dark`）。
 *  **浅色不挂 `light`**：`theme.workbench.css` 的默认段就是浅色，两处都写的话
 *  「不挂属性 = 浅色」这条约定会分叉，将来加第三档时没人说得清哪一档是默认。
 *  换皮（2026-11-20）之前是反过来的（深色不挂、浅色挂 `light`），方向在这一个函数里翻。 */
export function applyWorkbenchTheme(theme) {
  if (typeof document === 'undefined') return
  const html = document.documentElement
  if (theme === THEME_DARK) html.setAttribute('data-wb-theme', THEME_DARK)
  else html.removeAttribute('data-wb-theme')
  syncBrowserChrome(theme)
}

/** **浏览器自己的颜色**也跟着走（index.html 里那两条 meta）。
 *
 *  theme-color 决定手机浏览器地址栏 / 状态栏的底色，color-scheme 决定原生控件（滚动条、
 *  下拉箭头、日期选择器）按哪一档渲染。这两个不是 CSS，改不了 var()，只能在切换的同一处
 *  直接写死 —— 写在这里而不是壳里，是因为壳有移动端变体，而这两条属于文档。
 *  非工作台页面由 clearWorkbenchTheme 还原成后台那套深色。 */
const THEME_COLOR_LIGHT = '#FFF7E8'
const THEME_COLOR_DARK = '#171432'
/** index.html 里两条 meta 的出厂值（后台页的深色），退出工作台时还原成它。 */
const THEME_COLOR_ADMIN = '#0a0d16'
const COLOR_SCHEME_ADMIN = 'dark'

function writeMeta(name, content) {
  const meta = document.querySelector('meta[name="' + name + '"]')
  if (meta) meta.setAttribute('content', content)
}

function syncBrowserChrome(theme) {
  const dark = theme === THEME_DARK
  writeMeta('theme-color', dark ? THEME_COLOR_DARK : THEME_COLOR_LIGHT)
  writeMeta('color-scheme', dark ? 'dark' : 'light')
}

/** 用户明确选一档：存下来 + 立刻生效。 */
export function setWorkbenchTheme(mode) {
  const next = VALID.includes(mode) ? mode : THEME_SYSTEM
  try {
    window.localStorage.setItem(WORKBENCH_THEME_KEY, next)
  } catch {
    /* 存不下只影响下次打开，本次仍生效 */
  }
  applyWorkbenchTheme(resolveWorkbenchTheme(next))
  return next
}

/** 页面（重新）进入工作台时把存着的偏好刷上去。`workbenchDocument` 在挂 `data-workbench`
 *  的同一处调用它 —— 「令牌层开着但主题没挂」会闪一帧深色再跳浅色。 */
export function applyStoredWorkbenchTheme() {
  applyWorkbenchTheme(resolveWorkbenchTheme())
}

/** 进入非工作台页时把主题属性摘掉：后台页吃 `theme.css` 的 :root，不认这一套。 */
export function clearWorkbenchTheme() {
  if (typeof document === 'undefined') return
  document.documentElement.removeAttribute('data-wb-theme')
  writeMeta('theme-color', THEME_COLOR_ADMIN)
  writeMeta('color-scheme', COLOR_SCHEME_ADMIN)
}
