/**
 * One 出餐 confirm → one complete-cooking request.
 * Flattens a multi-item plan into the planned 订单行; does not re-FIFO.
 */

import { orderLineId } from './batchCooking.js'

/**
 * @param {Array<{
 *   dishName: string,
 *   completeQuantity: number,
 *   allocations: Array<{ order: object, serveQuantity: number }>
 * }>} plan
 * @param {{
 *   station: string,
 *   operatorId?: string,
 *   readyTime: string
 * }} meta
 * @returns {object|null}
 */
export function buildCompleteCookingRequest(plan, meta) {
  if (!Array.isArray(plan) || plan.length === 0 || !meta) return null

  const orders = []
  for (const item of plan) {
    for (const { order, serveQuantity } of item.allocations || []) {
      const take = Number(serveQuantity) || 0
      if (take <= 0 || !order) continue
      const orderId = orderLineId(order)
      if (!orderId) continue
      orders.push({
        order_id: orderId,
        business_flow_id: order.business_flow_id || undefined,
        table_number: order.table_number,
        complete_quantity: take,
        original_quantity: order.quantity || 1
      })
    }
  }

  if (!orders.length) return null

  const completeQuantity = orders.reduce((sum, line) => sum + line.complete_quantity, 0)
  return {
    dish_name: plan[0].dishName,
    station: meta.station,
    complete_quantity: completeQuantity,
    orders,
    operator_id: meta.operatorId,
    ready_time: meta.readyTime
  }
}

/**
 * Hold kitchen pull during 提交中. After settle, same-station (or unscoped /
 * reconcile) events may pull; another 档口 must not.
 *
 * @param {{
 *   submitting?: boolean,
 *   steamerLoading?: boolean,
 *   lockedStation?: string,
 *   scope?: { station?: string, reconcile?: boolean }
 * }} [state]
 * @returns {boolean}
 */
export function kitchenShouldPull(state = {}) {
  if (state.submitting || state.steamerLoading) return false
  const locked = state.lockedStation
  const station = state.scope?.station
  if (station && locked && String(station) !== String(locked)) return false
  return true
}

function rejectDetail(errorOrDetail) {
  if (!errorOrDetail) return null
  const nested = errorOrDetail.response?.data?.detail
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) return nested
  if (typeof errorOrDetail === 'object' && Array.isArray(errorOrDetail.conflicts)) {
    return errorOrDetail
  }
  return null
}

/**
 * @param {object|Error|null|undefined} errorOrDetail
 * @returns {string[]}
 */
export function conflictOrderIdsFromReject(errorOrDetail) {
  const detail = rejectDetail(errorOrDetail)
  if (!detail || !Array.isArray(detail.conflicts)) return []
  return detail.conflicts
    .map((item) => String(item?.order_id ?? ''))
    .filter(Boolean)
}

/**
 * @param {object|Error|null|undefined} errorOrDetail
 * @returns {string}
 */
export function serveConfirmErrorMessage(errorOrDetail) {
  const detail = rejectDetail(errorOrDetail)
  if (typeof detail?.message === 'string' && detail.message) return detail.message
  if (typeof errorOrDetail?.message === 'string' && errorOrDetail.message) {
    return errorOrDetail.message
  }
  return '出餐失败'
}

/**
 * One confirm: one request, print only after success, pull once after settle.
 *
 * @param {object} deps
 * @param {object[]} deps.plan
 * @param {object} deps.meta
 * @param {(body: object) => Promise<unknown>} deps.completeCooking
 * @param {(job: { order: object, dishName: string, readyTime: string }) => void} [deps.enqueuePrint]
 * @param {() => (void|Promise<void>)} [deps.pull]
 * @returns {Promise<{ submitted: boolean, processed: number, request: object|null }>}
 */
export async function runServeConfirm({
  plan,
  meta,
  completeCooking,
  enqueuePrint,
  pull
}) {
  const request = buildCompleteCookingRequest(plan, meta)
  if (!request) return { submitted: false, processed: 0, request: null }

  try {
    await completeCooking(request)
    if (typeof enqueuePrint === 'function') {
      for (const item of plan) {
        const dishName = item.dishName
        for (const { order, serveQuantity } of item.allocations || []) {
          const copies = Number(serveQuantity) || 0
          for (let i = 0; i < copies; i++) {
            enqueuePrint({
              order,
              dishName: dishName || order?.dish_name,
              readyTime: request.ready_time
            })
          }
        }
      }
    }
    return { submitted: true, processed: request.complete_quantity, request }
  } finally {
    if (typeof pull === 'function') {
      try {
        await pull()
      } catch (pullError) {
        console.error('Settle pull after 出餐 failed:', pullError)
      }
    }
  }
}
