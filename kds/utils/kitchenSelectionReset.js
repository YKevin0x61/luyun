/**
 * Settings-return signal for 厨房控制台 selections.
 * Visiting 系统设置 marks a pending clear; the kitchen page consumes it on show.
 * Cap / 下单间隔 changes are handled separately by the kitchen watcher.
 */

let settingsReturnPending = false

export function noteSettingsVisit() {
  settingsReturnPending = true
}

/** @returns {boolean} true once per settings visit, then false until the next visit */
export function takeSettingsReturnClear() {
  const pending = settingsReturnPending
  settingsReturnPending = false
  return pending
}

/**
 * Clear 出餐选中 only when the displayed station actually changes.
 * Same-station lock after a new-order refresh / 60s reconcile must keep picks.
 *
 * @param {string} currentStationId
 * @param {string} nextStationId
 * @returns {boolean}
 */
export function stationChangeClearsSelection(currentStationId, nextStationId) {
  if (nextStationId == null || nextStationId === '') return false
  return String(currentStationId ?? '') !== String(nextStationId)
}
