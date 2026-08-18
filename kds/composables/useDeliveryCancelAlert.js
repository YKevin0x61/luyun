/**
 * Kitchen glue for the 退菜/取消 engine: banner state + shared sound engine.
 * kitchen.vue only renders the banner and calls syncOrders / dismiss.
 * 1s tick re-prompts at CANCEL_REPEAT_SEC while the banner is visible.
 */

import { ref, shallowRef } from 'vue'
import {
  createInitialState,
  dismiss as dismissEngine,
  step
} from '../utils/deliveryCancelEngine.js'
import {
  acknowledgeNeverLoadedCancels,
  businessDateKey,
  loadAcknowledgedCancelIds
} from '../utils/cancelAck.js'
import { playCancelAlert } from '../utils/sound.js'
import { ScreenSettingsManager } from '../utils/storage.js'

const TICK_INTERVAL_MS = 1000
/** One kitchen tick / one higher-kind playback. Losing sounds are dropped, not queued. */
const HIGHER_KIND_CLAIM_MS = 1000

/**
 * @param {{
 *   watchedStations?: string[],
 *   getWatchedStations?: () => string[],
 * }} [options]
 */
export function useDeliveryCancelAlert(options = {}) {
  let watchedStations = Array.isArray(options.watchedStations) ? [...options.watchedStations] : []
  const getWatchedStations =
    typeof options.getWatchedStations === 'function' ? options.getWatchedStations : null
  /** @type {import('vue').ShallowRef<ReturnType<typeof createInitialState>>} */
  const engineState = shallowRef(createInitialState())
  const deliveryCancelAlert = ref({ ...engineState.value.banner })
  const acknowledgedCancelIds = ref([...loadAcknowledgedCancelIds(businessDateKey())])
  let claimedAt = null
  let alertParams = ScreenSettingsManager.getAlertParams()
  /** @type {object[] | null} */
  let latestOrders = null
  /** @type {ReturnType<typeof setInterval> | null} */
  let tickTimer = null
  let started = false

  function reloadAcked(now = Date.now()) {
    acknowledgedCancelIds.value = [...loadAcknowledgedCancelIds(businessDateKey(now))]
  }

  function reloadConfig() {
    alertParams = ScreenSettingsManager.getAlertParams()
    reloadAcked()
  }

  function resolveWatchedStations() {
    if (getWatchedStations) {
      const next = getWatchedStations()
      return Array.isArray(next) ? next : []
    }
    return watchedStations
  }

  function setWatchedStations(next) {
    watchedStations = Array.isArray(next) ? [...next] : []
  }

  function publish(next) {
    engineState.value = next
    deliveryCancelAlert.value = { ...next.banner }
  }

  /**
   * @param {object[]} orders
   * @param {number} [now]
   */
  function runStep(orders, now = Date.now()) {
    const { state, effects } = step(engineState.value, {
      orders: Array.isArray(orders) ? orders : [],
      watchedStations: resolveWatchedStations(),
      now
    })
    publish(state)
    if (effects.playAlert) {
      playCancelAlert({ tone: alertParams.cancelTone, volume: alertParams.alertVolume })
      claimedAt = now
    }
    return Boolean(effects.playAlert)
  }

  /**
   * @param {object[]} orders
   */
  function syncOrders(orders) {
    latestOrders = Array.isArray(orders) ? orders : []
    return runStep(latestOrders, Date.now())
  }

  function tick() {
    if (latestOrders == null) return false
    return runStep(latestOrders, Date.now())
  }

  function start() {
    if (started) return
    started = true
    reloadConfig()
    tickTimer = setInterval(tick, TICK_INTERVAL_MS)
  }

  function stop() {
    if (tickTimer != null) {
      clearInterval(tickTimer)
      tickTimer = null
    }
    started = false
    latestOrders = null
  }

  function higherKindClaimed() {
    if (engineState.value.banner.visible) return true
    return claimedAt != null && Date.now() - claimedAt < HIGHER_KIND_CLAIM_MS
  }

  function dismissDeliveryCancelAlert() {
    claimedAt = null
    const next = acknowledgeNeverLoadedCancels({
      orders: latestOrders || [],
      watchedStations: resolveWatchedStations(),
      now: Date.now()
    })
    acknowledgedCancelIds.value = [...next]
    publish(dismissEngine(engineState.value))
  }

  return {
    deliveryCancelAlert,
    acknowledgedCancelIds,
    syncOrders,
    dismissDeliveryCancelAlert,
    setWatchedStations,
    reloadConfig,
    higherKindClaimed,
    start,
    stop
  }
}
