/** Hygiene roster copy and permission helpers. Rules stay on the server. */

export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']
export const HYGIENE_SHIFTS = ['白班', '夜班']

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

export function rosterStatusLabel(employee) {
  if (employee && employee.disabled) return '已停用'
  if (employee && employee.approved) return '已批准'
  return '待批准'
}
