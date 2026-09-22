// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const getOrders = vi.fn()
const completeCooking = vi.fn()

vi.mock('../../../api/orders.js', () => ({
  ordersAPI: {
    getOrders: (...args) => getOrders(...args),
    completeCooking: (...args) => completeCooking(...args)
  }
}))

const setFilters = vi.fn()

vi.mock('../../../composables/useNudgePull.js', () => ({
  useNudgePull: () => ({ setFilters })
}))

let realtimeState
vi.mock('../../../stores/realtime.js', () => ({
  useRealtimeStore: () => realtimeState
}))

let stationsState
vi.mock('../../../stores/stations.js', () => ({
  useStationsStore: () => stationsState
}))

vi.mock('../../../utils/printQueue.js', () => ({
  enqueuePrintTicket: vi.fn(),
  subscribeQueueState: vi.fn(() => vi.fn()),
  retryAllFailedJobs: vi.fn(() => 0)
}))

// SvgIcon.vue imports through the `@/…` alias, which vitest.config.mjs does not map
// (only jsconfig.json does). Stub the icon rather than widen the shared test config.
vi.mock('../../../components/SvgIcon/SvgIcon.vue', () => ({
  default: {
    name: 'SvgIcon',
    props: ['name', 'size', 'color'],
    template: '<span class="svg-icon" />'
  }
}))

const { default: KitchenPage } = await import('../kitchen.vue')

const SCREEN_SETTINGS_KEY = 'kds_screen_settings'

function orderFixture(overrides = {}) {
  const now = new Date().toISOString()
  return {
    id: 1,
    dish_name: '虾饺',
    station: 'changfen',
    table_number: '8',
    quantity: 1,
    order_time: now,
    work_enter_time: now,
    is_pending_kitchen_work: true,
    notes: '',
    ...overrides
  }
}

let wrappers = []

function mountKitchen() {
  const wrapper = mount(KitchenPage, {
    global: {
      stubs: { PwaUpdateBanner: true, ShulongSteamerConsole: true }
    }
  })
  wrappers.push(wrapper)
  return wrapper
}

/** Mount then let onMounted's initial pull settle (fetch resolved or rejected). */
async function mountSettled() {
  const wrapper = mountKitchen()
  await flushPromises()
  await flushPromises()
  return wrapper
}

let storage

beforeEach(() => {
  storage = new Map()
  // One locked station — otherwise the page jumps straight to 设置.
  storage.set(SCREEN_SETTINGS_KEY, JSON.stringify({ watchedStations: ['changfen'] }))
  globalThis.uni = {
    showToast: vi.fn(),
    reLaunch: vi.fn(),
    vibrateShort: vi.fn(),
    vibrateLong: vi.fn(),
    getStorageSync: (key) => (storage.has(key) ? storage.get(key) : ''),
    setStorageSync: (key, value) => {
      storage.set(key, value)
    },
    removeStorageSync: (key) => {
      storage.delete(key)
    },
    onWindowResize: vi.fn(),
    offWindowResize: vi.fn()
  }
  setActivePinia(createPinia())
  setFilters.mockReset()
  getOrders.mockReset()
  completeCooking.mockReset()
  realtimeState = { connectionStatus: 'connected', init: vi.fn() }
  stationsState = {
    initializeStations: vi.fn().mockResolvedValue(true),
    setCurrentStation: vi.fn(),
    stationList: [{ id: 'changfen', name: '肠粉档', color: '#22c55e' }],
    steamerLayout: null
  }
})

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount())
  wrappers = []
  delete globalThis.uni
})

describe('厨房主屏 — 订单拉取失败的错误态', () => {
  it('拉取失败时替代空态：不含「暂无待制作订单」，含错误文案与可点的重试入口', async () => {
    getOrders.mockRejectedValue(new Error('HTTP 502'))
    const wrapper = await mountSettled()

    expect(wrapper.find('.empty-container').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('暂无待制作订单')

    const errorState = wrapper.find('.load-error-container')
    expect(errorState.exists()).toBe(true)
    expect(errorState.text()).toContain('订单加载失败')
    expect(errorState.text()).toContain('HTTP 502')
    expect(errorState.find('.load-error-retry-btn').exists()).toBe(true)
  })

  it('重试成功后错误态消失，回到正常订单列表', async () => {
    getOrders.mockRejectedValueOnce(new Error('HTTP 502'))
    const wrapper = await mountSettled()
    expect(wrapper.find('.load-error-container').exists()).toBe(true)

    getOrders.mockResolvedValueOnce({ success: true, data: [orderFixture()] })
    await wrapper.find('.load-error-retry-btn').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.load-error-container').exists()).toBe(false)
    expect(wrapper.find('.empty-container').exists()).toBe(false)
    expect(wrapper.find('.orders-list').exists()).toBe(true)
  })

  it('首次拉取进行中仍是「加载中」，不被错误态或空态抢走', async () => {
    getOrders.mockImplementation(() => new Promise(() => {}))
    const wrapper = mountKitchen()
    await flushPromises()

    expect(wrapper.find('.loading-container').exists()).toBe(true)
    expect(wrapper.text()).toContain('加载中')
    expect(wrapper.find('.load-error-container').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('暂无待制作订单')
  })

  it('已有订单时刷新失败：保留列表并给出持久提示，不清空屏幕', async () => {
    getOrders.mockResolvedValueOnce({ success: true, data: [orderFixture()] })
    const wrapper = await mountSettled()
    expect(wrapper.find('.orders-list').exists()).toBe(true)
    expect(wrapper.find('.load-error-banner').exists()).toBe(false)

    getOrders.mockRejectedValueOnce(new Error('HTTP 502'))
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.orders-list').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('暂无待制作订单')
    const banner = wrapper.find('.load-error-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('HTTP 502')
  })

  it('断连横幅管 WS、错误态管 HTTP 拉取：两者可以同时出现', async () => {
    realtimeState = { connectionStatus: 'reconnecting', init: vi.fn() }
    getOrders.mockRejectedValue(new Error('HTTP 502'))
    const wrapper = await mountSettled()

    expect(wrapper.find('.disconnect-banner').exists()).toBe(true)
    expect(wrapper.find('.load-error-container').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('暂无待制作订单')
  })
})
