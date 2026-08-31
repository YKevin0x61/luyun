/** Build and resolve /login?next= without double-encoding query values. */

export const RECIPE_READER_PATHS = ['/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr']

export function isRecipeReaderPath(pathname) {
  return RECIPE_READER_PATHS.includes(pathname || '')
}

export function shouldSkipLoginRedirect(pathname) {
  if (pathname === '/login' || pathname === '/setup') return true
  return isRecipeReaderPath(pathname)
}

export function buildLoginNextFromRoute(route) {
  const path = (route && route.path) || '/'
  const query = (route && route.query) || {}
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === '') continue
    const values = Array.isArray(value) ? value : [value]
    for (const item of values) {
      if (item == null || item === '') continue
      params.append(key, String(item))
    }
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

export function resolveLoginNext(raw, fallback = '/') {
  const fallbackPath = fallback || '/'
  if (raw == null || raw === '') return fallbackPath
  let next = Array.isArray(raw) ? raw[0] : String(raw)
  if (/%25/i.test(next)) {
    try {
      next = decodeURIComponent(next)
    } catch {
      return fallbackPath
    }
  }
  if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
    return fallbackPath
  }
  if (next === '/login' || next.startsWith('/login?') || next.startsWith('/login#')) {
    return fallbackPath
  }
  return next
}
