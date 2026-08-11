/**
 * Kitchen glue for deliveryCancelEngine: banner state + APP beep/vibrate.
 * kitchen.vue only renders the banner and calls syncOrders / dismiss.
 */

import { ref, shallowRef } from 'vue'
import {
  createInitialState,
  dismiss as dismissEngine,
  step
} from '../utils/deliveryCancelEngine.js'

const CANCEL_ALERT_BEEP_COUNT = 4

function playCancelAlert() {
  // #ifdef APP-PLUS
  try {
    if (typeof plus !== 'undefined' && plus.device && typeof plus.device.beep === 'function') {
      plus.device.beep(CANCEL_ALERT_BEEP_COUNT)
    }
  } catch (error) {
    console.warn('[厨房] 外卖取消提示音播放失败:', error)
  }
  try {
    uni.vibrateLong()
  } catch (error) {
    console.warn('[厨房] 外卖取消振动提示失败:', error)
  }
  // #endif
}

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
    if (effects.playAlert) playCancelAlert()
  }

  function dismissDeliveryCancelAlert() {
    publish(dismissEngine(engineState.value))
  }

  return {
    deliveryCancelAlert,
    syncOrders,
    dismissDeliveryCancelAlert,
    setWatchedStations
  }
}
