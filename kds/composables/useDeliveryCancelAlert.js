/**
 * Kitchen glue for deliveryCancelEngine: banner state + shared sound engine.
 * kitchen.vue only renders the banner and calls syncOrders / dismiss.
 */

import { ref, shallowRef } from 'vue'
import {
  createInitialState,
  dismiss as dismissEngine,
  step
} from '../utils/deliveryCancelEngine.js'
import { playCancelAlert } from '../utils/sound.js'
import { ScreenSettingsManager } from '../utils/storage.js'

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
  let claimedAt = null
  let alertParams = ScreenSettingsManager.getAlertParams()

  function reloadConfig() {
    alertParams = ScreenSettingsManager.getAlertParams()
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
   */
  function syncOrders(orders) {
    const { state, effects } = step(engineState.value, {
      orders: Array.isArray(orders) ? orders : [],
      watchedStations: resolveWatchedStations()
    })
    publish(state)
    if (effects.playAlert) {
      playCancelAlert({ tone: alertParams.cancelTone, volume: alertParams.alertVolume })
      claimedAt = Date.now()
    }
    return Boolean(effects.playAlert)
  }

  function higherKindClaimed() {
    return claimedAt != null && Date.now() - claimedAt < HIGHER_KIND_CLAIM_MS
  }

  function dismissDeliveryCancelAlert() {
    publish(dismissEngine(engineState.value))
  }

  return {
    deliveryCancelAlert,
    syncOrders,
    dismissDeliveryCancelAlert,
    setWatchedStations,
    reloadConfig,
    higherKindClaimed
  }
}
