<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  series: { type: Array, default: () => [] },
  error: { type: String, default: '' },
})

// 图表按容器实际宽度 1:1 绘制（viewBox 宽 = 容器宽），这样圆点不会被
// 拉成椭圆（审查 V11），窄屏也不会挤成一团。
const wrapRef = ref(null)
const boxWidth = ref(520)
const height = 110
const padX = 14
const padTop = 20
const padBottom = 16
let observer = null

function measure() {
  const w = wrapRef.value?.clientWidth
  if (w) boxWidth.value = Math.max(280, w)
}

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined' && wrapRef.value) {
    observer = new ResizeObserver(measure)
    observer.observe(wrapRef.value)
  } else if (typeof window !== 'undefined') {
    window.addEventListener('resize', measure)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  if (typeof window !== 'undefined') window.removeEventListener('resize', measure)
})

function fmtPeriod(period) {
  const s = String(period ?? '')
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s.slice(5)
  if (/^\d{4}-W\d{2}$/.test(s)) return s.slice(5)
  if (/^\d{4}-\d{2}$/.test(s)) return s.slice(2)
  return s
}

const points = computed(() => {
  const s = props.series
  if (!s.length) return null
  const width = boxWidth.value
  const maxRevenue = Math.max(...s.map((i) => Number(i.revenue || 0)), 1)
  const maxOrders = Math.max(...s.map((i) => Number(i.bills ?? i.order_lines ?? 0)), 1)
  const xFor = (idx) => (s.length === 1 ? width / 2 : padX + idx * ((width - padX * 2) / (s.length - 1)))
  const yFor = (v, max) => height - padBottom - (Number(v || 0) / max) * (height - padTop - padBottom)
  const rows = s.map((item, i) => ({
    x: xFor(i),
    revY: yFor(item.revenue, maxRevenue),
    ordY: yFor(item.bills ?? item.order_lines ?? 0, maxOrders),
    revenue: Number(item.revenue || 0),
    bills: Number(item.bills ?? item.order_lines ?? 0),
    label: fmtPeriod(item.period),
  }))
  const revenuePoints = rows.map((r) => `${r.x},${r.revY}`).join(' ')
  const orderPoints = rows.map((r) => `${r.x},${r.ordY}`).join(' ')
  const axis = []
  for (const idx of [0, Math.floor((rows.length - 1) / 2), rows.length - 1]) {
    if (!axis.some((a) => a.idx === idx)) axis.push({ idx, label: rows[idx].label, x: rows[idx].x })
  }
  return {
    width, rows, revenuePoints, orderPoints, axis,
    maxRevenue,
    last: rows[rows.length - 1],
  }
})
</script>

<template>
  <div ref="wrapRef" class="chart-wrap">
    <div v-if="error" class="empty-state" style="padding:28px 0">{{ error }}</div>
    <div v-else-if="!points" class="empty-state" style="padding:28px 0">暂无趋势数据</div>
    <svg v-else class="chart-svg" :viewBox="`0 0 ${points.width} ${height}`" role="img" aria-label="营业额与订单走势">
      <!-- 图例 -->
      <line :x1="padX" y1="10" :x2="padX + 16" y2="10" stroke="var(--green)" stroke-width="2"></line>
      <text :x="padX + 20" y="13" class="axis-label">营业额</text>
      <line :x1="padX + 66" y1="10" :x2="padX + 82" y2="10" stroke="var(--cyan)" stroke-width="2" stroke-dasharray="4 3"></line>
      <text :x="padX + 86" y="13" class="axis-label">账单数</text>
      <text :x="points.width - padX" y="13" class="axis-label" text-anchor="end">峰值 ¥{{ Number(points.maxRevenue).toFixed(0) }}</text>

      <!-- 基线 -->
      <line :x1="padX" :y1="height - padBottom" :x2="points.width - padX" :y2="height - padBottom" stroke="var(--border)" stroke-width="1"></line>

      <polyline :points="points.revenuePoints" fill="none" stroke="var(--green)" stroke-width="2.5" vector-effect="non-scaling-stroke"></polyline>
      <polyline :points="points.orderPoints" fill="none" stroke="var(--cyan)" stroke-width="1.5" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"></polyline>

      <!-- 每个点带原生 tooltip；单点时半径放大，避免只剩一个小点（审查 R12）。 -->
      <g v-for="(r, i) in points.rows" :key="i">
        <circle :cx="r.x" :cy="r.revY" r="3" fill="var(--green)"></circle>
        <circle :cx="r.x" :cy="r.ordY" r="2.2" fill="var(--cyan)"></circle>
        <circle :cx="r.x" :cy="r.revY" r="10" fill="transparent" class="hit">
          <title>{{ r.label }}：¥{{ r.revenue.toFixed(0) }} / {{ r.bills }} 单</title>
        </circle>
      </g>

      <!-- x 轴日期：首、中、末三个，避免窄屏标签互相压住 -->
      <text v-for="a in points.axis" :key="a.idx" :x="a.x" :y="height - 4" class="axis-label" text-anchor="middle">{{ a.label }}</text>
    </svg>
  </div>
</template>
