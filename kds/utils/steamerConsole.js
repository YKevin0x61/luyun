/**
 * 熟笼蒸炉屏: derived phase, 上笼 / 换孔 / 下笼 / 笼上出餐 / 抽笼 intents.
 * Phase is not a 出餐状态.
 */

import { isRefundOrder } from './constants.js'
import { cancelAckLineId, isCancelAcknowledged } from './cancelAck.js'
import { composeKitchenDishCards, sortKitchenDishCardsByOldest } from './dishCardChunks.js'

export const STEAMER_PHASE_AWAITING = '待上笼'
export const STEAMER_PHASE_STEAMING = '在蒸'
export const STEAMER_PHASE_CANCEL_HOLD = '退菜占位'
export const STEAMER_PHASE_AWAITING_NOTICE = '待上笼退示'
export const SHULONG_STATION_ID = 'shulong'
export const DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS = 180

/** Fallback when /api/stations has no shulong.steamer_layout. Keep in sync with config.KITCHEN_STATIONS.shulong. */
export const SHULONG_STEAMER_LAYOUT = Object.freeze({
  steamers: Object.freeze([
    Object.freeze({ id: '1', portCount: 6 }),
    Object.freeze({ id: '2', portCount: 6 })
  ]),
  portCapacity: 10,
  awaitingCancelNoticeSeconds: DEFAULT_AWAITING_CANCEL_NOTICE_SECONDS
})

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

function cageLineId(cage) {
  return String(cage?.id ?? cage?._id ?? '')
}

export function sortAwaitingCagesFifo(cages) {
  return (Array.isArray(cages) ? cages.slice() : []).sort((a, b) => {
    const ta = asTimestamp(a?.order_time)
    const tb = asTimestamp(b?.order_time)
    const aOk = Number.isFinite(ta)
    const bOk = Number.isFinite(tb)
    if (aOk && bOk && ta !== tb) return ta - tb
    if (aOk !== bOk) return aOk ? -1 : 1
    return cageLineId(a).localeCompare(cageLineId(b))
  })
}

export function awaitingGroupSelectedCount(selectableCages, selectedIds) {
  const fifoIds = new Set(sortAwaitingCagesFifo(selectableCages).map(cageLineId).filter(Boolean))
  return (Array.isArray(selectedIds) ? selectedIds : []).filter((id) => fifoIds.has(String(id))).length
}

/** Click count on a 待上笼组: earliest N cages. Wrap at full selection clears this group only. */
export function advanceAwaitingGroupSelection({ selectableCages, selectedIds } = {}) {
  const fifoIds = sortAwaitingCagesFifo(selectableCages).map(cageLineId).filter(Boolean)
  const current = Array.isArray(selectedIds) ? selectedIds.map(String) : []
  if (fifoIds.length === 0) return current
  const fifoSet = new Set(fifoIds)
  const selectedInGroup = fifoIds.filter((id) => current.includes(id)).length
  const nextCount = selectedInGroup >= fifoIds.length ? 0 : selectedInGroup + 1
  const keep = current.filter((id) => !fifoSet.has(id))
  return [...keep, ...fifoIds.slice(0, nextCount)]
}

export function deriveSteamerPhase(order, { acknowledgedCancelIds } = {}) {
  if (!order) return null
  const cancelled = isCancelledOrRefund(order)
  if (cancelled && order.placement) return STEAMER_PHASE_CANCEL_HOLD
  if (cancelled) {
    // 抽走 keeps loaded_at; 待上笼退示 is never-loaded and unacked on this screen.
    if (order.loaded_at) return null
    if (isCancelAcknowledged(cancelAckLineId(order), acknowledgedCancelIds)) return null
    return STEAMER_PHASE_AWAITING_NOTICE
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
      group = { dishName, cages: [], selectableCages: [], noticeCages: [] }
      byName.set(dishName, group)
      groups.push(group)
    }
    group.cages.push(cage)
    const phase = deriveSteamerPhase(cage, opts)
    if (phase === STEAMER_PHASE_AWAITING) {
      group.selectableCages.push(cage)
    } else if (phase === STEAMER_PHASE_AWAITING_NOTICE) {
      group.noticeCages.push(cage)
    }
  }
  return groups
}

/**
 * 待上笼组走和其他屏相同的拆卡：菜卡份数上限 + 下单间隔，再按每组最早订单排。
 * 待上笼退示挂在该菜名最早一组上，不参与拆卡。
 */
export function composeAwaitingSteamerGroups(
  cages,
  opts,
  { cap = 0, orderGapMinutes = 0, previousByDish = {} } = {}
) {
  const grouped = groupAwaitingSteamerCages(cages, opts)
  const noticeByDish = new Map()
  const logicalDishes = []
  const noticeOnly = []

  for (const group of grouped) {
    if (group.noticeCages.length) noticeByDish.set(group.dishName, group.noticeCages)
    if (group.selectableCages.length === 0) {
      if (group.noticeCages.length) noticeOnly.push(group)
      continue
    }
    logicalDishes.push({
      dishName: group.dishName,
      orders: group.selectableCages
    })
  }

  const { cards, previousByDish: nextPrevious } = composeKitchenDishCards({
    logicalDishes,
    cap: Number(cap) || 0,
    orderGapMinutes: Number(orderGapMinutes) || 0,
    previousByDish
  })

  const attached = new Set()
  const groups = sortKitchenDishCardsByOldest(cards).map((card) => {
    let noticeCages = []
    if (!attached.has(card.dishName) && noticeByDish.has(card.dishName)) {
      noticeCages = noticeByDish.get(card.dishName)
      attached.add(card.dishName)
    }
    return {
      dishName: card.dishName,
      chunkId: card.chunkId,
      cages: [...card.orders, ...noticeCages],
      selectableCages: card.orders,
      noticeCages,
      totalQuantity: card.totalQuantity,
      oldestTimestamp: card.oldestTimestamp
    }
  })

  for (const group of noticeOnly) {
    groups.push({
      dishName: group.dishName,
      chunkId: `${group.dishName}::notice`,
      cages: group.noticeCages,
      selectableCages: [],
      noticeCages: group.noticeCages,
      totalQuantity: 0,
      oldestTimestamp: 0
    })
  }

  return { groups, previousByDish: nextPrevious }
}

export function listSteamingSteamerCages(orders, opts) {
  return (Array.isArray(orders) ? orders : []).filter((order) => {
    const phase = deriveSteamerPhase(order, opts)
    return phase === STEAMER_PHASE_STEAMING || phase === STEAMER_PHASE_CANCEL_HOLD
  })
}

export function isSteamerConsole({ stationId } = {}) {
  return stationId === SHULONG_STATION_ID
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
  idsOnHole
} = {}) {
  const awaiting = _asIdList(awaitingIds)
  const steaming = _asIdList(steamingIds)
  const holds = _asIdList(holdIds)
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
    return { type: 'load', orderIds: awaiting, steamerId, portIndex }
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

function uniqueIds(ids) {
  const seen = new Set()
  const out = []
  for (const id of _asIdList(ids)) {
    if (seen.has(id)) continue
    seen.add(id)
    out.push(id)
  }
  return out
}

function holeSelectAllTarget(cagesOnHole, opts) {
  const steaming = []
  const holds = []
  for (const cage of sortHoleDisplay(cagesOnHole, opts?.now)) {
    const id = cageLineId(cage)
    if (!id) continue
    const phase = deriveSteamerPhase(cage, opts)
    if (phase === STEAMER_PHASE_STEAMING) steaming.push(id)
    else if (phase === STEAMER_PHASE_CANCEL_HOLD) holds.push(id)
  }
  if (steaming.length) return { bucket: 'steaming', ids: steaming }
  if (holds.length) return { bucket: 'hold', ids: holds }
  return { bucket: null, ids: [] }
}

/** Select every 在蒸 cage on a hole; if the hole is only 退菜占位, select those. Tap again clears this hole. */
export function selectAllHoleCages({
  cagesOnHole,
  awaitingIds = [],
  steamingIds = [],
  holdIds = [],
  now,
  noticeSeconds
} = {}) {
  const target = holeSelectAllTarget(cagesOnHole, { now, noticeSeconds })
  const currentSteaming = _asIdList(steamingIds)
  const currentHolds = _asIdList(holdIds)
  const currentAwaiting = _asIdList(awaitingIds)
  if (target.ids.length === 0) {
    return {
      awaitingIds: currentAwaiting,
      steamingIds: currentSteaming,
      holdIds: currentHolds
    }
  }
  const current = target.bucket === 'steaming' ? currentSteaming : currentHolds
  const allOn = target.ids.every((id) => current.includes(id))
  if (target.bucket === 'steaming') {
    return {
      awaitingIds: [],
      steamingIds: allOn
        ? currentSteaming.filter((id) => !target.ids.includes(id))
        : uniqueIds([...currentSteaming, ...target.ids]),
      holdIds: []
    }
  }
  return {
    awaitingIds: [],
    steamingIds: [],
    holdIds: allOn
      ? currentHolds.filter((id) => !target.ids.includes(id))
      : uniqueIds([...currentHolds, ...target.ids])
  }
}

export function isHoleFullySelected({
  cagesOnHole,
  steamingIds = [],
  holdIds = [],
  now,
  noticeSeconds
} = {}) {
  const target = holeSelectAllTarget(cagesOnHole, { now, noticeSeconds })
  if (target.ids.length === 0) return false
  const current = target.bucket === 'steaming' ? _asIdList(steamingIds) : _asIdList(holdIds)
  return target.ids.every((id) => current.includes(id))
}

function isHoleDisplayHold(cage, now) {
  return deriveSteamerPhase(cage, { now }) === STEAMER_PHASE_CANCEL_HOLD
}

function isRushedCage(cage) {
  if (cage?.priority === 'urgent') return true
  return String(cage?.notes || '').includes('催')
}

function elapsedMinutesSince(start, now) {
  const begin = asTimestamp(start)
  const moment = now == null ? Date.now() : asTimestamp(now)
  if (!Number.isFinite(begin) || !Number.isFinite(moment)) return 0
  return Math.max(0, Math.floor((moment - begin) / 60000))
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
  { match: /饿了么/, prefix: '外·饿了么' },
  { match: /淘宝|闪购/, prefix: '外·淘宝' }
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
  const minutes = elapsedMinutesSince(cage?.placement?.loaded_at, now)
  const totalMinutes = elapsedMinutesSince(cage?.order_time, now)
  const hold = isHoleDisplayHold(cage, now)
  const dishName = String(cage?.dish_name || '').replace(/^[（(]外卖[）)]/, '').trim()
  return {
    primary: dishName,
    secondary: `${table.lines.join(' ')} ${minutes}分`.trim(),
    tableLines: table.lines,
    steamMinutes: minutes,
    totalMinutes,
    timeLabel: totalMinutes > 0 ? `${minutes}分 总${totalMinutes}分` : `${minutes}分`,
    rushMark: isRushedCage(cage) ? '催' : '',
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

export function steamerAwaitingPlacement() {
  return 'side'
}
