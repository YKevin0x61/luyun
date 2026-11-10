// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrepReviewView from '../PrepReviewView.vue'
import { buildReviewCsv, reviewCsvFilename } from '../../utils/prepReviewCsv.js'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

// 2026-11-10：这一页按工作台身份分写面（备注 / 天气设置只给管理端会话）。
// 测试默认固定「管理端」；员工会话那条只读契约在本文件末尾单独切 state 验证。
const identityState = vi.hoisted(() => ({ identity: 'super', refresh: () => Promise.resolve() }))
vi.mock('../../stores/workbenchIdentity', () => ({
  useWorkbenchIdentityStore: () => ({
    get identity() {
      return identityState.identity
    },
    refresh: identityState.refresh,
  }),
}))

/**
 * 备货复盘页（ADR 0129 / 票 06 前端）。
 *
 * 断言两件事：页面上能不能读懂 90 天口径，以及点下去发了什么请求。
 * 硬边界：NULL 不等于 0 —— 没有分母、样本不足、窗口没结束都显示「—」或「样本不足」。
 */

const DAY_STR = new Date().toLocaleDateString('sv-SE')

const SUMMARY = {
  success: true,
  period: { start: '2026-08-09', end: '2026-11-06', days: 90 },
  primary_clock: '07:00',
  min_group_days: 5,
  metrics: {
    measured_days: 42,
    measured_item_days: 380,
    suggested_total: 1200,
    sold_total: 1000,
    shortfall_qty: 120,
    overfill_qty: 320,
    absolute_deviation: 0.164,
    direction_deviation: 0.032,
    coverage_qty: 0.872,
    covered_items: 300,
    coverage_items: 0.789,
  },
  groups: {
    weekday: [
      { key: '1', label: '周一', n: 8, direction_deviation: 0.05, absolute_deviation: 0.12, insufficient: false },
      { key: '2', label: '周二', n: 3, direction_deviation: null, absolute_deviation: null, insufficient: true },
      { key: '3', label: '周三', n: 6, direction_deviation: 0.02, absolute_deviation: 0.09, insufficient: false },
      { key: '4', label: '周四', n: 6, direction_deviation: -0.03, absolute_deviation: 0.11, insufficient: false },
      { key: '5', label: '周五', n: 7, direction_deviation: 0.06, absolute_deviation: 0.14, insufficient: false },
      { key: '6', label: '周六', n: 7, direction_deviation: 0.08, absolute_deviation: 0.16, insufficient: false },
      { key: '7', label: '周日', n: 5, direction_deviation: 0.01, absolute_deviation: 0.1, insufficient: false },
    ],
    holiday: [
      { key: 'holiday', label: '节假日', n: 6, direction_deviation: -0.02, absolute_deviation: 0.11, insufficient: false },
      { key: 'weekday', label: '平日', n: 30, direction_deviation: 0.04, absolute_deviation: 0.1, insufficient: false },
    ],
    weather: [
      { key: 'rain', label: '雨', n: 10, direction_deviation: 0.07, absolute_deviation: 0.15, insufficient: false },
      { key: 'hot', label: '高温', n: 2, direction_deviation: null, absolute_deviation: null, insufficient: true },
      { key: 'normal', label: '正常', n: 20, direction_deviation: 0.01, absolute_deviation: 0.09, insufficient: false },
      { key: 'unknown', label: '无数据', n: 1, direction_deviation: null, absolute_deviation: null, insufficient: true },
    ],
    staff: [
      { key: '1-2', label: '1-2 人', n: 4, direction_deviation: null, absolute_deviation: null, insufficient: true },
      { key: '3-4', label: '3-4 人', n: 12, direction_deviation: 0.05, absolute_deviation: 0.13, insufficient: false },
      { key: '5+', label: '5 人以上', n: 9, direction_deviation: -0.04, absolute_deviation: 0.12, insufficient: false },
      { key: 'unknown', label: '无数据', n: 0, direction_deviation: null, absolute_deviation: null, insufficient: true },
    ],
    note: [
      { key: 'with', label: '有备注', n: 6, direction_deviation: 0.09, absolute_deviation: 0.18, insufficient: false },
      { key: 'without', label: '无备注', n: 30, direction_deviation: 0.02, absolute_deviation: 0.1, insufficient: false },
    ],
  },
  cost: {
    overfill_qty: 320,
    next_day_sold: 120,
    wastage: 60,
    unexplained: 140,
    estimated_amount: 1860.5,
    estimated: true,
    wastage_recorded_qty: 75,
    wastage_reasons: ['excess', 'expired', 'spoiled'],
    pending_trajectory: 7,
  },
}

const ITEM = {
  item_name: '虾饺馅',
  unit: '份',
  station: 'shulong',
  suggested_qty: 80,
  final_qty: null,
  sold_qty: 60,
  sold_1h: 66,
  sold_2h: 70,
  sold_next_day: 90,
  refund_qty: 2,
  sold_out_at: '2026-11-06T13:07:00+08:00',
  quantile: 0.7,
  is_major: true,
  status: 'ok',
  same_rules: true,
}

const PENDING_ITEM = {
  item_name: '河粉',
  unit: '斤',
  station: 'shulong',
  suggested_qty: 20,
  final_qty: 20,
  sold_qty: null,
  sold_1h: null,
  sold_2h: null,
  sold_next_day: null,
  refund_qty: null,
  sold_out_at: null,
  quantile: 0.5,
  is_major: false,
  status: 'ok',
  same_rules: true,
}

const GROUP_SUMMARY = {
  measured: 1,
  pending: 1,
  drifted: 0,
  covered: 0.6,
  shortfall: 20,
  shortfall_ratio: 0.25,
  overfill: 0,
  overfill_ratio: 0,
  sold_total: 80,
  suggested_total: 100,
  ref_ratio: 1.25,
}

function dayPayload() {
  return {
    success: true,
    day: DAY_STR,
    primary_clock: '07:00',
    snapshots: [
      { clock: '07:00', summary: GROUP_SUMMARY, items: [ITEM, PENDING_ITEM] },
      { clock: '19:00', summary: GROUP_SUMMARY, items: [] },
    ],
    context: {
      holiday_name: '国庆节',
      weather_json: '{"text":"雨","temp_max":26}',
      staff_json: '{"shulong":4,"xibing":2}',
      note: '团餐 30 人',
      captured_at: '2026-11-06T07:05:00+08:00',
    },
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function probeFetch({ summary = SUMMARY, day = dayPayload(), noteResponse = null } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/stations')) {
      return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
    }
    if (path.includes('/api/prep-plan/context/note')) {
      return jsonResponse(noteResponse || { success: true, day: DAY_STR, context: day.context })
    }
    if (path.includes('/api/prep-plan/review/summary')) return jsonResponse(summary)
    if (path.includes('/api/prep-plan/snapshots')) return jsonResponse(day)
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView(preset = {}) {
  vi.resetModules()
  window.localStorage.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PrepReviewView, {
    // RouterLink 桩要渲染默认插槽：报废那行的「去损耗分析」是链接文字，不是属性
    global: { plugins: [pinia], stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

function callsTo(fetchMock, fragment) {
  return fetchMock.calls.filter((call) => call.path.includes(fragment))
}

beforeEach(() => {
  clearAuthStatusCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('90 天口径的新三卡', () => {
  it('绝对偏差 / 方向偏差 / 数量口径够卖率，按选中日回看 90 天', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    const cards = wrapper.findAll('.pr-metric-review')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('绝对偏差')
    expect(cards[0].text()).toContain('16.4%')
    expect(cards[1].text()).toContain('方向偏差')
    expect(cards[1].text()).toContain('+3.2%')
    expect(cards[1].text()).toContain('备多了')
    expect(cards[2].text()).toContain('数量口径够卖率')
    expect(cards[2].text()).toContain('87.2%')

    // 口径与期间必须写在页面上，不能只活在代码里
    expect(wrapper.text()).toContain('近 90 天')
    expect(wrapper.text()).toContain('07:00')

    const [summaryCall] = callsTo(fetchMock, '/api/prep-plan/review/summary')
    expect(summaryCall.path).toContain(`end=${DAY_STR}`)
    expect(summaryCall.path).toContain('days=90')
  })

  it('分母为 0 的比例是「—」，不是 0', async () => {
    const nullSummary = {
      ...SUMMARY,
      metrics: {
        ...SUMMARY.metrics,
        absolute_deviation: null,
        direction_deviation: null,
        coverage_qty: null,
      },
    }
    vi.stubGlobal('fetch', probeFetch({ summary: nullSummary }))
    const wrapper = await mountView()

    const cards = wrapper.findAll('.pr-metric-review')
    for (const card of cards) {
      expect(card.find('.pr-metric-v').text()).toBe('—')
    }
  })

  it('方向为负时说「备少了」', async () => {
    const negative = {
      ...SUMMARY,
      metrics: { ...SUMMARY.metrics, direction_deviation: -0.021 },
    }
    vi.stubGlobal('fetch', probeFetch({ summary: negative }))
    const wrapper = await mountView()

    const card = wrapper.findAll('.pr-metric-review')[1]
    expect(card.text()).toContain('-2.1%')
    expect(card.text()).toContain('备少了')
  })
})

describe('为什么不准：五组分组', () => {
  it('每组都带 n 天与两个偏差；n < 5 显示样本不足而不是 0', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const cards = wrapper.findAll('.pr-why-card')
    expect(cards).toHaveLength(5)
    expect(wrapper.text()).toContain('星期几')
    expect(wrapper.text()).toContain('节假日')
    expect(wrapper.text()).toContain('天气')
    expect(wrapper.text()).toContain('档口人数')
    expect(wrapper.text()).toContain('备注')

    const monday = cards[0].findAll('.pr-why-row').find((row) => row.text().includes('周一'))
    expect(monday.text()).toContain('8 天')
    expect(monday.text()).toContain('备多了')
    expect(monday.text()).toContain('+5.0%')
    expect(monday.text()).toContain('12.0%')

    const tuesday = cards[0].findAll('.pr-why-row').find((row) => row.text().includes('周二'))
    expect(tuesday.text()).toContain('3 天')
    expect(tuesday.text()).toContain('样本不足')
    expect(tuesday.text()).not.toContain('%')
  })
})

describe('多备的代价', () => {
  it('多出量拆成次日卖出 / 报废 / 未明，金额标估算，并提示待回填', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const cost = wrapper.find('.pr-cost')
    expect(cost.text()).toContain('多出量')
    expect(cost.text()).toContain('320.0')
    expect(cost.text()).toContain('次日卖出')
    expect(cost.text()).toContain('120.0')
    expect(cost.text()).toContain('报废')
    expect(cost.text()).toContain('60.0')
    expect(cost.text()).toContain('未明')
    expect(cost.text()).toContain('140.0')
    expect(cost.text()).toContain('估算')
    expect(cost.text()).toContain('¥1860.50')
    expect(cost.text()).toContain('7 个品-天的次日销量还在等回填')
    // 报废一行链到损耗分析（不带 query：那页现在只读 /api/wastage/meta）
    expect(cost.find('a').attributes('href')).toBe('/workbench/kitchen/loss')
    expect(cost.text()).toContain('去损耗分析')
  })
})

describe('今日备注', () => {
  it('有备注时展示，点「改备注」可改，回车提交 PUT 并重拉快照', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('团餐 30 人')

    await wrapper.find('.pr-note-btn').trigger('click')
    const input = wrapper.find('.pr-note-input')
    expect(input.element.value).toBe('团餐 30 人')
    await input.setValue('团餐 30 人 + 婚宴')
    await input.trigger('keyup', { key: 'Enter' })
    await flushPromises()
    await flushPromises()

    const puts = callsTo(fetchMock, '/api/prep-plan/context/note').filter((call) => call.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(JSON.parse(puts[0].body)).toEqual({ day: DAY_STR, note: '团餐 30 人 + 婚宴' })
    // 保存成功后要重拉一次快照（备注会进复盘分组的「有备注 / 无备注」）
    expect(callsTo(fetchMock, '/api/prep-plan/snapshots')).toHaveLength(2)
    expect(wrapper.find('.pr-note-input').exists()).toBe(false)
  })

  it('清空备注传空串', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    await wrapper.find('.pr-note-btn').trigger('click')
    await wrapper.find('.pr-note-input').setValue('')
    await wrapper.find('.pr-note-save').trigger('click')
    await flushPromises()
    await flushPromises()

    const put = callsTo(fetchMock, '/api/prep-plan/context/note').find((call) => call.method === 'PUT')
    expect(JSON.parse(put.body)).toEqual({ day: DAY_STR, note: '' })
  })
})

describe('环境信号', () => {
  it('context 的天气 / 人数文本 JSON 解析后显示，坏数据不炸页面', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const line = wrapper.find('.pr-context')
    expect(line.text()).toContain('国庆节')
    expect(line.text()).toContain('雨')
    expect(line.text()).toContain('26°')
    expect(line.text()).toContain('在岗 6 人')

    const broken = dayPayload()
    broken.context.weather_json = '{not json'
    broken.context.staff_json = '[]'
    vi.stubGlobal('fetch', probeFetch({ day: broken }))
    const wrapper2 = await mountView()
    expect(wrapper2.find('.pr-context').text()).toContain('国庆节')
  })
})

describe('明细表新列', () => {
  it('售罄时刻显示 HH:MM；窗口后是增量（累计 − 窗口内）', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const table = wrapper.findAll('.pr-table')[0]
    const cells = table.findAll('tbody tr')[0].findAll('td')
    expect(cells[7].text()).toBe('13:07')
    expect(cells[8].text()).toBe('6.0')
    expect(cells[9].text()).toBe('10.0')
    expect(cells[10].text()).toBe('30.0')
    expect(cells[11].text()).toBe('2.0')
  })

  it('未结束 / 缺数据的行一律「—」，不显示 0', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const cells = wrapper.findAll('.pr-table')[0].findAll('tbody tr')[1].findAll('td')
    expect(cells[4].text()).toBe('—')
    expect(cells[5].text()).toBe('—')
    for (const index of [7, 8, 9, 10, 11]) expect(cells[index].text()).toBe('—')
  })
})

describe('导出 CSV（纯函数）', () => {
  it('带 BOM、首行是约定表头、null 留空', () => {
    const csv = buildReviewCsv({ day: '2026-11-07', clock: '07:00', items: [ITEM, PENDING_ITEM] })
    expect(csv.charCodeAt(0)).toBe(0xfeff)

    const lines = csv.slice(1).split('\n')
    expect(lines[0]).toBe('品名,单位,参考值,实际卖出,偏差,售罄时刻,窗口后+1h,窗口后+2h,次日,退菜')
    expect(lines[1]).toBe('虾饺馅,份,80,60,-20,13:07,6,10,30,2')

    const pending = lines[2].split(',')
    expect(pending).toHaveLength(10)
    expect(pending[0]).toBe('河粉')
    expect(pending[2]).toBe('20')
    for (const index of [3, 4, 5, 6, 7, 8, 9]) expect(pending[index]).toBe('')
  })

  it('double 的浮点尾差收成两位小数', () => {
    const csv = buildReviewCsv({
      items: [{ ...ITEM, suggested_qty: 200.6, sold_qty: 180, sold_1h: 194.4, sold_2h: 198, sold_next_day: 210 }],
    })
    const row = csv.slice(1).split('\n')[1]
    expect(row).toBe('虾饺馅,份,200.6,180,-20.6,13:07,14.4,18,30,2')
  })

  it('文件名带日期与钟点', () => {
    expect(reviewCsvFilename('2026-11-07', '07:00')).toBe('备货复盘_2026-11-07_0700.csv')
  })
})

describe('数据质量提示（票 09）', () => {
  // 只伪造 Date：覆盖判据里有一项是「今天已经过去几个钟点」，不钉住时间这条用例就会
  // 在清早跑出另一套预期。定时器保持真实，`flushPromises` 才不会被卡住。
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-07T19:00:00+08:00'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function withQuality(quality) {
    const payload = dayPayload()
    return {
      ...payload,
      context: { ...payload.context, data_quality_json: quality },
    }
  }

  it('订单明显偏少 / 快照覆盖很少时提示，并说清别把断档读成「备多了」', async () => {
    const quality = JSON.stringify({ order_lines: 7, covered_hours: 2 })
    vi.stubGlobal('fetch', probeFetch({ day: withQuality(quality) }))
    const wrapper = await mountView()

    const banner = wrapper.get('.pr-quality')
    expect(banner.text()).toContain('数据可能不完整')
    expect(banner.text()).toContain('快照只覆盖 2 个钟点')
    expect(banner.text()).toContain('别把采集断档读成「备多了」')
  })

  it('数据正常时不出提示', async () => {
    const quality = JSON.stringify({ order_lines: 420, covered_hours: 14 })
    vi.stubGlobal('fetch', probeFetch({ day: withQuality(quality) }))
    const wrapper = await mountView()

    expect(wrapper.find('.pr-quality').exists()).toBe(false)
  })

  it('坏 JSON / 字段缺失都不炸，也不显示提示', async () => {
    vi.stubGlobal('fetch', probeFetch({ day: withQuality('{not json') }))
    let wrapper = await mountView()
    expect(wrapper.find('.pr-quality').exists()).toBe(false)
    expect(wrapper.text()).toContain('备货复盘')

    // 字段缺失（已经上线的库还没写 data_quality_json）走同一条路。
    vi.stubGlobal('fetch', probeFetch())
    wrapper = await mountView()
    expect(wrapper.find('.pr-quality').exists()).toBe(false)
  })

  it('读票 08 已解析的 data_quality 对象（orders_today / snapshot_hours）', async () => {
    const payload = dayPayload()
    payload.context = {
      ...payload.context,
      // 票 08 的 `_data_quality` 实际形状（接口已把 JSON 解析成对象）。
      data_quality: { orders_today: 5, snapshot_hours: 1, first_order_at: null, last_order_at: null },
    }
    vi.stubGlobal('fetch', probeFetch({ day: payload }))
    const wrapper = await mountView()

    const banner = wrapper.get('.pr-quality').text()
    expect(banner).toContain('快照只覆盖 1 个钟点')
    expect(banner).toContain('当日订单只有 5 条')
  })
})

describe('员工会话只读（2026-11-10 用户裁定）', () => {
  it('有「备货复盘」权限的员工能看全部只读内容，但备注 / 天气设置这类写面不渲染', async () => {
    identityState.identity = 'staff'
    try {
      const fetchMock = probeFetch()
      vi.stubGlobal('fetch', fetchMock)
      const wrapper = await mountView()

      // 只读内容照常
      expect(wrapper.text()).toContain('备货复盘')
      expect(wrapper.findAll('.pr-metric-review')).toHaveLength(3)
      // 档口名来自管理端门的 `/api/stations`（员工会话 401）：员工档不拉它，别挂 401。
      expect(callsTo(fetchMock, '/api/stations')).toHaveLength(0)

      // 写面两个入口都不在：备注按钮（文案「备注」/ aria）与天气设置按钮
      expect(wrapper.find('.pr-note-btn').exists()).toBe(false)
      expect(wrapper.find('button[aria-label="天气设置"]').exists()).toBe(false)
    } finally {
      identityState.identity = 'super'
    }
  })
})
