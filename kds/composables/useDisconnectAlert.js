/**
 * Kitchen glue for connection edge alerts: watch status + shared sound engine.
 */

import { computed, watch } from 'vue'
import { connectionEdge } from '../utils/connectionEdge.js'
import { playDisconnectAlert } from '../utils/sound.js'
import { ScreenSettingsManager } from '../utils/storage.js'

/**
 * @param {() => string} getStatus  e.g. () => realtimeStore.connectionStatus
 * @param {{ higherKindClaimed?: () => boolean }} [options]
 */
export function useDisconnectAlert(getStatus, options = {}) {
  const higherKindClaimed =
    typeof options.higherKindClaimed === 'function' ? options.higherKindClaimed : () => false
  const showDisconnectBanner = computed(() => getStatus() !== 'connected')
  let stopWatching = null
  let alertParams = ScreenSettingsManager.getAlertParams()

  function reloadConfig() {
    alertParams = ScreenSettingsManager.getAlertParams()
  }

  function start() {
    if (stopWatching) return
    reloadConfig()
    stopWatching = watch(getStatus, (newStatus, oldStatus) => {
      if (connectionEdge(oldStatus, newStatus) !== 'disconnect') return
      if (higherKindClaimed()) return
      playDisconnectAlert({ tone: alertParams.disconnectTone, volume: alertParams.alertVolume })
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
    stop,
    reloadConfig
  }
}
