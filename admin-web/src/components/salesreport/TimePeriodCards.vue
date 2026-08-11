<script setup>
import { computed } from 'vue'

const props = defineProps({
  hourSeries: { type: Array, default: () => [] },
  error: { type: String, default: '' },
})

const BUCKETS = [
  { id: 'morning', name: '早茶', hours: [6, 7, 8, 9, 10, 11] },
  { id: 'noon', name: '午市', hours: [12, 13, 14, 15, 16] },
  { id: 'night', name: '晚市', hours: [17, 18, 19, 20, 21, 22, 23] },
]

const cards = computed(() => {
  const hourMap = Object.fromEntries((props.hourSeries || []).map((item) => [Number(item.period), item]))
  return BUCKETS.map((bucket) => {
    const revenue = bucket.hours.reduce((sum, h) => sum + Number(hourMap[h]?.revenue || 0), 0)
    const orders = bucket.hours.reduce((sum, h) => sum + Number(hourMap[h]?.order_lines || 0), 0)
    return { ...bucket, revenue, orders }
  })
})
</script>

<template>
  <div v-if="error" class="empty-state" style="padding:28px 0">{{ error }}</div>
  <div v-else class="period-grid">
    <div v-for="card in cards" :key="card.id" class="period-card">
      <div class="name">{{ card.name }}</div>
      <div class="amount">¥{{ card.revenue.toFixed(0) }}</div>
      <div class="meta">{{ card.orders }} 单 · {{ card.hours[0] }}-{{ card.hours[card.hours.length - 1] }}点</div>
    </div>
  </div>
</template>
