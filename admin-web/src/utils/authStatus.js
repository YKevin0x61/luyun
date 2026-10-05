/** Session login-status cache for the SPA router guard. */

const AUTH_STATUS_TTL_MS = 10000
/** 探测超时。与员工端会话探针同一个量级（`hygieneStaff.js` 的 4000ms）。
 *
 *  这条不能省：路由守卫要等它出结论，而 `main.js` 现在是**等 `router.isReady()`
 *  之后才挂载**（票 11 的真机走查：401 兜底会抢在守卫前面劫走导航）—— 网络假死时
 *  没有超时就是整页白屏。超时按 fail-closed 处理（与 catch 分支同一条路）。 */
export const AUTH_STATUS_TIMEOUT_MS = 4000

let authStatusCache = null // { loggedIn: true, ts: number } | null

export function clearAuthStatusCache() {
  authStatusCache = null
}

export function setAuthLoggedIn(loggedIn) {
  if (loggedIn) {
    authStatusCache = { loggedIn: true, ts: Date.now() }
    return
  }
  authStatusCache = null
}

export async function isLoggedIn() {
  const now = Date.now()
  if (authStatusCache && authStatusCache.loggedIn && now - authStatusCache.ts < AUTH_STATUS_TTL_MS) {
    return true
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), AUTH_STATUS_TIMEOUT_MS)
  try {
    const resp = await fetch('/api/auth/status', {
      credentials: 'include',
      signal: controller.signal,
    })
    const data = await resp.json()
    const loggedIn = !!data.logged_in
    setAuthLoggedIn(loggedIn)
    return loggedIn
  } catch {
    // fail-closed：拿不到状态（拒绝 / 断网 / 超时）时按未登录处理，跳登录页。
    // 未登录结果不缓存，避免「被踢到 /login → 刚登入 → 10s 内仍当作未登录」打回登录页。
    clearAuthStatusCache()
    return false
  } finally {
    clearTimeout(timer)
  }
}
