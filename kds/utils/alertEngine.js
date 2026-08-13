/**
 * Pure KDS alert decision engine (no Vue / uni / DOM / audio).
 *
 * Snapshot-diff reducer: previous state + current orders + config + now
 * → next state + effects (dingCount / overtimeAlarm). Outer layer executes effects.
 */

import { DISH_STATUS, isRefundOrder } from './constants.js'

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
 *   overtimeRepeatSec?: number
 * }} AlertConfig
 */

const DEFAULT_CONFIG = Object.freeze({
  watchedStations: [],
  beepCap: 5,
  reescalateSec: 20,
  badgeDismissSec: 30,
  urgentMin: 20,
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
  if (!Array.isArray(watchedStations) || watchedStations.length === 0) return true
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
    overtimeRepeatSec: Number.isFinite(Number(c.overtimeRepeatSec))
      ? Number(c.overtimeRepeatSec)
      : DEFAULT_CONFIG.overtimeRepeatSec
  }
}

/**
 * @param {object[]} orders
 * @param {string[]} watchedStations
 * @returns {Map<string, { status: string, quantity: number, orderTime: number }>}
 */
function buildRelevantIndex(orders, watchedStations) {
  const index = new Map()
  if (!Array.isArray(orders)) return index
  for (const order of orders) {
    if (!order || isRefundOrder(order)) continue
    if (!isWatched(watchedStations, order)) continue
    const flowId = flowIdOf(order)
    if (!flowId) continue
    const quantity = Number(order.quantity)
    index.set(flowId, {
      status: order.dish_status,
      quantity: Number.isFinite(quantity) ? quantity : 0,
      orderTime: new Date(order.order_time).getTime()
    })
  }
  return index
}

/**
 * @param {Map<string, { status: string, quantity: number, orderTime: number }>} index
 * @param {number} now
 * @param {number} urgentMin
 */
function hasOvertimePending(index, now, urgentMin) {
  const thresholdMs = urgentMin * 60 * 1000
  for (const entry of index.values()) {
    if (entry.status !== DISH_STATUS.PENDING) continue
    if (!Number.isFinite(entry.orderTime)) continue
    if (now - entry.orderTime > thresholdMs) return true
  }
  return false
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
    nextSnapshot.set(flowId, { status: entry.status, quantity: entry.quantity })
  }

  let pendingCount = 0
  for (const entry of index.values()) {
    if (entry.status === DISH_STATUS.PENDING) pendingCount++
  }
  const hasPending = pendingCount > 0
  const overtime = hasOvertimePending(index, now, config.urgentMin)

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
    if (entry.status !== DISH_STATUS.PENDING) continue
    const prevEntry = prev.snapshot.get(flowId)
    if (!prevEntry) {
      newEventFlowIds.push(flowId)
      continue
    }
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
    if (entry.status === DISH_STATUS.PENDING) prevPendingCount++
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
  const hasPending = [...prev.snapshot.values()].some(
    (entry) => entry.status === DISH_STATUS.PENDING
  )
  return {
    ...prev,
    awaitingAck: false,
    newBadges: nextBadges,
    borderState: resolveBorderState(false, hasPending),
    lastReescalateAt: null
  }
}
