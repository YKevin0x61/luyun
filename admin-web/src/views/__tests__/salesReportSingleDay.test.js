// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises, shallowMount } from '@vue/test-utils'

/**
 * 销售报表两处真机问题（2026-11-20 截图）：
 *   1. 默认日期是「今天 → 今天」，走势图只有一个点 + 一个 x 轴标签，等于常驻空图；
 *   2. 口径行写「环比仅供参考」，卡片里却用红色警示色写「环比 -7.6%」，自相矛盾。
 *
 * 这里固定的是"渲染出来给人看的东西"，不是组件内部状态：
 *   单日不画图、给一行说明；跨天正常画图；环比只保留数字、换中性色并写明对比口径。
 */

// useSalesReport 被整体替身：要验的是页面怎么展示，不是取数。写法与 DataTable.test.js
// 一致 —— 先声明状态，再 dynamic import 被测组件，避免 vi.mock 提升后拿不到变量。
const state = {
  startDate: ref('2026-11-20'),
  endDate: ref('2026-11-20'),
  station: ref(''),
  reportData: ref(null),
  dishSalesCache: ref([]),
  semiFinishedCache: ref([]),
  refundStatsCache: ref({}),
  refundDetailCache: ref({ items: [], total: 0 }),
  refundDetailPage: ref(1),
  refundDetailKind: ref('all'),
  refundDetailLoading: ref(false),
  refundDetailError: ref(''),
  changeRefundPage: vi.fn(),
  changeRefundKind: vi.fn(),
  trendSeries: ref([{ day: '2026-11-20', revenue: 1200, orders: 30 }]),
  trendGranularity: ref('day'),
  trendError: ref(''),
  revenueCompareSub: ref('环比 -7.6%'),
  revenueCompareTrend: ref('down'),
  hourSeries: ref([]),
  hourError: ref(''),
  refreshInfo: ref(''),
  loadError: ref(''),
  lastLoadedAt: ref(''),
  isPartialPeriod: ref(false),
  loadReport: vi.fn(async () => {}),
  bindRealtime: vi.fn(),
  teardownRealtime: vi.fn(),
}

vi.mock('../../composables/useSalesReport', () => ({ useSalesReport: () => state }))
vi.mock('../../stores/stations', () => ({
  useStationsStore: () => ({ list: [], load: vi.fn(() => Promise.resolve()) }),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn(() => Promise.resolve()), push: vi.fn() }),
}))
vi.mock('../../api/client', () => ({
  api: { get: vi.fn(async () => ({ dishes: [] })), download: vi.fn() },
}))

const { default: SalesReportView } = await import('../SalesReportView.vue')

// 显式 stub 这两个子组件并声明 props：VTU 的自动 stub 不继承 props，props() 会拿到
// undefined，就验不出页面到底给卡片传了什么（本轮改的正是这个传参）。
const SummaryCardsStub = {
  name: 'SummaryCards',
  props: ['summary', 'topDish', 'refundCount', 'revenueCompareSub', 'revenueCompareTrend'],
  template: '<div class="summary-cards-stub" />',
}
const TrendChartStub = {
  name: 'TrendChart',
  props: ['series', 'error'],
  template: '<div class="trend-chart-stub" />',
}

function mountView() {
  return shallowMount(SalesReportView, {
    global: { stubs: { SummaryCards: SummaryCardsStub, TrendChart: TrendChartStub } },
  })
}

// 走一遍挂载时的异步加载（loadReport → loadFixedDishes → bindRealtime）
async function mountAndSettle() {
  const wrapper = mountView()
  await flushPromises()
  return wrapper
}

// 走势图卡片是 insight-grid 里的第一张卡。
function trendCard(wrapper) {
  return wrapper.findAll('.insight-card')[0]
}

function summaryCardStub(wrapper) {
  const stub = wrapper.findComponent(SummaryCardsStub)
  expect(stub.exists()).toBe(true)
  return {
    compareSub: stub.props('revenueCompareSub'),
    compareTrend: stub.props('revenueCompareTrend'),
  }
}

beforeEach(() => {
  state.startDate.value = '2026-11-20'
  state.endDate.value = '2026-11-20'
  state.revenueCompareSub.value = '环比 -7.6%'
  state.revenueCompareTrend.value = 'down'
  state.isPartialPeriod.value = false
  state.loadError.value = ''
})

describe('销售报表单日走势图', () => {
  it('起止同一天时不画走势图，换成一行说明并指向「时段分析」', async () => {
    const wrapper = await mountAndSettle()
    const card = trendCard(wrapper)

    expect(wrapper.find('.trend-chart-stub').exists()).toBe(false)
    expect(card.find('.badge').exists()).toBe(false)
    expect(card.find('.single-day-note').text()).toContain('单日查询')
    expect(card.find('.single-day-note').text()).toContain('时段分析')
    wrapper.unmount()
  })

  it('跨天时照常画走势图，说明消失', async () => {
    const wrapper = await mountAndSettle()
    state.endDate.value = '2026-11-21'
    await flushPromises()

    expect(wrapper.find('.trend-chart-stub').exists()).toBe(true)
    expect(trendCard(wrapper).find('.single-day-note').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('销售报表环比口径', () => {
  it('保留环比数字，但不给涨跌判断色（不传 revenue-compare-trend）', async () => {
    const wrapper = await mountAndSettle()
    const { compareSub, compareTrend } = summaryCardStub(wrapper)

    expect(compareSub).toContain('环比 -7.6%')
    expect(compareSub).toContain('对比前一个等长周期')
    expect(compareTrend).toBeFalsy()
    wrapper.unmount()
  })

  it('口径行写明环比的对比基准，本期未结束时补一句', async () => {
    const wrapper = await mountAndSettle()
    const note = wrapper.find('.scope-note')

    expect(note.text()).toContain('环比＝与前一个等长周期对比，仅供参考')
    expect(note.text()).not.toContain('本期尚未结束')

    state.isPartialPeriod.value = true
    await flushPromises()
    expect(wrapper.find('.scope-note').text()).toContain('（本期尚未结束）')
    wrapper.unmount()
  })

  it('没有环比数据时不硬塞一句对比口径', async () => {
    state.revenueCompareSub.value = ''
    state.revenueCompareTrend.value = ''
    const wrapper = await mountAndSettle()
    const { compareSub } = summaryCardStub(wrapper)

    expect(compareSub).toBe('')
    wrapper.unmount()
  })
})
