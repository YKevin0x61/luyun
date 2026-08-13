/**
 * Sticky FIFO split of one logical dish into kitchen cards of at most N portions.
 * N = 0 is a no-op (one card, all pending orders).
 */

function availableQuantity(order) {
  const quantity = order?.quantity || 1
  const served = order?.servedQuantity || order?.served_quantity || 0
  return Math.max(0, quantity - served)
}

function orderId(order) {
  return String(order?.id ?? order?._id ?? '')
}

function orderTimeMs(order) {
  const ts = new Date(order?.order_time).getTime()
  return Number.isFinite(ts) ? ts : 0
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

function hydrateChunk(dishName, chunkId, members, byId) {
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
  for (let i = built.length - 1; i >= 0; i--) {
    if (membersTotal(built[i].members) < cap) return built[i]
  }
  return null
}

/**
 * @param {object} params
 * @param {string} params.dishName
 * @param {object[]} params.pendingOrders
 * @param {number} params.cap 0 = no split; 1–99 = max portions per chunk
 * @param {Array<{ chunkId: string, orders: object[] }>} [params.previousChunks]
 * @returns {Array<{ chunkId: string, dishName: string, orders: object[], totalQuantity: number, oldestTimestamp: number }>}
 */
export function reconcileDishChunks({
  dishName,
  pendingOrders,
  cap,
  previousChunks
}) {
  const raw = []
  for (const order of pendingOrders || []) {
    if (!order) continue
    const id = orderId(order)
    if (!id) continue
    raw.push(order)
  }

  const n = Number(cap) || 0
  if (n < 1) {
    const fifo = sortFifo(raw)
    if (fifo.length === 0) return []
    const totalQuantity = fifo.reduce((sum, order) => sum + (order.quantity || 0), 0)
    return [
      {
        chunkId: dishName,
        dishName,
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

  const built = []
  for (const prev of previousChunks || []) {
    const members = []
    let total = 0
    for (const member of readMembers(prev)) {
      const left = remaining.get(member.orderId) || 0
      if (left <= 0) continue
      if (total >= n) break
      const take = Math.min(left, member.quantity, n - total)
      if (take <= 0) continue
      members.push({ orderId: member.orderId, quantity: take })
      remaining.set(member.orderId, left - take)
      total += take
    }
    if (total <= 0) continue
    built.push({ chunkId: prev.chunkId, members })
  }

  const leftover = leftoverFromRemaining(fifo, remaining)
  while (leftover.length > 0) {
    const target = lastUnderfilled(built, n)
    if (target) {
      fillChunk(target.members, leftover, n)
      continue
    }
    const members = []
    fillChunk(members, leftover, n)
    if (members.length === 0) break
    built.push({ chunkId: assignChunkId(dishName, members), members })
  }

  return built.map((chunk) =>
    hydrateChunk(dishName, chunk.chunkId, chunk.members, byId)
  )
}

/**
 * Flatten caller-sorted logical dishes into render cards.
 * Same-dish chunks stay adjacent in FIFO/chunk order.
 *
 * @param {object} params
 * @param {Array<{ dishName: string, station?: string, orders: object[], totalQuantity?: number }>} params.logicalDishes
 * @param {number} params.cap
 * @param {Record<string, object[]>} [params.previousByDish]
 * @returns {{ cards: object[], previousByDish: Record<string, object[]> }}
 */
export function composeKitchenDishCards({
  logicalDishes,
  cap,
  previousByDish = {}
}) {
  const nextPrevious = {}
  const cards = []
  for (const dish of logicalDishes || []) {
    if (!dish || !dish.dishName) continue
    const chunks = reconcileDishChunks({
      dishName: dish.dishName,
      pendingOrders: dish.orders,
      cap,
      previousChunks: previousByDish[dish.dishName] || []
    })
    nextPrevious[dish.dishName] = chunks
    for (const chunk of chunks) {
      cards.push({
        dishName: chunk.dishName,
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
