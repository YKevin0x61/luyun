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
      watchedStations: []
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

  it('ignores non-delivery cancels', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ source: '', dish_status: '待出餐' })],
      watchedStations: []
    }).state
    const next = step(primed, {
      orders: [
        makeOrder({ source: '', dish_status: DELIVERY_CANCELLED_DISH_STATUS })
      ],
      watchedStations: []
    })
    expect(next.effects.playAlert).toBe(false)
  })

  it('empty watched set means all stations', () => {
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
    expect(next.effects.playAlert).toBe(true)
  })

  it('dismiss clears banner', () => {
    const primed = step(createInitialState(), {
      orders: [makeOrder({ dish_status: '待出餐' })],
      watchedStations: []
    }).state
    const alerted = step(primed, {
      orders: [makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })],
      watchedStations: []
    }).state
    const cleared = dismiss(alerted)
    expect(cleared.banner.visible).toBe(false)
    expect(cleared.banner.count).toBe(0)
  })
})
