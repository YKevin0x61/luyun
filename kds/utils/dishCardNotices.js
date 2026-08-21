/**
 * Ordinary-station 退示 on dish cards: attach after 拆卡, like steamer noticeCages.
 * Notices do not go through the cap filler (cancelled rows have quantity 0).
 */

import { cancelAckLineId, isCancelAcknowledged, isNeverLoadedCancel } from './cancelAck.js'
import { composeKitchenDishCards, sortKitchenDishCardsByOldest } from './dishCardChunks.js'
import { canonicalOrderNotes, dishNotesIdentityKey } from './orderNotes.js'

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

function noticeIdentity(order) {
  return dishNotesIdentityKey(dishNameOf(order), order?.notes)
}

function groupNoticesByDishNotes(noticeOrders, acknowledgedIds) {
  const noticeByIdentity = new Map()
  for (const order of Array.isArray(noticeOrders) ? noticeOrders : []) {
    if (!isDishCardCancelNotice(order, acknowledgedIds)) continue
    const dishName = dishNameOf(order)
    if (!dishName) continue
    const identity = noticeIdentity(order)
    if (!noticeByIdentity.has(identity)) noticeByIdentity.set(identity, [])
    noticeByIdentity.get(identity).push(order)
  }
  return noticeByIdentity
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

function noticeOnlyCard(dishName, notes, noticeOrders, station) {
  return {
    dishName,
    notes: canonicalOrderNotes(notes),
    station,
    chunkId: `${dishNotesIdentityKey(dishName, notes)}::notice`,
    orders: [],
    noticeOrders,
    totalQuantity: 0,
    oldestTimestamp: oldestOrderTimeMs(noticeOrders)
  }
}

/**
 * Split serveable work with the ordinary 拆卡 engine, then pin 退示 onto the
 * earliest card of that 菜名+备注. Notice-only dishes get a card with totalQuantity 0.
 *
 * @param {object} params
 * @param {Array<{ dishName: string, notes?: string, station?: string, orders: object[] }>} params.logicalDishes
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
  const noticeByIdentity = groupNoticesByDishNotes(noticeOrders, acknowledgedCancelIds)
  const stationByIdentity = new Map()
  for (const dish of logicalDishes || []) {
    if (!dish?.dishName) continue
    const identity = dishNotesIdentityKey(dish.dishName, dish.notes)
    if (dish.station) stationByIdentity.set(identity, dish.station)
  }
  for (const [identity, notices] of noticeByIdentity) {
    if (!stationByIdentity.has(identity) && notices[0]?.station) {
      stationByIdentity.set(identity, notices[0].station)
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
    const identity = dishNotesIdentityKey(card.dishName, card.notes)
    let pinned = []
    if (!attached.has(identity) && noticeByIdentity.has(identity)) {
      pinned = noticeByIdentity.get(identity)
      attached.add(identity)
    }
    return {
      ...card,
      noticeOrders: pinned
    }
  })

  for (const [identity, notices] of noticeByIdentity) {
    if (attached.has(identity)) continue
    const dishName = dishNameOf(notices[0])
    const notes = canonicalOrderNotes(notices[0]?.notes)
    withNotices.push(noticeOnlyCard(dishName, notes, notices, stationByIdentity.get(identity)))
  }

  return { cards: withNotices, previousByDish: nextPrevious }
}
