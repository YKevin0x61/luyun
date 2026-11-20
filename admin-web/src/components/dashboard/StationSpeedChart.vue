<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import { useStationsStore } from '../../stores/stations'
import { mergeStationSpeedSeries, summarizeMergedSpeed, topMergedSpeedSlots } from '../../utils/stationSpeed'
import SvgIcon from '../SvgIcon.vue'

// 与旧 public/index.html 一致的具名常量（原 STATION_SPEED_WARNING_THRESHOLD/COLORS，L1506-1520）。
const STATION_SPEED_WARNING_THRESHOLD = 5 // 单/5分钟，超过即视为繁忙告警
const STATION_SPEED_COLORS = [
  '#22d3ee', '#fb923c', '#a78bfa', '#34d399', '#f472b6',
  '#fbbf24', '#60a5fa', '#f87171', '#4ade80', '#e879f9',
  '#38bdf8', '#facc15',
]
// 「合计」视图：所有档口逐槽位相加成一条线（算法见 utils/stationSpeed.js）。
// 合并线沿用调色板第一色 —— 它不与任何档口线同屏（两种视图互斥），不会混淆；
// 也不为这一条线新增裸色值（设计护栏按文件记账，见 styles/__tests__/designGuard.test.js）。
const MERGED_SERIES_NAME = '合计'
const MERGED_SERIES_COLOR = STATION_SPEED_COLORS[0]
// 合计视图的对照线：昨天 / 上周同日 / 上月同日。后端 aggregate_station_speed 已按这三个
// 日期各查一遍（compare_dates），这里只把每个口径的档口数据合并成整店曲线、按虚线画出来。
// 只出现在合计视图 —— 分档口视图不叠（6 个档口各 3 条会把图挤成一团，2026-11-19 定的口径）。
// 三条参考线与主线同线型（同宽、solid、smooth），只有颜色不同：灰=昨天、蓝=上周、黄=上月。
// 颜色取自 CSS 变量，不新增裸色值。
const MERGED_LINE_WIDTH = 3
const COMPARE_SERIES = [
  { type: 'yesterday', label: '昨天', color: '--text-dim' },
  { type: 'last_week', label: '上周', color: '--blue' },
  { type: 'last_month', label: '上月', color: '--yellow' },
]

/** 从主题变量取色：一条参考线不值得新增裸色值（设计护栏按文件记账 hex 出现次数）。 */
function themeColor(name, fallback) {
  if (typeof getComputedStyle !== 'function') return fallback
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}
const MOBILE_BREAKPOINT_PX = 600
const RANK_TOP_N = 6
const PEAK_TOP_N = 4
const RESIZE_DEBOUNCE_MS = 100

const chartRef = ref(null)
const loadError = ref('')
const stationStats = ref([])
// false = 分档口（默认，和图表原先的行为一致）；true = 全部档口合并成一条「合计」曲线。
const mergedMode = ref(false)
// 最近一次接口结果：切换视图只在本地重绘，不再打一次请求。
const speedResult = ref(null)
let chartInstance = null
let echartsLib = null
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

const mergedSummary = computed(() => summarizeMergedSpeed(speedResult.value))
const mergedPeakRows = computed(() => topMergedSpeedSlots(speedResult.value, PEAK_TOP_N))

function isMobileViewport() {
  return window.innerWidth < MOBILE_BREAKPOINT_PX
}

async function loadEcharts() {
  // 只 import 一次：切换「分档口 / 合计」要当场重绘，不该每次都等一次动态 import。
  if (!echartsLib) echartsLib = await import('echarts')
  return echartsLib
}

async function renderChart() {
  const seq = ++renderSeq
  try {
    const result = await api.get('/api/orders/station-speed')
    if (!result.success) throw new Error(result.detail || '档口进单速率加载失败')

    const echarts = await loadEcharts()
    if (seq !== renderSeq || !chartRef.value) return

    const stats = buildStationStats(result)
    speedResult.value = result
    stationStats.value = stats
    if (!chartInstance) chartInstance = echarts.init(chartRef.value, null, { renderer: 'canvas' })
    paintChart(result, stats)
    loadError.value = ''
  } catch (e) {
    // 图表加载失败不影响仪表盘其余部分
    if (seq !== renderSeq) return
    console.warn('档口速率图表加载失败', e)
    loadError.value = e.message || '档口进单速率加载失败'
  }
}

/** 用已经拿到的数据重绘：数据刷新与「分档口 / 合计」切换共用这一条绘制路径。 */
function paintChart(result, stats = buildStationStats(result)) {
  if (!chartInstance) return
  const sortedStations = stats.map((s) => s.stationId)
  const isMobile = isMobileViewport()
    const gridCfg = isMobile
      ? { top: 22, left: 32, right: 8, bottom: 56 }
      : { top: 36, left: 50, right: 20, bottom: 82 }
    const axisFont = isMobile ? 9 : 11
    const legendFont = isMobile ? 9 : 11

    const chartSeries = []
    const legendData = []
    if (mergedMode.value) {
      // 合计口径：各档口今日进单按槽位求和，只画一条线。求和线性可加，口径直接继承后端。
      const merged = mergeStationSpeedSeries(result)
      legendData.push(MERGED_SERIES_NAME)
      // 今日还没收档时，拿「今天到此刻」去比「昨天全天」是不公平的：对比线只画到今日最后
      // 一个出单槽位（再留两槽尾巴），同一时间窗内对比。今天没开单时退回画满全天。
      const todayEnd = merged.data.reduce((end, value, index) => (value > 0 ? index : end), -1)
      const compareEnd = todayEnd >= 0 ? Math.min(todayEnd + 2, merged.data.length - 1) : merged.data.length - 1
      // 先铺对比线、后压今日线：今日线带面积填充，画在上面才不会把历史参考线盖掉。
      COMPARE_SERIES.forEach(({ type, label, color }) => {
        const compare = mergeStationSpeedSeries(result, type)
        // 该对比日零单量时后端不会给系列；合并结果全 0 就跳过，不留一条贴地的死线。
        if (!compare.data.some((value) => value > 0)) return
        const date = result.compare_dates?.[type] || ''
        const name = date ? `${label} ${date.slice(5)}` : label
        const lineColor = themeColor(color, MERGED_SERIES_COLOR)
        legendData.push(name)
        chartSeries.push({
          id: `station-merged-${type}`,
          name,
          type: 'line',
          smooth: true,
          data: compare.data.slice(0, compareEnd + 1),
          lineStyle: { width: MERGED_LINE_WIDTH, color: lineColor },
          itemStyle: { color: lineColor },
          showSymbol: false,
          z: 1,
        })
      })
      chartSeries.push({
        id: 'station-merged',
        name: MERGED_SERIES_NAME,
        type: 'line',
        smooth: true,
        data: merged.data,
        lineStyle: { width: MERGED_LINE_WIDTH },
        itemStyle: { color: MERGED_SERIES_COLOR },
        showSymbol: false,
        areaStyle: { color: MERGED_SERIES_COLOR, opacity: 0.1 },
        z: 3,
      })
    } else {
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
    }

    if (!mergedMode.value) {
      // 警戒线是「单个档口 5 分钟 5 单」的繁忙线：合计口径下整条曲线都会落在红区里，没有信息量，隐藏。
      chartSeries.push({
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
      })
    }

    chartInstance.setOption(
      {
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'line' },
          formatter(params) {
            const timeLabel = result.slots?.[params[0]?.dataIndex] || ''
            // 合计是主角，固定排第一行；对比线跟在后面（分档口视图没有合计系列，顺序不变）。
            const ordered = [...params].sort(
              (a, b) => (a.seriesName === MERGED_SERIES_NAME ? 0 : 1) - (b.seriesName === MERGED_SERIES_NAME ? 0 : 1),
            )
            let html = `<b>${timeLabel}</b><br/>`
            ordered.forEach((param) => {
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
            const base = Math.max(10, value.max + 2, STATION_SPEED_WARNING_THRESHOLD + 2)
            // 合计口径峰值常落在警戒线上方很远，轴顶取整到 10 的倍数，免得出现 43/40 这种紧贴的两档标签。
            return mergedMode.value ? Math.ceil(base / 10) * 10 : base
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
        series: chartSeries,
      },
      { notMerge: false, replaceMerge: ['series'], lazyUpdate: true },
    )
}

/** 切换统计口径：数据已经拿到，只重绘，不重新请求。 */
function setMergedMode(next) {
  if (mergedMode.value === next) return
  mergedMode.value = next
  if (speedResult.value) paintChart(speedResult.value)
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
    <div class="panel-title station-speed-head">
      <span><SvgIcon name="trending-up" :size="16" /> 档口进单速率</span>
      <div class="station-speed-actions">
        <div class="ss-mode" role="group" aria-label="档口统计维度">
          <button
            type="button"
            class="ss-mode-btn"
            :class="{ 'is-on': !mergedMode }"
            :aria-pressed="String(!mergedMode)"
            @click="setMergedMode(false)"
          >
            分档口
          </button>
          <button
            type="button"
            class="ss-mode-btn"
            :class="{ 'is-on': mergedMode }"
            :aria-pressed="String(mergedMode)"
            @click="setMergedMode(true)"
          >
            合计
          </button>
        </div>
        <span class="badge">实时</span>
      </div>
    </div>
    <div v-if="loadError" class="empty-state" style="padding:24px">{{ loadError }}</div>
    <template v-else>
      <div ref="chartRef" style="width:100%;height:280px"></div>
      <div class="station-speed-insights">
        <template v-if="mergedMode">
          <div class="station-speed-insight-card">
            <div class="station-speed-insight-title"><span>合计概览</span><span>全部档口</span></div>
            <div class="ss-summary">
              <div class="ss-summary-row"><span>今日合计</span><b>{{ mergedSummary.total }} 单</b></div>
              <div class="ss-summary-row"><span>峰值</span><b>{{ mergedSummary.peak }} 单/5分钟</b></div>
              <div class="ss-summary-row"><span>峰值时段</span><b>{{ mergedSummary.peakTime }}</b></div>
              <div class="ss-summary-row"><span>活跃时段均值</span><b>{{ mergedSummary.avgActive.toFixed(1) }} 单/5分钟</b></div>
            </div>
          </div>
          <div class="station-speed-insight-card">
            <div class="station-speed-insight-title"><span>峰值时段</span><span>合计口径</span></div>
            <div v-if="!mergedPeakRows.length" class="empty-state ss-empty">暂无峰值数据</div>
            <div v-else class="ss-peak-list">
              <div v-for="item in mergedPeakRows" :key="item.time" class="ss-peak-row">
                <b>{{ item.time }}</b>
                <span>合计 {{ item.value }} 单/5分钟</span>
              </div>
            </div>
          </div>
        </template>
        <template v-else>
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
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.station-speed-head {
  justify-content: space-between;
  flex-wrap: wrap;
  row-gap: 6px;
}
.station-speed-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
/* 分段切换：两个按钮同宽靠内容撑开，选中态只换字色 + 内描边，不引入新色值。 */
.ss-mode {
  display: inline-flex;
  padding: 2px;
  border-radius: 999px;
  background: var(--card2);
  border: 1px solid var(--border);
}
.ss-mode-btn {
  border: 0;
  background: transparent;
  color: var(--text-dim);
  font: inherit;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 6px 11px;
  border-radius: 999px;
  cursor: pointer;
}
.ss-mode-btn.is-on {
  color: var(--text);
  box-shadow: inset 0 0 0 1px var(--accent);
}
.ss-mode-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.station-speed-insights {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 10px;
}
@media (max-width: 700px) {
  .station-speed-insights { grid-template-columns: 1fr; }
  .station-speed-head .badge { display: none; }
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
.ss-summary {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.ss-summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.ss-summary-row > span:first-child {
  color: var(--text-dim);
  font-size: 11px;
}
.ss-peak-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ss-peak-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--card2);
  font-size: 11px;
}
.ss-peak-row > span:last-child {
  color: var(--text-dim);
}
.ss-empty {
  padding: 18px 0;
}
</style>
