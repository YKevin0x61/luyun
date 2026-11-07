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
      history: [
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
      history: [],
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
    if (path.includes('/api/prep-plan/events')) {
      return jsonResponse({ success: true, recorded: true })
    }
    if (path.includes('/api/prep-plan/today')) return jsonResponse(today)
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView(preset = {}) {
  vi.resetModules()
  window.localStorage.clear()
  if (preset.horizon) window.localStorage.setItem('prepPlanHorizon', String(preset.horizon))
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PrepPlanView, {
    global: { plugins: [pinia], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
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

  it('员工展开能看到「这一份用不掉了 · 记损耗」，这是备货页到损耗页唯一的桥', async () => {
    const picked = { ...TODAY, staff: true, staff_station: 'shulong', needs_station_pick: false }
    vi.stubGlobal('fetch', probeFetch({ today: picked }))
    const wrapper = await mountView()

    await wrapper.get('.pp-row-main').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').text()).toContain('记损耗')
  })

  it('管理端展开没有「记损耗」入口（管理端记的损耗不归某个员工、员工端看不到）', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    await wrapper.get('.pp-row-main').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pp-detail').text()).not.toContain('记损耗')
  })
})


describe('近 8 天用量柱形图', () => {
  it('今天 + 前面 7 个完整营业日，一共 8 根柱', async () => {
    const history = [
      { day: '2026-10-22', qty: 9 },
      { day: '2026-10-21', qty: 6 },
      { day: '2026-10-20', qty: 17 },
      { day: '2026-10-19', qty: 16 },
      { day: '2026-10-18', qty: 15 },
      { day: '2026-10-17', qty: 20 },
      { day: '2026-10-16', qty: 20 },
      { day: '2026-10-15', qty: 18 },
    ]
    const fetchMock = vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/stations')) {
        return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
      }
      if (path.includes('/api/prep-plan/today')) {
        return jsonResponse({ ...TODAY, entries: [{ ...TODAY.entries[0], history }] })
      }
      return jsonResponse({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.find('.pp-row-main').trigger('click')
    await flushPromises()

    const bars = wrapper.findAll('.pp-chart--history .prep-bar')
    expect(bars).toHaveLength(8)
    // 最后一根永远是今天
    expect(bars[bars.length - 1].classes()).toContain('is-today')
  })
})

describe('同星期几柱形图', () => {
  const weekdays = [
    { day: '2026-10-29', qty: 17 },
    { day: '2026-10-22', qty: 15 },
    { day: '2026-10-15', qty: 17 },
    { day: '2026-10-08', qty: 14 },
    { day: '2026-10-01', qty: 17 },
    { day: '2026-09-24', qty: 14 },
    { day: '2026-09-17', qty: 17 },
    { day: '2026-09-10', qty: 13 },
  ]

  async function mountWithBasis(basis) {
    const fetchMock = vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/stations')) {
        return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
      }
      if (path.includes('/api/prep-plan/today')) {
        const entry = { ...TODAY.entries[0], basis: { ...TODAY.entries[0].basis, ...basis } }
        return jsonResponse({ ...TODAY, entries: [entry] })
      }
      return jsonResponse({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await wrapper.find('.pp-row-main').trigger('click')
    await flushPromises()
    return wrapper
  }

  it('两组柱子颜色不同（实际用量走主色，候选日走 alt）', async () => {
    const wrapper = await mountWithBasis({ status: 'ok', weekdays, quantile: 0.5, quantile_qty: 15 })
    expect(wrapper.get('.pp-chart--weekday svg').classes()).toContain('is-alt')
    expect(wrapper.get('.pp-chart--history svg').classes()).not.toContain('is-alt')
  })

  it('候选日画成柱形图，从左到右按日期排', async () => {
    const wrapper = await mountWithBasis({ status: 'ok', weekdays, quantile: 0.5, quantile_qty: 15 })
    const bars = wrapper.findAll('.pp-chart--weekday .prep-bar')
    expect(bars).toHaveLength(8)
    const ticks = wrapper.findAll('.pp-chart--weekday .prep-tick:not(.is-weekday)')
    expect(ticks[0].text()).toBe('09-10')
    expect(ticks[ticks.length - 1].text()).toBe('10-29')
    // 同星期几那根不是「今天」，不许高亮
    expect(bars[bars.length - 1].classes()).not.toContain('is-today')
  })

  it('虚线画在这一档的分位落点上，标签是 p50 / p70', async () => {
    const wrapper = await mountWithBasis({ status: 'ok', weekdays, quantile: 0.5, quantile_qty: 15 })
    expect(wrapper.get('.pp-chart--weekday figcaption').text().replace(/\s+/g, ' ')).toContain('p50 15')
    // 说明只讲规则（哪一档），落点数字不重复第二遍
    expect(wrapper.get('.pp-detail').text()).toContain('偏保守的那档（p50）')
  })

  it('落点带小数时保留 1 位，不按「整份」抹成整数', async () => {
    const wrapper = await mountWithBasis({
      status: 'ok', weekdays, quantile: 0.5, quantile_qty: 124.5,
    })
    expect(wrapper.get('.pp-chart--weekday figcaption').text().replace(/\s+/g, ' ')).toContain(
      'p50 124.5',
    )
  })

  it('主力品档位是 p70', async () => {
    const wrapper = await mountWithBasis({
      status: 'ok', weekdays, quantile: 0.7, quantile_qty: 17, is_major: true,
    })
    expect(wrapper.get('.pp-chart--weekday figcaption').text().replace(/\s+/g, ' ')).toContain(
      'p70 17',
    )
  })
})

describe('多段参考值（48 小时以上）', () => {
  /** 用户口径：72 小时 = 24 + 25–48 + 49–72，页面要把这个加法写出来。 */
  const weekdays = [
    { day: '2026-10-29', qty: 62 },
    { day: '2026-10-22', qty: 58 },
    { day: '2026-10-15', qty: 60 },
  ]
  const slices = (values) =>
    values.map((qty, k) => ({
      start: new Date(Date.parse('2026-10-22T08:00:00+08:00') + k * 86400000).toISOString(),
      end: new Date(Date.parse('2026-10-22T08:00:00+08:00') + (k + 1) * 86400000).toISOString(),
      quantile_qty: qty,
      demand_qty: qty,
    }))

  async function mountWithSlices(parts, { quantile_qty, store_factor = 1 } = {}) {
    const fetchMock = vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/stations')) {
        return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
      }
      if (path.includes('/api/prep-plan/today')) {
        const entry = {
          ...TODAY.entries[0],
          basis: {
            ...TODAY.entries[0].basis,
            status: 'ok',
            weekdays,
            quantile: 0.5,
            quantile_qty: quantile_qty ?? parts.reduce((sum, p) => sum + p.quantile_qty, 0),
            store_factor,
            slices: parts,
          },
        }
        return jsonResponse({ ...TODAY, entries: [entry] })
      }
      return jsonResponse({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()
    await wrapper.find('.pp-row-main').trigger('click')
    await flushPromises()
    return wrapper
  }

  it('48 小时：说明写「两段相加」，合计不重复写在图上', async () => {
    const wrapper = await mountWithSlices(slices([15, 17]))
    const caption = wrapper.get('.pp-chart--weekday figcaption').text().replace(/\s+/g, ' ')
    expect(caption).toContain('整窗口合计')
    expect(caption).toContain('p50 合计')
    // 数字留给下面那行加法，图上不再写一遍
    expect(caption).not.toContain('32')
    expect(wrapper.get('.pp-basis').text()).toContain('2 段相加：15 + 17 = 32（每段各取 p50）')
  })

  it('7 天：七段各写一个数，再乘整店水位', async () => {
    const wrapper = await mountWithSlices(
      slices([100, 120, 140, 160, 130, 150, 170]),
      { store_factor: 1.1 },
    )
    const text = wrapper.get('.pp-basis').text()
    expect(text).toContain('7 段相加：100 + 120 + 140 + 160 + 130 + 150 + 170 = 970')
    expect(text).toContain('每段各取 p50')
    expect(text).toContain('× 整店水位 1.10')
  })

  it('24 小时只有一段，仍走原来的说法', async () => {
    const wrapper = await mountWithSlices(slices([15]), { quantile_qty: 15 })
    const caption = wrapper.get('.pp-chart--weekday figcaption').text().replace(/\s+/g, ' ')
    expect(caption).toContain('同钟点区间')
    expect(caption).toContain('p50 15')
    expect(wrapper.get('.pp-basis').text()).toContain('偏保守的那档（p50）')
    expect(wrapper.get('.pp-basis').text()).not.toContain('段相加')
  })
})

describe('半成品只报整份', () => {
  /** 后端已经把半成品算成整数；前端再兜一层，老清单/手改的 58.7 也不许显示出来。 */
  function fetchWith(entries) {
    const fetchMock = vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/stations')) {
        return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
      }
      if (path.includes('/api/prep-plan/today')) return jsonResponse({ ...TODAY, entries })
      return jsonResponse({}, 404)
    })
    return fetchMock
  }

  it('半成品向上取整到整份', async () => {
    vi.stubGlobal(
      'fetch',
      fetchWith([{ ...TODAY.entries[0], suggested_qty: 58.7, final_qty: 58.7 }]),
    )
    const wrapper = await mountView()
    expect(wrapper.find('.pp-qty b').text()).toBe('59')
  })

  it('原料保留小数', async () => {
    vi.stubGlobal(
      'fetch',
      fetchWith([{ ...TODAY.entries[1], suggested_qty: 58.7, final_qty: 58.7 }]),
    )
    const wrapper = await mountView()
    expect(wrapper.find('.pp-qty b').text()).toBe('58.7')
  })
})

describe('时间跨度切换', () => {
  /** 终点按真实「今天」算：页头那句「现在 → 明天 / 后天 / M/D」是相对日期，写死日期会过期。 */
  function endIso(daysAhead, clock = '14:00') {
    const now = new Date()
    const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + daysAhead)
    const pad = (n) => String(n).padStart(2, '0')
    return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${clock}:00+08:00`
  }

  const ENDS = { 24: endIso(1), 48: endIso(2), 72: endIso(3), 168: endIso(7) }

  function horizonFetch() {
    const calls = []
    const fetchMock = vi.fn(async (url, options = {}) => {
      const path = String(url)
      calls.push({ path, method: options.method || 'GET' })
      if (path.includes('/api/stations')) {
        return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
      }
      if (path.includes('/api/prep-plan/today')) {
        const hours = Number(/hours=(\d+)/.exec(path)?.[1] || 24)
        return jsonResponse({
          ...TODAY,
          window: { ...TODAY.window, end: ENDS[hours] || ENDS[24], horizon_hours: hours },
        })
      }
      return jsonResponse({}, 404)
    })
    fetchMock.calls = calls
    return fetchMock
  }

  it('四个跨度都摆出来，默认选 24 小时', async () => {
    vi.stubGlobal('fetch', horizonFetch())
    const wrapper = await mountView()

    expect(wrapper.findAll('.pp-range').map((btn) => btn.text())).toEqual([
      '24 小时',
      '48 小时',
      '72 小时',
      '7 天',
    ])
    expect(wrapper.find('.pp-range.is-on').text()).toBe('24 小时')
    expect(wrapper.text()).toContain('今天参考值')
    expect(wrapper.text()).toContain('明天 14:00')
  })

  it('切到 7 天：按 hours=168 重拉，标题与页头跟着变，并记住选择', async () => {
    const fetchMock = horizonFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    const target = wrapper.findAll('.pp-range').find((btn) => btn.text() === '7 天')
    await target.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(
      fetchMock.calls.some(
        (call) => call.path.includes('/api/prep-plan/today') && call.path.includes('hours=168'),
      ),
    ).toBe(true)
    expect(wrapper.text()).toContain('未来 7 天参考值')
    // 超过后天就不写「大后天」，直接给日期
    const [month, day] = ENDS[168].slice(5, 10).split('-').map(Number)
    expect(wrapper.text()).toContain(`${month}/${day} 14:00`)
    expect(wrapper.text()).toContain('（未来 7 天）')
    expect(window.localStorage.getItem('prepPlanHorizon')).toBe('168')
  })

  it('上次选的跨度下次进来直接用（48 小时开工即生效）', async () => {
    const fetchMock = horizonFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView({ horizon: 48 })

    expect(fetchMock.calls[0].path).toContain('hours=48')
    expect(wrapper.text()).toContain('未来 48 小时参考值')
    expect(wrapper.find('.pp-range.is-on').text()).toBe('48 小时')
  })
})

describe('手改原因（票 09）', () => {
  it('改数量的编辑态给四个可选原因，默认一个都不选', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    await wrapper.get('.pp-qty').trigger('click')
    const reasons = wrapper.findAll('.pp-edit-reason')
    expect(reasons).toHaveLength(4)
    expect(reasons.map((node) => node.text())).toEqual(['实盘库存', '临时加单', '经验判断', '其他'])
    // 默认不选：不选 = 与改造前完全一样。
    expect(reasons.every((node) => node.attributes('aria-pressed') === 'false')).toBe(true)
  })

  it('选一个原因再提交：PATCH body 带上 reason', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.get('.pp-qty').trigger('click')
    const input = wrapper.get('.pp-qty-input')
    await input.setValue('95')
    // 点原因只是选中，不提交；输入框不失焦（mousedown.prevent）。
    await wrapper.findAll('.pp-edit-reason')[0].trigger('click')
    await input.trigger('keyup.enter')
    await flushPromises()

    const patch = fetchMock.calls.find((call) => call.method === 'PATCH')
    expect(patch.path).toContain('/api/prep-plan/today/entries/1')
    expect(JSON.parse(patch.body)).toEqual({ final_qty: 95, reason: 'physical_stock' })
  })

  it('不选原因提交时 body 与改造前一致（只有 final_qty）', async () => {
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
  })
})

describe('复制清单埋点（票 09）', () => {
  it('复制成功后 fire-and-forget 打一条 plan_copied', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.get('.pp-copy').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledTimes(1)
    const event = fetchMock.calls.find((call) => call.path.includes('/api/prep-plan/events'))
    expect(event).toBeTruthy()
    expect(event.method).toBe('POST')
    const body = JSON.parse(event.body)
    expect(body.kind).toBe('plan_copied')
    expect(body.meta.horizon_hours).toBe(24)
    expect(wrapper.text()).toContain('已复制')
  })

  it('埋点失败不影响复制（不弹错、仍然显示已复制）', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const fetchMock = probeFetch()
    const realFetch = fetchMock
    vi.stubGlobal('fetch', (url, options = {}) => {
      if (String(url).includes('/api/prep-plan/events')) {
        realFetch.calls.push({ path: String(url), method: options.method || 'GET', body: options.body })
        return Promise.reject(new Error('offline'))
      }
      return realFetch(url, options)
    })
    const wrapper = await mountView()

    await wrapper.get('.pp-copy').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已复制')
    expect(wrapper.find('.pp-error').exists()).toBe(false)
  })
})
