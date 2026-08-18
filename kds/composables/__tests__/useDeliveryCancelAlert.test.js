import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const playCancelAlert = vi.fn()
const getAlertParams = vi.fn(() => ({
  cancelTone: '清脆',
  alertVolume: 0.6
}))

vi.mock('../../utils/sound.js', () => ({
  playCancelAlert: (...args) => playCancelAlert(...args)
}))

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getAlertParams: (...args) => getAlertParams(...args)
  }
}))

const { useDeliveryCancelAlert } = await import('../useDeliveryCancelAlert.js')
const { DELIVERY_CANCELLED_DISH_STATUS } = await import(
  '../../utils/deliveryCancelEngine.js'
)

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

describe('useDeliveryCancelAlert', () => {
  beforeEach(() => {
    playCancelAlert.mockReset()
    getAlertParams.mockReset()
    getAlertParams.mockReturnValue({
      cancelTone: '清脆',
      alertVolume: 0.6
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('plays cancel alert when a watched delivery order is cancelled after prime', () => {
    const { syncOrders } = useDeliveryCancelAlert({ watchedStations: ['shulong'] })
    syncOrders([makeOrder({ dish_status: '已制作待上菜' })])
    expect(playCancelAlert).not.toHaveBeenCalled()

    const played = syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])
    expect(played).toBe(true)
    expect(playCancelAlert).toHaveBeenCalledTimes(1)
  })

  it('plays cancel alert when a watched dine-in order is cancelled after prime', () => {
    const { syncOrders } = useDeliveryCancelAlert({ watchedStations: ['shulong'] })
    syncOrders([makeOrder({ source: '', table_number: '12桌', dish_status: '待出餐' })])
    expect(playCancelAlert).not.toHaveBeenCalled()

    const played = syncOrders([
      makeOrder({
        source: '',
        table_number: '12桌',
        dish_status: DELIVERY_CANCELLED_DISH_STATUS
      })
    ])
    expect(played).toBe(true)
    expect(playCancelAlert).toHaveBeenCalledTimes(1)
  })

  it('does not play when the wave is only 退菜占位', () => {
    const { syncOrders, deliveryCancelAlert } = useDeliveryCancelAlert({
      watchedStations: ['shulong']
    })
    syncOrders([
      makeOrder({
        dish_status: '待出餐',
        placement: { steamer_id: '1', port_index: 3 }
      })
    ])
    const played = syncOrders([
      makeOrder({
        dish_status: DELIVERY_CANCELLED_DISH_STATUS,
        placement: { steamer_id: '1', port_index: 3 }
      })
    ])
    expect(played).toBe(false)
    expect(playCancelAlert).not.toHaveBeenCalled()
    expect(deliveryCancelAlert.value.visible).toBe(false)
  })

  it('does not play on prime or a non-cancel sync', () => {
    const { syncOrders } = useDeliveryCancelAlert({ watchedStations: ['shulong'] })
    syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])
    expect(playCancelAlert).not.toHaveBeenCalled()

    syncOrders([makeOrder({ dish_status: '待出餐' })])
    expect(playCancelAlert).not.toHaveBeenCalled()
  })

  it('does not call plus.device.beep or uni.vibrate for 退菜/取消', () => {
    const beep = vi.fn()
    const vibrateShort = vi.fn()
    const vibrateLong = vi.fn()
    globalThis.plus = { device: { beep } }
    globalThis.uni = { vibrateShort, vibrateLong }

    const { syncOrders } = useDeliveryCancelAlert({ watchedStations: ['shulong'] })
    syncOrders([makeOrder({ dish_status: '已制作待上菜' })])
    syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])

    expect(playCancelAlert).toHaveBeenCalledTimes(1)
    expect(beep).not.toHaveBeenCalled()
    expect(vibrateShort).not.toHaveBeenCalled()
    expect(vibrateLong).not.toHaveBeenCalled()

    delete globalThis.plus
    delete globalThis.uni
  })

  it('higherKindClaimed is true only for this moment after cancel plays', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const { syncOrders, higherKindClaimed } = useDeliveryCancelAlert({
      watchedStations: ['shulong']
    })
    expect(syncOrders([makeOrder({ dish_status: '已制作待上菜' })])).toBe(false)
    expect(higherKindClaimed()).toBe(false)

    expect(syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])).toBe(true)
    expect(higherKindClaimed()).toBe(true)

    vi.setSystemTime(1_000_000 + 1000)
    expect(higherKindClaimed()).toBe(false)
  })

  it('plays the snapshotted 款 until reloadConfig', () => {
    const { syncOrders, reloadConfig } = useDeliveryCancelAlert({
      watchedStations: ['shulong']
    })
    syncOrders([makeOrder({ dish_status: '已制作待上菜' })])
    getAlertParams.mockReturnValue({
      cancelTone: '穿透',
      alertVolume: 0.8
    })
    syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])
    expect(playCancelAlert).toHaveBeenCalledWith({ tone: '清脆', volume: 0.6 })

    reloadConfig()
    syncOrders([makeOrder({ dish_status: '待出餐' })])
    syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])
    expect(playCancelAlert).toHaveBeenLastCalledWith({ tone: '穿透', volume: 0.8 })
  })

  it('re-plays cancel alert every 20s until 知道了', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const {
      syncOrders,
      start,
      stop,
      dismissDeliveryCancelAlert
    } = useDeliveryCancelAlert({
      watchedStations: ['shulong']
    })
    start()
    syncOrders([makeOrder({ dish_status: '待出餐' })])
    syncOrders([makeOrder({ dish_status: DELIVERY_CANCELLED_DISH_STATUS })])
    expect(playCancelAlert).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(19_000)
    expect(playCancelAlert).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(1_000)
    expect(playCancelAlert).toHaveBeenCalledTimes(2)

    dismissDeliveryCancelAlert()
    vi.advanceTimersByTime(20_000)
    expect(playCancelAlert).toHaveBeenCalledTimes(2)

    stop()
  })
})
