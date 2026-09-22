import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * KDS 各页面拉"今天订单"都必须用营业日（中国时间）窗口。
 * 设备时区伪造：process.env.TZ = 'UTC'；时刻伪造：vi.useFakeTimers。
 * 断言落在 HTTP 请求参数上——那正是后端按 CHINA_TZ 归一的东西。
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

const mocks = vi.hoisted(() => ({ request: vi.fn() }))

vi.mock('../../utils/request.js', () => ({ request: mocks.request }))

const request = mocks.request

const { useOrdersStore } = await import('../orders.js')
const { ordersAPI } = await import('../../api/orders.js')

function todayWindowOf(call) {
  const { params } = call
  return { start_time: params.start_time, end_time: params.end_time }
}

describe('今日订单窗口（store + api）', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: CHINA_0230, toFake: ['Date'] })
    setActivePinia(createPinia())
    request.mockReset()
    request.mockResolvedValue({ success: true, data: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('useOrdersStore.fetchTodayOrders 拉的是中国日窗口', async () => {
    expect(new Date().getDate()).toBe(22) // 正对照：设备（UTC）日期还停在 09-22

    const store = useOrdersStore()
    await store.fetchTodayOrders()

    expect(request).toHaveBeenCalledTimes(1)
    expect(todayWindowOf(request.mock.calls[0][0])).toEqual({
      start_time: CHINA_DAY_START,
      end_time: CHINA_DAY_END
    })
  })

  it('ordersAPI.getTodayOrders 拉的是中国日窗口', async () => {
    await ordersAPI.getTodayOrders()

    expect(request).toHaveBeenCalledTimes(1)
    expect(todayWindowOf(request.mock.calls[0][0])).toEqual({
      start_time: CHINA_DAY_START,
      end_time: CHINA_DAY_END
    })
  })
})
