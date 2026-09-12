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

export async function staffUpload(path, formData) {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    body: formData,
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
