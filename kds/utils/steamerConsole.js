/**
 * 熟笼蒸炉屏: derived phase, work-surface gate, 上笼 / 换孔 / 下笼 / 笼上出餐 / 抽笼 intents.
 * Phase is not a 出餐状态.
 */

import { isRefundOrder } from './constants.js'

export const STEAMER_PHASE_AWAITING = '待上笼'
export const STEAMER_PHASE_STEAMING = '在蒸'
export const STEAMER_PHASE_CANCEL_HOLD = '退菜占位'
export const STEAMER_PHASE_AWAITING_NOTICE = '待上笼退示'
export const SHULONG_STATION_ID = 'shulong'
export const DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS = 180

export const STEAMER_WORK_SURFACES = Object.freeze({
  LOAD: 'load',
  STEAMING: 'steaming',
  SOLO: 'solo'
})

const STEAMER_WORK_SURFACE_SET = new Set(Object.values(STEAMER_WORK_SURFACES))

/** Fallback when /api/stations has no shulong.steamer_layout. Keep in sync with config.KITCHEN_STATIONS.shulong. */
export const SHULONG_STEAMER_LAYOUT = Object.freeze({
  steamers: Object.freeze([
    Object.freeze({ id: '1', portCount: 6 }),
    Object.freeze({ id: '2', portCount: 6 })
  ]),
  portCapacity: 10,
  awaitingCancelNoticeSeconds: DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS
})

export function normalizeSteamerWorkSurface(value) {
  return STEAMER_WORK_SURFACE_SET.has(value) ? value : ''
}

function isCancelledOrRefund(order) {
  if (!order) return false
  if (order.dish_status === '已取消') return true
  return isRefundOrder(order)
}

function asTimestamp(value) {
  if (value == null || value === '') return NaN
  if (typeof value === 'number') return value
  if (value instanceof Date) return value.getTime()
  return Date.parse(value)
}

export function deriveSteamerPhase(order, { now, noticeSeconds } = {}) {
  if (!order) return null
  const cancelled = isCancelledOrRefund(order)
  if (cancelled && order.placement) return STEAMER_PHASE_CANCEL_HOLD
  if (cancelled) {
    if (order.loaded_at) return null
    const windowSeconds = noticeSeconds == null
      ? SHULONG_STEAMER_LAYOUT.awaitingCancelNoticeSeconds
      : Number(noticeSeconds)
    const start = asTimestamp(order.updated_at)
    const moment = now == null ? Date.now() : asTimestamp(now)
    if (!Number.isFinite(start) || !Number.isFinite(moment) || !Number.isFinite(windowSeconds)) {
      return null
    }
    const elapsed = moment - start
    if (elapsed >= 0 && elapsed < windowSeconds * 1000) return STEAMER_PHASE_AWAITING_NOTICE
    return null
  }
  if (order.dish_status !== '待出餐') return null
  if (order.placement) return STEAMER_PHASE_STEAMING
  return STEAMER_PHASE_AWAITING
}

export function listAwaitingSteamerCages(orders, opts) {
  return (Array.isArray(orders) ? orders : []).filter((order) => {
    const phase = deriveSteamerPhase(order, opts)
    return phase === STEAMER_PHASE_AWAITING || phase === STEAMER_PHASE_AWAITING_NOTICE
  })
}

/** First-seen dish_name order; 待上笼退示 stay visible but out of selectableCages. */
export function groupAwaitingSteamerCages(cages, opts) {
  const groups = []
  const byName = new Map()
  for (const cage of Array.isArray(cages) ? cages : []) {
    const dishName = cage?.dish_name || ''
    let group = byName.get(dishName)
    if (!group) {
      group = { dishName, cages: [], selectableCages: [] }
      byName.set(dishName, group)
      groups.push(group)
    }
    group.cages.push(cage)
    if (deriveSteamerPhase(cage, opts) === STEAMER_PHASE_AWAITING) {
      group.selectableCages.push(cage)
    }
  }
  return groups
}

export function listSteamingSteamerCages(orders, opts) {
  return (Array.isArray(orders) ? orders : []).filter((order) => {
    const phase = deriveSteamerPhase(order, opts)
    return phase === STEAMER_PHASE_STEAMING || phase === STEAMER_PHASE_CANCEL_HOLD
  })
}

export function isSteamerConsole({ steamerWorkSurface, stationId } = {}) {
  return Boolean(normalizeSteamerWorkSurface(steamerWorkSurface)) && stationId === SHULONG_STATION_ID
}

export function steamerLoadIntent({ selectedOrderIds, steamerId, portIndex } = {}) {
  if (!Array.isArray(selectedOrderIds) || selectedOrderIds.length === 0) return null
  return {
    orderIds: selectedOrderIds,
    steamerId,
    portIndex
  }
}

function _asIdList(value) {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

export function steamerHoleTapIntent({
  awaitingIds,
  steamingIds,
  holdIds,
  steamerId,
  portIndex,
  occupiedOnHole,
  portCapacity,
  idsOnHole,
  workSurface
} = {}) {
  const awaiting = _asIdList(awaitingIds)
  const steaming = _asIdList(steamingIds)
  const holds = _asIdList(holdIds)
  const surface = normalizeSteamerWorkSurface(workSurface)
  if (awaiting.length === 0 && steaming.length === 0 && holds.length === 0) return null
  if (
    (awaiting.length > 0 && steaming.length > 0)
    || (holds.length > 0 && (awaiting.length > 0 || steaming.length > 0))
  ) {
    return { type: 'reject', reason: 'mixed' }
  }
  if (holds.length > 0) return null

  const onHole = new Set(_asIdList(idsOnHole))
  const incoming = awaiting.length > 0
    ? awaiting
    : steaming.filter((id) => !onHole.has(id))
  const occupied = Number(occupiedOnHole) || 0
  const capacity = Number(portCapacity) || SHULONG_STEAMER_LAYOUT.portCapacity
  if (occupied + incoming.length > capacity) {
    return { type: 'reject', reason: 'capacity' }
  }
  if (incoming.length === 0) return null

  if (awaiting.length > 0) {
    if (surface === STEAMER_WORK_SURFACES.STEAMING) {
      return { type: 'reject', reason: 'surface' }
    }
    return { type: 'load', orderIds: awaiting, steamerId, portIndex }
  }
  if (surface === STEAMER_WORK_SURFACES.LOAD) {
    return { type: 'reject', reason: 'surface' }
  }
  return { type: 'move', orderIds: steaming, steamerId, portIndex }
}

export function steamerUnloadIntent({ selectedOrderIds } = {}) {
  if (!Array.isArray(selectedOrderIds) || selectedOrderIds.length === 0) return null
  return { orderIds: selectedOrderIds.map(String) }
}

export function steamerBasketServeIntent({ selectedOrderIds, awaitingIds, steamingIds } = {}) {
  if (awaitingIds !== undefined || steamingIds !== undefined) {
    const awaiting = _asIdList(awaitingIds)
    const steaming = _asIdList(steamingIds)
    if (awaiting.length > 0 && steaming.length > 0) {
      return { type: 'reject', reason: 'mixed' }
    }
    const ids = awaiting.length > 0 ? awaiting : steaming
    if (ids.length === 0) return null
    return { orderIds: ids }
  }
  if (!Array.isArray(selectedOrderIds) || selectedOrderIds.length === 0) return null
  return { orderIds: selectedOrderIds.map(String) }
}

export function steamerPluckIntent({ selectedHoldIds } = {}) {
  if (!Array.isArray(selectedHoldIds) || selectedHoldIds.length === 0) return null
  return { orderIds: selectedHoldIds.map(String) }
}

export function toggleSteamerCageSelection({
  awaitingIds = [],
  steamingIds = [],
  holdIds = [],
  orderId,
  phase
} = {}) {
  const next = {
    awaitingIds: _asIdList(awaitingIds),
    steamingIds: _asIdList(steamingIds),
    holdIds: _asIdList(holdIds)
  }
  if (phase === STEAMER_PHASE_AWAITING_NOTICE) return next
  if (phase === STEAMER_PHASE_CANCEL_HOLD && next.steamingIds.length > 0) return next
  if (phase === STEAMER_PHASE_STEAMING && next.holdIds.length > 0) return next
  if (phase === STEAMER_PHASE_STEAMING) {
    next.steamingIds = toggleSteamerSelection(next.steamingIds, orderId)
  } else if (phase === STEAMER_PHASE_CANCEL_HOLD) {
    next.holdIds = toggleSteamerSelection(next.holdIds, orderId)
  } else if (phase === STEAMER_PHASE_AWAITING) {
    next.awaitingIds = toggleSteamerSelection(next.awaitingIds, orderId)
  }
  return next
}

export function toggleSteamerSelection(selectedOrderIds, orderId) {
  const id = String(orderId ?? '')
  if (!id) return Array.isArray(selectedOrderIds) ? [...selectedOrderIds] : []
  const current = Array.isArray(selectedOrderIds) ? selectedOrderIds.map(String) : []
  if (current.includes(id)) return current.filter((item) => item !== id)
  return [...current, id]
}

function isHoleDisplayHold(cage, now) {
  return deriveSteamerPhase(cage, { now }) === STEAMER_PHASE_CANCEL_HOLD
}

function isRushedCage(cage) {
  if (cage?.priority === 'urgent') return true
  return String(cage?.notes || '').includes('催')
}

function steamDurationMs(cage, now) {
  const loaded = asTimestamp(cage?.placement?.loaded_at)
  const moment = now == null ? Date.now() : asTimestamp(now)
  if (!Number.isFinite(loaded) || !Number.isFinite(moment)) return 0
  return Math.max(0, moment - loaded)
}

function stackOrderOf(cage) {
  return Number(cage?.placement?.stack_order || 0)
}

export function compareHoleDisplay(a, b, now) {
  const holdA = isHoleDisplayHold(a, now)
  const holdB = isHoleDisplayHold(b, now)
  if (holdA !== holdB) return holdA ? 1 : -1
  const rushA = isRushedCage(a)
  const rushB = isRushedCage(b)
  if (rushA !== rushB) return rushA ? -1 : 1
  const duration = steamDurationMs(b, now) - steamDurationMs(a, now)
  if (duration) return duration
  return stackOrderOf(a) - stackOrderOf(b)
}

export function sortHoleDisplay(cages, now) {
  return (Array.isArray(cages) ? cages.slice() : []).sort((a, b) => compareHoleDisplay(a, b, now))
}

export function fillHoleSlots(cages, { steamerId, portIndex, portCapacity, now } = {}) {
  const capacity = Number(portCapacity) || SHULONG_STEAMER_LAYOUT.portCapacity
  const onHole = (Array.isArray(cages) ? cages : []).filter((cage) => {
    const placement = cage?.placement
    return placement
      && String(placement.steamer_id) === String(steamerId)
      && Number(placement.port_index) === Number(portIndex)
  })
  const slots = sortHoleDisplay(onHole, now).map((cage) => ({ empty: false, cage }))
  while (slots.length < capacity) {
    slots.push({ empty: true })
  }
  return slots.slice(0, capacity)
}

const DELIVERY_TABLE_PLATFORMS = Object.freeze([
  { match: /美团/, prefix: '外·美团' },
  { match: /饿了么/, prefix: '外·饿了么' }
])

function trailingTableNumber(value) {
  const match = String(value).match(/(\d+)\s*$/)
  return match ? match[1] : ''
}

export function formatSteamerTableLabel(tableNumber, source) {
  const raw = String(tableNumber ?? '').trim()
  if (!raw) return { lines: [''] }

  const looksDelivery = source === 'delivery'
    || raw.startsWith('外')
    || DELIVERY_TABLE_PLATFORMS.some((platform) => platform.match.test(raw))
  if (looksDelivery) {
    const platform = DELIVERY_TABLE_PLATFORMS.find((item) => item.match.test(raw))
    const number = trailingTableNumber(raw)
    if (platform && number) return { lines: [platform.prefix, number] }
    if (platform) return { lines: [platform.prefix] }
    if (number) return { lines: ['外', number] }
    return { lines: ['外', raw.replace(/^外[·.]?/, '') || raw] }
  }

  if (/^包/.test(raw) || /包间|包厢/.test(raw)) {
    const number = trailingTableNumber(raw) || raw.replace(/^包(?:间|厢)?/, '')
    return { lines: ['包', number] }
  }

  return { lines: [`${raw}桌`] }
}

export function steamUrgencyLevel(cage, now, thresholds) {
  if (deriveSteamerPhase(cage, { now }) !== STEAMER_PHASE_STEAMING) return 'normal'
  const elapsed = steamDurationMs(cage, now)
  const urgent = Number(thresholds?.urgent)
  const warning = Number(thresholds?.warning)
  if (Number.isFinite(urgent) && elapsed > urgent) return 'urgent'
  if (Number.isFinite(warning) && elapsed > warning) return 'warn'
  return 'normal'
}

export function formatSteamerCageCard(cage, now) {
  const table = formatSteamerTableLabel(cage?.table_number, cage?.source)
  const minutes = Math.floor(steamDurationMs(cage, now) / 60000)
  const hold = isHoleDisplayHold(cage, now)
  return {
    primary: cage?.dish_name || '',
    secondary: `${table.lines.join(' ')} ${minutes}分`.trim(),
    tableLines: table.lines,
    steamMinutes: minutes,
    holdMark: hold ? '退' : ''
  }
}

function findShulongStation(stationsPayload) {
  if (Array.isArray(stationsPayload)) {
    return stationsPayload.find((station) => station && station.id === SHULONG_STATION_ID) || null
  }
  if (stationsPayload && typeof stationsPayload === 'object') {
    const direct = stationsPayload[SHULONG_STATION_ID] || stationsPayload.shulong
    if (direct && typeof direct === 'object') return direct
  }
  return null
}

function normalizeSteamerPorts(steamers) {
  if (!Array.isArray(steamers)) return []
  return steamers.map((steamer) => ({
    id: String(steamer?.id ?? ''),
    portCount: Number(steamer?.portCount ?? steamer?.port_count) || 6
  }))
}

export function steamerLayoutFromStations(stationsPayload) {
  const station = findShulongStation(stationsPayload)
  const raw = station?.steamer_layout || station?.steamerLayout
  if (!raw || typeof raw !== 'object') return SHULONG_STEAMER_LAYOUT
  const steamers = normalizeSteamerPorts(raw.steamers)
  if (steamers.length === 0) return SHULONG_STEAMER_LAYOUT
  const portCapacity = Number(raw.portCapacity ?? raw.port_capacity)
  const noticeSeconds = Number(
    raw.awaitingCancelNoticeSeconds ?? raw.awaiting_cancel_notice_seconds
  )
  return {
    steamers,
    portCapacity: Number.isFinite(portCapacity) && portCapacity > 0
      ? portCapacity
      : SHULONG_STEAMER_LAYOUT.portCapacity,
    awaitingCancelNoticeSeconds: Number.isFinite(noticeSeconds) && noticeSeconds > 0
      ? noticeSeconds
      : SHULONG_STEAMER_LAYOUT.awaitingCancelNoticeSeconds
  }
}

export function steamerAwaitingPlacement(workSurface) {
  const surface = normalizeSteamerWorkSurface(workSurface)
  if (surface === STEAMER_WORK_SURFACES.SOLO) return 'side'
  if (surface === STEAMER_WORK_SURFACES.STEAMING) return 'hidden'
  return 'top'
}
