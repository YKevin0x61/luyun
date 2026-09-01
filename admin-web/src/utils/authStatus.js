/** Session login-status cache for the SPA router guard. */

const AUTH_STATUS_TTL_MS = 10000

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
  try {
    const resp = await fetch('/api/auth/status', { credentials: 'include' })
    const data = await resp.json()
    const loggedIn = !!data.logged_in
    setAuthLoggedIn(loggedIn)
    return loggedIn
  } catch {
    // fail-closed：拿不到状态时按未登录处理，跳登录页。未登录结果不缓存，
    // 避免「被踢到 /login → 刚登入 → 10s 内仍当作未登录」打回登录页。
    clearAuthStatusCache()
    return false
  }
}
