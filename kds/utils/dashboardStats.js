/**
 * KDS 首页（运营总览）统计辅助：档口过滤、待做份数/紧急、档口卡已制作/平均制作、未映射计数。
 * 首页马赛克始终传空过滤 = 全店各档；厨房锁死档由 ScreenSettingsManager 单独表达。
 */

import { isRefundOrder } from './constants.js'
import { buildCompletedCookingStats } from './kitchenStationStats.js'

/**
 * 空过滤 = 不限制（首页全店马赛克）。告警锁死档不走这里。
 * @param {string|undefined|null} stationId
 * @param {string[]} watchedStationIds
 */
export function isStationInWatched(stationId, watchedStationIds) {
  if (!Array.isArray(watchedStationIds) || watchedStationIds.length === 0) return true
  return typeof stationId === 'string' && watchedStationIds.includes(stationId)
}

/**
 * @param {Array<{ station?: string }>} mergedDishes
 * @param {string[]} watchedStationIds
 */
export function filterMergedDishesByWatched(mergedDishes, watchedStationIds) {
  if (!Array.isArray(mergedDishes)) return []
  if (!Array.isArray(watchedStationIds) || watchedStationIds.length === 0) {
    return mergedDishes
  }
  const watched = new Set(watchedStationIds)
  return mergedDishes.filter((dish) => dish && watched.has(dish.station))
}

/**
 * 待出餐剩余份数（无 quantity 时按 1 份；扣已出 served_quantity）。
 * 退菜行不计入，与厨房控制台 isPendingCookOrder 一致。
 * @param {Array<{ dish_status?: string, quantity?: number, served_quantity?: number, servedQuantity?: number }>|undefined|null} orders
 * @param {string} pendingStatus
 */
function pendingPortions(orders, pendingStatus) {
  let total = 0
  for (const order of orders || []) {
    if (!order || order.dish_status !== pendingStatus || isRefundOrder(order)) continue
    const quantity = Number(order.quantity) || 1
    const served = Number(order.served_quantity ?? order.servedQuantity) || 0
    total += Math.max(0, quantity - served)
  }
  return total
}

/**
 * 待制作份数 / 其中含紧急的菜品数（份数按待出餐订单行剩余量计）。
 * @param {Array<{ orders?: Array<{ dish_status?: string }>, urgentCount?: number }>} mergedDishes
 * @param {string} pendingStatus
 */
export function countPendingAndUrgent(mergedDishes, pendingStatus) {
  let total = 0
  let urgent = 0
  for (const dish of mergedDishes || []) {
    const portions = pendingPortions(dish.orders, pendingStatus)
    if (portions <= 0) continue
    total += portions
    if (dish.urgentCount > 0) urgent += 1
  }
  return { total, urgent }
}

/**
 * 全店未映射菜品名数量（station 空或落入 qita 兜底）。
 * @param {Array<{ dishName?: string, station?: string }>} mergedDishes
 * @param {string} qitaStationId
 */
export function countUnmappedDishNames(mergedDishes, qitaStationId = 'qita') {
  const names = new Set()
  for (const dish of mergedDishes || []) {
    if (!dish?.dishName) continue
    if (!dish.station || dish.station === qitaStationId) {
      names.add(dish.dishName)
    }
  }
  return names.size
}

/**
 * 档口状态行（空过滤 = 全部档口，供首页马赛克）。
 * @param {Array<{ id: string, name: string, color?: string }>} stationList
 * @param {Array<{ station?: string, orders?: Array<{ dish_status?: string }> }>} mergedDishes
 * @param {string[]} watchedStationIds
 * @param {string} pendingStatus
 * @returns {Array<{ id: string, name: string, color?: string, pendingCount: number, urgentCount: number, completedToday: number, avgCookingTime: string, active: boolean }>}
 */
export function buildWatchedStationStatuses(
  stationList,
  mergedDishes,
  watchedStationIds,
  pendingStatus
) {
  const all = Array.isArray(stationList) ? stationList : []
  const stations =
    !Array.isArray(watchedStationIds) || watchedStationIds.length === 0
      ? all
      : all.filter((s) => watchedStationIds.includes(s.id))

  return stations.map((station) => {
    let pendingCount = 0
    let urgentCount = 0
    const stationOrders = []
    for (const dish of mergedDishes || []) {
      if (!dish || dish.station !== station.id) continue
      const orders = dish.orders || []
      for (const order of orders) {
        if (order) stationOrders.push(order)
      }
      const portions = pendingPortions(orders, pendingStatus)
      if (portions <= 0) continue
      pendingCount += portions
      if (dish.urgentCount > 0) urgentCount += 1
    }
    const cooking = buildCompletedCookingStats(stationOrders)
    return {
      id: station.id,
      name: station.name,
      color: station.color,
      pendingCount,
      urgentCount,
      completedToday: cooking.completedToday,
      avgCookingTime: cooking.avgCookingTime,
      active: pendingCount > 0
    }
  })
}
