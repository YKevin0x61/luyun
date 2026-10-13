/** Staff-phone session probe. Separate from the Admin SPA shared login. */

/** 员工端 JSON 请求超时：8 秒不回就当网络不可用（不要无限挂着）。 */
export const STAFF_REQUEST_TIMEOUT_MS = 8000
/** 会话探测在路由守卫上跑，员工等着它决定进不进得去，所以更短。 */
export const STAFF_SESSION_PROBE_TIMEOUT_MS = 4000

function offlineError(message) {
  const err = new Error(message)
  // offline 标记：调用方据此区分"网络不通"与"服务端拒绝"，前者不该清会话、
  // 不该丢待上传照片。
  err.offline = true
  return err
}

/**
 * 员工会话探针：**服务端永远回 200**，判据在 body 的 `authenticated` 里。
 *
 * 为什么不拿 `/staff/me` 的 401 当「没登录」：判断是对的，代价是浏览器控制台里一条
 * 红色错误 —— 401 在探针里是**预期**结果，而探针跑在路由守卫与工作台外壳上，等于每次
 * 导航都留一条（管理端身份看工作台时尤其吵）。管理端那一半早有对称入口
 * （`/api/auth/status`：未登录 200 + `logged_in=false`），`/api/hygiene/staff/session`
 * 就是员工端缺的那一半。
 *
 * 网络层失败（断网 / 超时 / 服务未就绪）**仍然抛**，带 `offline` 标记：调用方要能区分
 * 「确实没登录」与「问不到」（后者不该把员工甩去登录页）。
 *
 * @returns {Promise<{authenticated: boolean, employee: object|null}>}
 *   有会话时 `employee` 就是 `/staff/me` 那一份投影（姓名、能力档），于是探针拿到结论
 *   的同时也拿到了姓名，不必为一个名字再打一次业务接口。
 */
export async function staffSession({ timeoutMs = STAFF_SESSION_PROBE_TIMEOUT_MS } = {}) {
  const data = await staffRequest('/api/hygiene/staff/session', { timeoutMs })
  const payload = data && typeof data === 'object' ? data : {}
  return {
    authenticated: Boolean(payload.authenticated),
    employee: payload.employee || null,
  }
}

/**
 * 会话探测的三态结果。
 *
 * ``'unauthenticated'`` 才是"确实没登录"；``'unknown'`` 表示网络层不明（断网、
 * 后端刚重启、超时）。路由守卫必须区分这两者：把 ``unknown`` 当成未登录处理，
 * 弱网下会把员工反复甩到登录页。
 */
export async function staffSessionState() {
  try {
    const { authenticated } = await staffSession()
    return authenticated ? 'ok' : 'unauthenticated'
  } catch (err) {
    return err && err.offline ? 'unknown' : 'unauthenticated'
  }
}

export async function isStaffLoggedIn() {
  return (await staffSessionState()) === 'ok'
}

export function parseApiDetail(data) {
  if (!data) return '请求失败'
  const detail = data.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item.msg || String(item)).join('；')
  }
  return '请求失败'
}

export async function staffRequest(
  path,
  { method = 'GET', body, timeoutMs = STAFF_REQUEST_TIMEOUT_MS } = {},
) {
  // fetch 只在网络层失败时 reject（断网 / 超时 / DNS / 商场 AP 假死）。以前这里
  // 直接冒泡，店员看到的是 "Failed to fetch"「Load failed」这种英文原文；假死的
  // AP 更糟——请求永远不返回，路由守卫卡住就是一片白屏。
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res
  try {
    res = await fetch(path, {
      method,
      credentials: 'include',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } catch (err) {
    throw offlineError(
      err && err.name === 'AbortError'
        ? '网络太慢，请走到信号好的地方再试'
        : '网络连不上，请检查手机网络后重试',
    )
  } finally {
    clearTimeout(timer)
  }
  const contentType = res.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await res.json() : await res.text()
  if (!res.ok) {
    const message = typeof data === 'string' ? data : parseApiDetail(data)
    const err = new Error(message)
    err.status = res.status
    throw err
  }
  return data
}

/** 上传的整体时长上限：假死网络（连得上但不回包）会让任务永远停在 uploading。 */
export const STAFF_UPLOAD_TIMEOUT_MS = 120000

export function staffUpload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.withCredentials = true
    // 没有超时的话，商场 AP 假死时任务会永远停在 uploading，占满并发槽，后面的
    // 照片全排队不动；而这类任务又被"已入队"标记从待办里拿掉了，店员会以为交完了。
    // 超时按网络错误处理（不带 status），会走既有的自动重试。
    xhr.timeout = STAFF_UPLOAD_TIMEOUT_MS
    xhr.ontimeout = () => reject(new Error('上传超时，请到信号好的地方再试'))
    if (typeof onProgress === 'function' && xhr.upload) {
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable || !event.total) return
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100))
        onProgress({ percent, done: false })
      }
      xhr.upload.onload = () => onProgress({ percent: 100, done: true })
    }
    xhr.onload = () => {
      let data = null
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        data = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data)
        return
      }
      const message = data && typeof data === 'object'
        ? parseApiDetail(data)
        : `上传失败 (${xhr.status})`
      const err = new Error(message)
      err.status = xhr.status
      reject(err)
    }
    xhr.onerror = () => reject(new Error('网络错误，上传失败'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    xhr.send(formData)
  })
}
