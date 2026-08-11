import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const getWatchedStations = vi.fn(() => ['changfen'])
const getTimeThresholdsMs = vi.fn(() => ({ warning: 15 * 60 * 1000, urgent: 20 * 60 * 1000 }))

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getWatchedStations: (...args) => getWatchedStations(...args),
    getSettings: () => ({
      watchedStations: getWatchedStations(),
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
    stop: stopAlerts
  })
}))

const syncDeliveryOrders = vi.fn()
const setWatchedStations = vi.fn()

vi.mock('../useDeliveryCancelAlert.js', () => ({
  useDeliveryCancelAlert: (options) => ({
    deliveryCancelAlert: ref({ visible: false }),
    syncOrders: syncDeliveryOrders,
    dismissDeliveryCancelAlert: vi.fn(),
    setWatchedStations,
    getWatchedStations: options.getWatchedStations
  })
}))

const { useKitchenOrderSession } = await import('../useKitchenOrderSession.js')

describe('useKitchenOrderSession', () => {
  beforeEach(() => {
    getWatchedStations.mockReset()
    getTimeThresholdsMs.mockReset()
    syncAlertOrders.mockReset()
    syncDeliveryOrders.mockReset()
    reloadConfig.mockReset()
    startAlerts.mockReset()
    stopAlerts.mockReset()
    getWatchedStations.mockReturnValue(['changfen'])
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

    const session = useKitchenOrderSession({ ordersStore })
    await session.refresh()

    expect(fetchOrders).toHaveBeenCalledTimes(1)
    expect(syncAlertOrders).toHaveBeenCalledWith(orders)
    expect(syncDeliveryOrders).toHaveBeenCalledWith(orders)
    expect(reloadConfig).toHaveBeenCalled()
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

  it('start/stop delegate to kitchen alerts after settings reload', () => {
    const session = useKitchenOrderSession({
      ordersStore: { orders: [], fetchOrders: vi.fn() }
    })
    session.start()
    expect(reloadConfig).toHaveBeenCalled()
    expect(startAlerts).toHaveBeenCalled()
    session.stop()
    expect(stopAlerts).toHaveBeenCalled()
  })
})
