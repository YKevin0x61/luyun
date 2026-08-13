/**
 * Kitchen station stats: tab pending/urgent, wait-time decoration,
 * and completed-today / average-cooking (kitchen tiles + Hub mosaic secondary).
 */

import { TimeCalculator } from './timeCalculator.js'
import { DISH_STATUS } from './constants.js'

/**
 * @param {string[]} stationIds
 * @param {(stationId: string) => unknown[]} getOrdersByStation
 * @param {{ urgentMs: number, isPending?: (order: object) => boolean }} options
 * @returns {Record<string, { pending: number, urgent: number }>}
 */
export function buildStationTabStats(stationIds, getOrdersByStation, options) {
  const urgentMs = options.urgentMs
  const isPending =
    options.isPending ||
    ((order) => order && order.dish_status === DISH_STATUS.PENDING)
  const stats = {}
  for (const stationId of stationIds || []) {
    const pending = (getOrdersByStation(stationId) || []).filter(isPending)
    stats[stationId] = {
      pending: pending.length,
      urgent: pending.filter((order) => {
        try {
          return TimeCalculator.calculateWaitTime(new Date(order.order_time)) > urgentMs
        } catch {
          return false
        }
      }).length,
    }
  }
  return stats
}

/**
 * @param {string|Date|null|undefined} orderTime
 * @param {Date|number} now
 * @param {{ warningMs: number, urgentMs: number }} thresholds
 */
export function decorateOrderWait(orderTime, now, thresholds) {
  const start = new Date(orderTime).getTime()
  const nowMs = now instanceof Date ? now.getTime() : Number(now)
  const waitTime =
    Number.isFinite(start) && Number.isFinite(nowMs) ? Math.max(0, nowMs - start) : 0
  const waitTimeText = TimeCalculator.formatDurationClock(waitTime)
  let waitTimeClass = 'normal'
  if (waitTime > thresholds.urgentMs) waitTimeClass = 'urgent'
  else if (waitTime > thresholds.warningMs) waitTimeClass = 'warning'
  return {
    waitTime,
    waitTimeText,
    waitTimeClass,
    isOvertime: waitTime > thresholds.urgentMs,
  }
}

const MAX_COOKING_DURATION_MS = 24 * 60 * 60 * 1000
const EMPTY_COMPLETED_COOKING = Object.freeze({
  completedToday: 0,
  avgCookingTime: '0分',
})

/**
 * Today’s cooked 订单行 with ready_time (kitchen tile 已制作 / 平均制作).
 * @param {object[]} stationOrders
 * @returns {{ completedToday: number, avgCookingTime: string }}
 */
export function buildCompletedCookingStats(stationOrders) {
  if (!Array.isArray(stationOrders)) return { ...EMPTY_COMPLETED_COOKING }

  const completedCookingOrders = stationOrders.filter(
    (order) =>
      order &&
      (order.dish_status === DISH_STATUS.READY || order.dish_status === DISH_STATUS.SERVED) &&
      order.ready_time &&
      TimeCalculator.isToday(order.ready_time)
  )

  const validDurations = []
  for (const order of completedCookingOrders) {
    const hasValidOrderTime =
      order.order_time && order.order_time !== 'undefined' && order.order_time !== 'null'
    const hasValidReadyTime =
      order.ready_time && order.ready_time !== 'undefined' && order.ready_time !== 'null'
    if (!hasValidOrderTime || !hasValidReadyTime) continue
    try {
      const duration = TimeCalculator.calculateCookingDuration(order.order_time, order.ready_time)
      if (duration > 0 && duration <= MAX_COOKING_DURATION_MS) {
        validDurations.push(duration)
      }
    } catch {
      // skip bad timestamps
    }
  }

  let avgCookingMinutes = 0
  if (validDurations.length > 0) {
    const totalTime = validDurations.reduce((sum, duration) => sum + duration, 0)
    avgCookingMinutes = totalTime / validDurations.length / (60 * 1000)
  }

  return {
    completedToday: completedCookingOrders.length,
    avgCookingTime:
      avgCookingMinutes > 0
        ? TimeCalculator.formatAvgCookingMinutes(avgCookingMinutes)
        : EMPTY_COMPLETED_COOKING.avgCookingTime,
  }
}

/**
 * @param {object[]} stationOrders
 * @param {{ urgentMs: number, isPending?: (order: object) => boolean }} options
 */
export function buildCurrentStationStats(stationOrders, options) {
  const defaultStats = {
    overtimeCount: 0,
    pendingCount: 0,
    ...EMPTY_COMPLETED_COOKING,
  }
  if (!Array.isArray(stationOrders)) return defaultStats

  const isPending =
    options.isPending ||
    ((order) => order && order.dish_status === DISH_STATUS.PENDING)
  const pendingOrders = stationOrders.filter(isPending)

  const overtimeCount = pendingOrders.filter((order) => {
    try {
      return TimeCalculator.calculateWaitTime(new Date(order.order_time)) > options.urgentMs
    } catch {
      return false
    }
  }).length

  return {
    overtimeCount,
    pendingCount: pendingOrders.length,
    ...buildCompletedCookingStats(stationOrders),
  }
}
