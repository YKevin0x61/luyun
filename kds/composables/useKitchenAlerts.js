/**
 * Kitchen-page glue for the pure alert engine + sound engine.
 *
 * Owns: engine state, 1s tick, effect execution (new-order ding + overtime
 * alarm), H5 unlock overlay flag.
 * Does not own Vue template/CSS — kitchen.vue renders from the returned refs.
 *
 * Alert params are snapshotted in start() (settings tip: re-enter kitchen to apply).
 */

import { ref, computed, shallowRef } from 'vue'
import {
  createInitialState,
  step,
  acknowledge as acknowledgeEngine
} from '../utils/alertEngine.js'
import {
  playNewOrderDing,
  playOvertimeAlarm,
  unlockSound,
  isSoundUnlocked
} from '../utils/sound.js'
import { ScreenSettingsManager } from '../utils/storage.js'

const TICK_INTERVAL_MS = 1000
/** One kitchen tick / one higher-kind playback. Losing sounds are dropped, not queued. */
const HIGHER_KIND_CLAIM_MS = 1000

function isH5Runtime() {
  return typeof window !== 'undefined' && !!window.document
}

/**
 * Map engine borderState to kitchen screen-border CSS modifier.
 * Glossary: green | yellow-flash | red only — overtime is never a border visual.
 * @param {string} borderState
 * @returns {'green' | 'yellow' | 'red'}
 */
export function toScreenBorderVisual(borderState) {
  if (borderState === 'yellow') return 'yellow'
  if (borderState === 'red') return 'red'
  return 'green'
}

function readAlertConfig() {
  const settings = ScreenSettingsManager.getSettings()
  return {
    watchedStations: settings.watchedStations,
    ...settings.alert
  }
}

/**
 * @returns {object}
 */
export function useKitchenAlerts() {
  /** @type {import('vue').ShallowRef<ReturnType<typeof createInitialState>>} */
  const engineState = shallowRef(createInitialState())
  const borderState = ref('green')
  const awaitingAck = ref(false)
  /** flowId -> 'yellow' | 'busy' */
  const newBadgeModes = ref(/** @type {Record<string, string>} */ ({}))
  const showSoundUnlockOverlay = ref(false)

  /** @type {object[] | null} */
  let latestOrders = null
  /** @type {ReturnType<typeof setInterval> | null} */
  let tickTimer = null
  let started = false
  /** @type {ReturnType<typeof readAlertConfig> | null} */
  let alertConfig = null
  let claimedAt = null

  const screenBorderVisual = computed(() => toScreenBorderVisual(borderState.value))

  function publishState(next) {
    engineState.value = next
    borderState.value = next.borderState
    awaitingAck.value = next.awaitingAck
    const modes = {}
    for (const [flowId, badge] of next.newBadges) {
      modes[flowId] = badge.mode
    }
    newBadgeModes.value = modes
  }

  function runStep(orders, now = Date.now(), options = {}) {
    const config = alertConfig || readAlertConfig()
    const { state, effects } = step(engineState.value, {
      orders: Array.isArray(orders) ? orders : [],
      config,
      now
    })
    publishState(state)
    const cancelClaimed = Boolean(options.cancelClaimed)
    if (effects.dingCount > 0 && !cancelClaimed) {
      playNewOrderDing(effects.dingCount, {
        tone: config.newOrderTone,
        volume: config.alertVolume
      })
      claimedAt = now
    }
    if (effects.overtimeAlarm && !cancelClaimed) {
      playOvertimeAlarm({
        tone: config.overtimeTone,
        volume: config.alertVolume
      })
      claimedAt = now
    }
  }

  /**
   * Sync engine with the current order list (after mount baseline / nudge refetch).
   * Engine always runs (badges / awaitingAck / lastOvertimeAlarmAt).
   * Playback is skipped when delivery-cancel claimed this same orders sync.
   * @param {object[]} orders
   * @param {{ cancelClaimed?: boolean }} [options]
   */
  function syncOrders(orders, options = {}) {
    latestOrders = Array.isArray(orders) ? orders : []
    runStep(latestOrders, Date.now(), options)
  }

  function higherKindClaimed() {
    return claimedAt != null && Date.now() - claimedAt < HIGHER_KIND_CLAIM_MS
  }

  /** 1s tick: busy-badge auto-dismiss + idle re-escalate + overtime repeat. */
  function tick() {
    if (latestOrders == null) return
    runStep(latestOrders, Date.now())
  }

  function acknowledge() {
    const next = acknowledgeEngine(engineState.value)
    publishState(next)
  }

  /**
   * True if any order in the dish group carries a new-order badge.
   * @param {{ orders?: object[] } | null | undefined} dish
   */
  function dishHasNewBadge(dish) {
    const orders = dish?.orders
    if (!Array.isArray(orders) || orders.length === 0) return false
    const modes = newBadgeModes.value
    return orders.some((order) => {
      const flowId = String(order?.business_flow_id || order?.id || '')
      return flowId !== '' && Boolean(modes[flowId])
    })
  }

  async function unlockSoundFromGesture() {
    await unlockSound()
    showSoundUnlockOverlay.value = !isSoundUnlocked()
  }

  /** Re-read device-local alert + watched stations (settings tip: call on show / refresh). */
  function reloadConfig() {
    alertConfig = readAlertConfig()
  }

  function start() {
    if (started) return
    started = true
    reloadConfig()
    if (isH5Runtime() && !isSoundUnlocked()) {
      showSoundUnlockOverlay.value = true
    }
    tickTimer = setInterval(tick, TICK_INTERVAL_MS)
  }

  function stop() {
    if (tickTimer != null) {
      clearInterval(tickTimer)
      tickTimer = null
    }
    started = false
    latestOrders = null
    alertConfig = null
  }

  return {
    screenBorderVisual,
    awaitingAck,
    showSoundUnlockOverlay,
    syncOrders,
    acknowledge,
    dishHasNewBadge,
    unlockSoundFromGesture,
    reloadConfig,
    start,
    stop,
    higherKindClaimed
  }
}
