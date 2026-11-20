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
    sold_total: 1000,
    planned_sources: { manual: 40, suggested: 340 },
    reference: {
      qty_total: 1200,
      shortfall_qty: 120,
      overfill_qty: 320,
      ratio: 1.2,
      shortfall_ratio: 0.12,
      overfill_ratio: 0.32,
      absolute_deviation: 0.164,
      direction_deviation: 0.032,
      coverage_qty: 0.872,
      covered_items: 300,
      coverage_items: 0.789,
    },
    planned: {
      qty_total: 1180,
      shortfall_qty: 100,
      overfill_qty: 300,
      ratio: 1.18,
      shortfall_ratio: 0.1,
      overfill_ratio: 0.3,
      absolute_deviation: 0.151,
      direction_deviation: 0.021,
      coverage_qty: 0.861,
      covered_items: 310,
      coverage_items: 0.815,
    },
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

// 分组桶也改成两条基线：夹具里两套先取同一组数，专门的对拍用例另给不同值。
for (const rows of Object.values(SUMMARY.groups)) {
  for (const row of rows) {
    row.baselines = {
      reference: {
        direction_deviation: row.direction_deviation,
        absolute_deviation: row.absolute_deviation,
      },
      planned: {
        direction_deviation: row.direction_deviation,
        absolute_deviation: row.absolute_deviation,
      },
    }
    delete row.direction_deviation
    delete row.absolute_deviation
  }
}

const ITEM = {
  item_name: '虾饺馅',
  unit: '份',
  station: 'shulong',
  reference_qty: 80,
  planned_qty: 80,
  planned_source: 'suggested',
  sold_qty: 60,
  sold_1h: 66,
  sold_2h: 70,
  sold_next_day: 90,
  refund_qty: 2,
  sold_out_at: '2026-11-06T13:07:00+08:00',
  sold_out_reliable: true,
  quantile: 0.7,
  is_major: true,
  status: 'ok',
  same_rules: true,
}

const PENDING_ITEM = {
  item_name: '河粉',
  unit: '斤',
  station: 'shulong',
  reference_qty: 20,
  planned_qty: 20,
  planned_source: 'manual',
  sold_qty: null,
  sold_1h: null,
  sold_2h: null,
  sold_next_day: null,
  refund_qty: null,
  sold_out_at: null,
  sold_out_reliable: null,
  quantile: 0.5,
  is_major: false,
  status: 'ok',
  same_rules: true,
}

const GROUP_SUMMARY = {
  measured: 1,
  pending: 1,
  drifted: 0,
  sold_out: 1,
  sold_out_reliable: 1,
  sold_total: 80,
  reference: {
    qty_total: 100,
    shortfall_qty: 20,
    overfill_qty: 0,
    ratio: 1.25,
    shortfall_ratio: 0.25,
    overfill_ratio: 0,
    absolute_deviation: 0.25,
    direction_deviation: 0.25,
    coverage_qty: 0.8,
    covered_items: 1,
    coverage_items: 1.0,
  },
  planned: {
    qty_total: 100,
    shortfall_qty: 20,
    overfill_qty: 0,
    ratio: 1.25,
    shortfall_ratio: 0.25,
    overfill_ratio: 0,
    absolute_deviation: 0.25,
    direction_deviation: 0.25,
    coverage_qty: 0.8,
    covered_items: 1,
    coverage_items: 1.0,
  },
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

describe('90 天口径的两套基线', () => {
  it('绝对偏差 / 方向偏差 / 数量口径够卖率，参考与下达并排', async () => {
    const fetchMock = probeFetch()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView()

    const table = wrapper.find('.pr-compare-review')
    const rows = table.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('绝对偏差')
    expect(rows[0].text()).toContain('16.4%')
    expect(rows[0].text()).toContain('15.1%')
    expect(rows[1].text()).toContain('方向偏差')
    expect(rows[1].text()).toContain('+3.2%')
    expect(rows[1].text()).toContain('+2.1%')
    expect(rows[1].text()).toContain('备多了')
    expect(rows[2].text()).toContain('数量口径够卖率')
    expect(rows[2].text()).toContain('87.2%')
    expect(rows[2].text()).toContain('86.1%')
    expect(wrapper.text()).toContain('参考合计 1200.0')
    expect(wrapper.text()).toContain('下达合计 1180.0')

    // 口径与期间必须写在页面上，不能只活在代码里
    expect(wrapper.text()).toContain('近 90 天')
    expect(wrapper.text()).toContain('07:00')

    const [summaryCall] = callsTo(fetchMock, '/api/prep-plan/review/summary')
    expect(summaryCall.path).toContain(`end=${DAY_STR}`)
    expect(summaryCall.path).toContain('days=90')
  })

  it('当天两套口径的每个数字都带移动端标签', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const dayTable = wrapper.find('.pr-compare-day')
    expect(dayTable.findAll('tbody tr')).toHaveLength(2)
    for (const label of ['合计', '÷ 实卖', '够卖品数', '缺货', '多出']) {
      expect(dayTable.find(`tbody td[data-label="${label}"]`).exists()).toBe(true)
    }
  })

  it('分母为 0 的比例是「—」，不是 0', async () => {
    const nullBaseline = {
      ...SUMMARY.metrics.reference,
      absolute_deviation: null,
      direction_deviation: null,
      coverage_qty: null,
    }
    const nullSummary = {
      ...SUMMARY,
      metrics: {
        ...SUMMARY.metrics,
        reference: nullBaseline,
        planned: nullBaseline,
      },
    }
    vi.stubGlobal('fetch', probeFetch({ summary: nullSummary }))
    const wrapper = await mountView()

    for (const cell of wrapper.find('.pr-compare-review').findAll('tbody td.n')) {
      expect(cell.text()).toBe('—')
    }
  })

  it('方向为负时说「备少了」', async () => {
    const negative = {
      ...SUMMARY,
      metrics: {
        ...SUMMARY.metrics,
        reference: { ...SUMMARY.metrics.reference, direction_deviation: -0.021 },
        planned: { ...SUMMARY.metrics.planned, direction_deviation: -0.021 },
      },
    }
    vi.stubGlobal('fetch', probeFetch({ summary: negative }))
    const wrapper = await mountView()

    const row = wrapper.find('.pr-compare-review').findAll('tbody tr')[1]
    expect(row.text()).toContain('-2.1%')
    expect(row.text()).toContain('备少了')
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

    const table = wrapper.find('.pr-table-detail')
    const cells = table.findAll('tbody tr')[0].findAll('td')
    expect(cells[8].text()).toContain('13:07')
    expect(cells[9].text()).toBe('6.0')
    expect(cells[10].text()).toBe('10.0')
    expect(cells[11].text()).toBe('30.0')
    expect(cells[12].text()).toBe('2.0')
  })

  it('售罄时刻带可靠性角标：真停卖与旧判定分开', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const rows = wrapper.find('.pr-table-detail').findAll('tbody tr')
    const trusted = rows[0].find('.pr-soldout-tag')
    expect(trusted.text()).toBe('停卖')
    expect(trusted.classes()).toContain('is-trusted')

    expect(rows[1].findAll('td')[8].text()).toBe('—')

    // 旧事件（没有 reliable 字段）只能标「存疑」，不能当成删失。
    const legacy = dayPayload()
    legacy.snapshots[0].items = [{ ...ITEM, sold_out_reliable: false }]
    vi.stubGlobal('fetch', probeFetch({ day: legacy }))
    const wrapper2 = await mountView()
    const tag = wrapper2.find('.pr-table-detail').find('.pr-soldout-tag')
    expect(tag.text()).toBe('存疑')
    expect(tag.classes()).toContain('is-suspect')
  })

  it('钟点行提示售罄总数与其中停卖数', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    expect(wrapper.find('.pr-line').text()).toContain('售罄 1 个，其中停卖 1 个')
  })

  it('未结束 / 缺数据的行一律「—」，不显示 0', async () => {
    vi.stubGlobal('fetch', probeFetch())
    const wrapper = await mountView()

    const cells = wrapper.find('.pr-table-detail').findAll('tbody tr')[1].findAll('td')
    expect(cells[5].text()).toBe('—')
    expect(cells[6].text()).toBe('—')
    expect(cells[7].text()).toBe('—')
    for (const index of [8, 9, 10, 11, 12]) expect(cells[index].text()).toBe('—')
  })
})

describe('导出 CSV（纯函数）', () => {
  it('带 BOM、首行是约定表头、null 留空', () => {
    const csv = buildReviewCsv({ day: '2026-11-07', clock: '07:00', items: [ITEM, PENDING_ITEM] })
    expect(csv.charCodeAt(0)).toBe(0xfeff)

    const lines = csv.slice(1).split('\n')
    expect(lines[0]).toBe(
      '品名,单位,参考值,下达量,下达来源,实际卖出,对参考,对下达,售罄时刻,售罄判定,窗口后+1h,窗口后+2h,次日,退菜',
    )
    expect(lines[1]).toBe('虾饺馅,份,80,80,未手改,60,-20,-20,13:07,停卖,6,10,30,2')

    const pending = lines[2].split(',')
    expect(pending).toHaveLength(14)
    expect(pending[0]).toBe('河粉')
    expect(pending[2]).toBe('20')
    expect(pending[3]).toBe('20')
    expect(pending[4]).toBe('手改')
    for (const index of [5, 6, 7, 8, 9, 10, 11, 12, 13]) expect(pending[index]).toBe('')
  })

  it('double 的浮点尾差收成两位小数', () => {
    const csv = buildReviewCsv({
      items: [
        {
          ...ITEM,
          reference_qty: 200.6,
          planned_qty: 210.6,
          sold_qty: 180,
          sold_1h: 194.4,
          sold_2h: 198,
          sold_next_day: 210,
        },
      ],
    })
    const row = csv.slice(1).split('\n')[1]
    expect(row).toBe('虾饺馅,份,200.6,210.6,未手改,180,-20.6,-30.6,13:07,停卖,14.4,18,30,2')
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

  it('质量提示按 context.runtime 的开店/打烊时段算，不再写死 07:00 起', async () => {
    // 10 点、08:00 开门 → 预期只过了 3 个整点；覆盖 2 个不算断档。
    vi.setSystemTime(new Date('2026-11-07T10:00:00+08:00'))
    const quality = JSON.stringify({ order_lines: 420, covered_hours: 2 })
    const payload = withQuality(quality)
    payload.context = { ...payload.context, runtime: { open: '08:00', close: '22:00' } }
    vi.stubGlobal('fetch', probeFetch({ day: payload }))
    const wrapper = await mountView()

    expect(wrapper.find('.pr-quality').exists()).toBe(false)
  })

  it('context 缺 runtime 时仍按缺省 07:00 起算（同一份数据会提示）', async () => {
    vi.setSystemTime(new Date('2026-11-07T10:00:00+08:00'))
    const quality = JSON.stringify({ order_lines: 420, covered_hours: 2 })
    vi.stubGlobal('fetch', probeFetch({ day: withQuality(quality) }))
    const wrapper = await mountView()

    expect(wrapper.get('.pr-quality').text()).toContain('快照只覆盖 2 个钟点')
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
      expect(wrapper.findAll('.pr-compare-review')).toHaveLength(1)
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
