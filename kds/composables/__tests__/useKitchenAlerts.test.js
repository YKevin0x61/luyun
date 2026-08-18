import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const playNewOrderDing = vi.fn()
const playOvertimeAlarm = vi.fn()

vi.mock('../../utils/sound.js', () => ({
  playNewOrderDing: (...args) => playNewOrderDing(...args),
  playOvertimeAlarm: (...args) => playOvertimeAlarm(...args),
  unlockSound: vi.fn(),
  isSoundUnlocked: () => true
}))

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getSettings: () => ({
      watchedStations: ['shulong'],
      alert: {
        beepCap: 5,
        reescalateSec: 20,
        badgeDismissSec: 30,
        urgentMin: 20,
        overtimeRepeatSec: 30,
        newOrderTone: '清脆',
        overtimeTone: '清脆',
        alertVolume: 0.6
      }
    })
  }
}))

const { useKitchenAlerts } = await import('../useKitchenAlerts.js')

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

describe('useKitchenAlerts', () => {
  beforeEach(() => {
    playNewOrderDing.mockReset()
    playOvertimeAlarm.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('syncOrders with cancelClaimed updates state but does not play ding or overtime', () => {
    const alerts = useKitchenAlerts()
    alerts.syncOrders([])
    alerts.syncOrders([makeOrder()], { cancelClaimed: true })

    expect(alerts.awaitingAck.value).toBe(true)
    expect(alerts.screenBorderVisual.value).toBe('yellow')
    expect(playNewOrderDing).not.toHaveBeenCalled()
    expect(playOvertimeAlarm).not.toHaveBeenCalled()
    expect(alerts.higherKindClaimed()).toBe(false)
  })

  it('higherKindClaimed is true only for this moment after ding plays', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const alerts = useKitchenAlerts()
    alerts.syncOrders([])
    expect(alerts.higherKindClaimed()).toBe(false)

    alerts.syncOrders([makeOrder()])
    expect(playNewOrderDing).toHaveBeenCalled()
    expect(alerts.higherKindClaimed()).toBe(true)

    vi.setSystemTime(1_000_000 + 1000)
    expect(alerts.higherKindClaimed()).toBe(false)
  })

  it('syncOrders skips ding when getCancelClaimed is true even without cancelClaimed option', () => {
    const getCancelClaimed = vi.fn(() => true)
    const alerts = useKitchenAlerts({ getCancelClaimed })
    alerts.syncOrders([])
    alerts.syncOrders([makeOrder()])
    expect(getCancelClaimed).toHaveBeenCalled()
    expect(playNewOrderDing).not.toHaveBeenCalled()
    expect(alerts.awaitingAck.value).toBe(true)
  })

  it('tick skips ding when getCancelClaimed is true', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const getCancelClaimed = vi.fn(() => false)
    const alerts = useKitchenAlerts({ getCancelClaimed })
    alerts.start()
    alerts.syncOrders([])
    alerts.syncOrders([makeOrder()])
    expect(playNewOrderDing).toHaveBeenCalled()

    playNewOrderDing.mockReset()
    getCancelClaimed.mockReturnValue(true)
    vi.advanceTimersByTime(20_000)
    expect(getCancelClaimed).toHaveBeenCalled()
    expect(playNewOrderDing).not.toHaveBeenCalled()
    alerts.stop()
  })
})
