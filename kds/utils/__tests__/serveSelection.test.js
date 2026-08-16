import { describe, expect, it } from 'vitest'
import { applyServeSelection, emptyServeSelection, serveSelectionAfterConfirm } from '../serveSelection.js'

describe('出餐选中 reducer', () => {
  it('counts 卡上出餐 from 0 and clamps to remaining 份', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    expect(state.cardCounts).toEqual({ 虾饺: 1 })

    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    expect(state.cardCounts).toEqual({ 虾饺: 2 })
  })

  it('peels with minus and drops the card key at 0; several cards can be selected together', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    state = applyServeSelection(state, { type: 'increase', chunkId: '叉烧包', max: 1 })
    expect(state.cardCounts).toEqual({ 虾饺: 2, 叉烧包: 1 })

    state = applyServeSelection(state, { type: 'decrease', chunkId: '虾饺' })
    expect(state.cardCounts).toEqual({ 虾饺: 1, 叉烧包: 1 })

    state = applyServeSelection(state, { type: 'decrease', chunkId: '虾饺' })
    expect(state.cardCounts).toEqual({ 叉烧包: 1 })
  })

  it('opening 选桌 clears all card counts and ignores +1 while the sheet is open', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 3 })
    state = applyServeSelection(state, { type: 'increase', chunkId: '叉烧包', max: 1 })

    state = applyServeSelection(state, { type: 'openTablePick', chunkId: '虾饺' })
    expect(state.cardCounts).toEqual({})
    expect(state.tablePick).toEqual({ chunkId: '虾饺', selectedOrderIds: [] })

    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 3 })
    expect(state.cardCounts).toEqual({})
    expect(state.tablePick.chunkId).toBe('虾饺')
  })

  it('toggles 订单行 only while 选桌 is open', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'a' })
    expect(state.tablePick).toBeNull()

    state = applyServeSelection(state, { type: 'openTablePick', chunkId: '虾饺' })
    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'a' })
    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'b' })
    expect(state.tablePick.selectedOrderIds).toEqual(['a', 'b'])

    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'a' })
    expect(state.tablePick.selectedOrderIds).toEqual(['b'])
  })

  it('close, complete, and externalClear leave no leftover card counts or 选桌', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'openTablePick', chunkId: '虾饺' })
    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'a' })

    state = applyServeSelection(state, { type: 'closeTablePick' })
    expect(state).toEqual(emptyServeSelection())

    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    state = applyServeSelection(state, { type: 'completeServe' })
    expect(state).toEqual(emptyServeSelection())

    state = applyServeSelection(state, { type: 'openTablePick', chunkId: '虾饺' })
    state = applyServeSelection(state, { type: 'toggleOrderLine', orderId: 'a' })
    state = applyServeSelection(state, { type: 'externalClear' })
    expect(state).toEqual(emptyServeSelection())
  })

  it('keeps 出餐选中 when the confirm is rejected', () => {
    let state = emptyServeSelection()
    state = applyServeSelection(state, { type: 'increase', chunkId: '虾饺', max: 2 })
    state = applyServeSelection(state, { type: 'increase', chunkId: '叉烧包', max: 1 })

    expect(serveSelectionAfterConfirm(state, false)).toEqual({
      cardCounts: { 虾饺: 1, 叉烧包: 1 },
      tablePick: null
    })
    expect(serveSelectionAfterConfirm(state, true)).toEqual(emptyServeSelection())
  })
})
