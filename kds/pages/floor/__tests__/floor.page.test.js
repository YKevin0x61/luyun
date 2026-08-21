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

function dishGroup(wrapper, dishName) {
  return wrapper.findAll('.dish-group').find((group) => {
    const name = group.find('.dish-name')
    return name.exists() && name.text() === dishName
  })
}

function chromeBtn(group, label) {
  return group.findAll('.group-chrome-btn').find((btn) => btn.text() === label)
}

async function openFirstCard(wrapper) {
  await wrapper.find('.table-card').trigger('click')
  await flushPromises()
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
    offWindowResize: vi.fn(),
    vibrateShort: vi.fn(),
    vibrateLong: vi.fn(),
    showModal: vi.fn()
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
    expect(wrapper.find('[aria-current="true"]').text()).toContain('8')
  })

  it('follows the current viewport after rotation', async () => {
    getFloorConsole.mockResolvedValue({ tables: [tableFixture('8')] })
    setViewport(390, 844)
    const wrapper = mountPage()
    await flushPromises()
    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)
    expect(wrapper.find('.table-pane').exists()).toBe(false)

    setViewport(1200, 800)
    const resizeHandler = globalThis.uni.onWindowResize.mock.calls[0][0]
    resizeHandler()
    await flushPromises()
    expect(isHiddenByVShow(wrapper.find('.table-list'))).toBe(false)
    expect(wrapper.find('.table-pane').exists()).toBe(true)
    expect(wrapper.find('.pane-empty').text()).toBe('点左侧桌卡')
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
    expect(wrapper.find('[aria-current="true"]').exists()).toBe(false)

    await wrapper.find('.jump-input').setValue('12')
    await wrapper.find('.jump-go').trigger('click')
    await flushPromises()

    const active = wrapper.find('[aria-current="true"]')
    expect(active.exists()).toBe(true)
    expect(active.text()).toContain('12')
    expect(wrapper.find('.pane-title').text()).toBe('12 桌')
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
    await wrapper.find('.refresh-btn').trigger('click')
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
    expect(banner.text()).toBe('实时连接已断开，正在重连…')
    expect(wrapper.find('.screen-border--yellow').exists()).toBe(false)
    expect(globalThis.uni.vibrateShort).not.toHaveBeenCalled()
    expect(globalThis.uni.vibrateLong).not.toHaveBeenCalled()
  })

  it('shows no strip while connected', async () => {
    getFloorConsole.mockResolvedValue({ tables: [] })
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.disconnect-banner').exists()).toBe(false)
  })
})

describe('楼面控制台 page — per-dish-group actions', () => {
  it('等叫 acts on the 待出餐 group only, not the whole table', async () => {
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
    expect(holdBtn.exists()).toBe(true)

    await holdBtn.trigger('click')
    await flushPromises()
    expect(holdOrders).toHaveBeenCalledWith(['h1'])
    expect(holdOrders).not.toHaveBeenCalledWith(expect.arrayContaining(['w1']))
  })

  it('加急 acts on the 待出餐 group only, not the whole table', async () => {
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
    const shrimpGroup = groups.find((group) => group.text().includes('虾饺'))
    const rushBtn = shrimpGroup.find('.action-btn.rush')
    expect(rushBtn.exists()).toBe(true)

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

describe('楼面控制台 page — 单桌面 default check excludes 在蒸', () => {
  it('opening 待出餐 + 在蒸 + 等叫 does not 等叫 the 在蒸; 叫起 count is the 等叫 row only', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    const holdBtn = shrimp.find('.action-btn.hold')
    const fireBtn = shrimp.find('.action-btn.fire')
    expect(holdBtn.text()).toBe('等叫 1')
    expect(fireBtn.text()).toBe('叫起 1')

    await holdBtn.trigger('click')
    await flushPromises()
    expect(holdOrders).toHaveBeenCalledWith(['pending-1'])
    expect(holdOrders).not.toHaveBeenCalledWith(expect.arrayContaining(['steam-1']))
  })
})

describe('楼面控制台 page — tap 在蒸 then 等叫', () => {
  it('tapping 在蒸 includes it in the next 等叫 with no confirm and no 对调 copy', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    await shrimp.find('.line-row--steam').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('对调')
    expect(wrapper.text()).not.toContain('替补')
    expect(globalThis.uni.showModal).not.toHaveBeenCalled()

    await shrimp.find('.action-btn.hold').trigger('click')
    await flushPromises()
    expect(holdOrders).toHaveBeenCalledWith(['pending-1', 'steam-1'])
    expect(globalThis.uni.showModal).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('对调')
  })
})

describe('楼面控制台 page — 全选 / 清空 chrome', () => {
  it('renders 全选 / 清空 only on groups with an actionable portion', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'ready-1', dish_name: '叉烧包', phase: '已制作待上菜' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    const bun = dishGroup(wrapper, '叉烧包')
    expect(chromeBtn(shrimp, '全选')).toBeTruthy()
    expect(chromeBtn(shrimp, '清空')).toBeTruthy()
    expect(chromeBtn(bun, '全选')).toBeUndefined()
    expect(chromeBtn(bun, '清空')).toBeUndefined()
    expect(bun.text()).not.toContain('全选')
    expect(bun.text()).not.toContain('清空')
  })

  it('renders 全选 / 清空 on an 在蒸-only group', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)
    const shrimp = dishGroup(wrapper, '虾饺')
    expect(chromeBtn(shrimp, '全选')).toBeTruthy()
    expect(chromeBtn(shrimp, '清空')).toBeTruthy()
    await shrimp.find('.line-row--steam').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 1')
    await chromeBtn(shrimp, '全选').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').exists()).toBe(false)
  })

  it('全选 restores the default set and 清空 hides that group’s action buttons', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    await shrimp.find('.line-row--steam').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 2')

    await chromeBtn(shrimp, '全选').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 1')

    await chromeBtn(shrimp, '清空').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').exists()).toBe(false)
    expect(shrimp.find('.action-btn.fire').exists()).toBe(false)
    expect(shrimp.find('.action-btn.rush').exists()).toBe(false)
  })

  it('全选 / 清空 on 虾饺 do not reset 叉烧包 checks', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'shrimp-pending', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'shrimp-steam', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'bun-pending', dish_name: '叉烧包', phase: '待出餐' }),
          floorLine({ order_id: 'bun-steam', dish_name: '叉烧包', phase: '在蒸' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    const bun = dishGroup(wrapper, '叉烧包')
    await bun.find('.line-row--steam').trigger('click')
    await flushPromises()
    expect(bun.find('.action-btn.hold').text()).toBe('等叫 2')

    await chromeBtn(shrimp, '清空').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').exists()).toBe(false)
    expect(bun.find('.action-btn.hold').text()).toBe('等叫 2')

    await chromeBtn(shrimp, '全选').trigger('click')
    await flushPromises()
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 1')
    expect(bun.find('.action-btn.hold').text()).toBe('等叫 2')
  })
})

describe('楼面控制台 page — after-action drop', () => {
  it('successful hold unchecks those rows so 叫起 does not appear for them', async () => {
    const initial = tableFixture('8', [
      floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
      floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
    ])
    const afterHold = tableFixture('8', [
      floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '等叫' }),
      floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
    ])
    getFloorConsole.mockResolvedValueOnce({ tables: [initial] })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    expect(dishGroup(wrapper, '虾饺').find('.action-btn.fire').exists()).toBe(false)

    getFloorConsole.mockResolvedValueOnce({ tables: [afterHold] })
    holdOrders.mockResolvedValue({ conflicts: [] })
    await dishGroup(wrapper, '虾饺').find('.action-btn.hold').trigger('click')
    await flushPromises()

    expect(holdOrders).toHaveBeenCalledWith(['pending-1'])
    const shrimp = dishGroup(wrapper, '虾饺')
    expect(shrimp.find('.action-btn.fire').exists()).toBe(false)
    expect(shrimp.find('.line-row--hold').find('.line-mark').exists()).toBe(false)
  })

  it('partial 200 keeps conflict ids checked and toasts 在蒸且无替补', async () => {
    const initial = tableFixture('8', [
      floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
      floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
    ])
    const afterHold = tableFixture('8', [
      floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '等叫' }),
      floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
    ])
    getFloorConsole.mockResolvedValueOnce({ tables: [initial] })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    await dishGroup(wrapper, '虾饺').find('.line-row--steam').trigger('click')
    await flushPromises()

    getFloorConsole.mockResolvedValueOnce({ tables: [afterHold] })
    holdOrders.mockResolvedValue({
      conflicts: [{ order_id: 'steam-1', reason: '在蒸且无替补' }]
    })
    await dishGroup(wrapper, '虾饺').find('.action-btn.hold').trigger('click')
    await flushPromises()

    expect(holdOrders).toHaveBeenCalledWith(['pending-1', 'steam-1'])
    expect(globalThis.uni.showToast).toHaveBeenCalledWith({
      title: '1 份未改：在蒸且无替补',
      icon: 'none',
      duration: 2500
    })
    const shrimp = dishGroup(wrapper, '虾饺')
    expect(shrimp.find('.line-row--steam').find('.line-mark').exists()).toBe(true)
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 1')
    expect(shrimp.find('.action-btn.fire').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('对调')
  })

  it('409 leaves checks unchanged and toasts conflicts', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const error = new Error('HTTP 409: Conflict')
    error.statusCode = 409
    error.response = {
      data: { detail: { conflicts: [{ order_id: 'pending-1', reason: '在蒸且无替补' }] } }
    }
    holdOrders.mockRejectedValue(error)
    await dishGroup(wrapper, '虾饺').find('.action-btn.hold').trigger('click')
    await flushPromises()

    const shrimp = dishGroup(wrapper, '虾饺')
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 1')
    expect(shrimp.find('.action-btn.fire').text()).toBe('叫起 1')
    expect(shrimp.find('.line-row--steam').find('.line-mark').exists()).toBe(false)
    expect(globalThis.uni.showToast).toHaveBeenCalledWith({
      title: '1 份未改：在蒸且无替补',
      icon: 'none'
    })
  })
})

describe('楼面控制台 page — refresh keep while on the table', () => {
  it('keeps a peeled 待出餐 unchecked after refresh', async () => {
    const table = tableFixture('8', [
      floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
      floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
    ])
    getFloorConsole.mockResolvedValueOnce({ tables: [table] })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    await dishGroup(wrapper, '虾饺').find('.line-row--pending').trigger('click')
    await flushPromises()
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').exists()).toBe(false)

    getFloorConsole.mockResolvedValueOnce({ tables: [table] })
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').exists()).toBe(false)
    expect(dishGroup(wrapper, '虾饺').find('.line-row--pending').find('.line-mark').exists()).toBe(false)
  })

  it('does not auto-check a new portion in a seen group', async () => {
    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').text()).toBe('等叫 1')

    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'pending-2', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').text()).toBe('等叫 1')
  })

  it('unchecks a 待出餐 that becomes 在蒸', async () => {
    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'become-steam', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').text()).toBe('等叫 1')

    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'become-steam', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' })
        ])
      ]
    })
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()
    const shrimp = dishGroup(wrapper, '虾饺')
    expect(shrimp.find('.action-btn.hold').exists()).toBe(false)
    expect(shrimp.find('.line-row--steam').find('.line-mark').exists()).toBe(false)
    expect(shrimp.find('.action-btn.fire').text()).toBe('叫起 1')
  })

  it('keeps a hand-checked 在蒸 that is still 在蒸', async () => {
    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)
    await dishGroup(wrapper, '虾饺').find('.line-row--steam').trigger('click')
    await flushPromises()
    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').text()).toBe('等叫 2')

    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()
    const shrimp = dishGroup(wrapper, '虾饺')
    expect(shrimp.find('.line-row--steam').find('.line-mark').exists()).toBe(true)
    expect(shrimp.find('.action-btn.hold').text()).toBe('等叫 2')
  })

  it('uses the default set for a newly seen dish group on the same visit', async () => {
    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'shrimp-pending', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'shrimp-steam', dish_name: '虾饺', phase: '在蒸' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    getFloorConsole.mockResolvedValueOnce({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'shrimp-pending', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'shrimp-steam', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'bun-pending', dish_name: '叉烧包', phase: '待出餐' }),
          floorLine({ order_id: 'bun-steam', dish_name: '叉烧包', phase: '在蒸' })
        ])
      ]
    })
    await wrapper.find('.refresh-btn').trigger('click')
    await flushPromises()

    expect(dishGroup(wrapper, '虾饺').find('.action-btn.hold').text()).toBe('等叫 1')
    const bun = dishGroup(wrapper, '叉烧包')
    expect(bun.find('.action-btn.hold').text()).toBe('等叫 1')
    expect(bun.find('.line-row--steam').find('.line-mark').exists()).toBe(false)
    expect(bun.find('.line-row--pending').find('.line-mark').exists()).toBe(true)
  })
})

describe('楼面控制台 page — phase contrast modifiers', () => {
  it('marks pending / steam / selected / locked with existing row modifiers and ✓', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' }),
          floorLine({ order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' }),
          floorLine({ order_id: 'ready-1', dish_name: '虾饺', phase: '已制作待上菜' })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    const shrimp = dishGroup(wrapper, '虾饺')
    const pending = shrimp.find('.line-row--pending')
    const steam = shrimp.find('.line-row--steam')
    const locked = shrimp.find('.line-row--locked')
    expect(pending.exists()).toBe(true)
    expect(pending.classes()).toContain('line-row--selected')
    expect(pending.find('.line-mark').text()).toBe('✓')
    expect(steam.exists()).toBe(true)
    expect(steam.classes()).not.toContain('line-row--selected')
    expect(locked.exists()).toBe(true)
    expect(locked.classes()).toContain('line-row--ready')

    await steam.trigger('click')
    await flushPromises()
    expect(steam.classes()).toContain('line-row--selected')
    expect(steam.find('.line-mark').text()).toBe('✓')
  })
})

describe('楼面控制台 page — 单桌面 notes on each 份', () => {
  it('keeps 免葱 and plain 艇仔粥 in one group; notes on the line, never the title', async () => {
    getFloorConsole.mockResolvedValue({
      tables: [
        tableFixture('8', [
          floorLine({ order_id: 'onion', dish_name: '艇仔粥', notes: '免葱', phase: '待出餐' }),
          floorLine({ order_id: 'plain', dish_name: '艇仔粥', notes: '', phase: '待出餐' }),
          floorLine({
            order_id: 'platform',
            dish_name: '艇仔粥',
            notes: '外卖平台:美团|来源:美团1',
            phase: '待出餐'
          })
        ])
      ]
    })
    const wrapper = mountPage()
    await flushPromises()
    await openFirstCard(wrapper)

    expect(wrapper.findAll('.dish-group')).toHaveLength(1)
    const porridge = dishGroup(wrapper, '艇仔粥')
    expect(porridge).toBeTruthy()
    expect(porridge.find('.dish-name').text()).toBe('艇仔粥')
    expect(porridge.find('.dish-name').text()).not.toContain('免葱')

    const rows = porridge.findAll('.line-row')
    expect(rows).toHaveLength(3)
    expect(rows.map((row) => {
      const notes = row.find('.line-notes')
      return notes.exists() ? notes.text() : ''
    })).toEqual(['免葱', '', ''])
    expect(porridge.findAll('.line-notes')).toHaveLength(1)
    expect(porridge.text()).not.toContain('外卖平台')
    expect(porridge.findAll('.line-text').every((el) => !el.text().includes('免葱'))).toBe(true)
  })
})
