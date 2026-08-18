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
