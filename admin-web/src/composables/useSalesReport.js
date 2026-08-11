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
  const trendSeries = ref([])
  const trendGranularity = ref('day')
  const trendError = ref('')
  const revenueCompareSub = ref('按订单金额汇总')
  const revenueCompareTrend = ref('') // '' | 'up' | 'down'
  const hourSeries = ref([])
  const hourError = ref('')
  const refreshInfo = ref('')
  const loadError = ref('')

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

  function bindRealtime() {
    setupOrdersSubscription()
    adminNudge.bind()
    watch([startDate, endDate], setupOrdersSubscription)
  }

  function teardownRealtime() {
    ordersNudge.teardown()
    adminNudge.teardown()
  }

  async function loadReport() {
    const start = startDate.value
    const end = endDate.value || start
    refreshInfo.value = '加载中...'
    try {
      await refreshStations?.()
      const data = await api.get('/api/orders/sales-report', {
        start_date: start,
        end_date: end,
        station: station.value || undefined,
      })
      if (!data.success) throw new Error(data.detail || '加载失败')
      reportData.value = data
      dishSalesCache.value = data.dish_sales || []
      semiFinishedCache.value = data.semi_finished || []
      loadError.value = ''
      await Promise.all([loadTrend(start, end), loadRevenueCompare(start, end), loadRefundStats(start, end), loadTimePeriods(start, end)])
      refreshInfo.value = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
    } catch (e) {
      loadError.value = e.message || '加载失败'
      refreshInfo.value = '加载失败: ' + loadError.value
    }
  }

  async function loadRefundStats(start, end) {
    try {
      const data = await api.get('/api/analytics/refunds', { start_date: start, end_date: end, station: station.value || undefined })
      refundStatsCache.value = data.success ? data : null
    } catch (e) {
      refundStatsCache.value = null
    }
  }

  async function loadTrend(start, end) {
    const days = dayDiff(start, end)
    trendGranularity.value = days > 75 ? 'week' : 'day'
    try {
      const data = await api.get('/api/analytics/sales-trend', {
        granularity: trendGranularity.value,
        start_date: start,
        end_date: end,
        station: station.value || undefined,
      })
      if (!data.success) throw new Error(data.detail || '趋势加载失败')
      trendSeries.value = data.series || []
      trendError.value = ''
    } catch (e) {
      trendSeries.value = []
      trendError.value = e.message || '趋势加载失败'
    }
  }

  async function loadRevenueCompare(start, end) {
    const periodDays = dayDiff(start, end)
    const prevEnd = addDays(parseLocalDate(start), -1)
    const prevStart = addDays(prevEnd, 1 - periodDays)
    try {
      const data = await api.get('/api/orders/sales-report', {
        start_date: formatDate(prevStart),
        end_date: formatDate(prevEnd),
        station: station.value || undefined,
      })
      if (!data.success) throw new Error()
      const current = Number(reportData.value?.summary?.total_revenue || 0)
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

  async function loadTimePeriods(start, end) {
    try {
      const data = await api.get('/api/analytics/sales-trend', {
        granularity: 'hour',
        start_date: start,
        end_date: end,
        station: station.value || undefined,
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
    trendSeries, trendGranularity, trendError, revenueCompareSub, revenueCompareTrend, hourSeries, hourError,
    refreshInfo, loadError,
    loadReport, bindRealtime, teardownRealtime,
  }
}
