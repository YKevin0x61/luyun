/**
 * Kitchen glue for connection edge alerts: watch status + APP beep/vibrate.
 */

import { computed, watch } from 'vue'
import { connectionEdge } from '../utils/connectionEdge.js'

const DISCONNECT_ALERT_BEEP_COUNT = 2

function playDisconnectAlert() {
  // #ifdef APP-PLUS
  try {
    if (typeof plus !== 'undefined' && plus.device && typeof plus.device.beep === 'function') {
      plus.device.beep(DISCONNECT_ALERT_BEEP_COUNT)
    }
  } catch (error) {
    console.warn('[厨房] 断连提示音播放失败:', error)
  }
  try {
    uni.vibrateLong()
  } catch (error) {
    console.warn('[厨房] 断连振动提示失败:', error)
  }
  // #endif
}

/**
 * @param {() => string} getStatus  e.g. () => realtimeStore.connectionStatus
 */
export function useDisconnectAlert(getStatus) {
  const showDisconnectBanner = computed(() => getStatus() !== 'connected')
  let stopWatching = null

  function start() {
    if (stopWatching) return
    stopWatching = watch(getStatus, (newStatus, oldStatus) => {
      if (connectionEdge(oldStatus, newStatus) === 'disconnect') {
        playDisconnectAlert()
      }
    })
  }

  function stop() {
    if (stopWatching) {
      stopWatching()
      stopWatching = null
    }
  }

  return {
    showDisconnectBanner,
    start,
    stop
  }
}
