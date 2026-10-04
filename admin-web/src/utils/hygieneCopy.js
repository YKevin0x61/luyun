/** Hygiene roster copy and permission helpers. Rules stay on the server. */

import { supportsNativeCameraCapture } from './cameraCapabilities'
import { WORKBENCH_TITLE } from './workbenchCopy'

export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']
export const HYGIENE_SHIFTS = ['白班', '夜班']
export const HYGIENE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
export const HYGIENE_FIX_TYPES = ['卫生', '摆放', '标签']

// 这一组（卫生八页）是「工作台」里的**现场**那一组（2026-10-04 合并）：品牌三件套跟着
// 子系统走，页面上不再自称另一个系统。名字只写一次，这里引 `workbenchCopy` 那份。
export const HYGIENE_BRAND_MARK = '台'
export const HYGIENE_BRAND_TITLE = WORKBENCH_TITLE
export const HYGIENE_BRAND_TAGLINE = '现场 · 对照实拍验收'
export const HYGIENE_ADMIN_NAV = [
  { path: '/workbench/roster', title: '花名册', shortTitle: '人员', icon: 'clipboard', code: 'ROSTER' },
  { path: '/workbench/zones', title: '卫生工作区', shortTitle: '工作区', icon: 'layout-grid', code: 'ZONES' },
  { path: '/workbench/daily', title: '日常验收', shortTitle: '日常', icon: 'check-circle', code: 'DAILY' },
  // 仪容仪表（票 12）：按人拍，名单由排班给（休假的与没排到的不在表上）。
  { path: '/workbench/attire', title: '仪容仪表', shortTitle: '仪容', icon: 'sparkles', code: 'ATTIRE' },
  { path: '/workbench/deep-clean', title: '专项卫生', shortTitle: '专项', icon: 'calendar', code: 'DEEP' },
  { path: '/workbench/fix', title: '整改单', shortTitle: '整改', icon: 'siren', code: 'FIX' },
  { path: '/workbench/boards', title: '红黑榜', shortTitle: '榜', icon: 'star', code: 'BOARDS' },
  { path: '/workbench/data', title: '数据与照片', shortTitle: '数据', icon: 'folder', code: 'ARCHIVE' },
]

export const HYGIENE_STAFF_TABS = [
  { id: 'inbox', title: '待办', icon: 'inbox' },
  { id: 'deep', title: '专项', icon: 'calendar' },
  { id: 'fix', title: '整改', icon: 'siren' },
  { id: 'boards', title: '榜', icon: 'star' },
  { id: 'me', title: '我', icon: 'settings' },
]

export const HYGIENE_BACK_TO_ADMIN_LABEL = '后台'

/** 这一组（八页）的路径判据。
 *
 *  **不能再只看前缀**：票 04 之后卫生与排班同住 `/workbench/*`（`/workbench/inbox`、
 *  `/workbench/shifts` 是排班那两组、`/workbench` 本身是首页），光比前缀会把它们
 *  一起算成卫生页 —— 那会让它们套上"内部自己滚动"的壳（`.page-body-hygiene`），
 *  工作台首页的吸顶窄栏就废了。所以只认**清单里那八条**。
 *  （`/staff/clean`、`/register` 这些员工侧页面本来就不在清单里。）
 */
export function isHygieneAdminPath(pathname) {
  const path = String(pathname || '')
  return HYGIENE_ADMIN_NAV.some((item) => path === item.path || path.startsWith(`${item.path}/`))
}

export function hygieneDocumentTitle(pageName) {
  const name = pageName == null ? '' : String(pageName).trim()
  return name ? `${name} · ${HYGIENE_BRAND_TITLE}` : HYGIENE_BRAND_TITLE
}

export function isAllowedHygienePermission(permission) {
  return HYGIENE_PERMISSIONS.includes(permission)
}

export function isAllowedHygieneShift(shift) {
  return HYGIENE_SHIFTS.includes(shift)
}

export function hygienePermissionLabel(permission) {
  if (permission === '管理员') return '管理员'
  return '普通员工'
}

export function hygieneShiftLabel(shift) {
  if (shift === '夜班') return '夜班'
  if (shift === '白班') return '白班'
  return '未选'
}

export function hygieneWeekdayLabel(weekday) {
  const index = Number(weekday)
  if (Number.isInteger(index) && index >= 0 && index < HYGIENE_WEEKDAYS.length) {
    return HYGIENE_WEEKDAYS[index]
  }
  return ''
}

export function hasLiveCamera() {
  if (supportsNativeCameraCapture()) return true
  return Boolean(
    typeof navigator !== 'undefined'
      && navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function',
  )
}

export function canAcceptFixTicket(actor, ticket, now = Date.now()) {
  if (!ticket || ticket.status !== '待验收') return false
  if (actor && actor.kind === 'super' && ticket.opener_kind === 'super') return true
  if (actor && actor.id != null && Number(actor.id) === Number(ticket.opener_id)) return true
  if (!actor) return false
  const isAdmin = actor.kind === 'super' || actor.permission === '管理员'
  if (!isAdmin) return false
  const deadline = Date.parse(ticket.deadline)
  return Number.isFinite(deadline) && now >= deadline
}

export function rosterStatusLabel(employee) {
  if (employee && employee.disabled) return '已停用'
  if (employee && employee.approved) return '已批准'
  return '待批准'
}
