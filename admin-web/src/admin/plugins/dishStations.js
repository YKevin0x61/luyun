/**
 * dish_stations table specialization hooks for Admin DataTable.
 * UI extras are wired in tablePlugins.js (keeps this module Vue-free for unit tests).
 */

/** @param {{ column: string, res?: object, ids?: unknown[] }} args */
export function afterBatchUpdate({ column, res = {}, ids = [] }) {
  let msg = res.message || `已更新 ${res.affected ?? ids.length} 条`
  if (['station', 'station_id'].includes(String(column).toLowerCase())) {
    msg += '；如需同步订单档口请点击「补充档口」'
  }
  return msg
}

export function afterQuickAdd({ reload } = {}) {
  if (typeof reload === 'function') reload()
}

export const dishStationsPluginBase = {
  table: 'dish_stations',
  /** Generic Admin DataTable is read-only; writes go through /api/dish-stations. */
  readOnly: true,
  afterBatchUpdate,
  afterQuickAdd,
}
