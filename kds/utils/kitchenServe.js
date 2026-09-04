/**
 * Kitchen 出餐: selection, FIFO preview, one confirm, nudge freeze.
 * Pages import this module; plan/request builders stay internal.
 */

import {
  orderLineId,
  planBasketServeCookingCalls,
  planBatchCookingCalls,
  planTablePickCookingCalls,
  servePreviewOrderIds
} from './batchCooking.js'
import {
  hasMarkedOrderLine,
  hubShouldPull,
  kitchenShouldPull,
  kitchenShouldRedrawWork,
  nextConflictMarks,
  orderLineIsMarked,
  runServeConfirm,
  serveConfirmErrorMessage
} from './serveConfirm.js'
import {
  applyServeSelection,
  emptyServeSelection,
  serveSelectionAfterConfirm
} from './serveSelection.js'

export {
  applyServeSelection,
  emptyServeSelection,
  hasMarkedOrderLine,
  hubShouldPull,
  kitchenShouldPull,
  kitchenShouldRedrawWork,
  nextConflictMarks,
  orderLineId,
  orderLineIsMarked,
  serveConfirmErrorMessage,
  servePreviewOrderIds
}

async function settleConfirm({
  plan,
  selection,
  meta,
  completeCooking,
  enqueuePrint,
  pull
}) {
  const conflictMarks = nextConflictMarks([], { type: 'confirmStart' })
  try {
    const result = await runServeConfirm({
      plan,
      meta,
      completeCooking,
      enqueuePrint,
      pull
    })
    return {
      submitted: result.submitted,
      processed: result.processed,
      selection: serveSelectionAfterConfirm(selection, result.submitted),
      conflictMarks,
      error: null
    }
  } catch (error) {
    return {
      submitted: false,
      processed: 0,
      selection: serveSelectionAfterConfirm(selection, false),
      conflictMarks: nextConflictMarks(conflictMarks, { type: 'reject', error }),
      error
    }
  }
}

/**
 * @param {object} args
 * @param {{ cardCounts: Record<string, number>, tablePick: object|null }} args.selection
 * @param {object[]} args.pendingOrders
 * @param {Record<string, { dishName: string, orders: object[] }>} [args.chunkOrders]
 * @param {{ station: string, operatorId?: string, readyTime: string }} args.meta
 * @param {(body: object) => Promise<unknown>} args.completeCooking
 * @param {(job: object) => void} [args.enqueuePrint]
 * @param {() => (void|Promise<void>)} [args.pull]
 */
export async function confirmCardServe({
  selection,
  pendingOrders,
  chunkOrders,
  meta,
  completeCooking,
  enqueuePrint,
  pull
}) {
  const current = selection || emptyServeSelection()
  if (current.tablePick) {
    return {
      submitted: false,
      processed: 0,
      selection: current,
      conflictMarks: [],
      error: null
    }
  }
  const plan = planBatchCookingCalls({
    selectedQuantities: current.cardCounts,
    pendingOrders,
    chunkOrders
  })
  return settleConfirm({
    plan,
    selection: current,
    meta,
    completeCooking,
    enqueuePrint,
    pull
  })
}

/**
 * @param {object} args
 * @param {{ cardCounts: Record<string, number>, tablePick: object|null }} args.selection
 * @param {Record<string, { dishName: string, orders: object[] }>} args.chunkOrders
 */
export async function confirmTablePickServe({
  selection,
  chunkOrders,
  meta,
  completeCooking,
  enqueuePrint,
  pull
}) {
  const current = selection || emptyServeSelection()
  const pick = current.tablePick
  if (!pick || !Array.isArray(pick.selectedOrderIds) || pick.selectedOrderIds.length === 0) {
    return {
      submitted: false,
      processed: 0,
      selection: current,
      conflictMarks: [],
      error: null
    }
  }
  const plan = planTablePickCookingCalls({
    selectedOrderIds: pick.selectedOrderIds,
    chunkId: pick.chunkId,
    chunkOrders
  })
  return settleConfirm({
    plan,
    selection: current,
    meta,
    completeCooking,
    enqueuePrint,
    pull
  })
}

/**
 * @param {object} args
 * @param {string[]} args.selectedOrderIds
 * @param {object[]} args.cages
 * @param {{ cardCounts: Record<string, number>, tablePick: object|null }} [args.selection]
 */
export async function confirmBasketServe({
  selectedOrderIds,
  cages,
  selection,
  meta,
  completeCooking,
  enqueuePrint,
  pull
}) {
  const current = selection || emptyServeSelection()
  const plan = planBasketServeCookingCalls({ selectedOrderIds, cages })
  return settleConfirm({
    plan,
    selection: current,
    meta,
    completeCooking,
    enqueuePrint,
    pull
  })
}
