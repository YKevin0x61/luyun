// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrepPlanView from '../PrepPlanView.vue'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

/**
 * 备货计划页（ADR 0117）：进来就出清单、能勾能改、员工只看自己档口。
 *
 * 断言的是渲染出来的东西与人点了之后发出去的请求，不是组件内部状态。
 */

const TODAY = {
  success: true,
  plan_day: '2026-10-22',
  generated_at: '2026-10-22T08:00:00+08:00',
  window: { start: '2026-10-22T08:00:00+08:00', end: '2026-10-22T21:30:00+08:00', closed: false },
  missing_rule: [],
  staff: false,
  staff_station: null,
  needs_station_pick: false,
  remaining_count: 2,
  done_count: 0,
  stations: [
    { id: '熟笼', label: '熟笼' },
    { id: '西饼', label: '西饼' },
  ],
  entries: [
    {
      id: 1,
      prep_item_id: 11,
      item_name: '虾饺馅',
      unit: '份',
      station: 'shulong',
      position: '熟笼',
      kind: 'made',
      suggested_qty: 80,
      final_qty: 80,
      qty_source: 'suggested',
      done_at: null,
      basis: { status: 'ok', weekdays: [{ day: '2026-10-15', qty: 80 }], active_days: 1 },
      history7: [
        { day: '2026-10-22', qty: 10 },
        { day: '2026-10-21', qty: 80 },
      ],
    },
    {
      id: 2,
      prep_item_id: 12,
      item_name: '河粉',
      unit: '斤',
      station: 'shulong',
      position: '熟笼',
      kind: 'bought',
      suggested_qty: 20,
      final_qty: 20,
      qty_source: 'suggested',
      done_at: null,
      basis: { status: 'ok', weekdays: [], active_days: 1 },
      history7: [],
    },
  ],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function probeFetch({ today = TODAY, stations = [{ id: 'shulong', name: '熟笼', color: '#111' }] } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/stations')) {
      return jsonResponse(stations)
    }
    if (path.includes('/api/prep-plan/today/entries/')) {
      return jsonResponse({ success: true, id: 1 })
    }
    if (path.includes('/api/prep-plan/today')) return jsonResponse(today)
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView() {
  vi.resetModules()
  window.localStorage.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PrepPlanView, {
    global: { plugins: [pinia], stubs: { RouterLink: true } },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  clearAuthStatusCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('进页面就出清单', () => {
  it('不用点任何按钮，直接按档口分段列出品与数量', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('备货计划')
    expect(wrapper.text()).toContain('虾饺馅')
    expect(wrapper.text()).toContain('80份')
    // 分两段：半成品 / 原料
    expect(wrapper.text()).toContain('半成品')
    expect(wrapper.text()).toContain('原料')
    expect(wrapper.find('.pp-empty').exists()).toBe(false)
  })

  it('没有再挂时间窗选择器与「刷新建议」空态', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    // ADR 0122 起窗口就是「未来 24 小时」，这条改成正向断言
    expect(wrapper.text()).toContain('未来 24 小时')
    expect(wrapper.text()).not.toContain('午后补货')
    expect(wrapper.find('.pp-refresh').exists()).toBe(false)
  })

  it('打烊后仍然给到明天此刻的参考值', async () => {
    // ADR 0122：滚动 24 小时窗口下没有「打烊就没数」这回事
    const closedToday = {
      ...TODAY,
      window: { ...TODAY.window, closed: true },
    }
    vi.stubGlobal('fetch', probeFetch({ today: closedToday }))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('虾饺馅')
    expect(wrapper.text()).toContain('未来 24 小时')
  })
})

describe('档口与层级', () => {
  const TWO_STATIONS = {
    ...TODAY,
    entries: [
      TODAY.entries[0],
      { ...TODAY.entries[1], id: 3, prep_item_id: 13, item_name: '蛋挞盖', station: 'xibing', position: '西饼', kind: 'bought' },
    ],
  }

  it('当前档口带 is-on（选中态），不是写了类名却没样式', async () => {
    vi.stubGlobal(
      'fetch',
      probeFetch({
        today: TWO_STATIONS,
        stations: [
          { id: 'shulong', name: '熟笼', color: '#111' },
          { id: 'xibing', name: '西饼', color: '#222' },
        ],
      }),
    )
    const wrapper = await mountView()

    const tabs = wrapper.findAll('.pp-mode')
    expect(tabs.map((node) => node.text())).toEqual(['熟笼1', '西饼1'])
    expect(tabs[0].classes()).toContain('is-on')
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(tabs[1].classes()).not.toContain('is-on')

    await tabs[1].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.pp-mode')[1].classes()).toContain('is-on')
    // 切档后清单跟着换：第二个档口的品才在页面上
    expect(wrapper.find('.pp-rows').text()).toContain('蛋挞盖')
    expect(wrapper.find('.pp-rows').text()).not.toContain('虾饺馅')
  })

  it('参考值为 0 的行降权（is-zero），不是和真要做的抢眼睛', async () => {
    const withZero = {
      ...TODAY,
      entries: [{ ...TODAY.entries[0], final_qty: 0, suggested_qty: 0 }],
    }
    vi.stubGlobal('fetch', probeFetch({ today: withZero }))
    const wrapper = await mountView()

    expect(wrapper.find('.pp-row').classes()).toContain('is-zero')
    expect(wrapper.find('.pp-qty-unit').text()).toBe('份')
  })
})

describe('勾选与改数量', () => {
  it('点「做完」把这一项勾掉', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.get('.pp-check').trigger('click')
    await flushPromises()

    const patch = fetchMock.calls.find((call) => call.method === 'PATCH')
    expect(patch).toBeTruthy()
    expect(patch.path).toContain('/api/prep-plan/today/entries/1')
    expect(JSON.parse(patch.body)).toEqual({ done: true })
    // 勾过的从待办里消失、进「已做」
    const todoNames = wrapper.findAll('.pp-rows .pp-name').map((node) => node.text())
    expect(todoNames).toEqual(['河粉'])
    expect(wrapper.text()).toContain('已做（1）')
    expect(wrapper.find('.pp-done').text()).toContain('虾饺馅')
  })

  it('点数量就地改，回车提交', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.get('.pp-qty').trigger('click')
    const input = wrapper.get('.pp-qty-input')
    await input.setValue('95')
    await input.trigger('keyup.enter')
    await flushPromises()

    const patch = fetchMock.calls.find((call) => call.method === 'PATCH')
    expect(JSON.parse(patch.body)).toEqual({ final_qty: 95 })
    expect(wrapper.text()).toContain('95份')
  })

  it('手改过的数量带「手改」角标', async () => {
    const manual = {
      ...TODAY,
      entries: [{ ...TODAY.entries[0], final_qty: 95, qty_source: 'manual' }],
    }
    vi.stubGlobal('fetch', probeFetch({ today: manual }))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('手改')
  })
})

describe('员工这一档', () => {
  const staffToday = {
    ...TODAY,
    stations: [{ id: '熟笼', label: '熟笼' }],
    staff: true,
    staff_station: null,
    needs_station_pick: true,
    entries: [],
    remaining_count: 0,
  }

  it('没选档口时先让他选，不显示任何备货品', async () => {
    vi.stubGlobal('fetch', probeFetch({ today: staffToday }))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('先选你在哪个岗位')
    expect(wrapper.text()).not.toContain('虾饺馅')
    // 还没选档口时清单是空的：别用「0 项」告诉他今天没活，别让复制清单去复制一张空单
    expect(wrapper.get('.pp-title').text()).not.toContain('0')
    expect(wrapper.get('.pp-copy').attributes('disabled')).toBeDefined()
  })

  it('选了档口之后才出清单', async () => {
    const fetchMock = probeFetch({ today: staffToday })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.get('.pp-pick-chip').trigger('click')
    await flushPromises()

    const post = fetchMock.calls.find((call) => call.method === 'POST')
    expect(post.path).toContain('/api/prep-plan/staff-station')
    expect(JSON.parse(post.body)).toEqual({ station: '熟笼' })
  })

  it('员工端点整行（含数量那一块）都能展开近 7 天依据', async () => {
    const picked = { ...TODAY, staff: true, staff_station: 'shulong', needs_station_pick: false }
    vi.stubGlobal('fetch', probeFetch({ today: picked }))
    const wrapper = await mountView()

    // 员工端点数字（disabled 按钮，指针穿透）和点行空白都该落到同一个展开动作上
    await wrapper.get('.pp-row').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').exists()).toBe(true)
    expect(wrapper.find('.pp-detail').text()).toContain('同星期几')
  })

  it('员工端点名称也是「开一次」——不会开了又被冒泡回来的第二次 toggle 关掉', async () => {
    const picked = { ...TODAY, staff: true, staff_station: 'shulong', needs_station_pick: false }
    vi.stubGlobal('fetch', probeFetch({ today: picked }))
    const wrapper = await mountView()

    // 行主区（名称 + 小三角）是 button，点它会先触发自己的展开、再冒泡到 li 的展开
    await wrapper.get('.pp-row-main').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').exists()).toBe(true)

    await wrapper.get('.pp-row-main').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').exists()).toBe(false)
  })

  it('管理端点行空白不展开（点名称才展开，数量那一格是改数量）', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    await wrapper.get('.pp-row').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').exists()).toBe(false)
  })

  it('员工没有「改归属」入口（那是管理端的事）', async () => {
    const picked = { ...TODAY, staff: true, staff_station: 'shulong', needs_station_pick: false }
    vi.stubGlobal('fetch', probeFetch({ today: picked }))
    const wrapper = await mountView()

    await wrapper.get('.pp-row-main').trigger('click')
    expect(wrapper.text()).not.toContain('这个其实是买的')
  })
})
