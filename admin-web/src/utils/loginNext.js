/** Build and resolve /login?next= without double-encoding query values.
 *  RECIPE_READER_PATHS must stay in lockstep with main.py HTML_AUTH_PUBLIC_PAGES.
 */
import { isStaffPhonePath } from './staffPaths.js'

export const RECIPE_READER_PATHS = ['/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr']

export function isRecipeReaderPath(pathname) {
  return RECIPE_READER_PATHS.includes(pathname || '')
}

export function shouldSkipLoginRedirect(pathname) {
  if (pathname === '/login' || pathname === '/setup') return true
  // 员工手机端那两块（「今天」与卫生）：401 回的是员工登录，不是管理端登录 ——
  // 名单在 utils/staffPaths.js，跟 PWA 清单归属共用一份。
  if (isStaffPhonePath(pathname)) return true
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

/** 员工登录页的落点：`?next=` 只认员工手机端那两块（`/hygiene*`、`/today`）。
 *
 *  跟 `resolveLoginNext` 的区别只有白名单：那个放行任意站内路径（管理端登录页要的
 *  就是 `/hygiene-roster` 这种），员工登录页放行它等于把员工送进管理端 —— 所以这里
 *  再收一道，白名单外（含 `//evil.example`、`/\evil.example`、`/admin`）一律回落到
 *  `/today`：员工手机上只有一个入口，第一眼是自己的班。
 *
 *  判据写在 `staffPaths.js`、单测在 `__tests__/loginNext.test.js`，页面里不许再手写
 *  一遍正则 —— 手写的那份比这里弱，放松了也没有断言拦得住。
 */
export function resolveStaffNext(raw, fallback = '/today') {
  const fallbackPath = fallback || '/today'
  const next = resolveLoginNext(raw, fallbackPath)
  // 只看路径部分：`/hygiene/daily?tab=2` 是员工页，query 不参与前缀判断。
  const [pathname] = next.split(/[?#]/)
  return isStaffPhonePath(pathname) ? next : fallbackPath
}
