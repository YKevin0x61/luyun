/** 工作台主题的响应式门面（浅 / 深 / 跟随系统）。
 *
 *  状态是**模块级单例**：壳的切换按钮、手机页头菜单、将来任何一处都要看到同一个当前档，
 *  切一次全站同时变。真正的存储与 `<html>` 落点在 `utils/workbenchTheme.js`，这里只包一层
 *  Vue 的 ref + 系统偏好监听 —— 两个文件分工是「规则」与「响应式」。
 *
 *  系统偏好监听只注册一次：`(prefers-color-scheme: dark)` 的 change 在用户手动选了
 *  light/dark 之后仍会改 `systemDark`（它只影响 `system` 档算出来的结果），不会把用户
 *  手动选的档冲掉。
 */
import { computed, ref, watch } from 'vue'
import {
  THEME_DARK,
  THEME_LIGHT,
  THEME_SYSTEM,
  applyWorkbenchTheme,
  readWorkbenchTheme,
  resolveWorkbenchTheme,
  setWorkbenchTheme,
  systemPrefersDark,
} from '../utils/workbenchTheme'

const mode = ref(readWorkbenchTheme())
const systemDark = ref(systemPrefersDark())
const resolved = computed(() => resolveWorkbenchTheme(mode.value, systemDark.value))
const isDark = computed(() => resolved.value === THEME_DARK)
const isSystem = computed(() => mode.value === THEME_SYSTEM)

let installed = false
let mediaQuery = null

function onSystemChange(event) {
  systemDark.value = event.matches
}

/** 挂响应当前档：首次调用时注册一次监听，之后幂等。`immediate` 让首帧就是对的。 */
export function installWorkbenchTheme() {
  if (installed) return
  installed = true
  watch(resolved, (next) => applyWorkbenchTheme(next), { immediate: true })
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', onSystemChange)
    } else if (typeof mediaQuery.addListener === 'function') {
      /* 老 WebView（iOS < 14）只有 addListener */
      mediaQuery.addListener(onSystemChange)
    }
  }
}

export { THEME_LIGHT, THEME_DARK, THEME_SYSTEM }

export function useWorkbenchTheme() {
  function setMode(next) {
    mode.value = setWorkbenchTheme(next)
  }

  /** 在两档之间翻（跟随系统时，翻成与当前**相反**的那一档，用户下一步就落在明确选择上）。 */
  function toggle() {
    setMode(isDark.value ? THEME_LIGHT : THEME_DARK)
  }

  return { mode, resolved, isDark, isSystem, setMode, toggle }
}
