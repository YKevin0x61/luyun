import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const getWatchedStations = vi.fn(() => ['changfen'])
const getDishCardQuantityCap = vi.fn(() => 0)
const getOrderGapMinutes = vi.fn(() => 0)
const getTimeThresholdsMs = vi.fn(() => ({ warning: 15 * 60 * 1000, urgent: 20 * 60 * 1000 }))

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getWatchedStations: (...args) => getWatchedStations(...args),
    getDishCardQuantityCap: (...args) => getDishCardQuantityCap(...args),
    getOrderGapMinutes: (...args) => getOrderGapMinutes(...args),
    getSettings: () => ({
      watchedStations: getWatchedStations(),
      dishCardQuantityCap: getDishCardQuantityCap(),
      orderGapMinutes: getOrderGapMinutes(),
      alert: { warnMin: 15, urgentMin: 20 }
    })
  }
}))

vi.mock('../../utils/timeThresholds.js', () => ({
  getTimeThresholdsMs: (...args) => getTimeThresholdsMs(...args)
}))

const syncAlertOrders = vi.fn()
const reloadConfig = vi.fn()
const startAlerts = vi.fn()
const stopAlerts = vi.fn()
const kitchenHigherKindClaimed = vi.fn(() => false)

vi.mock('../useKitchenAlerts.js', () => ({
  useKitchenAlerts: () => ({
    screenBorderVisual: ref('green'),
    awaitingAck: ref(false),
    showSoundUnlockOverlay: ref(false),
    syncOrders: syncAlertOrders,
    acknowledge: vi.fn(),
    dishHasNewBadge: vi.fn(() => false),
    unlockSoundFromGesture: vi.fn(),
    reloadConfig,
    start: startAlerts,
    stop: stopAlerts,
    higherKindClaimed: (...args) => kitchenHigherKindClaimed(...args)
  })
}))

const syncDeliveryOrders = vi.fn()
const setWatchedStations = vi.fn()
const reloadCancelConfig = vi.fn()
const startCancel = vi.fn()
const stopCancel = vi.fn()
const cancelHigherKindClaimed = vi.fn(() => false)

vi.mock('../useDeliveryCancelAlert.js', () => ({
  useDeliveryCancelAlert: (options) => ({
    deliveryCancelAlert: ref({ visible: false }),
    syncOrders: syncDeliveryOrders,
    dismissDeliveryCancelAlert: vi.fn(),
    setWatchedStations,
    reloadConfig: reloadCancelConfig,
    start: startCancel,
    stop: stopCancel,
    getWatchedStations: options.getWatchedStations,
    higherKindClaimed: (...args) => cancelHigherKindClaimed(...args)
  })
}))

const { useKitchenOrderSession } = await import('../useKitchenOrderSession.js')

describe('useKitchenOrderSession', () => {
  beforeEach(() => {
    getWatchedStations.mockReset()
    getDishCardQuantityCap.mockReset()
    getOrderGapMinutes.mockReset()
    getTimeThresholdsMs.mockReset()
    syncAlertOrders.mockReset()
    syncDeliveryOrders.mockReset()
    kitchenHigherKindClaimed.mockReset()
    cancelHigherKindClaimed.mockReset()
    kitchenHigherKindClaimed.mockReturnValue(false)
    cancelHigherKindClaimed.mockReturnValue(false)
    reloadConfig.mockReset()
    reloadCancelConfig.mockReset()
    startAlerts.mockReset()
    stopAlerts.mockReset()
    startCancel.mockReset()
    stopCancel.mockReset()
    getWatchedStations.mockReturnValue(['changfen'])
    getDishCardQuantityCap.mockReturnValue(0)
    getOrderGapMinutes.mockReturnValue(0)
    getTimeThresholdsMs.mockReturnValue({
      warning: 15 * 60 * 1000,
      urgent: 20 * 60 * 1000
    })
  })

  it('refresh fetches once then fans out to both alert engines', async () => {
    const orders = [{ id: 1, station: 'changfen' }]
    const fetchOrders = vi.fn(async () => {
      ordersStore.orders = orders
    })
    const ordersStore = { orders: [], fetchOrders }
    const syncOrder = []
    syncDeliveryOrders.mockImplementation(() => {
      syncOrder.push('cancel')
      return false
    })
    syncAlertOrders.mockImplementation(() => {
      syncOrder.push('kitchen')
    })

    const session = useKitchenOrderSession({ ordersStore })
    await session.refresh()

    expect(fetchOrders).toHaveBeenCalledTimes(1)
    expect(syncOrder).toEqual(['cancel', 'kitchen'])
    expect(syncDeliveryOrders).toHaveBeenCalledWith(orders)
    expect(syncAlertOrders).toHaveBeenCalledWith(orders, { cancelClaimed: false })
    expect(reloadConfig).toHaveBeenCalled()
  })

  it('passes cancelClaimed when delivery-cancel sync plays', async () => {
    const orders = [{ id: 1, station: 'changfen' }]
    const fetchOrders = vi.fn(async () => {
      ordersStore.orders = orders
    })
    const ordersStore = { orders: [], fetchOrders }
    syncDeliveryOrders.mockReturnValue(true)

    const session = useKitchenOrderSession({ ordersStore })
    await session.refresh()

    expect(syncDeliveryOrders).toHaveBeenCalledWith(orders)
    expect(syncAlertOrders).toHaveBeenCalledWith(orders, { cancelClaimed: true })
  })

  it('higherKindClaimed is true when cancel or kitchen claimed this moment', () => {
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    expect(session.higherKindClaimed()).toBe(false)

    cancelHigherKindClaimed.mockReturnValue(true)
    expect(session.higherKindClaimed()).toBe(true)

    cancelHigherKindClaimed.mockReturnValue(false)
    kitchenHigherKindClaimed.mockReturnValue(true)
    expect(session.higherKindClaimed()).toBe(true)
  })

  it('reloadDeviceSettings / onShow re-reads watched stations', () => {
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    expect(session.watchedStationIds.value).toEqual(['changfen'])

    getWatchedStations.mockReturnValue(['xibing'])
    session.onShow()
    expect(session.watchedStationIds.value).toEqual(['xibing'])
    expect(reloadConfig).toHaveBeenCalled()
    expect(reloadCancelConfig).toHaveBeenCalled()
  })

  it('exposes dishCardQuantityCap from local screen settings (0 = no split)', () => {
    getDishCardQuantityCap.mockReturnValue(0)
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    expect(session.dishCardQuantityCap.value).toBe(0)

    getDishCardQuantityCap.mockReturnValue(10)
    session.reloadDeviceSettings()
    expect(session.dishCardQuantityCap.value).toBe(10)
  })

  it('exposes orderGapMinutes from local screen settings (0 = no 浪潮 split)', () => {
    getOrderGapMinutes.mockReturnValue(0)
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    expect(session.orderGapMinutes.value).toBe(0)

    getOrderGapMinutes.mockReturnValue(10)
    session.reloadDeviceSettings()
    expect(session.orderGapMinutes.value).toBe(10)
  })

  it('decorateDishWait uses device-local urgent threshold', () => {
    getTimeThresholdsMs.mockReturnValue({
      warning: 10 * 60 * 1000,
      urgent: 30 * 60 * 1000
    })
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    session.reloadDeviceSettings()
    const now = 1_000_000
    const mild = session.decorateDishWait(now - 20 * 60 * 1000, now)
    expect(mild.isOvertime).toBe(false)
    expect(mild.waitTimeClass).toBe('high')
    const hot = session.decorateDishWait(now - 31 * 60 * 1000, now)
    expect(hot.isOvertime).toBe(true)
    expect(hot.waitTimeClass).toBe('urgent')
  })

  it('start/stop delegate to kitchen alerts and cancel alert after settings reload', () => {
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    session.start()
    expect(reloadConfig).toHaveBeenCalled()
    expect(startAlerts).toHaveBeenCalled()
    expect(startCancel).toHaveBeenCalled()
    session.stop()
    expect(stopAlerts).toHaveBeenCalled()
    expect(stopCancel).toHaveBeenCalled()
  })
})
