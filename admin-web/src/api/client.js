// 统一 fetch 封装：同源 Cookie 会话鉴权（阶段二后端仍是 Session Cookie），
// 401 时跳转到 SPA 内的 /login 路由（阶段三登录页已迁移进 admin-web）。

import { shouldSkipLoginRedirect } from '../utils/loginNext'

// 登录页 / 配置页自身也会调用写接口鉴权（如 /api/credentials、/api/auth/tokens）。
// 若它们在未登录/会话过期时也触发跳转，会与页面自身的状态机互相打架，
// 甚至造成 /login <-> /setup 来回跳转，因此这两个路由自己吞掉 401，交给页面处理。
// 配方阅读面 API 读公开；401 不应把厨房扫码页整页踢去登录。
function isStandaloneAuthRoute() {
  return shouldSkipLoginRedirect(window.location.pathname)
}

function redirectToLogin() {
  const next = window.location.pathname + window.location.search
  window.location.href = '/login?next=' + encodeURIComponent(next)
}

async function request(path, { method = 'GET', params, body, signal, cache } = {}) {
  let url = path
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v)
    }
    const qsStr = qs.toString()
    if (qsStr) url += (url.includes('?') ? '&' : '?') + qsStr
  }

  const res = await fetch(url, {
    method,
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
    cache,
  })

  if (res.status === 401 && !isStandaloneAuthRoute()) {
    redirectToLogin()
    throw new Error('未登录，正在跳转登录页')
  }

  const contentType = res.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await res.json() : await res.text()

  if (!res.ok) {
    const detail = data && data.detail
    const message =
      typeof detail === 'string'
        ? detail
        : (detail && detail.message) ||
          (typeof data === 'string' ? data : `请求失败 (${res.status})`)
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    // 非 2xx 也带上已解析的响应体：/api/healthz 未就绪时返回 503，
    // 但 db/disk 水位只在 body 里，调用方需要能读到。
    err.data = data
    throw err
  }
  return data
}

// 上传封装：改用 XMLHttpRequest 以支持真实的上传进度（fetch 无法获取 upload 进度）。
// onProgress 可选，回调形如 { loaded, total, percent, done }：
//   - done=false：字节上传中，percent 为 0~100 的真实百分比；
//   - done=true：字节已全部发送、进入服务器处理阶段（percent=100）。
// 契约与旧版保持一致：resolve 解析后的 JSON；非 2xx 抛带 detail 的 Error；
// 401 且非独立鉴权路由时跳转登录。不传 onProgress 时行为与原 fetch 版等价。
function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.withCredentials = true

    if (typeof onProgress === 'function' && xhr.upload) {
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable || !event.total) return
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100))
        onProgress({ loaded: event.loaded, total: event.total, percent, done: false })
      }
      // 字节全部发送完成、等待后端处理时切换到「处理中」阶段。
      xhr.upload.onload = () => {
        onProgress({ loaded: 1, total: 1, percent: 100, done: true })
      }
    }

    xhr.onload = () => {
      if (xhr.status === 401 && !isStandaloneAuthRoute()) {
        redirectToLogin()
        reject(new Error('未登录，正在跳转登录页'))
        return
      }
      let data = null
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch (_) {
        data = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data)
        return
      }
      // 413 由反向代理/CDN 返回，响应体通常是 HTML 而非 JSON（data 为 null），
      // 给出可操作的提示而不是笼统的「上传失败」。
      // detail 可能是字符串（FastAPI 默认）或对象（备份恢复用 reason/message/missing
      // 表达结构性失败）；对象要取 message，并把 detail 原样挂到 err 上供调用方分派。
      const detail = data ? data.detail : null
      let message = typeof detail === 'string' ? detail : detail?.message
      if (!message) {
        message = xhr.status === 413
          ? '文件过大，超过服务器上传上限（413）。请调大反向代理 client_max_body_size / CDN 上传上限后重试'
          : `上传失败 (${xhr.status})`
      }
      const err = new Error(message)
      err.status = xhr.status
      if (detail !== undefined) err.detail = detail
      reject(err)
    }

    xhr.onerror = () => reject(new Error('网络错误，上传失败'))
    xhr.onabort = () => reject(new Error('上传已取消'))

    xhr.send(formData)
  })
}

// 下载文件型响应（如 DB 导出）：从 Content-Disposition 取原始文件名，
// 触发浏览器保存对话框；返回实际使用的文件名供调用方展示提示。
/**
 * 从 Content-Disposition 取文件名。
 *
 * 必须优先 RFC 5987 的 `filename*=UTF-8''…`：服务端把它放在 `filename="…"` 之后
 * （那个 ASCII 名只是给老浏览器兜底），而一个宽松的正则从左往右扫会先撞上 ASCII
 * 名，中文文件名就被顶掉了。
 */
export function downloadFilename(header, fallback = 'download') {
  const value = String(header || '')
  const utf8 = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;\n]+)/.exec(value)
  const plain = /filename\s*=\s*"?([^;"\n]+)"?/.exec(value)
  const trimmed = ((utf8 && utf8[1]) || (plain && plain[1]) || '').trim()
  if (!trimmed) return fallback
  try {
    return decodeURIComponent(trimmed) || fallback
  } catch (_) {
    // 不是合法的百分号编码（服务端直接写了原文），按原文用。
    return trimmed
  }
}

async function download(path, fallbackFilename = 'download') {
  const res = await fetch(path, { method: 'GET', credentials: 'include', cache: 'no-store' })
  if (res.status === 401 && !isStandaloneAuthRoute()) {
    redirectToLogin()
    throw new Error('未登录，正在跳转登录页')
  }
  if (!res.ok) {
    let message = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      message = data.detail || message
    } catch (_) {
      // 响应非 JSON，保留默认错误信息
    }
    throw new Error(message)
  }

  const filename = downloadFilename(
    res.headers.get('Content-Disposition'),
    fallbackFilename,
  )

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return filename
}

/**
 * 用浏览器原生下载（`<a download>`）取文件，不在 JS 里中转。
 *
 * 大文件（备份包动辄上百 MB）不能走 fetch + blob：整包先进 JS 内存，再经
 * objectURL 落盘，等于多拷两遍——用户感受到的就是「下载好慢」，而且页面里也
 * 拿不到真实进度。原生下载由浏览器流式写盘，自带进度条、可暂停续传。
 * 同源 GET，浏览器会带上会话 cookie。
 */
export function downloadUrl(path, filename) {
  const anchor = document.createElement('a')
  anchor.href = path
  if (filename) anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

// POST + JSON body 下载（如加密备份导出）：与 download 相同地解析 Content-Disposition 并触发保存。
async function downloadPost(path, body, fallbackFilename = 'download') {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 401 && !isStandaloneAuthRoute()) {
    redirectToLogin()
    throw new Error('未登录，正在跳转登录页')
  }
  if (!res.ok) {
    let message = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      message = data.detail || message
    } catch (_) {
      // 响应非 JSON，保留默认错误信息
    }
    throw new Error(message)
  }

  const filename = downloadFilename(
    res.headers.get('Content-Disposition'),
    fallbackFilename,
  )

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return filename
}

export const api = {
  get: (path, params, signal, cache) => request(path, { method: 'GET', params, signal, cache }),
  post: (path, body, params) => request(path, { method: 'POST', body, params }),
  put: (path, body, params) => request(path, { method: 'PUT', body, params }),
  patch: (path, body, params) => request(path, { method: 'PATCH', body, params }),
  delete: (path, params) => request(path, { method: 'DELETE', params }),
  upload,
  download,
  downloadPost,
  downloadUrl,
}
