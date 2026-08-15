/**
 * Kitchen order session: one fetch → fan-out to new-order + delivery-cancel engines,
 * plus device-local watched stations / dish-card quantity cap / 下单间隔 / urgency thresholds.
 *
 * kitchen.vue should not import useKitchenAlerts / useDeliveryCancelAlert directly.
 * Print / serve / disconnect stay in the page.
 */

import { ref } from 'vue'
import { ScreenSettingsManager } from '../utils/storage.js'
import { getTimeThresholdsMs } from '../utils/timeThresholds.js'
import { buildCurrentStationStats } from '../utils/kitchenStationStats.js'
import { useKitchenAlerts } from './useKitchenAlerts.js'
import { useDeliveryCancelAlert } from './useDeliveryCancelAlert.js'

/**
 * @param {{ ordersStore: { orders: unknown[], fetchOrders: (q: object) => Promise<unknown> } }} options
 */
export function useKitchenOrderSession({ ordersStore }) {
  const loading = ref(false)
  const watchedStationIds = ref([...ScreenSettingsManager.getWatchedStations()])
  const thresholdsMs = ref(getTimeThresholdsMs())
  /** 0 = no dish-card split (today’s one card per dish name). */
  const dishCardQuantityCap = ref(ScreenSettingsManager.getDishCardQuantityCap())
  /** 0 = no 浪潮 split. */
  const orderGapMinutes = ref(ScreenSettingsManager.getOrderGapMinutes())

  const kitchenAlerts = useKitchenAlerts()
  const deliveryCancel = useDeliveryCancelAlert({
    getWatchedStations: () => watchedStationIds.value
  })

  function reloadDeviceSettings() {
    watchedStationIds.value = [...ScreenSettingsManager.getWatchedStations()]
    thresholdsMs.value = getTimeThresholdsMs()
    dishCardQuantityCap.value = ScreenSettingsManager.getDishCardQuantityCap()
    orderGapMinutes.value = ScreenSettingsManager.getOrderGapMinutes()
    kitchenAlerts.reloadConfig()
    deliveryCancel.reloadConfig()
  }

  /**
   * Pull today's orders once, then sync both alert engines.
   * Reloads watched stations + thresholds so settings changes apply without remount.
   */
  async function refresh() {
    if (loading.value) return
    loading.value = true
    try {
      reloadDeviceSettings()
      const today = new Date()
      const startOfDay = new Date(
        today.getFullYear(),
        today.getMonth(),
        today.getDate(),
        0,
        0,
        0,
        0
      )
      const endOfDay = new Date(
        today.getFullYear(),
        today.getMonth(),
        today.getDate(),
        23,
        59,
        59,
        999
      )
      await ordersStore.fetchOrders({
        start_time: startOfDay.toISOString(),
        end_time: endOfDay.toISOString()
      })
      const cancelClaimed = Boolean(deliveryCancel.syncOrders(ordersStore.orders))
      kitchenAlerts.syncOrders(ordersStore.orders, { cancelClaimed })
    } finally {
      loading.value = false
    }
  }

  function start() {
    reloadDeviceSettings()
    kitchenAlerts.start()
  }

  function stop() {
    kitchenAlerts.stop()
  }

  /** onShow / settings return: refresh watched + alert config without fetching. */
  function onShow() {
    reloadDeviceSettings()
  }

  /**
   * @param {object[]} stationOrders
   * @param {(order: object) => boolean} [isPending]
   */
  function currentStationStats(stationOrders, isPending) {
    return buildCurrentStationStats(stationOrders, {
      urgentMs: thresholdsMs.value.urgent,
      isPending
    })
  }

  /**
   * @param {number} oldestTimestamp
   * @param {number} now
   */
  function decorateDishWait(oldestTimestamp, now) {
    const waitTime = Math.max(0, now - oldestTimestamp)
    const { urgent, warning } = thresholdsMs.value
    let waitTimeClass = 'normal'
    if (waitTime > urgent) waitTimeClass = 'urgent'
    else if (waitTime > warning) waitTimeClass = 'high'
    return {
      maxWaitTime: waitTime,
      isOvertime: waitTime > urgent,
      waitTimeClass
    }
  }

  /**
   * Urgent count from sorted order timestamps (binary-search friendly cutoff).
   * @param {number[]} sortedTimestamps
   * @param {number} now
   */
  function urgentCutoff(now) {
    return now - thresholdsMs.value.urgent
  }

  function higherKindClaimed() {
    return Boolean(deliveryCancel.higherKindClaimed() || kitchenAlerts.higherKindClaimed())
  }

  return {
    loading,
    watchedStationIds,
    thresholdsMs,
    dishCardQuantityCap,
    orderGapMinutes,
    reloadDeviceSettings,
    refresh,
    start,
    stop,
    onShow,
    currentStationStats,
    decorateDishWait,
    urgentCutoff,
    screenBorderVisual: kitchenAlerts.screenBorderVisual,
    awaitingAck: kitchenAlerts.awaitingAck,
    showSoundUnlockOverlay: kitchenAlerts.showSoundUnlockOverlay,
    acknowledgeNewOrders: kitchenAlerts.acknowledge,
    dishHasNewBadge: kitchenAlerts.dishHasNewBadge,
    unlockSoundFromGesture: kitchenAlerts.unlockSoundFromGesture,
    deliveryCancelAlert: deliveryCancel.deliveryCancelAlert,
    dismissDeliveryCancelAlert: deliveryCancel.dismissDeliveryCancelAlert,
    higherKindClaimed
  }
}
