/**
 * Pure delivery-cancel alert engine (no Vue / uni / audio).
 *
 * Snapshot-diff: previous dish_status by flowId + current orders + watchedStations
 * → next state + effects. Only source=delivery orders in the watched station set
 * can raise an alert (empty watched = all stations).
 */

export const DELIVERY_CANCELLED_DISH_STATUS = '已取消'
export const WORKED_DISH_STATUSES = Object.freeze(['已制作待上菜', '已上菜'])
export const CANCEL_SUMMARY_MAX_ITEMS = 6
export const DELIVERY_SOURCE = 'delivery'

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
 *   banner: CancelBanner
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
    banner: emptyBanner()
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
  if (!Array.isArray(watchedStations) || watchedStations.length === 0) return true
  const station = typeof order.station === 'string' ? order.station.trim() : ''
  return station !== '' && watchedStations.includes(station)
}

/**
 * @param {object} order
 */
function isDeliveryOrder(order) {
  return (order?.source || '') === DELIVERY_SOURCE
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
 * @param {{ orders?: object[], watchedStations?: string[] }} input
 * @returns {{ state: DeliveryCancelState, effects: DeliveryCancelEffects }}
 */
export function step(state, input = {}) {
  const orders = Array.isArray(input.orders) ? input.orders : []
  const watchedStations = Array.isArray(input.watchedStations) ? input.watchedStations : []
  const workedSet = new Set(WORKED_DISH_STATUSES)
  const nextMap = buildStatusMap(orders)

  /** @type {{ tableNumber: string, dishName: string, cooked: boolean }[]} */
  const newlyCancelled = []

  if (state.primed) {
    for (const order of orders) {
      if (!order || order.dish_status !== DELIVERY_CANCELLED_DISH_STATUS) continue
      if (!isDeliveryOrder(order)) continue
      if (!isWatched(watchedStations, order)) continue
      const flowId = flowIdOf(order)
      if (!flowId) continue
      const previousStatus = state.statusByFlowId.get(flowId)
      if (previousStatus !== undefined && previousStatus !== DELIVERY_CANCELLED_DISH_STATUS) {
        newlyCancelled.push({
          tableNumber: order.table_number || '外卖',
          dishName: order.dish_name || '',
          cooked: workedSet.has(previousStatus)
        })
      }
    }
  }

  const banner = newlyCancelled.length > 0 ? buildBanner(newlyCancelled) : state.banner

  return {
    state: {
      primed: true,
      statusByFlowId: nextMap,
      banner
    },
    effects: { playAlert: newlyCancelled.length > 0 }
  }
}

/**
 * @param {DeliveryCancelState} state
 * @returns {DeliveryCancelState}
 */
export function dismiss(state) {
  return {
    ...state,
    banner: emptyBanner()
  }
}
