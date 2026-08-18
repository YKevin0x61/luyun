/**
 * Pure 退菜/取消 alert engine (no Vue / uni / audio).
 *
 * Snapshot-diff: previous dish_status by flowId + current orders + watchedStations
 * → next state + effects. Dine-in and delivery in the watched station set can
 * raise an alert (empty watched = no stations). A wave of only 退菜占位 is silent.
 */

import { deriveSteamerPhase, STEAMER_PHASE_CANCEL_HOLD } from './steamerConsole.js'

export const DELIVERY_CANCELLED_DISH_STATUS = '已取消'
export const WORKED_DISH_STATUSES = Object.freeze(['已制作待上菜', '已上菜'])
export const CANCEL_SUMMARY_MAX_ITEMS = 6
/** Re-prompt cadence for 退菜/取消. Independent of new-order busy/idle. */
export const CANCEL_REPEAT_SEC = 20

/**
 * @typedef {{
 *   visible: boolean,
 *   count: number,
 *   hasCooked: boolean,
 *   summary: string
 * }} CancelBanner
 */

/**
 * @typedef {{
 *   primed: boolean,
 *   statusByFlowId: Map<string, string|undefined>,
 *   banner: CancelBanner,
 *   lastAlertAt: number|null
 * }} DeliveryCancelState
 */

/**
 * @typedef {{ playAlert: boolean }} DeliveryCancelEffects
 */

/**
 * @returns {CancelBanner}
 */
export function emptyBanner() {
  return { visible: false, count: 0, hasCooked: false, summary: '' }
}

/**
 * @returns {DeliveryCancelState}
 */
export function createInitialState() {
  return {
    primed: false,
    statusByFlowId: new Map(),
    banner: emptyBanner(),
    lastAlertAt: null
  }
}

/**
 * @param {object} order
 * @returns {string}
 */
function flowIdOf(order) {
  return String(order?.business_flow_id || order?.id || '')
}

/**
 * @param {string[]} watchedStations
 * @param {object} order
 */
function isWatched(watchedStations, order) {
  if (!Array.isArray(watchedStations) || watchedStations.length === 0) return false
  const station = typeof order.station === 'string' ? order.station.trim() : ''
  return station !== '' && watchedStations.includes(station)
}

/**
 * 退菜占位: cancelled and already on a steamer hole.
 * Uses steamer phase so hole detection stays one definition.
 * @param {object} order
 */
function isCancelHold(order) {
  return deriveSteamerPhase(order) === STEAMER_PHASE_CANCEL_HOLD
}

/**
 * 未做退示 at cancel time: cancelled, never loaded.
 * Do not use notice_seconds expiry here (ticket 03); a just-cancelled
 * unloaded line still counts so the banner can fire.
 * @param {object} order
 */
function isUnloadedNotice(order) {
  if (order?.placement) return false
  if (order?.loaded_at) return false
  return true
}

/**
 * @param {object[]} items
 * @returns {CancelBanner}
 */
export function buildBanner(items) {
  const list = Array.isArray(items) ? items : []
  const shown = list.slice(0, CANCEL_SUMMARY_MAX_ITEMS)
  let summary = shown.map((item) => `${item.tableNumber} ${item.dishName}`.trim()).join('、')
  if (list.length > CANCEL_SUMMARY_MAX_ITEMS) {
    summary += `等 ${list.length} 项`
  }
  return {
    visible: list.length > 0,
    count: list.length,
    hasCooked: list.some((item) => item.cooked),
    summary
  }
}

/**
 * @param {object[]} orders
 * @returns {Map<string, string|undefined>}
 */
function buildStatusMap(orders) {
  const map = new Map()
  if (!Array.isArray(orders)) return map
  for (const order of orders) {
    if (!order) continue
    const flowId = flowIdOf(order)
    if (!flowId) continue
    map.set(flowId, order.dish_status)
  }
  return map
}

/**
 * @param {DeliveryCancelState} state
 * @param {{ orders?: object[], watchedStations?: string[], now?: number }} input
 * @returns {{ state: DeliveryCancelState, effects: DeliveryCancelEffects }}
 */
export function step(state, input = {}) {
  const orders = Array.isArray(input.orders) ? input.orders : []
  const watchedStations = Array.isArray(input.watchedStations) ? input.watchedStations : []
  const now = Number.isFinite(input.now) ? input.now : Date.now()
  const workedSet = new Set(WORKED_DISH_STATUSES)
  const nextMap = buildStatusMap(orders)

  /** @type {{ tableNumber: string, dishName: string, cooked: boolean }[]} */
  const alertable = []

  if (state.primed) {
    for (const order of orders) {
      if (!order || order.dish_status !== DELIVERY_CANCELLED_DISH_STATUS) continue
      if (!isWatched(watchedStations, order)) continue
      const flowId = flowIdOf(order)
      if (!flowId) continue
      const previousStatus = state.statusByFlowId.get(flowId)
      if (previousStatus === undefined || previousStatus === DELIVERY_CANCELLED_DISH_STATUS) {
        continue
      }
      const cooked = workedSet.has(previousStatus)
      if (!cooked && isCancelHold(order)) continue
      if (!cooked && !isUnloadedNotice(order)) continue
      alertable.push({
        tableNumber: order.table_number || '',
        dishName: order.dish_name || '',
        cooked
      })
    }
  }

  let banner = state.banner
  let playAlert = false
  let lastAlertAt = state.lastAlertAt ?? null

  if (alertable.length > 0) {
    banner = buildBanner(alertable)
    playAlert = true
    lastAlertAt = now
  } else if (
    banner.visible
    && lastAlertAt != null
    && now - lastAlertAt >= CANCEL_REPEAT_SEC * 1000
  ) {
    playAlert = true
    lastAlertAt = now
  }

  return {
    state: {
      primed: true,
      statusByFlowId: nextMap,
      banner,
      lastAlertAt
    },
    effects: { playAlert }
  }
}

/**
 * @param {DeliveryCancelState} state
 * @returns {DeliveryCancelState}
 */
export function dismiss(state) {
  return {
    ...state,
    banner: emptyBanner(),
    lastAlertAt: null
  }
}
