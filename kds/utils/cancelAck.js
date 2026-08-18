/**
 * Device-local 退菜已确认. Not a server field.
 * Keyed by 营业日 (local calendar date). Another screen has its own storage.
 */

export const CANCEL_ACK_STORAGE_KEY = 'kds_cancel_ack'

/**
 * @param {object} order
 * @returns {string}
 */
export function cancelAckLineId(order) {
  return String(order?.business_flow_id || order?.id || order?._id || '')
}

/**
 * Local calendar date YYYY-MM-DD, matching kitchen startOfDay.
 * @param {number|Date} [now]
 * @returns {string}
 */
export function businessDateKey(now = Date.now()) {
  const date = now instanceof Date ? now : new Date(now)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function readPayload() {
  try {
    if (typeof uni === 'undefined' || typeof uni.getStorageSync !== 'function') return null
    const raw = uni.getStorageSync(CANCEL_ACK_STORAGE_KEY)
    if (!raw) return null
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (!parsed || typeof parsed !== 'object') return null
    return parsed
  } catch {
    return null
  }
}

function writePayload(businessDate, ids) {
  try {
    if (typeof uni === 'undefined' || typeof uni.setStorageSync !== 'function') return
    uni.setStorageSync(
      CANCEL_ACK_STORAGE_KEY,
      JSON.stringify({
        businessDate: String(businessDate),
        ids: [...ids]
      })
    )
  } catch (error) {
    console.error('保存退菜已确认失败:', error)
  }
}

/**
 * @param {string} businessDate
 * @returns {Set<string>}
 */
export function loadAcknowledgedCancelIds(businessDate) {
  const date = String(businessDate)
  const parsed = readPayload()
  if (!parsed || parsed.businessDate !== date) return new Set()
  const ids = Array.isArray(parsed.ids) ? parsed.ids : []
  return new Set(ids.map(String).filter(Boolean))
}

/**
 * @param {string} businessDate
 * @param {Iterable<string>} ids
 * @returns {Set<string>}
 */
export function acknowledgeCancelIds(businessDate, ids) {
  const date = String(businessDate)
  const next = loadAcknowledgedCancelIds(date)
  for (const id of ids || []) {
    const lineId = String(id || '')
    if (lineId) next.add(lineId)
  }
  writePayload(date, next)
  return next
}

function isWatched(watchedStations, order) {
  if (!Array.isArray(watchedStations) || watchedStations.length === 0) return false
  const station = typeof order?.station === 'string' ? order.station.trim() : ''
  return station !== '' && watchedStations.includes(station)
}

function isCancelled(order) {
  return Boolean(order && order.dish_status === '已取消')
}

/**
 * 未做退示 candidate: 已取消 and never loaded (no placement, no loaded_at).
 * 抽走 keeps loaded_at so it is not 退示. 退菜占位 stays on the hole.
 * Parallel `_refund_` rows are not 退示.
 * @param {object} order
 */
export function isNeverLoadedCancel(order) {
  if (!isCancelled(order)) return false
  if (order.placement) return false
  if (order.loaded_at) return false
  return true
}

/**
 * @param {object[]} orders
 * @param {{ watchedStations?: string[], acknowledgedIds?: Iterable<string> }} [opts]
 * @returns {string[]}
 */
export function listUnackedNeverLoadedCancelIds(orders, opts = {}) {
  const watchedStations = Array.isArray(opts.watchedStations) ? opts.watchedStations : []
  const acked = new Set([...(opts.acknowledgedIds || [])].map(String).filter(Boolean))
  const ids = []
  for (const order of Array.isArray(orders) ? orders : []) {
    if (!isWatched(watchedStations, order)) continue
    if (!isNeverLoadedCancel(order)) continue
    const lineId = cancelAckLineId(order)
    if (!lineId || acked.has(lineId)) continue
    ids.push(lineId)
  }
  return ids
}

/**
 * 「知道了」: persist every current watched 未做退示 for this 营业日.
 * @param {{ orders?: object[], watchedStations?: string[], now?: number|Date }} [input]
 * @returns {Set<string>}
 */
export function acknowledgeNeverLoadedCancels(input = {}) {
  const date = businessDateKey(input.now)
  const current = loadAcknowledgedCancelIds(date)
  const extra = listUnackedNeverLoadedCancelIds(input.orders, {
    watchedStations: input.watchedStations,
    acknowledgedIds: current
  })
  return acknowledgeCancelIds(date, extra)
}

/**
 * @param {string} lineId
 * @param {Iterable<string>|Set<string>|null|undefined} acknowledgedIds
 */
export function isCancelAcknowledged(lineId, acknowledgedIds) {
  const id = String(lineId || '')
  if (!id || acknowledgedIds == null) return false
  if (typeof acknowledgedIds.has === 'function') return acknowledgedIds.has(id)
  if (Array.isArray(acknowledgedIds)) {
    return acknowledgedIds.some((item) => String(item) === id)
  }
  return false
}
