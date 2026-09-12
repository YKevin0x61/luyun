/** Hygiene roster copy and permission helpers. Rules stay on the server. */

export const HYGIENE_PERMISSIONS = ['普通员工', '管理员']

export function isAllowedHygienePermission(permission) {
  return HYGIENE_PERMISSIONS.includes(permission)
}

export function hygienePermissionLabel(permission) {
  if (permission === '管理员') return '管理员'
  return '普通员工'
}

export function rosterStatusLabel(employee) {
  if (employee && employee.disabled) return '已停用'
  if (employee && employee.approved) return '已批准'
  return '待批准'
}
