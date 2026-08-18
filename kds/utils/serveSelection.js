/**
 * Pure 出餐选中 reducer: card counts and 选桌出餐 are mutually exclusive.
 * No Vue / uni / DOM.
 */

export function emptyServeSelection() {
  return {
    cardCounts: {},
    tablePick: null
  }
}

/**
 * @param {{ cardCounts: Record<string, number>, tablePick: null | { chunkId: string, selectedOrderIds: string[] } }} state
 * @param {{ type: string, chunkId?: string, max?: number, orderId?: string }} event
 */
export function applyServeSelection(state, event) {
  const current = state || emptyServeSelection()
  if (!event || !event.type) return current

  if (event.type === 'increase') {
    if (current.tablePick) return current
    const chunkId = event.chunkId
    if (!chunkId) return current
    const max = Number(event.max) || 0
    const currentCount = current.cardCounts[chunkId] || 0
    if (currentCount >= max) return current
    return {
      ...current,
      cardCounts: { ...current.cardCounts, [chunkId]: currentCount + 1 }
    }
  }

  if (event.type === 'decrease') {
    if (current.tablePick) return current
    const chunkId = event.chunkId
    if (!chunkId) return current
    const currentCount = current.cardCounts[chunkId] || 0
    if (currentCount <= 0) return current
    const nextCounts = { ...current.cardCounts }
    if (currentCount === 1) {
      delete nextCounts[chunkId]
    } else {
      nextCounts[chunkId] = currentCount - 1
    }
    return { ...current, cardCounts: nextCounts }
  }

  if (event.type === 'openTablePick') {
    const chunkId = event.chunkId
    if (!chunkId) return current
    return {
      cardCounts: {},
      tablePick: { chunkId, selectedOrderIds: [] }
    }
  }

  if (event.type === 'toggleOrderLine') {
    if (!current.tablePick) return current
    const orderId = event.orderId
    if (!orderId) return current
    const selected = current.tablePick.selectedOrderIds
    const exists = selected.includes(orderId)
    if (!exists && Array.isArray(event.selectableOrderIds)) {
      const allowed = new Set(event.selectableOrderIds.map((id) => String(id)))
      if (!allowed.has(String(orderId))) return current
    }
    const selectedOrderIds = exists
      ? selected.filter((id) => id !== orderId)
      : [...selected, orderId]
    return {
      cardCounts: {},
      tablePick: { ...current.tablePick, selectedOrderIds }
    }
  }

  if (
    event.type === 'closeTablePick' ||
    event.type === 'completeServe' ||
    event.type === 'externalClear'
  ) {
    return emptyServeSelection()
  }

  if (event.type === 'syncLiveWork') {
    const live = new Set((event.liveOrderIds || []).map(String).filter(Boolean))
    const chunkMax = event.chunkMax && typeof event.chunkMax === 'object' ? event.chunkMax : {}
    let tablePick = current.tablePick
    if (tablePick) {
      const selectedOrderIds = tablePick.selectedOrderIds.filter((id) => live.has(String(id)))
      if (!Object.prototype.hasOwnProperty.call(chunkMax, tablePick.chunkId)) {
        tablePick = null
      } else {
        tablePick = { ...tablePick, selectedOrderIds }
      }
    }
    const cardCounts = {}
    for (const [chunkId, count] of Object.entries(current.cardCounts || {})) {
      const max = Number(chunkMax[chunkId]) || 0
      const clamped = Math.min(Number(count) || 0, max)
      if (clamped > 0) cardCounts[chunkId] = clamped
    }
    return { cardCounts, tablePick }
  }

  return current
}

/**
 * Failed confirm keeps 出餐选中 so the chef can drop marked lines and retry.
 *
 * @param {{ cardCounts: Record<string, number>, tablePick: object|null }} selection
 * @param {boolean} success
 */
export function serveSelectionAfterConfirm(selection, success) {
  if (success) return emptyServeSelection()
  return selection || emptyServeSelection()
}
