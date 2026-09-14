/** Staff-phone session probe. Separate from the Admin SPA shared login. */

export async function isStaffLoggedIn() {
  try {
    const resp = await fetch('/api/hygiene/staff/me', { credentials: 'include' })
    return resp.ok
  } catch {
    return false
  }
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

export async function staffRequest(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
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

export function staffUpload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.withCredentials = true
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
