/**
 * Floor console selection and phase actionability.
 * Vue pages stay as adapters; hold / fire / rush predicates live here.
 */

const HOLD_PHASES = ['待出餐', '待上笼', '在蒸']
const RUSH_PHASES = ['待出餐', '待上笼']

export function canHold(line) {
  return HOLD_PHASES.includes(line?.phase)
}

export function canFire(line) {
  return line?.phase === '等叫'
}

export function canRush(line) {
  return RUSH_PHASES.includes(line?.phase) && !line?.is_rushed
}

export function isActionable(line) {
  return canHold(line) || canFire(line) || canRush(line)
}

export function defaultSelectedOrderIds(lines) {
  return (lines || []).filter(isActionable).map((line) => line.order_id)
}

export function nextSelectedOrderIds(previous, lines, { groupSeen = false } = {}) {
  if (!groupSeen) return defaultSelectedOrderIds(lines)
  const allowed = new Set(defaultSelectedOrderIds(lines))
  return (previous || []).filter((id) => allowed.has(id))
}

function orderClockLabel(orderTime) {
  const match = String(orderTime || '').match(/T(\d{2}):(\d{2})/)
  return match ? `${match[1]}:${match[2]}` : ''
}

export function floorLineChipText(line) {
  const phase = line?.phase || ''
  const clock = orderClockLabel(line?.order_time)
  const rush = line?.is_rushed ? '·加急' : ''
  if (!clock) return `${phase}${rush}`
  return `${phase} ${clock}${rush}`
}

export function floorConflictsToastTitle(conflicts) {
  const list = Array.isArray(conflicts) ? conflicts : []
  if (!list.length) return ''
  const reasons = []
  for (const item of list) {
    const reason = item?.reason
    if (reason && !reasons.includes(reason)) reasons.push(reason)
  }
  return `${list.length} 份未改：${reasons.join('；')}`
}

export function groupLinesByDishName(lines) {
  const byDish = new Map()
  for (const line of lines || []) {
    const name = line.dish_name || ''
    if (!byDish.has(name)) byDish.set(name, [])
    byDish.get(name).push(line)
  }
  return [...byDish.entries()].map(([dishName, dishLines]) => ({
    dishName,
    lines: dishLines
  }))
}
