import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

/**
 * 厨房屏拉"今天订单"的窗口必须是营业日（中国时间），不是设备本地日。
 * 设备时区伪造方式：process.env.TZ（Node 赋值时重读时区，用正对照断言确认生效）；
 * 时刻伪造方式：vi.useFakeTimers 固定在中国时间凌晨 02:00。
 * 两者都不改 vitest 全局配置，且在本文件 afterAll 里还原。
 */

const AMBIENT_TZ = process.env.TZ
process.env.TZ = 'UTC'

afterAll(() => {
  if (AMBIENT_TZ === undefined) delete process.env.TZ
  else process.env.TZ = AMBIENT_TZ
})

// 中国时间 2026-09-23 02:00；设备时区 UTC 时设备日期还是 09-22
const CHINA_0230 = Date.parse('2026-09-22T18:00:00.000Z')
const CHINA_DAY_START = '2026-09-22T16:00:00.000Z'
const CHINA_DAY_END = '2026-09-23T15:59:59.999Z'

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getWatchedStations: () => ['changfen'],
    getDishCardQuantityCap: () => 0,
    getOrderGapMinutes: () => 0,
    getSettings: () => ({
      watchedStations: ['changfen'],
      dishCardQuantityCap: 0,
      orderGapMinutes: 0,
      alert: { warnMin: 15, urgentMin: 20 }
    })
  }
}))

vi.mock('../../utils/timeThresholds.js', () => ({
  getTimeThresholdsMs: () => ({ warning: 15 * 60 * 1000, urgent: 20 * 60 * 1000 })
}))

vi.mock('../useKitchenAlerts.js', () => ({
  useKitchenAlerts: () => ({
    screenBorderVisual: ref('green'),
    awaitingAck: ref(false),
    showSoundUnlockOverlay: ref(false),
    syncOrders: vi.fn(),
    acknowledge: vi.fn(),
    dishHasNewBadge: vi.fn(() => false),
    unlockSoundFromGesture: vi.fn(),
    reloadConfig: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    higherKindClaimed: () => false
  })
}))

vi.mock('../useDeliveryCancelAlert.js', () => ({
  useDeliveryCancelAlert: (options) => ({
    deliveryCancelAlert: ref({ visible: false }),
    acknowledgedCancelIds: ref([]),
    syncOrders: vi.fn(() => false),
    dismissDeliveryCancelAlert: vi.fn(),
    setWatchedStations: vi.fn(),
    reloadConfig: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    getWatchedStations: options.getWatchedStations,
    higherKindClaimed: () => false
  })
}))

const { useKitchenOrderSession } = await import('../useKitchenOrderSession.js')

describe('useKitchenOrderSession 拉单窗口', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: CHINA_0230, toFake: ['Date'] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('设备时区 UTC、中国时间凌晨 02:00 时，上送的窗口是中国日 00:00–23:59:59.999', async () => {
    // 正对照：设备（UTC）本地日期还停在 09-22，中国日期已经是 09-23
    expect(new Date().getDate()).toBe(22)

    const fetchOrders = vi.fn(async () => {})
    const session = useKitchenOrderSession({ ordersStore: { orders: [], fetchOrders } })

    await session.refresh()

    expect(fetchOrders).toHaveBeenCalledWith({
      start_time: CHINA_DAY_START,
      end_time: CHINA_DAY_END
    })
    expect(fetchOrders).toHaveBeenCalledTimes(1)
  })
})
