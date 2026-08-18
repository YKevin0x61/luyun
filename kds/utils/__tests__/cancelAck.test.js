import { beforeEach, describe, expect, it, vi } from 'vitest'

const memory = new Map()

vi.stubGlobal('uni', {
  getStorageSync(key) {
    return memory.has(key) ? memory.get(key) : ''
  },
  setStorageSync(key, value) {
    memory.set(key, value)
  },
  removeStorageSync(key) {
    memory.delete(key)
  }
})

const {
  acknowledgeCancelIds,
  acknowledgeNeverLoadedCancels,
  businessDateKey,
  cancelAckLineId,
  loadAcknowledgedCancelIds
} = await import('../cancelAck.js')

describe('cancelAck', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('prefers business_flow_id then id as the ack identity', () => {
    expect(cancelAckLineId({ business_flow_id: 'flow-1', id: '9' })).toBe('flow-1')
    expect(cancelAckLineId({ id: '9' })).toBe('9')
    expect(cancelAckLineId({ _id: 'row-3' })).toBe('row-3')
    expect(cancelAckLineId({})).toBe('')
  })

  it('keys 退菜已确认 by local 营业日 and reloads the same set', () => {
    acknowledgeCancelIds('2026-08-18', ['flow-1', 'flow-2'])
    expect([...loadAcknowledgedCancelIds('2026-08-18')].sort()).toEqual(['flow-1', 'flow-2'])
  })

  it('starts empty on a new 营业日 and does not keep yesterday', () => {
    acknowledgeCancelIds('2026-08-17', ['flow-old'])
    expect([...loadAcknowledgedCancelIds('2026-08-18')]).toEqual([])
    acknowledgeCancelIds('2026-08-18', ['flow-new'])
    expect([...loadAcknowledgedCancelIds('2026-08-17')]).toEqual([])
    expect([...loadAcknowledgedCancelIds('2026-08-18')]).toEqual(['flow-new'])
  })

  it('acks every watched never-loaded cancel and skips 退菜占位 plus plucked cages', () => {
    const now = new Date(2026, 7, 18, 12, 0, 0)
    const ids = acknowledgeNeverLoadedCancels({
      now,
      watchedStations: ['shulong'],
      orders: [
        {
          business_flow_id: 'flow-notice',
          id: '1',
          dish_status: '已取消',
          status: '退菜',
          station: 'shulong'
        },
        {
          business_flow_id: 'flow-hold',
          id: '2',
          dish_status: '已取消',
          status: '退菜',
          station: 'shulong',
          placement: { steamer_id: '1', port_index: 3 }
        },
        {
          business_flow_id: 'flow-plucked',
          id: '3',
          dish_status: '已取消',
          status: '退菜',
          station: 'shulong',
          loaded_at: '2026-08-18T11:00:00+08:00'
        },
        {
          business_flow_id: 'flow-other',
          id: '4',
          dish_status: '已取消',
          status: '退菜',
          station: 'changfen'
        }
      ]
    })
    expect([...ids]).toEqual(['flow-notice'])
    expect(businessDateKey(now)).toBe('2026-08-18')
    expect([...loadAcknowledgedCancelIds('2026-08-18')]).toEqual(['flow-notice'])
  })
})
