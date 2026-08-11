/**
 * KDS 首页（运营总览）统计辅助：职责档口过滤、待做/紧急、未映射计数。
 */

/**
 * 空职责集 = 全部档口（与 ScreenSettingsManager / ADR 0002 一致）。
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
 * 待制作菜品数 / 其中含紧急的菜品数（按 mergedDishes 行计，非份数）。
 * @param {Array<{ orders?: Array<{ dish_status?: string }>, urgentCount?: number }>} mergedDishes
 * @param {string} pendingStatus
 */
export function countPendingAndUrgent(mergedDishes, pendingStatus) {
  let total = 0
  let urgent = 0
  for (const dish of mergedDishes || []) {
    const hasPending = (dish.orders || []).some((o) => o && o.dish_status === pendingStatus)
    if (!hasPending) continue
    total += 1
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
 * 本屏职责档口状态行（空职责集 = 全部档口）。
 * @param {Array<{ id: string, name: string, color?: string }>} stationList
 * @param {Array<{ station?: string, orders?: Array<{ dish_status?: string }> }>} mergedDishes
 * @param {string[]} watchedStationIds
 * @param {string} pendingStatus
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
    const pendingCount = (mergedDishes || []).filter(
      (dish) =>
        dish &&
        dish.station === station.id &&
        (dish.orders || []).some((o) => o && o.dish_status === pendingStatus)
    ).length
    return {
      id: station.id,
      name: station.name,
      color: station.color,
      pendingCount,
      active: pendingCount > 0
    }
  })
}
