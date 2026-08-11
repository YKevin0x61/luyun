// 统一 fetch 封装：同源 Cookie 会话鉴权（阶段二后端仍是 Session Cookie），
// 401 时跳转到 SPA 内的 /login 路由（阶段三登录页已迁移进 admin-web）。

// 登录页 / 配置页自身也会调用写接口鉴权（如 /api/credentials、/api/auth/tokens）。
// 若它们在未登录/会话过期时也触发跳转，会与页面自身的状态机互相打架，
// 甚至造成 /login <-> /setup 来回跳转，因此这两个路由自己吞掉 401，交给页面处理。
function isStandaloneAuthRoute() {
  const path = window.location.pathname
  return path === '/login' || path === '/setup'
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
      let message = data && data.detail
      if (!message) {
        message = xhr.status === 413
          ? '文件过大，超过服务器上传上限（413）。请调大反向代理 client_max_body_size / CDN 上传上限后重试'
          : `上传失败 (${xhr.status})`
      }
      const err = new Error(message)
      err.status = xhr.status
      reject(err)
    }

    xhr.onerror = () => reject(new Error('网络错误，上传失败'))
    xhr.onabort = () => reject(new Error('上传已取消'))

    xhr.send(formData)
  })
}

// 下载文件型响应（如 DB 导出）：从 Content-Disposition 取原始文件名，
// 触发浏览器保存对话框；返回实际使用的文件名供调用方展示提示。
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

  const cd = res.headers.get('Content-Disposition') || ''
  const match = /filename\*?=(?:UTF-8''|)"?([^;"\n]+)"?/i.exec(cd)
  const filename = match && match[1] ? decodeURIComponent(match[1].trim()) : fallbackFilename

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

  const cd = res.headers.get('Content-Disposition') || ''
  const match = /filename\*?=(?:UTF-8''|)"?([^;"\n]+)"?/i.exec(cd)
  const filename = match && match[1] ? decodeURIComponent(match[1].trim()) : fallbackFilename

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
  delete: (path, params) => request(path, { method: 'DELETE', params }),
  upload,
  download,
  downloadPost,
}
