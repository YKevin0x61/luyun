import { describe, expect, it } from 'vitest'
import {
  DELIVERY_CANCELLED_DISH_STATUS,
  createInitialState,
  dismiss,
  step
} from '../deliveryCancelEngine.js'

function makeOrder(overrides = {}) {
  return {
    id: '1',
    business_flow_id: 'flow-1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    table_number: '美团1',
    station: 'shulong',
    source: 'delivery',
    ...overrides
  }
}

describe('deliveryCancelEngine', () => {
  it('first sync only primes baseline without alert', () => {
    const { state, effects } = step(createInitialState(), {
      orders: [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })],
      watchedStations: ['shulong']
    })
    expect(state.primed).toBe(true)
    expect(effects.playAlert).toBe(false)
    expect(state.banner.visible).toBe(false)
  })

  it('alerts when watched delivery order becomes cancelled', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '已制作待上菜' })],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(true)
    expect(next.state.banner.visible).toBe(true)
    expect(next.state.banner.count).toBe(1)
    expect(next.state.banner.hasCooked).toBe(true)
    expect(next.state.banner.summary).toContain('虾饺')
  })

  it('ignores cancel outside watched stations', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ station: 'changfen', dish_status: '待出餐' })],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({
          station: 'changfen',
          dish_status: DELIVERY_CANCELLED_DISH_STATUS
        })
      ],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(false)
    expect(next.state.banner.visible).toBe(false)
  })

  it('alerts when watched dine-in order becomes cancelled without steamer placement', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ source: '', table_number: '12桌', dish_status: '待出餐' })],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({
          source: '',
          table_number: '12桌',
          dish_status: DELIVERY_CANCELLED_DISH_STATUS
        })
      ],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(true)
    expect(next.state.banner.visible).toBe(true)
    expect(next.state.banner.hasCooked).toBe(false)
    expect(next.state.banner.summary).toContain('12桌')
    expect(next.state.banner.summary).toContain('虾饺')
  })

  it('empty watched set means no stations', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ station: 'xibing', dish_status: '待出餐' })],
      watchedStations: []
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({ station: 'xibing', dish_status: DELIVERY_CANCELLED_DISH_STATUS })
      ],
      watchedStations: []
    })
    expect(next.effects.playAlert).toBe(false)
  })

  it('stays silent when the wave is only 退菜占位', () => {
    const primed = step(createInitialState(), {
      orders: [
        makeOrder({
          dish_status: '待出餐',
          placement: { steamer_id: '1', port_index: 3 }
        })
      ],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({
          dish_status: DELIVERY_CANCELLED_DISH_STATUS,
          placement: { steamer_id: '1', port_index: 3 }
        })
      ],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(false)
    expect(next.state.banner.visible).toBe(false)
  })

  it('banners only 退示 and cooked items when mixed with 退菜占位', () => {
    const primed = step(createInitialState(), {
      orders: [
        makeOrder({
          id: '1',
          business_flow_id: 'flow-hold',
          dish_name: '烧卖',
          dish_status: '待出餐',
          placement: { steamer_id: '1', port_index: 2 }
        }),
        makeOrder({
          id: '2',
          business_flow_id: 'flow-notice',
          dish_name: '虾饺',
          table_number: '12桌',
          source: '',
          dish_status: '待出餐'
        })
      ],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({
          id: '1',
          business_flow_id: 'flow-hold',
          dish_name: '烧卖',
          dish_status: DELIVERY_CANCELLED_DISH_STATUS,
          placement: { steamer_id: '1', port_index: 2 }
        }),
        makeOrder({
          id: '2',
          business_flow_id: 'flow-notice',
          dish_name: '虾饺',
          table_number: '12桌',
          source: '',
          dish_status: DELIVERY_CANCELLED_DISH_STATUS
        })
      ],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(true)
    expect(next.state.banner.visible).toBe(true)
    expect(next.state.banner.count).toBe(1)
    expect(next.state.banner.hasCooked).toBe(false)
    expect(next.state.banner.summary).toContain('虾饺')
    expect(next.state.banner.summary).not.toContain('烧卖')
  })

  it('alerts on an unloaded cancel even if steamer notice_seconds would have expired', () => {
    const stale = '2020-01-01T00:00:00.000Z'
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '待出餐', updated_at: stale })],
      watchedStations: ['shulong']
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({
          dish_status: DELIVERY_CANCELLED_DISH_STATUS,
          updated_at: stale
        })
      ],
      watchedStations: ['shulong']
    })
    expect(next.effects.playAlert).toBe(true)
    expect(next.state.banner.visible).toBe(true)
  })

  it('dismiss clears banner', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '待出餐' })],
      watchedStations: ['shulong']
    }).state
    const alerted = step(primed, {
      orders: [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })],
      watchedStations: ['shulong']
    }).state
    const cleared = dismiss(alerted)
    expect(cleared.banner.visible).toBe(false)
    expect(cleared.banner.count).toBe(0)
  })

  it('re-prompts after 20s while banner is still visible', () => {
    const t0 = 1_000_000
    const cancelled = [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })]
    const watchedStations = ['shulong']
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '待出餐' })],
      watchedStations,
      now: t0
    }).state
    const alerted = step(primed, {
      orders: cancelled,
      watchedStations,
      now: t0
    })
    expect(alerted.effects.playAlert).toBe(true)

    const early = step(alerted.state, {
      orders: cancelled,
      watchedStations,
      now: t0 + 19_000
    })
    expect(early.effects.playAlert).toBe(false)
    expect(early.state.banner.visible).toBe(true)

    const due = step(early.state, {
      orders: cancelled,
      watchedStations,
      now: t0 + 20_000
    })
    expect(due.effects.playAlert).toBe(true)
    expect(due.state.banner.visible).toBe(true)
  })

  it('stops re-prompt after dismiss', () => {
    const t0 = 1_000_000
    const cancelled = [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })]
    const watchedStations = ['shulong']
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '待出餐' })],
      watchedStations,
      now: t0
    }).state
    const alerted = step(primed, {
      orders: cancelled,
      watchedStations,
      now: t0
    }).state
    const cleared = dismiss(alerted)
    const later = step(cleared, {
      orders: cancelled,
      watchedStations,
      now: t0 + 60_000
    })
    expect(later.effects.playAlert).toBe(false)
    expect(later.state.banner.visible).toBe(false)
  })
})
