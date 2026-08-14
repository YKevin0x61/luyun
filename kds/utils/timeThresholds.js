/**
 * KDS wait thresholds (ms) derived from local alert params.
 * Defaults match backend PRIORITY_LEVELS (warn 15 / urgent 20 minutes).
 */

import { ScreenSettingsManager } from './storage.js'

/**
 * @param {{ warnMin?: number, urgentMin?: number } | null | undefined} [params]
 * @returns {{ warning: number, urgent: number }}
 */
export function getTimeThresholdsMs(params) {
  const alert = params && typeof params === 'object'
    ? params
    : ScreenSettingsManager.getAlertParams()
  const warnMin = Number(alert.warnMin)
  const urgentMin = Number(alert.urgentMin)
  return {
    warning: (Number.isFinite(warnMin) ? warnMin : 15) * 60 * 1000,
    urgent: (Number.isFinite(urgentMin) ? urgentMin : 20) * 60 * 1000
  }
}

/**
 * Steam-urgency thresholds (ms). Independent of wait warnMin / urgentMin.
 * @param {{ steamWarnMin?: number, steamUrgentMin?: number } | null | undefined} [params]
 * @returns {{ warning: number, urgent: number }}
 */
export function getSteamTimeThresholdsMs(params) {
  const alert = params && typeof params === 'object'
    ? params
    : ScreenSettingsManager.getAlertParams()
  const warnMin = Number(alert.steamWarnMin)
  const urgentMin = Number(alert.steamUrgentMin)
  return {
    warning: (Number.isFinite(warnMin) ? warnMin : 15) * 60 * 1000,
    urgent: (Number.isFinite(urgentMin) ? urgentMin : 20) * 60 * 1000
  }
}
