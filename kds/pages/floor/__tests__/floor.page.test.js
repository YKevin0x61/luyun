// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { FLOOR_JUMP_MISS_TOAST, tableLeftToastTitle } from '../../../utils/floorConsole.js'

const getFloorConsole = vi.fn()
const holdOrders = vi.fn()
const fireOrders = vi.fn()
const rushOrders = vi.fn()

vi.mock('../../../api/orders.js', () => ({
  ordersAPI: {
    getFloorConsole: (...args) => getFloorConsole(...args),
    holdOrders: (...args) => holdOrders(...args),
    fireOrders: (...args) => fireOrders(...args),
    rushOrders: (...args) => rushOrders(...args)
  }
}))

vi.mock('../../../composables/useNudgePull.js', () => ({
  useNudgePull: vi.fn()
}))

let realtimeState
vi.mock('../../../stores/realtime.js', () => ({
  useRealtimeStore: () => realtimeState
}))

vi.mock('../../../stores/stations.js', () => ({
  useStationsStore: () => ({
    initializeStations: vi.fn().mockResolvedValue(true),
    getStationById: () => null
  })
}))

const { default: FloorConsolePage } = await import('../floor.vue')

function floorLine(overrides = {}) {
  return {
    order_id: 'line-1',
    dish_name: '虾饺',
    station: 'changfen',
    phase: '待出餐',
    is_rushed: false,
    order_time: '2026-08-18T10:00:00+08:00',
    work_enter_time: '2026-08-18T10:00:00+08:00',
    ...overrides
  }
}

function tableFixture(tableNumber, lines) {
  return {
    table_number: tableNumber,
    lines:
      lines || [
        floorLine({ order_id: `${tableNumber}-hold`, phase: '等叫' }),
        floorLine({ order_id: `${tableNumber}-work`, phase: '待出餐' })
      ]
  }
}

function setViewport(width, height) {
  globalThis.uni.getSystemInfoSync.mockReturnValue({ windowWidth: width, windowHeight: height })
}

function mountPage() {
  return mount(FloorConsolePage)
}

// happy-dom's getComputedStyle only reflects inline style once the node is attached to
// `document`, which the default (detached) mount() container is not — so this reads the
// v-show-driven `style="display: none"` attribute directly instead of using isVisible().
function isHiddenByVShow(domWrapper) {
  return /display:\s*none/.test(domWrapper.attributes('style') || '')
}

beforeEach(() => {
  getFloorConsole.mockReset().mockResolvedValue({ tables: [] })
  holdOrders.mockReset().mockResolvedValue({ conflicts: [] })
  fireOrders.mockReset().mockResolvedValue({ conflicts: [] })
  rushOrders.mockReset().mockResolvedValue({ conflicts: [] })
  realtimeState = { connectionStatus: 'connected', init: vi.fn() }
  globalThis.uni = {
    showToast: vi.fn(),
    reLaunch: vi.fn(),
    getSystemInfoSync: vi.fn().mockReturnValue({ windowWidth: 390, windowHeight: 844 }),
    onWindowResize: vi.fn(),
    offWindowResize: vi.fn()
  }
})

afterEach(() => {
  delete globalThis.uni
})

describe('楼面控制台 page — 桌卡 list has no act controls', () => {
  it('lists 桌卡 with no dish names or hold/fire/rush buttons; opening a table shows both', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('8')] })
    const wrapper = mountPage()
    await flushPromises()

    const list = wrapper.find('.table-list')
    expect(list.exists()).toBe(true)
    expect(list.findAll('.action-btn')).toHaveLength(0)
    expect(list.text()).not.toContain('虾饺')
    expect(wrapper.find('.table-pane').exists()).toBe(false)

    await wrapper.find('.table-card').trigger('click')
    await flushPromises()

    const pane = wrapper.find('.table-pane')
    expect(pane.exists()).toBe(true)
    expect(pane.text()).toContain('虾饺')
    expect(pane.findAll('.action-btn').length).toBeGreaterThan(0)
  })
})

describe('楼面控制台 page — stacked (phone) layout', () => {
  it('hides the list while a table is open, and 返回 restores it', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('8')] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()

    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)

    await wrapper.find('.table-card').trigger('click')
    await flushPromises()
    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(true)
    expect(wrapper.find('.table-pane').exists()).toBe(true)

    await wrapper.find('.back-link').trigger('click')
    await flushPromises()
    expect(wrapper.find('.table-pane').exists()).toBe(false)
    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)
  })

  it('返回 from the list goes to KDS 首页', async () => {
    getFloorConsole.mockResolvedValue({ tables: [] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.back-link').trigger('click')
    expect(globalThis.uni.reLaunch).toHaveBeenCalledWith({ url: '/pages/index/index' })
  })
})

describe('楼面控制台 page — split (tablet) layout', () => {
  it('shows both surfaces, an empty prompt on first landing, and highlights the open 桌卡', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('8'), tableFixture('12')] })
    setViewport(1200, 800)
    const wrapper = mountPage()
    await flushPromises()

    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)
    expect(wrapper.find('.table-pane').exists()).toBe(true)
    expect(wrapper.find('.pane-empty').text()).toBe('点左侧桌卡')

    const cards = wrapper.findAll('.table-card')
    await cards[0].trigger('click')
    await flushPromises()

    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)
    expect(wrapper.find('.pane-empty').exists()).toBe(false)
    expect(wrapper.find('.table-card--active').exists()).toBe(true)
  })

  it('follows the current viewport after rotation', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('8')] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.floor-page').classes()).not.toContain('floor-page--split')

    setViewport(1200, 800)
    const resizeHandler = globalThis.uni.onWindowResize.mock.calls[0][0]
    resizeHandler()
    await flushPromises()
    expect(wrapper.find('.floor-page').classes()).toContain('floor-page--split')
  })
})

describe('楼面控制台 page — jump to 桌号', () => {
  it('opens an exact-match table and toasts a miss without navigating', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('1'), tableFixture('12')] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.jump-input').setValue('10')
    await wrapper.find('.jump-go').trigger('click')
    await flushPromises()
    expect(globalThis.uni.showToast).toHaveBeenCalledWith({ title: FLOOR_JUMP_MISS_TOAST, icon: 'none' })
    expect(wrapper.find('.table-pane').exists()).toBe(false)

    await wrapper.find('.jump-input').setValue('1')
    await wrapper.find('.jump-go').trigger('click')
    await flushPromises()
    expect(wrapper.find('.table-pane').exists()).toBe(true)
    expect(wrapper.find('.pane-title').text()).toBe('1 桌')
  })

  it('selects the matching 桌卡 in the split left pane on a jump hit', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('1'), tableFixture('12')] })
    setViewport(1200, 800)
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.table-card--active').exists()).toBe(false)

    await wrapper.find('.jump-input').setValue('12')
    await wrapper.find('.jump-go').trigger('click')
    await flushPromises()

    const active = wrapper.find('.table-card--active')
    expect(active.exists()).toBe(true)
    expect(active.text()).toContain('12')
  })
})

describe('楼面控制台 page — table leaves the list', () => {
  it('closes the open 单桌面 and toasts 「{桌号}桌已离台」 on the next refresh', async () => {
    getFloorConsole.mockResolvedValueOnce({ tables: [tableFixture('8')] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.table-card').trigger('click')
    await flushPromises()
    expect(wrapper.find('.table-pane').exists()).toBe(true)

    getFloorConsole.mockResolvedValueOnce({ tables: [] })
    await wrapper.vm.refresh()
    await flushPromises()

    expect(wrapper.find('.table-pane').exists()).toBe(false)
    expect(globalThis.uni.showToast).toHaveBeenCalledWith({
      title: tableLeftToastTitle('8'),
      icon: 'none'
    })
  })
})

describe('楼面控制台 page — disconnect strip', () => {
  it('shows a plain strip with no extra alert classes when the realtime link drops', async () => {
    realtimeState = { connectionStatus: 'reconnecting', init: vi.fn() }
    getFloorConsole.mockResolvedValue({ tables: [] })
    const wrapper = mountPage()
    await flushPromises()

    const banner = wrapper.find('.disconnect-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.classes()).toEqual(['disconnect-banner'])
  })

  it('shows no strip while connected', async () => {
    getFloorConsole.mockResolvedValue({ tables: [] })
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.disconnect-banner').exists()).toBe(false)
  })
})

describe('楼面控制台 page — per-dish-group actions', () => {
  it('等叫/加急 act on the 待出餐 group only, not the whole table', async () => {
    const table = tableFixture('8', [
      floorLine({ order_id: 'h1', dish_name: '虾饺', phase: '待出餐' }),
      floorLine({ order_id: 'w1', dish_name: '叉烧包', phase: '等叫' })
    ])
    getFloorConsole.mockResolvedValue({ tables: [table] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.table-card').trigger('click')
    await flushPromises()

    const groups = wrapper.findAll('.dish-group')
    expect(groups).toHaveLength(2)
    const shrimpGroup = groups.find((group) => group.text().includes('虾饺'))
    const holdBtn = shrimpGroup.find('.action-btn.hold')
    const rushBtn = shrimpGroup.find('.action-btn.rush')
    expect(holdBtn.exists()).toBe(true)
    expect(rushBtn.exists()).toBe(true)

    await holdBtn.trigger('click')
    await flushPromises()
    expect(holdOrders).toHaveBeenCalledWith(['h1'])
    expect(holdOrders).not.toHaveBeenCalledWith(expect.arrayContaining(['w1']))

    await rushBtn.trigger('click')
    await flushPromises()
    expect(rushOrders).toHaveBeenCalledWith(['h1'])
    expect(rushOrders).not.toHaveBeenCalledWith(expect.arrayContaining(['w1']))
  })

  it('叫起 acts on the 等叫 group only, not the whole table', async () => {
    const table = tableFixture('8', [
      floorLine({ order_id: 'h1', dish_name: '虾饺', phase: '待出餐' }),
      floorLine({ order_id: 'w1', dish_name: '叉烧包', phase: '等叫' })
    ])
    getFloorConsole.mockResolvedValue({ tables: [table] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.table-card').trigger('click')
    await flushPromises()

    const groups = wrapper.findAll('.dish-group')
    const bbqGroup = groups.find((group) => group.text().includes('叉烧包'))
    const fireBtn = bbqGroup.find('.action-btn.fire')
    expect(fireBtn.exists()).toBe(true)

    await fireBtn.trigger('click')
    await flushPromises()
    expect(fireOrders).toHaveBeenCalledWith(['w1'])
    expect(fireOrders).not.toHaveBeenCalledWith(expect.arrayContaining(['h1']))
  })
})
