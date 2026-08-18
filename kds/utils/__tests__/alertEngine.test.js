import { describe, expect, it } from 'vitest'
import {
  acknowledge,
  createInitialState,
  step
} from '../alertEngine.js'

const T0 = Date.parse('2026-07-23T12:00:00.000Z')

function defaultConfig(overrides = {}) {
  return {
    watchedStations: ['shulong'],
    beepCap: 5,
    reescalateSec: 20,
    badgeDismissSec: 30,
    urgentMin: 20,
    overtimeRepeatSec: 30,
    ...overrides
  }
}

function makeOrder(overrides = {}) {
  return {
    id: '1',
    business_flow_id: 'flow-1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    order_time: '2026-07-23T11:55:00.000Z',
    table_number: 'A1',
    station: 'shulong',
    ...overrides
  }
}

function badgeModes(state) {
  return Object.fromEntries(
    [...state.newBadges.entries()].map(([id, badge]) => [id, badge.mode])
  )
}

function prime(orders, config = defaultConfig(), now = T0) {
  return step(createInitialState(), { orders, config, now }).state
}

describe('alertEngine', () => {
  it('first step builds baseline only — no ding, no badges, no awaitingAck', () => {
    const order = makeOrder()
    const { state, effects } = step(createInitialState(), {
      orders: [order],
      config: defaultConfig(),
      now: T0
    })

    expect(effects).toEqual({ dingCount: 0, overtimeAlarm: false })
    expect(state.awaitingAck).toBe(false)
    expect(state.newBadges.size).toBe(0)
    expect(state.borderState).toBe('red')
    expect(state.snapshot.get('flow-1')).toEqual({
      status: '待出餐',
      quantity: 1,
      awaiting: true,
      mapWork: false
    })
  })

  describe('new-order event detection', () => {
    it('treats a new pending flowId as a new-order event (idle → yellow)', () => {
      const baseline = prime([])
      const { state, effects } = step(baseline, {
        orders: [makeOrder({ business_flow_id: 'flow-new' })],
        config: defaultConfig(),
        now: T0
      })

      expect(effects.dingCount).toBe(1)
      expect(state.awaitingAck).toBe(true)
      expect(state.borderState).toBe('yellow')
      expect(badgeModes(state)).toEqual({ 'flow-new': 'yellow' })
    })

    it('treats 叫起 as a new-order event', () => {
      const held = makeOrder({ business_flow_id: 'flow-hold', is_hold: true })
      const baseline = prime([held])
      expect(baseline.borderState).toBe('green')
      expect(baseline.awaitingAck).toBe(false)
      const { state, effects } = step(baseline, {
        orders: [makeOrder({ business_flow_id: 'flow-hold', is_hold: false })],
        config: defaultConfig(),
        now: T0
      })
      expect(effects.dingCount).toBe(1)
      expect(state.awaitingAck).toBe(true)
      expect(state.borderState).toBe('yellow')
    })

    it('clears yellow when 等叫 empties 待出餐工作', () => {
      const pending = makeOrder({ business_flow_id: 'flow-hold' })
      const afterNew = step(prime([]), {
        orders: [pending],
        config: defaultConfig(),
        now: T0
      }).state
      expect(afterNew.borderState).toBe('yellow')
      expect(afterNew.awaitingAck).toBe(true)
      const { state, effects } = step(afterNew, {
        orders: [makeOrder({ business_flow_id: 'flow-hold', is_hold: true })],
        config: defaultConfig(),
        now: T0
      })
      expect(effects.dingCount).toBe(0)
      expect(state.borderState).toBe('green')
      expect(state.awaitingAck).toBe(false)
    })

    it('treats pending quantity increase as a new-order event (busy path)', () => {
      const baseline = prime([makeOrder({ quantity: 1 })])
      const { state, effects } = step(baseline, {
        orders: [makeOrder({ quantity: 3 })],
        config: defaultConfig(),
        now: T0
      })

      // Already had pending → busy: one ding, busy badge, no awaitingAck
      expect(effects.dingCount).toBe(1)
      expect(state.awaitingAck).toBe(false)
      expect(state.borderState).toBe('red')
      expect(badgeModes(state)).toEqual({ 'flow-1': 'busy' })
    })

    it('treats non-pending → pending (re-order after cancel) as a new-order event', () => {
      const baseline = prime([
        makeOrder({ dish_status: '已制作待上菜', quantity: 1 })
      ])
      const { state, effects } = step(baseline, {
        orders: [makeOrder({ dish_status: '待出餐', quantity: 1 })],
        config: defaultConfig(),
        now: T0
      })

      expect(effects.dingCount).toBe(1)
      expect(state.awaitingAck).toBe(true)
      expect(badgeModes(state)).toEqual({ 'flow-1': 'yellow' })
    })

    it('ignores orders outside watched stations; empty watched = no stations', () => {
      const watched = defaultConfig({ watchedStations: ['shulong'] })
      const baseline = prime([], watched)
      const { state, effects } = step(baseline, {
        orders: [
          makeOrder({ business_flow_id: 'other', station: 'changfen' }),
          makeOrder({ business_flow_id: 'mine', station: 'shulong' })
        ],
        config: watched,
        now: T0
      })

      expect(effects.dingCount).toBe(1)
      expect(badgeModes(state)).toEqual({ mine: 'yellow' })
      expect(state.snapshot.has('other')).toBe(false)

      const empty = defaultConfig({ watchedStations: [] })
      const emptyBaseline = prime([], empty)
      const ignored = step(emptyBaseline, {
        orders: [makeOrder({ business_flow_id: 'mine', station: 'shulong' })],
        config: empty,
        now: T0
      })
      expect(ignored.effects.dingCount).toBe(0)
      expect(ignored.state.snapshot.has('mine')).toBe(false)
    })

    it('ignores refund orders', () => {
      const baseline = prime([])
      const { effects, state } = step(baseline, {
        orders: [
          makeOrder({
            business_flow_id: 'flow_refund_1',
            change_type: '退菜',
            quantity: -1
          })
        ],
        config: defaultConfig(),
        now: T0
      })

      expect(effects.dingCount).toBe(0)
      expect(state.snapshot.size).toBe(0)
      expect(state.borderState).toBe('green')
    })
  })

  describe('busy / idle grading', () => {
    it('caps idle dingCount at beepCap and sets yellow badges for each new line', () => {
      const baseline = prime([])
      const orders = [1, 2, 3, 4, 5, 6, 7].map((n) =>
        makeOrder({ id: String(n), business_flow_id: `flow-${n}` })
      )
      const { state, effects } = step(baseline, {
        orders,
        config: defaultConfig({ beepCap: 5 }),
        now: T0
      })

      expect(effects.dingCount).toBe(5)
      expect(state.awaitingAck).toBe(true)
      expect(state.borderState).toBe('yellow')
      expect(Object.keys(badgeModes(state))).toHaveLength(7)
      expect(Object.values(badgeModes(state)).every((m) => m === 'yellow')).toBe(true)
    })

    it('while busy, new lines ding once with busy badges and keep red border', () => {
      const existing = makeOrder({ business_flow_id: 'existing' })
      const baseline = prime([existing])
      const { state, effects } = step(baseline, {
        orders: [
          existing,
          makeOrder({ id: '2', business_flow_id: 'n1' }),
          makeOrder({ id: '3', business_flow_id: 'n2' })
        ],
        config: defaultConfig(),
        now: T0
      })

      expect(effects.dingCount).toBe(1)
      expect(state.awaitingAck).toBe(false)
      expect(state.borderState).toBe('red')
      expect(badgeModes(state)).toEqual({ n1: 'busy', n2: 'busy' })
    })
  })

  describe('border priority', () => {
    it('green when no pending after baseline', () => {
      const { state } = step(createInitialState(), {
        orders: [],
        config: defaultConfig(),
        now: T0
      })
      expect(state.borderState).toBe('green')
    })

    it('yellow when awaitingAck even if some pending lines are overtime', () => {
      const baseline = prime([])
      const overdue = makeOrder({
        order_time: '2026-07-23T11:00:00.000Z' // 60 min before T0
      })
      const { state, effects } = step(baseline, {
        orders: [overdue],
        config: defaultConfig({ urgentMin: 20 }),
        now: T0
      })

      expect(state.awaitingAck).toBe(true)
      expect(state.borderState).toBe('yellow')
      expect(effects.dingCount).toBe(1)
      expect(effects.overtimeAlarm).toBe(false)
    })

    it('red when pending and not awaitingAck even if overtime', () => {
      const old = makeOrder({
        business_flow_id: 'old',
        order_time: '2026-07-23T11:00:00.000Z' // 60 min before T0
      })
      const baseline = prime([old], defaultConfig({ urgentMin: 20 }), T0)
      expect(baseline.borderState).toBe('red')

      const { state, effects } = step(baseline, {
        orders: [old, makeOrder({ id: '2', business_flow_id: 'new' })],
        config: defaultConfig({ urgentMin: 20 }),
        now: T0
      })

      expect(state.borderState).toBe('red')
      expect(effects.dingCount).toBe(1)
      expect(effects.overtimeAlarm).toBe(false)
      expect(state.awaitingAck).toBe(false)
    })

    it('yellow when awaitingAck and pending is not overtime', () => {
      const baseline = prime([])
      const { state } = step(baseline, {
        orders: [makeOrder()],
        config: defaultConfig(),
        now: T0
      })
      expect(state.borderState).toBe('yellow')
      expect(state.awaitingAck).toBe(true)
    })

    it('acknowledge after overtime pending yields red, not overtime', () => {
      const baseline = prime([])
      const overdue = makeOrder({
        order_time: '2026-07-23T11:00:00.000Z'
      })
      const afterNew = step(baseline, {
        orders: [overdue],
        config: defaultConfig({ urgentMin: 20 }),
        now: T0
      }).state

      expect(afterNew.borderState).toBe('yellow')

      const acked = acknowledge(afterNew)
      expect(acked.awaitingAck).toBe(false)
      expect(acked.borderState).toBe('red')
    })
  })

  describe('acknowledge', () => {
    it('clears yellow badges and awaitingAck; border falls to red while pending remain', () => {
      const baseline = prime([])
      const afterNew = step(baseline, {
        orders: [
          makeOrder({ business_flow_id: 'a' }),
          makeOrder({ id: '2', business_flow_id: 'b' })
        ],
        config: defaultConfig(),
        now: T0
      }).state

      expect(afterNew.awaitingAck).toBe(true)
      expect(afterNew.borderState).toBe('yellow')

      const acked = acknowledge(afterNew)
      expect(acked.awaitingAck).toBe(false)
      expect(acked.newBadges.size).toBe(0)
      expect(acked.borderState).toBe('red')
    })

    it('keeps busy badges when acknowledging', () => {
      const existing = makeOrder({ business_flow_id: 'existing' })
      const baseline = prime([existing])
      const afterBusy = step(baseline, {
        orders: [existing, makeOrder({ id: '2', business_flow_id: 'n1' })],
        config: defaultConfig(),
        now: T0
      }).state

      // Force awaitingAck with a synthetic yellow badge alongside busy
      afterBusy.awaitingAck = true
      afterBusy.newBadges.set('n1', { mode: 'busy', at: T0 })
      afterBusy.newBadges.set('ghost', { mode: 'yellow', at: T0 })

      const acked = acknowledge(afterBusy)
      expect(acked.awaitingAck).toBe(false)
      expect(badgeModes(acked)).toEqual({ n1: 'busy' })
    })
  })

  describe('badge lifecycle', () => {
    it('auto-dismisses busy badges after badgeDismissSec', () => {
      const existing = makeOrder({ business_flow_id: 'existing' })
      const baseline = prime([existing])
      const afterBusy = step(baseline, {
        orders: [existing, makeOrder({ id: '2', business_flow_id: 'n1' })],
        config: defaultConfig({ badgeDismissSec: 30 }),
        now: T0
      }).state

      expect(badgeModes(afterBusy)).toEqual({ n1: 'busy' })

      const later = step(afterBusy, {
        orders: [existing, makeOrder({ id: '2', business_flow_id: 'n1' })],
        config: defaultConfig({ badgeDismissSec: 30 }),
        now: T0 + 30_000
      }).state

      expect(later.newBadges.size).toBe(0)
    })

    it('clears badges when the order is cooked or disappears', () => {
      const baseline = prime([])
      const afterNew = step(baseline, {
        orders: [makeOrder()],
        config: defaultConfig(),
        now: T0
      }).state
      expect(badgeModes(afterNew)).toEqual({ 'flow-1': 'yellow' })

      const cooked = step(afterNew, {
        orders: [makeOrder({ dish_status: '已制作待上菜' })],
        config: defaultConfig(),
        now: T0 + 1000
      }).state
      expect(cooked.newBadges.size).toBe(0)
      // no pending left → clear awaitingAck; border returns to green
      expect(cooked.awaitingAck).toBe(false)
      expect(cooked.borderState).toBe('green')
    })
  })

  describe('overtime alarm + re-escalate', () => {
    it('same-step ding and overtime emits ding only; overtime waits for the next repeat interval', () => {
      const baseline = prime([])
      const config = defaultConfig({ urgentMin: 20, overtimeRepeatSec: 30 })
      const overdue = makeOrder({
        order_time: '2026-07-23T11:00:00.000Z'
      })
      const first = step(baseline, {
        orders: [overdue],
        config,
        now: T0
      })

      expect(first.effects.dingCount).toBe(1)
      expect(first.effects.overtimeAlarm).toBe(false)
      expect(first.state.lastOvertimeAlarmAt).toBe(T0)

      const acked = acknowledge(first.state)
      const oneSecondLater = step(acked, {
        orders: [overdue],
        config,
        now: T0 + 1000
      })
      expect(oneSecondLater.effects.overtimeAlarm).toBe(false)
      expect(oneSecondLater.effects.dingCount).toBe(0)

      const atRepeat = step(oneSecondLater.state, {
        orders: [overdue],
        config,
        now: T0 + 30_000
      })
      expect(atRepeat.effects.overtimeAlarm).toBe(true)
      expect(atRepeat.effects.dingCount).toBe(0)
    })

    it('emits overtimeAlarm when pending wait exceeds urgentMin, then repeats on interval', () => {
      const old = makeOrder({
        order_time: '2026-07-23T11:30:00.000Z' // 30 min before T0
      })
      const config = defaultConfig({ urgentMin: 20, overtimeRepeatSec: 30 })
      const baseline = prime([old], config, T0)
      // first pass: no alarm
      expect(
        step(createInitialState(), { orders: [old], config, now: T0 }).effects.overtimeAlarm
      ).toBe(false)

      // next tick after prime should alarm immediately once overtime exists
      const first = step(baseline, { orders: [old], config, now: T0 + 1000 })
      expect(first.effects.overtimeAlarm).toBe(true)
      expect(first.state.borderState).toBe('red')

      const tooSoon = step(first.state, { orders: [old], config, now: T0 + 10_000 })
      expect(tooSoon.effects.overtimeAlarm).toBe(false)

      const due = step(tooSoon.state, { orders: [old], config, now: T0 + 1_000 + 30_000 })
      expect(due.effects.overtimeAlarm).toBe(true)
    })

    it('does not treat a just-fired line as overtime from the old 下单时间', () => {
      const fired = makeOrder({
        order_time: '2026-07-23T11:30:00.000Z',
        fired_at: '2026-07-23T12:00:00.000Z'
      })
      const config = defaultConfig({ urgentMin: 20, overtimeRepeatSec: 30 })
      const baseline = prime([fired], config, T0)
      const { effects } = step(baseline, { orders: [fired], config, now: T0 + 1000 })
      expect(effects.overtimeAlarm).toBe(false)
    })

    it('re-escalates with dingCount=1 while awaitingAck after reescalateSec', () => {
      const baseline = prime([])
      const config = defaultConfig({ reescalateSec: 20 })
      const afterNew = step(baseline, {
        orders: [makeOrder()],
        config,
        now: T0
      })
      expect(afterNew.state.awaitingAck).toBe(true)
      expect(afterNew.effects.dingCount).toBe(1)

      const tooSoon = step(afterNew.state, {
        orders: [makeOrder()],
        config,
        now: T0 + 10_000
      })
      expect(tooSoon.effects.dingCount).toBe(0)

      const due = step(tooSoon.state, {
        orders: [makeOrder()],
        config,
        now: T0 + 20_000
      })
      expect(due.effects.dingCount).toBe(1)
      expect(due.state.awaitingAck).toBe(true)
    })
  })

  describe('熟笼蒸炉屏', () => {
    const placement = {
      steamer_id: '1',
      port_index: 1,
      stack_order: 1,
      loaded_at: '2026-07-23T11:58:00.000Z'
    }

    it('new 待上笼 yellow-flashes; a new 在蒸 cage does not', () => {
      const baseline = prime([])
      const steaming = makeOrder({
        business_flow_id: 'already-on',
        placement
      })
      const afterSteam = step(baseline, {
        orders: [steaming],
        config: defaultConfig(),
        now: T0
      })
      expect(afterSteam.effects.dingCount).toBe(0)
      expect(afterSteam.state.awaitingAck).toBe(false)
      expect(afterSteam.state.borderState).toBe('green')
      expect(afterSteam.state.newBadges.size).toBe(0)

      const awaiting = makeOrder({ business_flow_id: 'need-load' })
      const afterAwaiting = step(afterSteam.state, {
        orders: [steaming, awaiting],
        config: defaultConfig(),
        now: T0 + 1000
      })
      expect(afterAwaiting.effects.dingCount).toBe(1)
      expect(afterAwaiting.state.awaitingAck).toBe(true)
      expect(afterAwaiting.state.borderState).toBe('yellow')
      expect(badgeModes(afterAwaiting.state)).toEqual({ 'need-load': 'yellow' })
    })

    it('下笼 of a 在蒸 line is not a 新单事件', () => {
      const steaming = makeOrder({
        business_flow_id: 'same-line',
        placement
      })
      const baseline = prime([steaming])
      const unloaded = makeOrder({ business_flow_id: 'same-line' })
      const { state, effects } = step(baseline, {
        orders: [unloaded],
        config: defaultConfig(),
        now: T0 + 1000
      })
      expect(effects.dingCount).toBe(0)
      expect(state.awaitingAck).toBe(false)
      expect(state.newBadges.size).toBe(0)
      expect(state.borderState).toBe('red')
    })

    it('hasPending for border is 待上笼 only', () => {
      const steaming = makeOrder({ business_flow_id: 'on-hole', placement })
      const { state } = step(createInitialState(), {
        orders: [steaming],
        config: defaultConfig(),
        now: T0
      })
      expect(state.borderState).toBe('green')
      expect(state.awaitingAck).toBe(false)
    })

    it('steam overtime uses loaded_at and does not steal the yellow border', () => {
      const cfg = defaultConfig({
        steamUrgentMin: 15,
        overtimeRepeatSec: 30
      })
      const baseline = prime([], cfg)
      const awaiting = makeOrder({ business_flow_id: 'need-load' })
      const overdueSteam = makeOrder({
        business_flow_id: 'over',
        placement: {
          ...placement,
          loaded_at: '2026-07-23T11:30:00.000Z'
        }
      })
      const { state, effects } = step(baseline, {
        orders: [awaiting, overdueSteam],
        config: cfg,
        now: T0
      })
      expect(effects.dingCount).toBe(1)
      expect(effects.overtimeAlarm).toBe(false)
      expect(state.awaitingAck).toBe(true)
      expect(state.borderState).toBe('yellow')
      expect(badgeModes(state)).toEqual({ 'need-load': 'yellow' })
    })

    it('cancel-holds are not new-order events', () => {
      const baseline = prime([])
      const hold = makeOrder({
        business_flow_id: 'hold-1',
        dish_status: '已取消',
        status: '退菜',
        placement
      })
      const { state, effects } = step(baseline, {
        orders: [hold],
        config: defaultConfig(),
        now: T0
      })
      expect(effects.dingCount).toBe(0)
      expect(state.awaitingAck).toBe(false)
      expect(state.newBadges.size).toBe(0)
      expect(state.borderState).toBe('green')
    })

    it('待上笼退示 does not redden the border and is not a 新单事件', () => {
      const baseline = prime([])
      const notice = makeOrder({
        business_flow_id: 'notice-1',
        dish_status: '已取消',
        status: '退菜'
      })
      const { state, effects } = step(baseline, {
        orders: [notice],
        config: defaultConfig(),
        now: T0
      })
      expect(effects.dingCount).toBe(0)
      expect(state.awaitingAck).toBe(false)
      expect(state.newBadges.size).toBe(0)
      expect(state.borderState).toBe('green')
    })

    it('a 待上笼 line becoming 待上笼退示 is not a 新单事件 and clears pending red', () => {
      const live = makeOrder({ business_flow_id: 'same-line' })
      const baseline = prime([live])
      expect(baseline.borderState).toBe('red')
      const { state, effects } = step(baseline, {
        orders: [
          makeOrder({
            business_flow_id: 'same-line',
            dish_status: '已取消',
            status: '退菜'
          })
        ],
        config: defaultConfig(),
        now: T0 + 1000
      })
      expect(effects.dingCount).toBe(0)
      expect(state.awaitingAck).toBe(false)
      expect(state.newBadges.size).toBe(0)
      expect(state.borderState).toBe('green')
    })
  })
})
