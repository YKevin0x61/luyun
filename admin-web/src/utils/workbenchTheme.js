/** 工作台主题（浅色 / 深色）的**唯一落点**（2026-10-17 双主题）。
 *
 *  两套主题在 `styles/theme.workbench.css` 里：`html[data-workbench]` 是深色（默认，原
 *  深青墨那套保留不动），`html[data-workbench][data-wb-theme="light"]` 是浅色（B 案日间档）。
 *  这里只做三件事：**存偏好、算最终档、把 `data-wb-theme` 挂到 `<html>` 上**。
 *
 *  三档语义：`light` / `dark` 是用户明确选的；`system` 跟随 `prefers-color-scheme`。
 *  默认 `system` —— 后厨手机与店长电脑未必同档，跟着系统走不用每人配一次。
 *
 *  **不在组件里各写一份**：壳的切换按钮、启动时的首帧、系统主题变化的监听，都读这里。
 *  属性挂在 `<html>` 上（与 `data-workbench` 同一个宿主）：令牌是文档级契约，页面里套了
 *  哪层壳都继承得到；挂在壳的根节点上，弹层与 portal 出去的那部分就掉回浅色。
 *
 *  jsdom / SSR 安全：没有 `window.matchMedia` 时按深色算（工作台的默认皮是深色）。
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

/** 系统现在是不是深色（没有 API 时 true —— 工作台的默认皮是深色）。 */
export function systemPrefersDark() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** 把用户选的档 + 系统现状算成最终生效的一档（只有 light / dark）。 */
export function resolveWorkbenchTheme(mode = readWorkbenchTheme(), systemDark = systemPrefersDark()) {
  if (mode === THEME_LIGHT || mode === THEME_DARK) return mode
  return systemDark ? THEME_DARK : THEME_LIGHT
}

/** 把最终档写到 `<html data-wb-theme>` 上（深色是默认，不挂属性）。 */
export function applyWorkbenchTheme(theme) {
  if (typeof document === 'undefined') return
  const html = document.documentElement
  if (theme === THEME_LIGHT) html.setAttribute('data-wb-theme', THEME_LIGHT)
  else html.removeAttribute('data-wb-theme')
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
}
