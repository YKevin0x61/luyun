/**
 * 待出餐工作 vs 等叫, and 进入待出餐工作时刻.
 */

import { DISH_STATUS, isRefundOrder } from './constants.js'

export function isHold(order) {
  return Boolean(order && (order.is_hold === true || order.is_hold === 1 || order.is_hold === '1'))
}

export function isRushed(order) {
  return Boolean(order && (order.is_rushed === true || order.is_rushed === 1 || order.is_rushed === '1'))
}

export function isPendingKitchenWork(order) {
  return Boolean(
    order &&
      order.dish_status === DISH_STATUS.PENDING &&
      !isRefundOrder(order) &&
      !isHold(order)
  )
}

export function workEnterTimeMs(order) {
  const raw = order?.fired_at || order?.order_time
  const ts = new Date(raw).getTime()
  return Number.isFinite(ts) ? ts : 0
}

export function compareKitchenFifo(a, b) {
  const ta = workEnterTimeMs(a)
  const tb = workEnterTimeMs(b)
  const aOk = ta > 0
  const bOk = tb > 0
  if (aOk && bOk && ta !== tb) return ta - tb
  if (aOk !== bOk) return aOk ? -1 : 1
  const idA = String(a?.id ?? a?._id ?? '')
  const idB = String(b?.id ?? b?._id ?? '')
  return idA.localeCompare(idB)
}

export function compareRushThenFifo(a, b) {
  const rushA = isRushed(a) ? 0 : 1
  const rushB = isRushed(b) ? 0 : 1
  if (rushA !== rushB) return rushA - rushB
  return compareKitchenFifo(a, b)
}
