import { ref, watch } from 'vue'
import { api } from '../api/client'
import { addDays, dayDiff, formatDate, parseLocalDate } from '../utils/dateRange'
import { useNudgePull } from './useNudgePull'

const REALTIME_DEBOUNCE_MS = 500

export function useSalesReport({ refreshStations } = {}) {
  const today = formatDate(new Date())
  const startDate = ref(today)
  const endDate = ref(today)
  const station = ref('')

  const reportData = ref(null)
  const dishSalesCache = ref([])
  const semiFinishedCache = ref([])
  const refundStatsCache = ref(null)
  // 退菜明细（商龙退单）与上面的 refundStatsCache 是两套口径：
  // 前者读 refund_records（一手明细，带份数/原因/操作人），后者读 orders 软取消行。
  const refundDetailCache = ref({ items: [], total: 0, page: 1, page_size: 50, summary: null })
  const refundDetailPage = ref(1)
  // '' = 退单 + 赠单；'co' = 只看退单；'present' = 只看赠单。
  const refundDetailKind = ref('')
  const refundDetailLoading = ref(false)
  const refundDetailError = ref('')
  const trendSeries = ref([])
  const trendGranularity = ref('day')
  const trendError = ref('')
  const revenueCompareSub = ref('按订单金额汇总')
  const revenueCompareTrend = ref('') // '' | 'up' | 'down'
  const hourSeries = ref([])
  const hourError = ref('')
  const refreshInfo = ref('')
  const loadError = ref('')
  const lastLoadedAt = ref('')
  // 结束日期含今天时，今天的营业日尚未结束（06:00 才切），汇总/环比都不完整。
  const isPartialPeriod = ref(false)
  // 请求序号：日期/档口连续切换时，只让最后一次请求落地（审查 R19）。
  let loadSeq = 0

  function isTodayRange() {
    const today = formatDate(new Date())
    const start = startDate.value
    const end = endDate.value || start
    return start <= today && end >= today
  }

  const ordersNudge = useNudgePull({
    id: 'sales-report-orders',
    topics: ['orders'],
    pull: () => {
      if (isTodayRange()) loadReport()
    },
    filters: { date: formatDate(new Date()) },
    debounceMs: REALTIME_DEBOUNCE_MS,
    fallback: { when: () => isTodayRange() },
    manual: true,
  })

  // 档口映射/换算规则/固定菜品等后台配置变更会影响任意日期的报表，
  // 因此 admin nudge 不像 orders nudge 那样受 isTodayRange() 限制。
  const adminNudge = useNudgePull({
    id: 'sales-report-admin',
    topics: ['admin'],
    pull: () => loadReport(),
    debounceMs: REALTIME_DEBOUNCE_MS,
    fallback: false,
    manual: true,
  })

  function setupOrdersSubscription() {
    if (!isTodayRange()) {
      ordersNudge.teardown()
      return
    }
    ordersNudge.setFilters({ date: formatDate(new Date()) })
    ordersNudge.bind()
  }

  // watch 句柄必须留着：原来每次 bindRealtime 都新建一个永不停止的 watch，
  // 页面二次挂载后改日期会叠加订阅并重复拉报表（审查 R20）。
  let stopDateWatch = null

  function bindRealtime() {
    setupOrdersSubscription()
    adminNudge.bind()
    if (!stopDateWatch) stopDateWatch = watch([startDate, endDate], setupOrdersSubscription)
  }

  function teardownRealtime() {
    ordersNudge.teardown()
    adminNudge.teardown()
    if (stopDateWatch) {
      stopDateWatch()
      stopDateWatch = null
    }
  }

  async function loadReport() {
    const start = startDate.value
    const end = endDate.value || start
    if (!start) {
      loadError.value = '请选择开始日期'
      refreshInfo.value = '请选择开始日期'
      return
    }
    const seq = ++loadSeq
    refreshInfo.value = '加载中...'
    try {
      // 档口列表失败只影响下拉选择，不能阻断整张报表（本页审查 R7）。
      try {
        await refreshStations?.()
      } catch (e) {
        /* 忽略：报表本身不依赖档口列表 */
      }
      // 用入口时的快照，避免请求途中切档口把 A 档口的结果标成 B 档口（审查 R19）。
      const stationSnapshot = station.value
      const data = await api.get('/api/orders/sales-report', {
        start_date: start,
        end_date: end,
        station: stationSnapshot || undefined,
      })
      if (seq !== loadSeq) return
      if (!data.success) throw new Error(data.detail || '加载失败')
      reportData.value = data
      dishSalesCache.value = data.dish_sales || []
      semiFinishedCache.value = data.semi_finished || []
      loadError.value = ''
      lastLoadedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      isPartialPeriod.value = end >= formatDate(new Date())
      await Promise.all([
        loadTrend(start, end, stationSnapshot),
        loadRevenueCompare(start, end, data.summary?.total_revenue, stationSnapshot),
        loadRefundStats(start, end, stationSnapshot),
        loadTimePeriods(start, end, stationSnapshot),
        loadRefundDetail(start, end, 1),
      ])
      if (seq !== loadSeq) return
      refreshInfo.value = `更新于 ${lastLoadedAt.value}`
    } catch (e) {
      if (seq !== loadSeq) return
      loadError.value = e.message || '加载失败'
      refreshInfo.value = '加载失败: ' + loadError.value
    }
  }

  async function loadRefundStats(start, end, stationFilter) {
    try {
      const data = await api.get('/api/analytics/refunds', {
        start_date: start,
        end_date: end,
        station: stationFilter || undefined,
      })
      refundStatsCache.value = data.success ? data : null
    } catch (e) {
      refundStatsCache.value = null
    }
  }

  async function loadRefundDetail(start, end, page = 1, kind = refundDetailKind.value) {
    refundDetailLoading.value = true
    try {
      const params = { start_date: start, end_date: end, page, page_size: 50 }
      if (kind) params.kind = kind
      const data = await api.get('/api/analytics/refunds/detail', params)
      if (!data.success) throw new Error(data.detail || '退菜明细加载失败')
      refundDetailCache.value = data
      refundDetailPage.value = data.page || page
      refundDetailError.value = ''
    } catch (e) {
      refundDetailCache.value = { items: [], total: 0, page, page_size: 50, summary: null }
      refundDetailError.value = e.message || '退菜明细加载失败'
    } finally {
      refundDetailLoading.value = false
    }
  }

  function changeRefundPage(page) {
    const start = startDate.value
    const end = endDate.value || start
    loadRefundDetail(start, end, page)
  }

  function changeRefundKind(kind) {
    if (refundDetailKind.value === kind) return
    refundDetailKind.value = kind
    const start = startDate.value
    const end = endDate.value || start
    loadRefundDetail(start, end, 1, kind)
  }

  async function loadTrend(start, end, stationFilter) {
    const days = dayDiff(start, end)
    trendGranularity.value = days > 75 ? 'week' : 'day'
    try {
      const data = await api.get('/api/analytics/sales-trend', {
        granularity: trendGranularity.value,
        start_date: start,
        end_date: end,
        station: stationFilter || undefined,
      })
      if (!data.success) throw new Error(data.detail || '趋势加载失败')
      trendSeries.value = data.series || []
      trendError.value = ''
    } catch (e) {
      trendSeries.value = []
      trendError.value = e.message || '趋势加载失败'
    }
  }

  async function loadRevenueCompare(start, end, currentRevenue, stationFilter) {
    const periodDays = dayDiff(start, end)
    const prevEnd = addDays(parseLocalDate(start), -1)
    const prevStart = addDays(prevEnd, 1 - periodDays)
    try {
      const data = await api.get('/api/orders/sales-report', {
        start_date: formatDate(prevStart),
        end_date: formatDate(prevEnd),
        station: stationFilter || undefined,
      })
      if (!data.success) throw new Error()
      // 本期金额用入口快照传入，不再从 reportData 里读（连续切换时可能已被下一条覆盖）。
      const current = Number(currentRevenue || 0)
      const previous = Number(data.summary?.total_revenue || 0)
      if (!previous) {
        revenueCompareSub.value = current ? '上一周期无销售额' : '按订单金额汇总'
        revenueCompareTrend.value = ''
        return
      }
      const rate = ((current - previous) / previous) * 100
      revenueCompareSub.value = `${rate >= 0 ? '环比 +' : '环比 '}${rate.toFixed(1)}%`
      revenueCompareTrend.value = rate >= 0 ? 'up' : 'down'
    } catch (e) {
      revenueCompareSub.value = '按订单金额汇总'
      revenueCompareTrend.value = ''
    }
  }

  async function loadTimePeriods(start, end, stationFilter) {
    try {
      const data = await api.get('/api/analytics/sales-trend', {
        granularity: 'hour',
        start_date: start,
        end_date: end,
        station: stationFilter || undefined,
      })
      if (!data.success) throw new Error(data.detail || '时段加载失败')
      hourSeries.value = data.series || []
      hourError.value = ''
    } catch (e) {
      hourSeries.value = []
      hourError.value = e.message || '时段加载失败'
    }
  }

  return {
    startDate, endDate, station,
    reportData, dishSalesCache, semiFinishedCache, refundStatsCache,
    refundDetailCache, refundDetailPage, refundDetailKind, refundDetailLoading, refundDetailError,
    changeRefundPage, changeRefundKind,
    trendSeries, trendGranularity, trendError, revenueCompareSub, revenueCompareTrend, hourSeries, hourError,
    refreshInfo, loadError, lastLoadedAt, isPartialPeriod,
    loadReport, bindRealtime, teardownRealtime,
  }
}
