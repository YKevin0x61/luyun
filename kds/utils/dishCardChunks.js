import { workEnterTimeMs } from './pendingKitchenWork.js'
import { canonicalOrderNotes, dishNotesIdentityKey } from './orderNotes.js'

function availableQuantity(order) {
  const quantity = order?.quantity || 1
  const served = order?.servedQuantity || order?.served_quantity || 0
  return Math.max(0, quantity - served)
}

function orderId(order) {
  return String(order?.id ?? order?._id ?? '')
}

function orderTimeMs(order) {
  return workEnterTimeMs(order)
}

function sortFifo(orders) {
  return [...orders].sort((a, b) => {
    const byTime = orderTimeMs(a) - orderTimeMs(b)
    if (byTime !== 0) return byTime
    return orderId(a).localeCompare(orderId(b))
  })
}

function oldestTimestamp(orders) {
  const times = orders.map(orderTimeMs).filter((ts) => ts > 0)
  return times.length > 0 ? Math.min(...times) : 0
}

function hydrateChunk(dishName, chunkId, members, byId, notes) {
  const merged = []
  for (const member of members) {
    if (!member || member.quantity <= 0) continue
    const last = merged[merged.length - 1]
    if (last && last.orderId === member.orderId) {
      last.quantity += member.quantity
    } else {
      merged.push({ orderId: member.orderId, quantity: member.quantity })
    }
  }
  const orders = []
  let totalQuantity = 0
  for (const member of merged) {
    const source = byId.get(member.orderId)
    if (!source) continue
    orders.push({
      ...source,
      quantity: member.quantity,
      served_quantity: 0,
      servedQuantity: 0
    })
    totalQuantity += member.quantity
  }
  return {
    chunkId,
    dishName,
    notes: canonicalOrderNotes(notes),
    orders,
    totalQuantity,
    oldestTimestamp: oldestTimestamp(orders)
  }
}

function assignChunkId(dishName, members) {
  return `${dishName}::${members.map((m) => `${m.orderId}:${m.quantity}`).join('|')}`
}

function fillChunk(members, leftover, cap) {
  let total = membersTotal(members)
  while (leftover.length > 0 && total < cap) {
    const next = leftover[0]
    const take = Math.min(next.qty, cap - total)
    if (take <= 0) break
    members.push({ orderId: next.orderId, quantity: take })
    total += take
    next.qty -= take
    if (next.qty <= 0) leftover.shift()
  }
  return total
}

function readMembers(chunk) {
  if (!chunk) return []
  if (Array.isArray(chunk.members) && chunk.members.length > 0) {
    return chunk.members.map((member) => ({
      orderId: String(member.orderId),
      quantity: Number(member.quantity) || 0
    }))
  }
  return (chunk.orders || []).map((order) => ({
    orderId: orderId(order),
    quantity: Number(order.quantity) || 0
  }))
}

function leftoverFromRemaining(fifo, remaining) {
  return fifo
    .map((order) => ({
      orderId: orderId(order),
      qty: remaining.get(orderId(order)) || 0
    }))
    .filter((item) => item.qty > 0)
}

function membersTotal(members) {
  return members.reduce((sum, member) => sum + member.quantity, 0)
}

function lastUnderfilled(built, cap) {
  // Only the tail of this 浪潮: filling an earlier hole would put new tickets ahead of later FIFO work.
  if (built.length === 0) return null
  const last = built[built.length - 1]
  return membersTotal(last.members) < cap ? last : null
}

const ORDER_GAP_MS_PER_MINUTE = 60 * 1000

function partitionWaves(fifo, gapMinutes) {
  const t = Number(gapMinutes) || 0
  if (t < 1 || fifo.length === 0) return [fifo]
  const thresholdMs = t * ORDER_GAP_MS_PER_MINUTE
  const waves = []
  let current = [fifo[0]]
  for (let i = 1; i < fifo.length; i++) {
    const gap = orderTimeMs(fifo[i]) - orderTimeMs(fifo[i - 1])
    if (gap >= thresholdMs) {
      waves.push(current)
      current = [fifo[i]]
    } else {
      current.push(fifo[i])
    }
  }
  waves.push(current)
  return waves
}

/**
 * @param {object} params
 * @param {string} params.dishName
 * @param {string} [params.notes]
 * @param {object[]} params.pendingOrders
 * @param {number} params.cap 0 = no portion split; 1–99 = max portions per chunk
 * @param {number} [params.orderGapMinutes] 0 = no 浪潮 split; 1–99 = adjacent gap in minutes
 * @param {Array<{ chunkId: string, orders: object[] }>} [params.previousChunks]
 * @returns {Array<{ chunkId: string, dishName: string, notes: string, orders: object[], totalQuantity: number, oldestTimestamp: number }>}
 */
export function reconcileDishChunks({
  dishName,
  notes,
  pendingOrders,
  cap,
  orderGapMinutes = 0,
  previousChunks
}) {
  const raw = []
  for (const order of pendingOrders || []) {
    if (!order) continue
    const id = orderId(order)
    if (!id) continue
    raw.push(order)
  }

  const canonicalNotes = canonicalOrderNotes(notes)
  const identity = dishNotesIdentityKey(dishName, canonicalNotes)

  const n = Number(cap) || 0
  const t = Number(orderGapMinutes) || 0
  if (n < 1 && t < 1) {
    const fifo = sortFifo(raw)
    if (fifo.length === 0) return []
    const totalQuantity = fifo.reduce((sum, order) => sum + (order.quantity || 0), 0)
    return [
      {
        chunkId: identity,
        dishName,
        notes: canonicalNotes,
        orders: fifo,
        totalQuantity,
        oldestTimestamp: oldestTimestamp(fifo)
      }
    ]
  }

  const byId = new Map()
  const pending = []
  for (const order of raw) {
    const qty = availableQuantity(order)
    if (qty <= 0) continue
    const id = orderId(order)
    const normalized = { ...order, quantity: qty, served_quantity: 0, servedQuantity: 0 }
    byId.set(id, normalized)
    pending.push(normalized)
  }

  const fifo = sortFifo(pending)
  if (fifo.length === 0) return []

  const remaining = new Map()
  for (const order of fifo) {
    remaining.set(orderId(order), order.quantity)
  }

  const portionCap = n < 1 ? Number.POSITIVE_INFINITY : n
  const usedChunkIds = new Set()
  const built = []

  for (const wave of partitionWaves(fifo, t)) {
    const waveIds = new Set(wave.map((order) => orderId(order)))
    const waveBuilt = []

    for (const prev of previousChunks || []) {
      const members = []
      let total = 0
      for (const member of readMembers(prev)) {
        if (!waveIds.has(member.orderId)) continue
        const left = remaining.get(member.orderId) || 0
        if (left <= 0) continue
        if (total >= portionCap) break
        const take = Math.min(left, member.quantity, portionCap - total)
        if (take <= 0) continue
        members.push({ orderId: member.orderId, quantity: take })
        remaining.set(member.orderId, left - take)
        total += take
      }
      if (total <= 0) continue
      let chunkId = prev.chunkId
      if (usedChunkIds.has(chunkId)) {
        chunkId = assignChunkId(dishName, members)
      }
      usedChunkIds.add(chunkId)
      waveBuilt.push({ chunkId, members })
    }

    const leftover = leftoverFromRemaining(wave, remaining)
    while (leftover.length > 0) {
      const target = lastUnderfilled(waveBuilt, portionCap)
      if (target) {
        fillChunk(target.members, leftover, portionCap)
        continue
      }
      const members = []
      fillChunk(members, leftover, portionCap)
      if (members.length === 0) break
      const chunkId = assignChunkId(dishName, members)
      usedChunkIds.add(chunkId)
      waveBuilt.push({ chunkId, members })
    }

    built.push(...waveBuilt)
  }

  return built.map((chunk) =>
    hydrateChunk(dishName, chunk.chunkId, chunk.members, byId, canonicalNotes)
  )
}

/**
 * Flatten logical dishes into render cards. Same-dish chunks are not kept
 * adjacent; the caller sorts the flat list (typically by each chunk’s oldest order).
 *
 * @param {object} params
 * @param {Array<{ dishName: string, notes?: string, station?: string, orders: object[], totalQuantity?: number }>} params.logicalDishes
 * @param {number} params.cap
 * @param {number} [params.orderGapMinutes]
 * @param {Record<string, object[]>} [params.previousByDish]
 * @returns {{ cards: object[], previousByDish: Record<string, object[]> }}
 */
export function composeKitchenDishCards({
  logicalDishes,
  cap,
  orderGapMinutes = 0,
  previousByDish = {}
}) {
  const nextPrevious = {}
  const cards = []
  for (const dish of logicalDishes || []) {
    if (!dish || !dish.dishName) continue
    const notes = canonicalOrderNotes(dish.notes ?? dish.orders?.[0]?.notes)
    const identity = dishNotesIdentityKey(dish.dishName, notes)
    const chunks = reconcileDishChunks({
      dishName: dish.dishName,
      notes,
      pendingOrders: dish.orders,
      cap,
      orderGapMinutes,
      previousChunks: previousByDish[identity] || []
    })
    nextPrevious[identity] = chunks
    for (const chunk of chunks) {
      cards.push({
        dishName: chunk.dishName,
        notes: chunk.notes,
        station: dish.station,
        chunkId: chunk.chunkId,
        orders: chunk.orders,
        totalQuantity: chunk.totalQuantity,
        oldestTimestamp: chunk.oldestTimestamp
      })
    }
  }
  return { cards, previousByDish: nextPrevious }
}

/**
 * Order kitchen cards by each chunk’s current earliest pending order.
 * Same-dish cards may interleave with other dishes.
 *
 * @param {object[]} cards
 * @returns {object[]}
 */
export function sortKitchenDishCardsByOldest(cards) {
  return [...(cards || [])]
    .map((card) => ({
      ...card,
      orders: [...(card.orders || [])].sort((a, b) => {
        const byTime = workEnterTimeMs(a) - workEnterTimeMs(b)
        if (byTime !== 0) return byTime
        return String(a.id ?? a._id ?? '').localeCompare(String(b.id ?? b._id ?? ''))
      })
    }))
    .sort((a, b) => {
      const byTime = (a.oldestTimestamp || 0) - (b.oldestTimestamp || 0)
      if (byTime !== 0) return byTime
      return String(a.chunkId || '').localeCompare(String(b.chunkId || ''))
    })
}

/**
 * Sticky snapshot is discarded when either 拆卡 knob changes.
 * First run (seen == null) is not a change — do not clear 出餐选中.
 *
 * @param {{ cap: number, orderGapMinutes: number } | null} seen
 * @param {number} cap
 * @param {number} orderGapMinutes
 */
export function dishSplitKnobsChanged(seen, cap, orderGapMinutes) {
  if (seen == null) return false
  return Number(seen.cap) !== Number(cap) || Number(seen.orderGapMinutes) !== Number(orderGapMinutes)
}
