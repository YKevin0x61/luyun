<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import SvgIcon from '../SvgIcon.vue'
import LuyunDatePicker from '../ui/LuyunDatePicker.vue'

// 与旧 public/index.html loadSalesTrend() 一致：默认展示截止日期往前 14 天的营业额走势。
const REVENUE_TREND_DAYS = 14
const RESIZE_DEBOUNCE_MS = 150

function formatDate(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

const chartRef = ref(null)
const endDate = ref(formatDate(new Date()))
const loadFailed = ref(false)
let chartInstance = null
let resizeTimer = null

async function loadEcharts() {
  return await import('echarts')
}

async function renderChart() {
  if (!chartRef.value || !endDate.value) return
  try {
    const end = new Date(`${endDate.value}T12:00:00`)
    const start = new Date(end)
    start.setDate(start.getDate() - (REVENUE_TREND_DAYS - 1))

    const result = await api.get('/api/analytics/sales-trend', {
      granularity: 'day',
      start_date: formatDate(start),
      end_date: endDate.value,
    })
    if (!result.success) throw new Error(result.detail || '趋势加载失败')

    const echarts = await loadEcharts()
    if (!chartRef.value) return
    if (!chartInstance) chartInstance = echarts.init(chartRef.value)

    const series = result.series || []
    chartInstance.setOption(
      {
        backgroundColor: 'transparent',
        grid: { left: 44, right: 12, top: 16, bottom: 28 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: series.map((p) => p.period),
          axisLabel: { color: '#9ca3af', fontSize: 10 },
        },
        yAxis: { type: 'value', axisLabel: { color: '#9ca3af', fontSize: 10 } },
        series: [
          {
            type: 'line',
            smooth: true,
            data: series.map((p) => p.revenue),
            areaStyle: { opacity: 0.12 },
            lineStyle: { color: '#6366f1' },
            itemStyle: { color: '#6366f1' },
          },
        ],
      },
      true,
    )
    loadFailed.value = false
  } catch (e) {
    console.warn('营收趋势加载失败', e)
    loadFailed.value = true
  }
}

function handleResize() {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => chartInstance?.resize(), RESIZE_DEBOUNCE_MS)
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (resizeTimer) clearTimeout(resizeTimer)
  chartInstance?.dispose()
})
</script>

<template>
  <div class="card">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="trending-down" :size="16" /> 营收趋势（近 {{ REVENUE_TREND_DAYS }} 天）</span>
      <LuyunDatePicker v-model="endDate" style="width:130px" @update:model-value="renderChart" aria-label="趋势结束日期" />
    </div>
    <div v-if="loadFailed" class="empty-state" style="padding:24px">趋势加载失败</div>
    <div v-else ref="chartRef" style="width:100%;height:160px"></div>
  </div>
</template>
