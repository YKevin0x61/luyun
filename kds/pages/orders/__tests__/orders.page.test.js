// @vitest-environment happy-dom

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

/**
 * 订单页（pages/orders/orders.vue）页面级回归。
 *
 * 这个文件承载两条互不相干的诉求（两个都必须在页面级验证）：
 *
 * 1. TEST-10：页面**不 mock SvgIcon.vue** 也能 mount。SvgIcon 是 `@/utils/iconPaths.js`
 *    的唯一调用方，所以这条用例同时是 vitest.config.mjs 里 `'@'` 别名的回归探针——
 *    别名被删掉时这里会直接红（以前只能把 SvgIcon 整个 mock 掉绕过）。
 * 2. TEST-13：默认日期必须是**中国自然日**，与后端 `/api/orders/search` 解释
 *    start_date/end_date 的口径（api/orders.py 按 CHINA_TZ 取 00:00–23:59）一致。
 *
 * 设备时区用 process.env.TZ 伪造成 UTC（门店 kiosk 常年如此）：默认日期若还按
 * `toISOString()` 取设备日期，东八区凌晨会落到前一天，用例必红。
 */

const AMBIENT_TZ = process.env.TZ
process.env.TZ = 'UTC'

afterAll(() => {
  if (AMBIENT_TZ === undefined) delete process.env.TZ
  else process.env.TZ = AMBIENT_TZ
})

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  initializeStations: vi.fn(),
}))

vi.mock('../../../utils/request.js', () => ({ request: mocks.request }))

vi.mock('../../../stores/stations.js', () => ({
  useStationsStore: () => ({
    stationList: [],
    initializeStations: mocks.initializeStations,
    getStationById: () => null,
  }),
}))

// 中国时间 2026-09-24 02:00——东八区的「今天」是 09-24，设备时区 UTC 的「今天」还是 09-23
const CHINA_0200 = Date.parse('2026-09-23T18:00:00.000Z')
const DEVICE_TODAY = '2026-09-23'
const CHINA_TODAY = '2026-09-24'

let OrdersPage

beforeAll(async () => {
  // 动态 import：TZ 必须在这个模块图被求值前设好
  ;({ default: OrdersPage } = await import('../orders.vue'))
})

beforeEach(() => {
  vi.useFakeTimers({ now: CHINA_0200, toFake: ['Date'] })

  mocks.request.mockReset()
  mocks.request.mockResolvedValue({ success: true, orders: [], stats: null })
  mocks.initializeStations.mockReset()
  mocks.initializeStations.mockResolvedValue(undefined)

  globalThis.uni = {
    showToast: vi.fn(),
    hideToast: vi.fn(),
    reLaunch: vi.fn(),
    setClipboardData: vi.fn(),
    getStorageSync: vi.fn(() => ''),
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
  }
})

afterEach(() => {
  delete globalThis.uni
  vi.useRealTimers()
})

function mountOrdersPage() {
  return mount(OrdersPage, { global: { stubs: { PwaUpdateBanner: true } } })
}

describe('orders 页面默认日期', () => {
  it('默认「今天」取中国自然日，与后端 CHINA_TZ 口径一致', async () => {
    const wrapper = mountOrdersPage()
    await flushPromises()

    // 正对照：设备时区 UTC 时设备日期仍停在 09-23，旧的 toISOString() 写法会默认查这一天
    expect(new Date().toISOString().split('T')[0]).toBe(DEVICE_TODAY)

    expect(wrapper.vm.filters.startDate).toBe(CHINA_TODAY)
    expect(wrapper.vm.filters.endDate).toBe(CHINA_TODAY)

    // 页面上（开始/结束日期两个 picker）显示的也是这一天
    const pickerTexts = wrapper.findAll('.picker-text')
    expect(pickerTexts[0].text()).toBe(CHINA_TODAY)
    expect(pickerTexts[1].text()).toBe(CHINA_TODAY)

    // 并且带着这一天去查询，而不是设备日期
    expect(mocks.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/orders/search',
        method: 'GET',
        data: expect.objectContaining({
          start_date: CHINA_TODAY,
          end_date: CHINA_TODAY,
        }),
      })
    )

    wrapper.unmount()
  })
})

describe('orders 页面图标（@ 别名解析）', () => {
  it('不 mock SvgIcon.vue 也能 mount，图标渲染成真实 data URI', async () => {
    const wrapper = mountOrdersPage()
    await flushPromises()

    const icons = wrapper.findAll('.svg-icon')
    expect(icons.length).toBeGreaterThan(0)
    // 真实 SvgIcon 把 ICON_PATHS[name]（经 '@' 别名导入）编成 data URI；
    // 少了别名这条 import 根本解析不了，组件树里也就不会有 .svg-icon
    expect(icons.map((icon) => icon.attributes('src'))).toEqual(
      icons.map(() => expect.stringMatching(/^data:image\/svg\+xml,/))
    )

    wrapper.unmount()
  })
})
