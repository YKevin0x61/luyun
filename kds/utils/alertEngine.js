/**
 * Pure KDS alert decision engine (no Vue / uni / DOM / audio).
 *
 * Snapshot-diff reducer: previous state + current orders + config + now
 * → next state + effects (dingCount / overtimeAlarm). Outer layer executes effects.
 */

import { DISH_STATUS, isRefundOrder } from './constants.js'
import { isNeverLoadedCancel } from './cancelAck.js'

/** @typedef {'green' | 'yellow' | 'red'} BorderState */
/** @typedef {'yellow' | 'busy'} BadgeMode */

/**
 * @typedef {{
 *   borderState: BorderState,
 *   awaitingAck: boolean,
 *   newBadges: Map<string, { mode: BadgeMode, at: number }>,
 *   snapshot: Map<string, { status: string, quantity: number }>,
 *   primed: boolean,
 *   lastReescalateAt: number | null,
 *   lastOvertimeAlarmAt: number | null
 * }} AlertState
 */

/**
 * @typedef {{ dingCount: number, overtimeAlarm: boolean }} AlertEffects
 */

/**
 * @typedef {{
 *   watchedStations?: string[],
 *   beepCap?: number,
 *   reescalateSec?: number,
 *   badgeDismissSec?: number,
 *   urgentMin?: number,
 *   steamUrgentMin?: number,
 *   overtimeRepeatSec?: number
 * }} AlertConfig
 */

const DEFAULT_CONFIG = Object.freeze({
  watchedStations: [],
  beepCap: 5,
  reescalateSec: 20,
  badgeDismissSec: 30,
  urgentMin: 20,
  steamUrgentMin: 20,
  overtimeRepeatSec: 30
})

/**
 * @returns {AlertState}
 */
export function createInitialState() {
  return {
    borderState: 'green',
    awaitingAck: false,
    newBadges: new Map(),
    snapshot: new Map(),
    primed: false,
    lastReescalateAt: null,
    lastOvertimeAlarmAt: null
  }
}

/**
 * @param {object} order
 * @returns {string}
 */
function flowIdOf(order) {
  return String(order.business_flow_id || order.id || '')
}

/**
 * @param {string[]} watchedStations
 * @param {object} order
 */
function isWatched(watchedStations, order) {
  if (!Array.isArray(watchedStations) || watchedStations.length === 0) return false
  const station = typeof order.station === 'string' ? order.station.trim() : ''
  return station !== '' && watchedStations.includes(station)
}

/**
 * @param {AlertConfig} raw
 */
function normalizeConfig(raw) {
  const c = raw && typeof raw === 'object' ? raw : {}
  return {
    watchedStations: Array.isArray(c.watchedStations) ? c.watchedStations : DEFAULT_CONFIG.watchedStations,
    beepCap: Number.isFinite(Number(c.beepCap)) ? Number(c.beepCap) : DEFAULT_CONFIG.beepCap,
    reescalateSec: Number.isFinite(Number(c.reescalateSec))
      ? Number(c.reescalateSec)
      : DEFAULT_CONFIG.reescalateSec,
    badgeDismissSec: Number.isFinite(Number(c.badgeDismissSec))
      ? Number(c.badgeDismissSec)
      : DEFAULT_CONFIG.badgeDismissSec,
    urgentMin: Number.isFinite(Number(c.urgentMin)) ? Number(c.urgentMin) : DEFAULT_CONFIG.urgentMin,
    steamUrgentMin: Number.isFinite(Number(c.steamUrgentMin))
      ? Number(c.steamUrgentMin)
      : DEFAULT_CONFIG.steamUrgentMin,
    overtimeRepeatSec: Number.isFinite(Number(c.overtimeRepeatSec))
      ? Number(c.overtimeRepeatSec)
      : DEFAULT_CONFIG.overtimeRepeatSec
  }
}

function asTimestamp(value) {
  if (value == null || value === '') return NaN
  if (typeof value === 'number') return value
  if (value instanceof Date) return value.getTime()
  return Date.parse(value)
}

function isCancelHold(order) {
  return Boolean(order && order.dish_status === '已取消' && order.placement)
}

function isCancelNotice(order) {
  return isNeverLoadedCancel(order)
}

/**
 * @param {object[]} orders
 * @param {string[]} watchedStations
 * @returns {Map<string, object>}
 */
function buildRelevantIndex(orders, watchedStations) {
  const index = new Map()
  if (!Array.isArray(orders)) return index
  for (const order of orders) {
    if (!order) continue
    const hold = isCancelHold(order)
    const notice = isCancelNotice(order)
    if (isRefundOrder(order) && !hold && !notice) continue
    if (!isWatched(watchedStations, order)) continue
    const flowId = flowIdOf(order)
    if (!flowId) continue
    const quantity = Number(order.quantity)
    const hasPlacement = Boolean(order.placement)
    const pending = order.dish_status === DISH_STATUS.PENDING
    index.set(flowId, {
      status: order.dish_status,
      quantity: Number.isFinite(quantity) ? quantity : 0,
      orderTime: new Date(order.order_time).getTime(),
      awaiting: pending && !hasPlacement && !notice,
      steaming: pending && hasPlacement,
      cancelHold: hold,
      cancelNotice: notice,
      loadedAt: asTimestamp(order.placement?.loaded_at)
    })
  }
  return index
}

/**
 * @param {Map<string, { status: string, quantity: number, orderTime: number }>} index
 * @param {number} now
 * @param {number} urgentMin
 */
function hasAwaitingOvertime(index, now, urgentMin) {
  const thresholdMs = urgentMin * 60 * 1000
  for (const entry of index.values()) {
    if (!entry.awaiting) continue
    if (!Number.isFinite(entry.orderTime)) continue
    if (now - entry.orderTime > thresholdMs) return true
  }
  return false
}

function hasSteamOvertime(index, now, steamUrgentMin) {
  const thresholdMs = steamUrgentMin * 60 * 1000
  for (const entry of index.values()) {
    if (!entry.steaming) continue
    if (!Number.isFinite(entry.loadedAt)) continue
    if (now - entry.loadedAt > thresholdMs) return true
  }
  return false
}

function hasPendingWork(index) {
  for (const entry of index.values()) {
    if (entry.awaiting) return true
  }
  return false
}

function isNewOrderEntry(entry) {
  if (entry.cancelHold || entry.cancelNotice) return false
  return Boolean(entry.awaiting)
}

function snapshotEntry(entry) {
  return {
    status: entry.status,
    quantity: entry.quantity,
    awaiting: entry.awaiting,
    mapWork: entry.steaming || entry.cancelHold
  }
}

/**
 * @param {boolean} awaitingAck
 * @param {boolean} hasPending
 * @returns {BorderState}
 */
function resolveBorderState(awaitingAck, hasPending) {
  if (awaitingAck) return 'yellow'
  if (hasPending) return 'red'
  return 'green'
}

/**
 * @param {AlertState} state
 * @param {{ orders: object[], config?: AlertConfig, now: number }} input
 * @returns {{ state: AlertState, effects: AlertEffects }}
 */
export function step(state, input) {
  const prev = state || createInitialState()
  const now = Number.isFinite(input?.now) ? input.now : Date.now()
  const config = normalizeConfig(input?.config)
  const index = buildRelevantIndex(input?.orders, config.watchedStations)

  const nextSnapshot = new Map()
  for (const [flowId, entry] of index) {
    nextSnapshot.set(flowId, snapshotEntry(entry))
  }

  const hasPending = hasPendingWork(index)
  const waitOvertime = hasAwaitingOvertime(index, now, config.urgentMin)
  const steamOvertime = hasSteamOvertime(index, now, config.steamUrgentMin)
  const overtime = waitOvertime || steamOvertime

  // First pass: baseline only — mirror reality, no alerts.
  if (!prev.primed) {
    return {
      state: {
        borderState: resolveBorderState(false, hasPending),
        awaitingAck: false,
        newBadges: new Map(),
        snapshot: nextSnapshot,
        primed: true,
        lastReescalateAt: null,
        lastOvertimeAlarmAt: null
      },
      effects: { dingCount: 0, overtimeAlarm: false }
    }
  }

  /** @type {string[]} */
  const newEventFlowIds = []
  for (const [flowId, entry] of index) {
    if (!isNewOrderEntry(entry)) continue
    const prevEntry = prev.snapshot.get(flowId)
    if (!prevEntry) {
      newEventFlowIds.push(flowId)
      continue
    }
    // 下笼 clears placement but the line was already 待出餐 — not a 新单事件.
    if (prevEntry.status !== DISH_STATUS.PENDING) {
      newEventFlowIds.push(flowId)
      continue
    }
    if (entry.quantity > prevEntry.quantity) {
      newEventFlowIds.push(flowId)
    }
  }

  let prevPendingCount = 0
  for (const entry of prev.snapshot.values()) {
    if (Object.prototype.hasOwnProperty.call(entry, 'awaiting')) {
      if (entry.awaiting) prevPendingCount++
    } else if (entry.status === DISH_STATUS.PENDING) {
      prevPendingCount++
    }
  }
  const wasIdle = prevPendingCount === 0

  let awaitingAck = prev.awaitingAck
  /** @type {Map<string, { mode: BadgeMode, at: number }>} */
  const nextBadges = new Map(prev.newBadges)
  let dingCount = 0
  let lastReescalateAt = prev.lastReescalateAt

  // No pending left → nothing to acknowledge; border falls to green.
  if (!hasPending) {
    awaitingAck = false
    lastReescalateAt = null
  }

  if (newEventFlowIds.length > 0) {
    if (wasIdle) {
      awaitingAck = true
      dingCount = Math.min(newEventFlowIds.length, config.beepCap)
      for (const flowId of newEventFlowIds) {
        nextBadges.set(flowId, { mode: 'yellow', at: now })
      }
      lastReescalateAt = now
    } else {
      dingCount = 1
      for (const flowId of newEventFlowIds) {
        nextBadges.set(flowId, { mode: 'busy', at: now })
      }
    }
  } else if (awaitingAck && lastReescalateAt != null) {
    const dueAt = lastReescalateAt + config.reescalateSec * 1000
    if (now >= dueAt) {
      dingCount = 1
      lastReescalateAt = now
    }
  }

  // Drop badges for cooked/gone orders; auto-dismiss busy badges past badgeDismissSec.
  const badgeDismissMs = config.badgeDismissSec * 1000
  for (const flowId of [...nextBadges.keys()]) {
    const entry = index.get(flowId)
    if (!entry || entry.status !== DISH_STATUS.PENDING) {
      nextBadges.delete(flowId)
      continue
    }
    const badge = nextBadges.get(flowId)
    if (
      badge &&
      badge.mode === 'busy' &&
      Number.isFinite(badge.at) &&
      now - badge.at >= badgeDismissMs
    ) {
      nextBadges.delete(flowId)
    }
  }

  let overtimeAlarm = false
  let lastOvertimeAlarmAt = prev.lastOvertimeAlarmAt
  if (overtime) {
    const repeatMs = config.overtimeRepeatSec * 1000
    if (
      lastOvertimeAlarmAt == null ||
      now - lastOvertimeAlarmAt >= repeatMs
    ) {
      overtimeAlarm = true
      lastOvertimeAlarmAt = now
    }
  } else {
    lastOvertimeAlarmAt = null
  }

  // Alert preempt: same-step 新单提醒 wins; overtime waits for the next repeat interval.
  if (dingCount > 0 && overtimeAlarm) {
    overtimeAlarm = false
  }

  return {
    state: {
      borderState: resolveBorderState(awaitingAck, hasPending),
      awaitingAck,
      newBadges: nextBadges,
      snapshot: nextSnapshot,
      primed: true,
      lastReescalateAt: awaitingAck ? lastReescalateAt : null,
      lastOvertimeAlarmAt
    },
    effects: { dingCount, overtimeAlarm }
  }
}

/**
 * Clear yellow badges and awaitingAck; busy badges kept.
 * @param {AlertState} state
 * @returns {AlertState}
 */
export function acknowledge(state) {
  const prev = state || createInitialState()
  const nextBadges = new Map()
  for (const [flowId, badge] of prev.newBadges) {
    if (badge.mode === 'busy') nextBadges.set(flowId, badge)
  }
  const hasPending = [...prev.snapshot.values()].some((entry) =>
    Object.prototype.hasOwnProperty.call(entry, 'awaiting')
      ? entry.awaiting
      : entry.status === DISH_STATUS.PENDING
  )
  return {
    ...prev,
    awaitingAck: false,
    newBadges: nextBadges,
    borderState: resolveBorderState(false, hasPending),
    lastReescalateAt: null
  }
}
