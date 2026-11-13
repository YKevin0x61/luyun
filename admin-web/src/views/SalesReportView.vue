<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useStationsStore } from '../stores/stations'
import { useSalesReport } from '../composables/useSalesReport'
import { quickRange } from '../utils/dateRange'
import { buildTextReport, buildSemiReportText, downloadTextFile } from '../utils/salesReportText'
import SvgIcon from '../components/SvgIcon.vue'
import SummaryCards from '../components/salesreport/SummaryCards.vue'
import TrendChart from '../components/salesreport/TrendChart.vue'
import StationShareChart from '../components/salesreport/StationShareChart.vue'
import TimePeriodCards from '../components/salesreport/TimePeriodCards.vue'
import DishSalesTable from '../components/salesreport/DishSalesTable.vue'
import SemiFinishedTable from '../components/salesreport/SemiFinishedTable.vue'
import RulesReadonlyTable from '../components/salesreport/RulesReadonlyTable.vue'
import TextExportModal from '../components/salesreport/TextExportModal.vue'
import WecomPushModal from '../components/salesreport/WecomPushModal.vue'
import RefundModal from '../components/salesreport/RefundModal.vue'
import RefundDetailTable from '../components/salesreport/RefundDetailTable.vue'
import ReportDishSettingsModal from '../components/salesreport/ReportDishSettingsModal.vue'
import { api } from '../api/client'
import LuyunDatePicker from '../components/ui/LuyunDatePicker.vue'

const stationsStore = useStationsStore()
const {
  startDate, endDate, station,
  reportData, dishSalesCache, semiFinishedCache, refundStatsCache,
  refundDetailCache, refundDetailPage, refundDetailKind, refundDetailLoading, refundDetailError,
  changeRefundPage, changeRefundKind,
  trendSeries, trendGranularity, trendError, revenueCompareSub, revenueCompareTrend, hourSeries, hourError,
  refreshInfo, loadError, lastLoadedAt, isPartialPeriod, loadReport, bindRealtime, teardownRealtime,
} = useSalesReport({ refreshStations: () => stationsStore.load(true) })

const route = useRoute()
const router = useRouter()

// 筛选条件写进 URL（审查 R27）：刷新/分享/前进后退都还原同一份报表。
// 只在 URL 显式带了合法值时才覆盖默认「今天」，避免空串把页面打空。
if (typeof route.query.start === 'string' && route.query.start) startDate.value = route.query.start
if (typeof route.query.end === 'string' && route.query.end) endDate.value = route.query.end
if (typeof route.query.station === 'string' && route.query.station) station.value = route.query.station

const activeTab = ref('report') // 'report' | 'rules'
const fixedDishes = ref([])
const refundSection = ref(null)

const modal = ref(null) // 'text-export' | 'wecom-push' | 'refund' | 'report-dish-settings'
const wecomMode = ref('report')

const topDish = computed(() => dishSalesCache.value[0] || null)
const stationShareTotal = computed(() => {
  const total = dishSalesCache.value.reduce((s, d) => s + Number(d.total_amount || 0), 0)
  return total ? `¥${total.toFixed(0)}` : '--'
})
const textExportContent = computed(() => buildTextReport(reportData.value, fixedDishes.value))
const semiPushContent = computed(() => buildSemiReportText(reportData.value))
const scopeLabel = computed(() => {
  const scope = reportData.value?.scope
  if (scope === 'full') return '全店（含楼面套餐/茶位）'
  if (scope === 'station') return '指定档口'
  return '厨房出品（不含楼面）'
})

async function loadFixedDishes() {
  try {
    const data = await api.get('/api/report-dishes/')
    fixedDishes.value = data.dishes || []
  } catch (e) { /* 固定菜品设置加载失败不影响其余报表功能 */ }
}

function setQuick(type) {
  const { start, end } = quickRange(type)
  startDate.value = start
  endDate.value = end
}

function switchTab(tab) {
  activeTab.value = tab
}

function openTextExport() {
  if (!reportData.value) {
    window.alert('请先查询报表数据')
    return
  }
  modal.value = 'text-export'
}

function openWecom(mode) {
  if (!reportData.value) {
    window.alert('请先查询报表数据')
    return
  }
  wecomMode.value = mode
  modal.value = 'wecom-push'
}

// CSV 公式注入防护：Excel 会把 = + - @ 开头的单元格当公式执行（审查 R42）。
function csvCell(value) {
  let text = String(value ?? '')
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text
  return `"${text.replace(/"/g, '""')}"`
}

function exportCsvClient() {
  if (!reportData.value) {
    window.alert('请先查询报表')
    return
  }
  const d = reportData.value
  const rows = [['类型', '名称', '档口/岗位', '数量', '金额', '单位', '子分类']]
  ;(d.dish_sales || []).forEach((item) => {
    rows.push(['菜品', item.dish_name, item.station || '', item.qty, item.total_amount ?? '', '', ''])
  })
  ;(d.semi_finished || []).forEach((block) => {
    ;(block.items || []).forEach((item) => {
      rows.push(['半成品', item.semi_name, block.position || '', item.qty, '', item.unit || '', item.sub_category || item.category || ''])
    })
  })
  const csv = rows.map((r) => r.map(csvCell).join(',')).join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `销售报表_${d.date_range.start}_${d.date_range.end}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

async function exportServerCsv() {
  if (!reportData.value) {
    window.alert('请先查询报表')
    return
  }
  const params = new URLSearchParams({
    start_date: startDate.value,
    end_date: endDate.value || startDate.value,
  })
  if (station.value) params.set('station', station.value)
  try {
    // 走 fetch + blob：422/500 在页面上提示，而不是把整页导航到 JSON 错误体（审查 R6）。
    await api.download(
      `/api/export/sales-report.csv?${params.toString()}`,
      `销售报表_${startDate.value}_${endDate.value || startDate.value}.csv`,
    )
  } catch (e) {
    window.alert('导出失败：' + (e.message || '未知错误'))
  }
}

function showRefundDetail() {
  modal.value = null
  refundSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function syncQuery() {
  const query = {}
  if (startDate.value) query.start = startDate.value
  if (endDate.value) query.end = endDate.value
  if (station.value) query.station = station.value
  router.replace({ query }).catch(() => {})
}

// R28/R27：改日期或档口后自动查询，并把条件同步进地址栏。
watch([startDate, endDate, station], () => {
  if (!startDate.value) return
  syncQuery()
  loadReport()
})

onMounted(async () => {
  // R53：页面原先没有 H1、document.title 也不带页面名，读屏器和浏览器标签都认不出这是哪一页。
  document.title = '销售报表 · 厨务管家'
  // 首次进入也把默认「今天」写进 URL，刷新/分享才不会丢筛选条件（R27）。
  syncQuery()
  // 档口列表失败只影响下拉选择，不能阻断整张报表（审查 R7）。
  stationsStore.load().catch(() => {})
  await loadReport()
  await loadFixedDishes()
  bindRealtime()
})

onBeforeUnmount(() => {
  teardownRealtime()
  document.title = '厨务管家'
})
</script>

<template>
  <div>
    <h1 class="sr-only">销售报表</h1>
    <div v-if="activeTab === 'report'" class="card" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <label style="font-size:12px;color:var(--text-dim)">日期</label>
      <LuyunDatePicker v-model="startDate" style="width:150px" />
      <label style="font-size:12px;color:var(--text-dim)">至</label>
      <LuyunDatePicker v-model="endDate" style="width:150px" />
      <div style="display:flex;gap:4px">
        <button class="btn btn-sm" @click="setQuick('today')">今天</button>
        <button class="btn btn-sm" @click="setQuick('yesterday')">昨天</button>
        <button class="btn btn-sm" @click="setQuick('week')">近7天</button>
        <button class="btn btn-sm" @click="setQuick('month')">本月</button>
        <button class="btn btn-sm" @click="setQuick('lastWeek')">上周</button>
        <button class="btn btn-sm" @click="setQuick('lastMonth')">上月</button>
      </div>
      <label style="font-size:12px;color:var(--text-dim)">档口</label>
      <select class="select" v-model="station">
        <option value="">厨房出品（不含楼面）</option>
        <option value="all">全店（含楼面）</option>
        <option v-for="s in stationsStore.list" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <button class="btn btn-primary btn-sm" @click="loadReport"><SvgIcon name="search" :size="13" /> 查询报表</button>
      <button class="btn btn-sm" @click="loadReport" title="立即刷新"><SvgIcon name="refresh-cw" :size="13" /> 刷新</button>
      <button class="btn btn-sm" @click="exportCsvClient"><SvgIcon name="download" :size="13" /> 导出CSV</button>
      <button class="btn btn-sm" @click="exportServerCsv"><SvgIcon name="download" :size="13" /> 服务端导出</button>
      <button class="btn btn-sm" @click="openTextExport"><SvgIcon name="clipboard" :size="13" /> 复制文字版</button>
      <button class="btn btn-sm" @click="modal = 'report-dish-settings'"><SvgIcon name="settings" :size="13" /> 固定菜品</button>
      <span style="font-size:11px;color:var(--text-dim);margin-left:auto">{{ refreshInfo }}</span>
    </div>

    <div class="view-tabs">
      <button class="view-tab" :class="{ active: activeTab === 'report' }" @click="switchTab('report')"><SvgIcon name="bar-chart" :size="13" /> 报表数据</button>
      <button class="view-tab" :class="{ active: activeTab === 'rules' }" @click="switchTab('rules')"><SvgIcon name="settings" :size="13" /> 换算规则</button>
    </div>

    <div v-if="loadError" class="dash-error-banner load-error-banner">
      <SvgIcon name="alert-triangle" :size="14" />
      <span>
        {{ loadError.startsWith('请选择') ? loadError : '加载失败：' + loadError }}<template v-if="reportData">（下方为 {{ lastLoadedAt || '上次' }} 的旧数据）</template>
      </span>
      <button class="btn btn-sm" @click="loadReport">重试</button>
    </div>

    <div v-if="activeTab === 'report'" class="scope-note">
      口径：{{ scopeLabel }}<template v-if="isPartialPeriod"> · 本期尚未结束，环比仅供参考</template>
    </div>

    <div v-if="activeTab === 'report'" style="display:flex;flex-direction:column;gap:8px">
      <SummaryCards
        :summary="reportData?.summary"
        :top-dish="topDish"
        :refund-count="refundStatsCache?.refund_line_count ?? '--'"
        :revenue-compare-sub="revenueCompareSub"
        :revenue-compare-trend="revenueCompareTrend"
        @open-refunds="modal = 'refund'"
      />

      <div class="insight-grid">
        <div class="insight-card">
          <div class="insight-card-header">
            <h3>营业额 / 订单走势</h3>
            <span class="badge">{{ trendGranularity === 'week' ? '按周' : '按日' }}</span>
          </div>
          <div class="insight-card-body"><TrendChart :series="trendSeries" :error="trendError" /></div>
        </div>
        <div class="insight-card insight-card--share">
          <div class="insight-card-header">
            <h3>档口收入占比</h3>
            <span class="badge">{{ stationShareTotal }}</span>
          </div>
          <div class="insight-card-body"><StationShareChart :dish-sales="dishSalesCache" /></div>
        </div>
        <div class="insight-card insight-card--period">
          <div class="insight-card-header">
            <h3>时段分析</h3>
            <span class="badge">早 / 午 / 晚</span>
          </div>
          <div class="insight-card-body"><TimePeriodCards :hour-series="hourSeries" :error="hourError" /></div>
        </div>
      </div>

      <div class="two-col">
        <DishSalesTable :dishes="dishSalesCache" />
        <SemiFinishedTable :semi-finished="semiFinishedCache" :report-data="reportData" @push="openWecom('semi')" />
      </div>

      <div ref="refundSection">
      <RefundDetailTable
        :items="refundDetailCache.items"
        :total="refundDetailCache.total"
        :page="refundDetailPage"
        :page-size="refundDetailCache.page_size || 50"
        :summary="refundDetailCache.summary"
        :kind="refundDetailKind"
        :loading="refundDetailLoading"
        :error="refundDetailError"
        @page-change="changeRefundPage"
        @kind-change="changeRefundKind"
      />
      </div>
    </div>

    <RulesReadonlyTable v-else />

    <TextExportModal
      v-if="modal === 'text-export'"
      :content="textExportContent"
      @close="modal = null"
      @push="openWecom('report')"
    />
    <WecomPushModal
      v-if="modal === 'wecom-push'"
      :mode="wecomMode"
      :content="wecomMode === 'semi' ? semiPushContent : textExportContent"
      @close="modal = null"
    />
    <RefundModal
      v-if="modal === 'refund'"
      :items="refundStatsCache?.items || []"
      @close="modal = null"
      @show-detail="showRefundDetail"
    />
    <ReportDishSettingsModal
      v-if="modal === 'report-dish-settings'"
      @close="modal = null"
      @changed="loadFixedDishes"
    />
  </div>
</template>

<style scoped>
/* 「档口收入占比」允许随内容长高。`theme.css` 给 `.insight-card-body` 定的是固定
   `height: 110px`，那是为走势图/时段卡这类"一张图占满"的内容定的；档口数多于 4 个时
   这张列表就会在第 5 行被从字形中间切断、第 6–7 项完全看不到（真机实测 7 个档口只排到
   第 5 行，卡片总高 151px，也没有任何展开或滚动提示）。这里只放开这一张卡，其余卡片
   维持 110px。 */
.insight-card--share .insight-card-body {
  height: auto;
  max-height: 240px;
}

/* R8：≤1100px 时段卡变一列后，主题里 110px 的固定 body 高度会把晚市裁掉。 */
.insight-card--period .insight-card-body {
  height: auto;
  max-height: 240px;
}

/* R10/R34：并排两卡不再被 grid 等高拉满（菜品表 109 行会把半成品卡撑出
   2000px 空白，并把异常明细推到第 9 屏）；菜品表自己内部滚动。 */
.two-col {
  align-items: start;
  min-height: 0;
}

.two-col .table-card {
  height: auto;
}

/* R9/R31：窄屏横滑时菜名固定在左侧，sticky 表头也因为表格有了滚动容器而生效。 */
@media (max-width: 900px) {
  :deep(.data-table th:nth-child(2)),
  :deep(.data-table td:nth-child(2)) {
    position: sticky;
    left: 0;
    z-index: var(--z-tabbar);
    background: var(--card);
  }
}

/* R15：移动端触控目标不小于 44px（按钮与下拉）。 */
@media (max-width: 900px) {
  :deep(.btn),
  :deep(.select) {
    min-height: 44px;
  }
}

/* R13：本页不在工作台命名空间下，--focus-ring 未定义会让 focus-visible 直接 outline:none。 */
:deep(:focus-visible) {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* 主接口失败时把「旧数据」和「新结果」明确分开：以前只有右上角一行小字，
   用户会把没刷新的数据当成刷新后的结果（审查 R5）。配色复用主题里的
   .dash-error-banner，这里只补布局。 */
.load-error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
}

.load-error-banner .btn {
  flex-shrink: 0;
  white-space: nowrap;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.scope-note {
  margin: 0 0 8px;
  font-size: 11px;
  color: var(--text-dim);
}
</style>
