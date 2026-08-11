<script setup>
import { computed } from 'vue'

const props = defineProps({
  series: { type: Array, default: () => [] },
  error: { type: String, default: '' },
})

const width = 520
const height = 108
const pad = 18

const points = computed(() => {
  const s = props.series
  if (!s.length) return null
  const maxRevenue = Math.max(...s.map((i) => Number(i.revenue || 0)), 1)
  const maxOrders = Math.max(...s.map((i) => Number(i.order_lines || 0)), 1)
  const xFor = (idx) => (s.length === 1 ? width / 2 : pad + idx * ((width - pad * 2) / (s.length - 1)))
  const revYFor = (v) => height - pad - (Number(v || 0) / maxRevenue) * (height - pad * 2)
  const ordYFor = (v) => height - pad - (Number(v || 0) / maxOrders) * (height - pad * 2)
  const revenuePoints = s.map((item, i) => `${xFor(i)},${revYFor(item.revenue)}`).join(' ')
  const orderPoints = s.map((item, i) => `${xFor(i)},${ordYFor(item.order_lines)}`).join(' ')
  const dots = s.map((item, i) => ({ cx: xFor(i), cy: revYFor(item.revenue) }))
  const last = s[s.length - 1]
  return { revenuePoints, orderPoints, dots, maxRevenue, last }
})
</script>

<template>
  <div class="chart-wrap">
    <div v-if="error" class="empty-state" style="padding:28px 0">{{ error }}</div>
    <div v-else-if="!points" class="empty-state" style="padding:28px 0">暂无趋势数据</div>
    <svg v-else class="chart-svg" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none">
      <polyline :points="points.revenuePoints" fill="none" stroke="var(--green)" stroke-width="3" vector-effect="non-scaling-stroke"></polyline>
      <polyline :points="points.orderPoints" fill="none" stroke="var(--cyan)" stroke-width="2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"></polyline>
      <circle v-for="(d, i) in points.dots" :key="i" :cx="d.cx" :cy="d.cy" r="2.4" fill="var(--green)"></circle>
      <text :x="pad" y="12" class="axis-label">¥{{ Number(points.maxRevenue).toFixed(0) }}</text>
      <text :x="width - pad - 120" y="12" class="axis-label">末期：¥{{ Number(points.last.revenue || 0).toFixed(0) }} / {{ points.last.order_lines || 0 }}单</text>
    </svg>
  </div>
</template>
