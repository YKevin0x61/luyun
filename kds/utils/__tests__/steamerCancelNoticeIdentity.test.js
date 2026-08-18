import { describe, expect, it } from 'vitest'
import { deriveSteamerPhase, listAwaitingSteamerCages } from '../steamerConsole.js'

describe('deriveSteamerPhase kitchen identity', () => {
  it('does not treat a parallel _refund_ 待出餐 row as 待上笼退示 or 待上笼', () => {
    const refund = {
      business_flow_id: 't8_虾饺_refund_1',
      dish_status: '待出餐',
      status: '退菜',
      quantity: 1
    }
    expect(deriveSteamerPhase(refund)).toBeNull()
    expect(listAwaitingSteamerCages([refund])).toEqual([])
  })

  it('still treats 已取消 never-loaded as 待上笼退示', () => {
    const notice = {
      business_flow_id: 'flow-n1',
      dish_status: '已取消',
      status: '退菜',
      quantity: 0
    }
    expect(deriveSteamerPhase(notice)).toBe('待上笼退示')
  })
})
