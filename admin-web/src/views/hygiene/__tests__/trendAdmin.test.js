// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

/**
 * 卫生趋势页（`/workbench/floor/trend`，2026-10-05 用户裁定）。
 *
 * 这一页最容易错的两处只有**渲染一遍**才看得准：
 *   1. 数字必须是服务端给的 —— 所以下面那份桩数据里 `totals.pass_rate` 与按 `daily`
 *      现算的结果**故意不一致**，页面上出现哪个就说明它用的是哪一份；`zones` 同理。
 *   2. `pass_rate: null` 不能当 0 —— 所以桩里安排了一天「只有实拍、没有判定」，
 *      断言它既不进点集、也不让折线跨过去（宁可比相邻两天断开）。
 */

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../HygieneTrendView.vue'), 'utf8')

// 窗口 2026-09-06 ~ 2026-10-05（30 天）。daily 只包含**有事件的日子**：
//   09-06 判过（0.8）／09-07 只有实拍 → pass_rate null／09-08~09-10 连判三天／
//   09-25 判过（0.5，落在「前 7 天」里）／10-05 判过（0.9，落在「最近 7 天」里）。
const TREND = {
  days: 30,
  since: '2026-09-06',
  business_date: '2026-10-05',
  daily: [
    { business_date: '2026-09-06', first_pass: 8, rejected: 2, captured: 10, missed: 0, pass_rate: 0.8 },
    { business_date: '2026-09-07', first_pass: 0, rejected: 0, captured: 3, missed: 0, pass_rate: null },
    { business_date: '2026-09-08', first_pass: 6, rejected: 4, captured: 10, missed: 1, pass_rate: 0.6 },
    { business_date: '2026-09-09', first_pass: 7, rejected: 3, captured: 10, missed: 0, pass_rate: 0.7 },
    { business_date: '2026-09-10', first_pass: 9, rejected: 1, captured: 10, missed: 0, pass_rate: 0.9 },
    { business_date: '2026-09-25', first_pass: 5, rejected: 5, captured: 10, missed: 2, pass_rate: 0.5 },
    { business_date: '2026-10-05', first_pass: 9, rejected: 1, captured: 10, missed: 0, pass_rate: 0.9 },
  ],
  zones: [
    // 驳回 6 → 排第一（后端也是驳回降序）。`pass_rate` 给一个跟 18/24 不一样的值：
    // 页面上出现 0.75 就说明它照抄了服务端，而不是自己除了一遍。
    { zone_id: 5, zone_name: '西饼', first_pass: 18, rejected: 6, missed: 3, pass_rate: 0.75 },
    { zone_id: 3, zone_name: '案板', first_pass: 5, rejected: 1, missed: 0, pass_rate: 0.44 },
    // 一次判定都没有的区：显示「—」，不是 0%。
    { zone_id: 7, zone_name: '凉菜', first_pass: 0, rejected: 0, missed: 4, pass_rate: null },
  ],
  reasons: [
    { reason: '玻璃窗有水渍', count: 2 },
    { reason: '没写原因', count: 1 },
  ],
  // 同样故意与 daily 的合计（44 / 16 → 0.733）不一致：这一条压的就是「不自己另算」。
  totals: { first_pass: 44, rejected: 16, captured: 63, missed: 3, pass_rate: 0.5 },
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 挂页面并记录它打过的接口（带查询串，窗口切换要查那个 `days`）。 */
async function mountTrend({ payload = TREND, status = 200 } = {}) {
  const requested = []
  const fetchMock = vi.fn(async (url) => {
    requested.push(String(url))
    if (String(url).startsWith('/api/hygiene/admin/trend')) {
      return status === 200 ? jsonResponse(payload) : jsonResponse({ detail: '趋势算不出来' }, status)
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: HygieneTrendView } = await import('../HygieneTrendView.vue')
  const wrapper = mount(HygieneTrendView)
  await flushPromises()
  return { wrapper, requested }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('卫生趋势 · 登记成现场组的一页', () => {
  it('页面清单、main.py 的 SPA_PAGE_ROUTES、vue-router 三处都在（缺一条就是 404 或白屏）', () => {
    const inventory = JSON.parse(
      readFileSync(join(here, '../../../router/pageRoutes.json'), 'utf8'),
    )
    const row = inventory.pages.find((page) => page.path === '/workbench/floor/trend')
    expect(row).toMatchObject({
      title: '卫生趋势',
      group: 'floor',
      audience: 'admin',
      standalone: true,
      public: false,
    })

    const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')
    expect(mainPy).toMatch(/"\/workbench\/floor\/trend"/)

    const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
    expect(router).toMatch(
      /hygieneAdminPage\('\/workbench\/floor\/trend', 'workbench-floor-trend', \(\) => import\('\.\.\/views\/hygiene\/HygieneTrendView\.vue'\)\)/,
    )
  })

  it('曲线手写 SVG，不引图表库', () => {
    expect(view).toContain('<svg')
    // 按**引用的写法**查（注释里会提到 echarts 这个反面例子，那不是引用）。
    expect(view).not.toMatch(/['"]echarts['"]/)
    expect(view).not.toMatch(/['"]chart\.js['"]/i)
  })

  it('标签页标题自己写：现场壳那份 rail 名单里还没有这一页，认不出来会退回「卫生工作区」', async () => {
    await mountTrend()
    expect(document.title).toBe('卫生趋势 · 工作台')
  })
})

describe('卫生趋势 · 顶部汇总', () => {
  it('一次通过率与三个计数都照抄服务端，不按 daily 现算', async () => {
    const { wrapper } = await mountTrend()
    const text = wrapper.text()

    // 服务端给的是 0.5；按 daily 现算会是 44/60 = 73.3% —— 出现哪个就是用了哪一份。
    expect(text).toContain('50.0%')
    expect(text).not.toContain('73.3%')
    expect(text).toContain('44')
    expect(text).toContain('16')
    // 逾期是另一条轴：单独一格，且明说了不参与通过率。
    expect(text).toContain('逾期（次）· 另一条轴')
    // 窗口首尾与天数都来自服务端（营业日在服务端切，前端不自己算「今天」）。
    expect(text).toContain('2026-09-06 ~ 2026-10-05')
    expect(text).toContain('30 天')
  })

  it('最近 7 天 vs 前 7 天：两段各自把计数相加再相除，不平均每天的率', async () => {
    const { wrapper } = await mountTrend()
    const text = wrapper.get('.trend-wow').text()

    // 最近 7 天只有 10-05：9/(9+1) = 90.0%；前 7 天只有 09-25：5/(5+5) = 50.0%。
    // （把每天的 pass_rate 求平均会得到别的数：那等于给每天一样重。）
    expect(text).toContain('90.0%')
    expect(text).toContain('50.0%')
    expect(text).toContain('▲ 40.0pt')
  })

  it('窗口不足两周时不给这行比较（没有上一段可比）', async () => {
    const { wrapper } = await mountTrend({
      payload: { ...TREND, days: 7, since: '2026-09-29' },
    })
    expect(wrapper.find('.trend-wow').exists()).toBe(false)
  })
})

describe('卫生趋势 · 曲线', () => {
  it('纵轴固定 0% / 50% / 100% 三条参照，横轴只标几个日期', async () => {
    const { wrapper } = await mountTrend()
    const labels = wrapper.findAll('.trend-axis').map((node) => node.text())

    expect(labels).toContain('100%')
    expect(labels).toContain('50%')
    expect(labels).toContain('0%')
    // 横轴的日期（MM-DD）：首日是窗口起点、末日是 business_date。
    expect(labels).toContain('09-06')
    expect(labels).toContain('10-05')
  })

  it('没有判定的那天不进点集，折线也不跨过去（断开，不当 0）', async () => {
    const { wrapper } = await mountTrend()

    // 6 个有判定的日子（09-07 那天只有实拍、pass_rate 是 null，不在其中）。
    expect(wrapper.findAll('.trend-dot')).toHaveLength(6)
    // 分段：09-06 孤点 ／ 09-08~09-10 连成一段 ／ 09-25 孤点 ／ 10-05 孤点。
    expect(wrapper.findAll('.trend-line')).toHaveLength(4)
    // 那天连一个点都不该有：把它当 0% 画会变成「全军覆没」。
    expect(wrapper.find('svg').text()).not.toContain('2026-09-07')
    expect(wrapper.findAll('.trend-dot title').map((node) => node.text()).join(' '))
      .not.toContain('2026-09-07')
  })

  it('每个点都带那天的四个数（逾期也在里面，方便解释曲线）', async () => {
    const { wrapper } = await mountTrend()
    const titles = wrapper.findAll('.trend-dot title').map((node) => node.text())
    expect(titles[0]).toContain('2026-09-06')
    expect(titles[0]).toContain('80.0%')
    expect(titles[0]).toContain('一次通过 8')
    expect(titles[0]).toContain('驳回 2')
    expect(titles[0]).toContain('实拍 10')
    expect(titles[0]).toContain('逾期 0')
  })

  it('整窗口一条事件都没有时给一句话，不画空图', async () => {
    const { wrapper } = await mountTrend({
      payload: { ...TREND, daily: [], totals: { first_pass: 0, rejected: 0, captured: 0, missed: 0, pass_rate: null } },
    })

    expect(wrapper.find('svg').exists()).toBe(false)
    expect(wrapper.text()).toContain('一条验收事件都没有')
    // 没有判定 → 通过率是「—」而不是 0%。
    expect(wrapper.get('.trend-headline').text()).toContain('—')
    expect(wrapper.get('.trend-headline').text()).not.toContain('0.0%')
  })
})

describe('卫生趋势 · 两张小表', () => {
  it('按区表按驳回降序，率照抄服务端，没判定的区留「—」', async () => {
    const { wrapper } = await mountTrend()
    const rows = wrapper.findAll('.trend-table tbody tr').map((tr) => tr.findAll('td').map((td) => td.text()))

    expect(rows).toEqual([
      ['西饼', '18', '6', '3', '75.0%'],
      ['案板', '5', '1', '0', '44.0%'],
      ['凉菜', '0', '0', '4', '—'],
    ])
  })

  it('驳回原因一行一条：原因 + 次数', async () => {
    const { wrapper } = await mountTrend()
    const rows = wrapper.findAll('.reason-list li').map((li) => li.text())

    expect(rows).toEqual(['玻璃窗有水渍2', '没写原因1'])
  })

  it('空态各自给一句话', async () => {
    const { wrapper } = await mountTrend({
      payload: { ...TREND, daily: [], zones: [], reasons: [] },
    })
    expect(wrapper.text()).toContain('没有工作区的判定记录')
    expect(wrapper.text()).toContain('没有被驳回的记录')
  })
})

describe('卫生趋势 · 窗口切换', () => {
  it('默认 30 天，切到 7 / 84 天各按新窗口重取', async () => {
    const { wrapper, requested } = await mountTrend()

    expect(requested[0]).toContain('days=30')
    expect(wrapper.get('[aria-pressed="true"]').text()).toContain('30 天')

    const buttons = wrapper.findAll('.trend-range button')
    expect(buttons.map((button) => button.text())).toEqual(['7 天', '30 天', '84 天'])

    await buttons[0].trigger('click')
    await flushPromises()
    expect(requested[1]).toContain('days=7')
    expect(wrapper.get('[aria-pressed="true"]').text()).toContain('7 天')

    await wrapper.findAll('.trend-range button')[2].trigger('click')
    await flushPromises()
    expect(requested[2]).toContain('days=84')
  })

  it('取不到数时报错并让出重试，不留上一份数字', async () => {
    const { wrapper } = await mountTrend({ status: 500 })

    expect(wrapper.get('.roster-error').text()).toContain('趋势算不出来')
    expect(wrapper.find('svg').exists()).toBe(false)
    expect(wrapper.get('.trend-headline').text()).toContain('—')
  })
})
