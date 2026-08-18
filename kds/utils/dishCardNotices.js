/**
 * Ordinary-station 退示 on dish cards: attach after 拆卡, like steamer noticeCages.
 * Notices do not go through the cap filler (cancelled rows have quantity 0).
 */

import { cancelAckLineId, isCancelAcknowledged, isNeverLoadedCancel } from './cancelAck.js'
import { composeKitchenDishCards, sortKitchenDishCardsByOldest } from './dishCardChunks.js'

/**
 * Unacked never-loaded 已取消 — visible 退示 on an ordinary-station card.
 * @param {object} order
 * @param {Iterable<string>|Set<string>|null|undefined} [acknowledgedIds]
 */
export function isDishCardCancelNotice(order, acknowledgedIds) {
  if (!isNeverLoadedCancel(order)) return false
  return !isCancelAcknowledged(cancelAckLineId(order), acknowledgedIds)
}

function dishNameOf(order) {
  return typeof order?.dish_name === 'string' ? order.dish_name : ''
}

function groupNoticesByDish(noticeOrders, acknowledgedIds) {
  const noticeByDish = new Map()
  for (const order of Array.isArray(noticeOrders) ? noticeOrders : []) {
    if (!isDishCardCancelNotice(order, acknowledgedIds)) continue
    const dishName = dishNameOf(order)
    if (!dishName) continue
    if (!noticeByDish.has(dishName)) noticeByDish.set(dishName, [])
    noticeByDish.get(dishName).push(order)
  }
  return noticeByDish
}

function oldestOrderTimeMs(orders) {
  const times = (Array.isArray(orders) ? orders : [])
    .map((order) => {
      const ts = new Date(order?.order_time).getTime()
      return Number.isFinite(ts) ? ts : 0
    })
    .filter((ts) => ts > 0)
  return times.length > 0 ? Math.min(...times) : 0
}

function noticeOnlyCard(dishName, noticeOrders, station) {
  return {
    dishName,
    station,
    chunkId: `${dishName}::notice`,
    orders: [],
    noticeOrders,
    totalQuantity: 0,
    oldestTimestamp: oldestOrderTimeMs(noticeOrders)
  }
}

/**
 * Split serveable work with the ordinary 拆卡 engine, then pin 退示 onto the
 * earliest card of that dishName. Notice-only dishes get a card with totalQuantity 0.
 *
 * @param {object} params
 * @param {Array<{ dishName: string, station?: string, orders: object[] }>} params.logicalDishes
 * @param {object[]} [params.noticeOrders]
 * @param {Iterable<string>|Set<string>} [params.acknowledgedCancelIds]
 * @param {number} params.cap
 * @param {number} [params.orderGapMinutes]
 * @param {Record<string, object[]>} [params.previousByDish]
 * @returns {{ cards: object[], previousByDish: Record<string, object[]> }}
 */
export function composeKitchenDishCardsWithNotices({
  logicalDishes,
  noticeOrders = [],
  acknowledgedCancelIds,
  cap = 0,
  orderGapMinutes = 0,
  previousByDish = {}
} = {}) {
  const noticeByDish = groupNoticesByDish(noticeOrders, acknowledgedCancelIds)
  const stationByDish = new Map()
  for (const dish of logicalDishes || []) {
    if (dish?.dishName && dish.station) stationByDish.set(dish.dishName, dish.station)
  }
  for (const [dishName, notices] of noticeByDish) {
    if (!stationByDish.has(dishName) && notices[0]?.station) {
      stationByDish.set(dishName, notices[0].station)
    }
  }

  const { cards, previousByDish: nextPrevious } = composeKitchenDishCards({
    logicalDishes,
    cap: Number(cap) || 0,
    orderGapMinutes: Number(orderGapMinutes) || 0,
    previousByDish
  })

  const attached = new Set()
  const withNotices = sortKitchenDishCardsByOldest(cards).map((card) => {
    let pinned = []
    if (!attached.has(card.dishName) && noticeByDish.has(card.dishName)) {
      pinned = noticeByDish.get(card.dishName)
      attached.add(card.dishName)
    }
    return {
      ...card,
      noticeOrders: pinned
    }
  })

  for (const [dishName, notices] of noticeByDish) {
    if (attached.has(dishName)) continue
    withNotices.push(noticeOnlyCard(dishName, notices, stationByDish.get(dishName)))
  }

  return { cards: withNotices, previousByDish: nextPrevious }
}
