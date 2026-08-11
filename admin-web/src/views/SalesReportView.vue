<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
import RulesTab from '../components/salesreport/RulesTab.vue'
import TextExportModal from '../components/salesreport/TextExportModal.vue'
import WecomPushModal from '../components/salesreport/WecomPushModal.vue'
import RefundModal from '../components/salesreport/RefundModal.vue'
import ReportDishSettingsModal from '../components/salesreport/ReportDishSettingsModal.vue'
import { api } from '../api/client'
import LuyunDatePicker from '../components/ui/LuyunDatePicker.vue'

const stationsStore = useStationsStore()
const {
  startDate, endDate, station,
  reportData, dishSalesCache, semiFinishedCache, refundStatsCache,
  trendSeries, trendGranularity, trendError, revenueCompareSub, revenueCompareTrend, hourSeries, hourError,
  refreshInfo, loadReport, bindRealtime, teardownRealtime,
} = useSalesReport({ refreshStations: () => stationsStore.load(true) })

const activeTab = ref('report') // 'report' | 'rules'
const fixedDishes = ref([])

const modal = ref(null) // 'text-export' | 'wecom-push' | 'refund' | 'report-dish-settings'
const wecomMode = ref('report')

const topDish = computed(() => dishSalesCache.value[0] || null)
const stationShareTotal = computed(() => {
  const total = dishSalesCache.value.reduce((s, d) => s + Number(d.total_amount || 0), 0)
  return total ? `¥${total.toFixed(0)}` : '--'
})
const textExportContent = computed(() => buildTextReport(reportData.value, fixedDishes.value))
const semiPushContent = computed(() => buildSemiReportText(reportData.value))

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
  loadReport()
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
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `销售报表_${d.date_range.start}_${d.date_range.end}.csv`
  a.click()
}

function exportServerCsv() {
  if (!reportData.value) {
    window.alert('请先查询报表')
    return
  }
  let url = `/api/export/sales-report.csv?start_date=${encodeURIComponent(startDate.value)}&end_date=${encodeURIComponent(endDate.value)}`
  if (station.value) url += `&station=${encodeURIComponent(station.value)}`
  window.location.href = url
}

onMounted(async () => {
  await stationsStore.load()
  await loadReport()
  await loadFixedDishes()
  bindRealtime()
})

onBeforeUnmount(() => {
  teardownRealtime()
})
</script>

<template>
  <div>
    <div class="card" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">
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
        <option value="">全部档口</option>
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
        <div class="insight-card">
          <div class="insight-card-header">
            <h3>档口收入占比</h3>
            <span class="badge">{{ stationShareTotal }}</span>
          </div>
          <div class="insight-card-body"><StationShareChart :dish-sales="dishSalesCache" /></div>
        </div>
        <div class="insight-card">
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
    </div>

    <RulesTab v-else />

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
    <RefundModal v-if="modal === 'refund'" :items="refundStatsCache?.items || []" @close="modal = null" />
    <ReportDishSettingsModal v-if="modal === 'report-dish-settings'" @close="modal = null" />
  </div>
</template>
