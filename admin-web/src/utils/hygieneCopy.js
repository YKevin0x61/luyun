/** Hygiene roster copy and permission helpers. Rules stay on the server. */

export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']
export const HYGIENE_SHIFTS = ['白班', '夜班']
export const HYGIENE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
export const HYGIENE_FIX_TYPES = ['卫生', '摆放', '标签']

export const HYGIENE_BRAND_MARK = '卫'
export const HYGIENE_BRAND_TITLE = '卫生'
export const HYGIENE_BRAND_TAGLINE = '对照实拍验收'
export const HYGIENE_DASHBOARD_BLURB = '花名册、责任区、验收、整改、红黑榜'
export const HYGIENE_ADMIN_NAV = [
  { path: '/hygiene-roster', title: '花名册', shortTitle: '人员', icon: 'clipboard', code: 'ROSTER' },
  { path: '/hygiene-zones', title: '卫生区', shortTitle: '区域', icon: 'layout-grid', code: 'ZONES' },
  { path: '/hygiene-daily', title: '日常验收', shortTitle: '日常', icon: 'check-circle', code: 'DAILY' },
  { path: '/hygiene-deep-clean', title: '专项卫生', shortTitle: '专项', icon: 'calendar', code: 'DEEP' },
  { path: '/hygiene-fix', title: '整改单', shortTitle: '整改', icon: 'siren', code: 'FIX' },
  { path: '/hygiene-boards', title: '红黑榜', shortTitle: '榜', icon: 'star', code: 'BOARDS' },
]

export const HYGIENE_STAFF_TABS = [
  { id: 'inbox', title: '待办', icon: 'inbox' },
  { id: 'deep', title: '专项', icon: 'calendar' },
  { id: 'fix', title: '整改', icon: 'siren' },
  { id: 'boards', title: '榜', icon: 'star' },
  { id: 'me', title: '我', icon: 'settings' },
]

export const HYGIENE_BACK_TO_ADMIN_LABEL = '后台'

export function isHygieneAdminPath(pathname) {
  return String(pathname || '').startsWith('/hygiene-')
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
