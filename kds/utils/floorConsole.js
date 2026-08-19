/**
 * Floor console selection and phase actionability.
 * Vue pages stay as adapters; hold / fire / rush predicates live here.
 */

const HOLD_PHASES = ['待出餐', '待上笼', '在蒸']
const RUSH_PHASES = ['待出餐', '待上笼']
const STEAMING_PHASE = '在蒸'

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

function isDefaultSelected(line) {
  return isActionable(line) && line?.phase !== STEAMING_PHASE
}

export function defaultSelectedOrderIds(lines) {
  return (lines || []).filter(isDefaultSelected).map((line) => line.order_id)
}

export function nextSelectedOrderIds(previous, lines, { groupSeen = false, previousLines } = {}) {
  if (!groupSeen) return defaultSelectedOrderIds(lines)
  const previousPhaseById = new Map()
  for (const line of previousLines || []) {
    if (line?.order_id != null) previousPhaseById.set(line.order_id, line.phase)
  }
  const nextById = new Map((lines || []).map((line) => [line.order_id, line]))
  return (previous || []).filter((id) => {
    const line = nextById.get(id)
    if (!line || !isActionable(line)) return false
    if (line.phase === STEAMING_PHASE) return previousPhaseById.get(id) === STEAMING_PHASE
    return true
  })
}

export function dropSuccessfulOrderIds(selectedIds, submittedIds, conflicts) {
  const conflictIds = new Set((conflicts || []).map((item) => item?.order_id))
  const successes = new Set((submittedIds || []).filter((id) => !conflictIds.has(id)))
  return (selectedIds || []).filter((id) => !successes.has(id))
}

function floorWorkEnterClock(line) {
  return line?.work_enter_time || line?.fired_at || line?.order_time
}

function orderClockLabel(orderTime) {
  const match = String(orderTime || '').match(/T(\d{2}):(\d{2})/)
  return match ? `${match[1]}:${match[2]}` : ''
}

export function floorLineRowText(line) {
  const phase = line?.phase || ''
  const clock = orderClockLabel(floorWorkEnterClock(line))
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

export const FLOOR_SPLIT_MIN_WIDTH = 960
export const FLOOR_SPLIT_MIN_SHORT_EDGE = 768
export const FLOOR_JUMP_MISS_TOAST = '不在盯桌列表'

const READY_PHASE = '已制作待上菜'
const HOLD_PHASE = '等叫'

export function isFloorSplitLayout(viewport) {
  const width = Number(viewport?.width) || 0
  const height = Number(viewport?.height) || 0
  if (width >= FLOOR_SPLIT_MIN_WIDTH) return true
  return width >= FLOOR_SPLIT_MIN_SHORT_EDGE && height >= FLOOR_SPLIT_MIN_SHORT_EDGE
}

export function tableCardStats(lines) {
  let holdCount = 0
  let pendingWorkCount = 0
  let readyCount = 0
  let hasRush = false
  for (const line of lines || []) {
    const phase = line?.phase
    if (phase === HOLD_PHASE) holdCount += 1
    else if (phase === READY_PHASE) readyCount += 1
    else if (HOLD_PHASES.includes(phase)) pendingWorkCount += 1
    if (line?.is_rushed) hasRush = true
  }
  return { holdCount, pendingWorkCount, readyCount, hasRush }
}

export function tableCardEmphasis(stats) {
  return (stats?.readyCount || 0) > 0 ? 'ready' : 'plain'
}

export function compareFloorTableNumber(a, b) {
  return String(a || '').localeCompare(String(b || ''), undefined, { numeric: true })
}

export function matchFloorTable(tables, rawQuery) {
  const query = String(rawQuery || '').trim()
  if (!query) return null
  return (tables || []).find((table) => String(table.table_number) === query) || null
}

export function nextSelectedTableNumber(previous, tables) {
  const current = String(previous || '')
  if (!current) return ''
  const stillThere = (tables || []).some((table) => String(table.table_number) === current)
  return stillThere ? current : ''
}

export function tableLeftToastTitle(tableNumber) {
  return `${tableNumber}桌已离台`
}

function floorWorkEnterMs(line) {
  const ts = new Date(floorWorkEnterClock(line)).getTime()
  return Number.isFinite(ts) ? ts : 0
}

function compareFloorLineClock(a, b) {
  const ta = floorWorkEnterMs(a)
  const tb = floorWorkEnterMs(b)
  if (ta !== tb) return ta - tb
  return String(a?.order_id || '').localeCompare(String(b?.order_id || ''))
}

function floorLineBand(line) {
  if (line?.phase === STEAMING_PHASE) return 1
  if (isActionable(line)) return 0
  return 2
}

export function sortFloorGroupLines(lines) {
  return [...(lines || [])].sort((a, b) => {
    const bandA = floorLineBand(a)
    const bandB = floorLineBand(b)
    if (bandA !== bandB) return bandA - bandB
    return compareFloorLineClock(a, b)
  })
}

export function sortFloorDishGroups(groups) {
  return [...(groups || [])].sort((a, b) => {
    const actionableA = (a.lines || []).some(isActionable) ? 0 : 1
    const actionableB = (b.lines || []).some(isActionable) ? 0 : 1
    if (actionableA !== actionableB) return actionableA - actionableB
    return String(a.dishName || '').localeCompare(String(b.dishName || ''), 'zh')
  })
}

export function decorateFloorTable(table) {
  const lines = table?.lines || []
  const stats = tableCardStats(lines)
  const groups = sortFloorDishGroups(
    groupLinesByDishName(lines).map((group) => ({
      dishName: group.dishName,
      lines: sortFloorGroupLines(group.lines)
    }))
  )
  return {
    table_number: table?.table_number,
    stats,
    emphasis: tableCardEmphasis(stats),
    groups
  }
}

export function decorateFloorTables(rawTables) {
  return (rawTables || [])
    .map((table) => decorateFloorTable(table))
    .sort((a, b) => compareFloorTableNumber(a.table_number, b.table_number))
}
