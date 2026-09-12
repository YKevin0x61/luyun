/** Hygiene roster copy and permission helpers. Rules stay on the server. */

export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']
export const HYGIENE_SHIFTS = ['白班', '夜班']
export const HYGIENE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
export const HYGIENE_FIX_TYPES = ['卫生', '摆放', '标签']

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
