<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

// 与旧 public/index.html 一致的具名常量（原 STATION_SPEED_WARNING_THRESHOLD/COLORS，L1506-1520）。
const STATION_SPEED_WARNING_THRESHOLD = 5 // 单/5分钟，超过即视为繁忙告警
const STATION_SPEED_COLORS = [
  '#22d3ee', '#fb923c', '#a78bfa', '#34d399', '#f472b6',
  '#fbbf24', '#60a5fa', '#f87171', '#4ade80', '#e879f9',
  '#38bdf8', '#facc15',
]
const MOBILE_BREAKPOINT_PX = 600
const RANK_TOP_N = 6
const PEAK_TOP_N = 4
const RESIZE_DEBOUNCE_MS = 100

const chartRef = ref(null)
const loadError = ref('')
const stationStats = ref([])
let chartInstance = null
let resizeTimer = null
let renderSeq = 0
const stationsStore = useStationsStore()

function stationSpeedColor(stationId, index) {
  return stationsStore.colorOf(stationId) || STATION_SPEED_COLORS[index % STATION_SPEED_COLORS.length]
}

function getSeries(result, stationId) {
  return (result.series || []).find((item) => item.station_id === stationId && item.type === 'today')
}

function buildStationStats(result) {
  return (result.stations || [])
    .map((stationId) => {
      const todaySeries = getSeries(result, stationId)
      const values = todaySeries?.data || []
      const total = values.reduce((sum, v) => sum + Number(v || 0), 0)
      const peak = Math.max(...values, 0)
      const peakIndex = values.indexOf(peak)
      const activeSlots = values.filter((v) => Number(v || 0) > 0).length
      return {
        stationId,
        stationName: stationsStore.nameOf(stationId),
        total,
        peak,
        peakTime: peakIndex >= 0 ? result.slots[peakIndex] : '—',
        avgActive: activeSlots ? total / activeSlots : 0,
      }
    })
    .sort((a, b) => b.total - a.total)
}

const rankingRows = computed(() => {
  const maxTotal = Math.max(...stationStats.value.map((s) => s.total), 1)
  return stationStats.value.slice(0, RANK_TOP_N).map((item, index) => ({
    ...item,
    percent: Math.round((item.total / maxTotal) * 100),
    color: stationSpeedColor(item.stationId, index),
  }))
})

const peakRows = computed(() =>
  stationStats.value.filter((item) => item.total > 0).slice(0, PEAK_TOP_N),
)

function isMobileViewport() {
  return window.innerWidth < MOBILE_BREAKPOINT_PX
}

async function loadEcharts() {
  return await import('echarts')
}

async function renderChart() {
  const seq = ++renderSeq
  try {
    const result = await api.get('/api/orders/station-speed')
    if (!result.success) throw new Error(result.detail || '档口进单速率加载失败')

    const stats = buildStationStats(result)
    if (seq !== renderSeq || !chartRef.value) return
    stationStats.value = stats

    const echarts = await loadEcharts()
    if (seq !== renderSeq || !chartRef.value) return
    if (!chartInstance) chartInstance = echarts.init(chartRef.value, null, { renderer: 'canvas' })

    const sortedStations = stats.map((s) => s.stationId)
    const isMobile = isMobileViewport()
    const gridCfg = isMobile
      ? { top: 22, left: 32, right: 8, bottom: 56 }
      : { top: 36, left: 50, right: 20, bottom: 82 }
    const axisFont = isMobile ? 9 : 11
    const legendFont = isMobile ? 9 : 11

    const chartSeries = []
    const legendData = []
    sortedStations.forEach((stationId, index) => {
      const seriesItem = getSeries(result, stationId)
      if (!seriesItem) return
      const name = stationsStore.nameOf(stationId)
      const color = stationSpeedColor(stationId, index)
      legendData.push(name)
      chartSeries.push({
        id: `station-${stationId}`,
        name,
        type: 'line',
        smooth: true,
        data: seriesItem.data,
        lineStyle: { width: 2.5 },
        itemStyle: { color },
        showSymbol: false,
        areaStyle: { color, opacity: 0.06 },
      })
    })

    chartInstance.setOption(
      {
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'line' },
          formatter(params) {
            const timeLabel = result.slots?.[params[0]?.dataIndex] || ''
            let html = `<b>${timeLabel}</b><br/>`
            params.forEach((param) => {
              if (param.value > 0) {
                html += `<span style="display:inline-block;margin-right:5px;border-radius:50%;width:10px;height:10px;background:${param.color};"></span>${param.seriesName}: <b>${param.value}</b> 单<br/>`
              }
            })
            return html || timeLabel
          },
        },
        toolbox: isMobile
          ? { show: false }
          : {
              right: 8,
              top: 0,
              feature: {
                dataZoom: { yAxisIndex: 'none' },
                restore: {},
                saveAsImage: { backgroundColor: '#111827' },
              },
              iconStyle: { borderColor: '#9ca3af' },
            },
        legend: {
          data: legendData,
          bottom: isMobile ? 4 : 24,
          type: isMobile ? 'scroll' : 'plain',
          pageIconColor: '#9ca3af',
          pageTextStyle: { color: '#9ca3af' },
          textStyle: { color: '#9ca3af', fontSize: legendFont },
          inactiveColor: '#4b5563',
          itemWidth: isMobile ? 10 : 14,
          itemHeight: isMobile ? 6 : 10,
          itemGap: isMobile ? 6 : 14,
        },
        grid: gridCfg,
        xAxis: {
          type: 'category',
          data: (result.slots || []).map((label, index) => (index % 12 === 0 ? label : '')),
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: axisFont, interval: isMobile ? 'auto' : 11 },
          splitLine: { show: false },
        },
        yAxis: {
          type: 'value',
          minInterval: 1,
          axisLine: { show: false },
          axisLabel: { color: '#9ca3af', fontSize: axisFont },
          splitLine: { lineStyle: { color: '#1f2937', type: 'dashed' } },
          max(value) {
            return Math.max(10, value.max + 2, STATION_SPEED_WARNING_THRESHOLD + 2)
          },
        },
        dataZoom: isMobile
          ? [{ type: 'inside', xAxisIndex: 0, filterMode: 'none' }]
          : [
              { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
              {
                type: 'slider',
                xAxisIndex: 0,
                height: 18,
                bottom: 0,
                borderColor: '#374151',
                fillerColor: 'rgba(99,102,241,0.18)',
                textStyle: { color: '#9ca3af' },
              },
            ],
        series: [
          ...chartSeries,
          {
            id: 'warning-area',
            name: '警戒区',
            type: 'line',
            data: (result.slots || []).map(() => null),
            lineStyle: { width: 0 },
            itemStyle: { color: 'transparent' },
            showSymbol: false,
            markArea: {
              silent: true,
              data: [
                [
                  { yAxis: STATION_SPEED_WARNING_THRESHOLD, itemStyle: { color: 'rgba(239, 68, 68, 0.15)' } },
                  { yAxis: 9999 },
                ],
              ],
            },
            markLine: {
              silent: true,
              symbol: 'none',
              label: { color: '#ef4444', formatter: `警戒线 ${STATION_SPEED_WARNING_THRESHOLD}` },
              lineStyle: { color: '#ef4444', type: 'dashed', width: 1 },
              data: [{ yAxis: STATION_SPEED_WARNING_THRESHOLD }],
            },
            z: 0,
          },
        ],
      },
      { notMerge: false, lazyUpdate: true },
    )
    if (seq === renderSeq) loadError.value = ''
  } catch (e) {
    // 图表加载失败不影响仪表盘其余部分
    if (seq !== renderSeq) return
    console.warn('档口速率图表加载失败', e)
    loadError.value = e.message || '档口进单速率加载失败'
  }
}

function handleResize() {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    // 移动端断点跨越需要切换 toolbox/legend/grid 布局，做整段重渲染而非单纯 resize()。
    renderChart()
  }, RESIZE_DEBOUNCE_MS)
}

// dashboard nudge 已由 orders/tables 变更合并触发，只订阅一次即可。
useNudgePull({
  id: 'dashboard-station-speed',
  topics: ['dashboard'],
  pull: renderChart,
  immediate: true,
})

onMounted(() => {
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
      <span><SvgIcon name="trending-up" :size="16" /> 档口进单速率</span>
      <span class="badge">实时</span>
    </div>
    <div v-if="loadError" class="empty-state" style="padding:24px">{{ loadError }}</div>
    <template v-else>
      <div ref="chartRef" style="width:100%;height:280px"></div>
      <div class="station-speed-insights">
        <div class="station-speed-insight-card">
          <div class="station-speed-insight-title"><span>档口进单排行</span><span>按今日总单量</span></div>
          <div v-if="!rankingRows.length" class="empty-state" style="padding:18px 0">暂无排行数据</div>
          <div v-else style="display:flex;flex-direction:column;gap:6px">
            <div v-for="(item, index) in rankingRows" :key="item.stationId" style="display:flex;align-items:center;gap:8px;font-size:12px">
              <span style="width:20px;color:var(--text-dim)">#{{ index + 1 }}</span>
              <span style="width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ item.stationName }}</span>
              <div class="bar-track" style="flex:1">
                <div class="bar-fill" :style="{ width: item.percent + '%', background: item.color }"></div>
              </div>
              <b style="width:28px;text-align:right">{{ item.total }}</b>
            </div>
          </div>
        </div>
        <div class="station-speed-insight-card">
          <div class="station-speed-insight-title"><span>峰值分析</span><span>警戒线 {{ STATION_SPEED_WARNING_THRESHOLD }} 单/5分钟</span></div>
          <div v-if="!peakRows.length" class="empty-state" style="padding:18px 0">暂无峰值数据</div>
          <div v-else style="display:flex;flex-direction:column;gap:6px">
            <div
              v-for="item in peakRows"
              :key="item.stationId"
              style="display:flex;flex-direction:column;gap:2px;padding:6px 8px;border-radius:8px;background:var(--card2);font-size:11px"
            >
              <b>{{ item.peakTime }}</b>
              <span style="color:var(--text-dim)">{{ item.stationName }} · 峰值 {{ item.peak }} 单 · 均值 {{ item.avgActive.toFixed(1) }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.station-speed-insights {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 10px;
}
@media (max-width: 700px) {
  .station-speed-insights { grid-template-columns: 1fr; }
}
.station-speed-insight-card {
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px;
}
.station-speed-insight-title {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 8px;
}
</style>
