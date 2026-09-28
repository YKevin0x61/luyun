/** Build and resolve /login?next= without double-encoding query values.
 *  RECIPE_READER_PATHS must stay in lockstep with main.py HTML_AUTH_PUBLIC_PAGES.
 */
import { STAFF_ENTRY_PATH, isStaffLandingPath, isStaffPhonePath } from './staffPaths.js'

export const RECIPE_READER_PATHS = ['/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr']

export function isRecipeReaderPath(pathname) {
  return RECIPE_READER_PATHS.includes(pathname || '')
}

export function shouldSkipLoginRedirect(pathname) {
  if (pathname === '/login' || pathname === '/settings') return true
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

/** 站内相对路径的公共判据：站外、协议相对、反斜杠变体、登录页自身一律不算。
 *
 *  按身份互斥的两份白名单（管理员 / 员工）都从这里出发 —— 不各写一遍正则，否则
 *  两份会漂：强的那份放松了也没有断言拦得住。
 */
function sanitizeNext(raw) {
  if (raw == null || raw === '') return null
  let next = Array.isArray(raw) ? raw[0] : String(raw)
  if (next == null || next === '') return null
  if (/%25/i.test(next)) {
    try {
      next = decodeURIComponent(next)
    } catch {
      return null
    }
  }
  if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return null
  if (next === '/login' || next.startsWith('/login?') || next.startsWith('/login#')) return null
  return next
}

/** 只看路径部分：query / hash 不参与前缀判断。 */
function pathnameOf(next) {
  const [pathname] = String(next).split(/[?#]/)
  return pathname
}

/** `?next=` 指定的员工端落点；不是员工端路径（或本身非法）时返回 null。
 *
 *  三个消费者共用这一份判据：员工栏的登录后落点（`resolveStaffNext`）、面板默认
 *  开在哪一栏（`resolveLoginTab`）。判据本身在 `staffPaths.js` 的
 *  `isStaffLandingPath`（只要员工前缀，`/register` 不算 —— 账号还没批准、会话也
 *  不存在，把 `?next=/register` 当落点就是把员工送进死路）。
 */
export function staffNextTarget(raw) {
  const next = sanitizeNext(raw)
  if (!next) return null
  return isStaffLandingPath(pathnameOf(next)) ? next : null
}

/** 管理员身份的落点：认站内路径，但拒绝员工端前缀（那是员工 cookie 那扇门）与登录页自身。
 *
 *  两个方向都用同一份判据：员工栏只认员工端路径（`resolveStaffNext`），管理栏不认员工端
 *  路径。不这么收，管理端身份会被 `?next=/staff/today` 送进员工页，再被客户端守卫弹回登录页。
 */
export function resolveLoginNext(raw, fallback = '/') {
  const fallbackPath = fallback || '/'
  const next = sanitizeNext(raw)
  if (!next) return fallbackPath
  if (isStaffLandingPath(pathnameOf(next))) return fallbackPath
  return next
}

/** 员工栏登录后的落点：`?next=` 只认员工端前缀，别的一律回落到员工默认落点。
 *
 *  `/register` 是「员工侧界面」却不是落点：账号还没被批准、会话也不存在，把
 *  `?next=/register` 当合法落点就是把员工送进死路。
 *
 *  判据写在 `staffPaths.js`、单测在 `__tests__/loginNext.test.js`，页面里不许再手写
 *  一遍正则 —— 手写的那份比这里弱，放松了也没有断言拦得住。
 */
export function resolveStaffNext(raw, fallback = STAFF_ENTRY_PATH) {
  const fallbackPath = fallback || STAFF_ENTRY_PATH
  return staffNextTarget(raw) || fallbackPath
}

/** 面板默认开在哪一栏：`?next=` 落在员工端前缀内时**强制**员工栏，优先于记住值。
 *
 *  没有记住值（或记的是脏值）时落在员工栏：员工手机上打开 `/login` 就该直接看到
 *  手机号那一栏，不必先点一下（spec 故事 3 / ADR 0091）。管理端机器靠「上次选的是
 *  管理员栏」记住 —— 见 `loginPrefs.js` 的 `loadLoginTab`。
 */
export function resolveLoginTab(raw, remembered) {
  if (staffNextTarget(raw)) return 'staff'
  return remembered === 'admin' ? 'admin' : 'staff'
}
