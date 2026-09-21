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
 * 会话探测的三态结果。
 *
 * ``'unauthenticated'`` 才是"确实没登录"；``'unknown'`` 表示网络层不明（断网、
 * 后端刚重启、超时）。路由守卫必须区分这两者：把 ``unknown`` 当成未登录处理，
 * 弱网下会把员工反复甩到登录页。
 */
export async function staffSessionState() {
  try {
    await staffRequest('/api/hygiene/staff/me', {
      timeoutMs: STAFF_SESSION_PROBE_TIMEOUT_MS,
    })
    return 'ok'
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

/** 员工端登录页地址（带 next 回跳）。卫生入口不能复用管理端的 /login。 */
export function hygieneLoginUrl(next) {
  const fallback = typeof window === 'undefined'
    ? '/hygiene'
    : `${window.location.pathname}${window.location.search}`
  return `/hygiene/login?next=${encodeURIComponent(next || fallback)}`
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
