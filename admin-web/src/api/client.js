// 统一 fetch 封装：同源 Cookie 会话鉴权（阶段二后端仍是 Session Cookie），
// 401 时跳转到 SPA 内的 /login 路由（阶段三登录页已迁移进 admin-web）。

import { skipsAdminLoginRedirect } from '../router/pageRoutes.js'

// 401 兜底豁免：判据在**页面清单**（`router/pageRoutes.json`）那一处 ——
// 「这一页的凭据不是管理端会话」就不该被甩去 /login（票 10 消掉的 audit 条目 10：
// 这份名单以前手写在 utils/loginNext.js，路径一搬家就漂，`/staff*` 与配方四页都换过地址）。
//   - `/login`、`/register`：public，登录页自己拿 401 渲染错误；
//   - `/settings`：清单里 `authRedirect: false`，它自己把 401 讲清楚（登录页自身也会用
//     写接口鉴权，与它自己的状态机互相打架时会来回跳）；
//   - 员工页与配方阅读面（`audience` 是 staff / both）：钥匙可能是员工 cookie。
function isStandaloneAuthRoute() {
  return skipsAdminLoginRedirect(window.location.pathname)
}

// 401 之后怎么回登录页：**客户端导航**，不再整页重载（票 10）。
// 整页重载会把 Service Worker、实时连接、滚动位置全丢掉，还要多一次冷启动；
// 而本模块不是组件、拿不到 `useRouter()` —— 所以由 app 启动时（`main.js`）把
// 「跳登录页」这个动作注入进来，注入方负责带上原目标（`router.currentRoute`）。
// 没人注入时（单测、非 SPA 上下文）退回整页跳一次：宁可多一次冷启动，
// 也不能把人留在一页已经无权的界面上。
let unauthorizedHandler = null

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler
}

function redirectToLogin() {
  if (typeof unauthorizedHandler === 'function') {
    unauthorizedHandler()
    return
  }
  const next = window.location.pathname + window.location.search
  window.location.href = '/login?next=' + encodeURIComponent(next)
}

/** 普通请求的默认超时（毫秒）：门店网络假死（连得上但不回包）时不能永久 loading。 */
export const REQUEST_TIMEOUT_MS = 20000

/** 上传的默认超时（毫秒）：要真传字节，比普通请求宽得多；调用方仍可再覆盖。 */
export const UPLOAD_TIMEOUT_MS = 120000

/** 超时错误：`code = 'timeout'` 让调用方把「没等到响应」和「服务端拒绝」分开。 */
function timeoutError(message) {
  const err = new Error(message)
  err.code = 'timeout'
  return err
}

async function request(
  path,
  {
    method = 'GET',
    params,
    body,
    signal,
    cache,
    // 默认 20 秒；上传、导出等慢接口由调用方传更长（api.* 的最后一个 options 参数）。
    timeoutMs = REQUEST_TIMEOUT_MS,
  } = {},
) {
  let url = path
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v)
    }
    const qsStr = qs.toString()
    if (qsStr) url += (url.includes('?') ? '&' : '?') + qsStr
  }

  // fetch 只在网络层失败时 reject（断网 / DNS 失败 / 被 abort）。以前这里直接冒泡，
  // 店长看到的是 "Failed to fetch"「Load failed」这种英文原文；服务端 400 的中文 `detail`
  // 走的是下面那条路（带 `status`），两者本来可以分开 —— 冒泡之后 `status` 是 undefined，
  // 页面无从分派。这里统一翻成中文，并且**不带 status**（口径同 `utils/hygieneStaff.js`
  // 的 `staffRequest` 与上传那一路的 `xhr.onerror`）：调用方据此知道「没连上，可以重试」，
  // 而不是「服务端拒绝了这次操作」。
  //
  // 但「商场 AP 假死」不会 reject —— 请求永远挂着。所以这里必须自己兜时间上限：
  // 到点 abort，与「立刻连不上」分开报（`code = 'timeout'`、文案说「请求超时」），
  // 否则调用方只能一直等 loading。请求结束（成功 / 失败 / 超时）都要 clearTimeout。
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  // 调用方自带的 signal（组件卸载 / 新一轮请求顶掉旧请求）仍要能取消：挂到同一个
  // controller 上，并在 finally 里摘掉监听，避免长命的 signal 上越积越多。
  let onExternalAbort = null
  if (signal && typeof signal.addEventListener === 'function') {
    onExternalAbort = () => controller.abort()
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onExternalAbort, { once: true })
  }

  let res
  try {
    res = await fetch(url, {
      method,
      credentials: 'include',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache,
    })
  } catch (_err) {
    if (timedOut) throw timeoutError('请求超时，请检查网络后重试')
    throw new Error('网络连不上，请检查网络后重试')
  } finally {
    clearTimeout(timer)
    if (onExternalAbort && typeof signal.removeEventListener === 'function') {
      signal.removeEventListener('abort', onExternalAbort)
    }
  }

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
// timeoutMs 默认取 UPLOAD_TIMEOUT_MS（上传比普通请求宽），调用方可通过
// api.upload 的第 4 个 options 参数覆盖。
function upload(path, formData, onProgress, timeoutMs = UPLOAD_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.withCredentials = true
    // 网络假死时 XHR 同样不会自己返回：任务会永远停在 uploading，占满上传队列。
    // 用 XHR 原生的 timeout 兜底（拒绝时带 code='timeout'，与「网络错误，上传失败」分开）。
    xhr.timeout = timeoutMs
    xhr.ontimeout = () => reject(timeoutError('上传超时，请检查网络后重试'))

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

// 各方法的最后一个 options 参数只用来覆盖超时：`{ timeoutMs: 60000 }`。
// 现有调用方的位置参数（get 的 cache、post 的 params……）一个都没动 —— 新增的只能
// 追加在末尾，传了就覆盖默认值，不传（或传 null）走 REQUEST_TIMEOUT_MS / UPLOAD_TIMEOUT_MS。
export const api = {
  get: (path, params, signal, cache, options) =>
    request(path, { method: 'GET', params, signal, cache, timeoutMs: options?.timeoutMs }),
  post: (path, body, params, options) =>
    request(path, { method: 'POST', body, params, timeoutMs: options?.timeoutMs }),
  put: (path, body, params, options) =>
    request(path, { method: 'PUT', body, params, timeoutMs: options?.timeoutMs }),
  patch: (path, body, params, options) =>
    request(path, { method: 'PATCH', body, params, timeoutMs: options?.timeoutMs }),
  delete: (path, params, options) =>
    request(path, { method: 'DELETE', params, timeoutMs: options?.timeoutMs }),
  upload: (path, formData, onProgress, options) =>
    upload(path, formData, onProgress, options?.timeoutMs),
  download,
  downloadPost,
  downloadUrl,
}
