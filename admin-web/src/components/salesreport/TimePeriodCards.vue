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

const hasData = computed(() => (props.hourSeries || []).length > 0)

const cards = computed(() => {
  const hourMap = Object.fromEntries((props.hourSeries || []).map((item) => [Number(item.period), item]))
  return BUCKETS.map((bucket) => {
    const revenue = bucket.hours.reduce((sum, h) => sum + Number(hourMap[h]?.revenue || 0), 0)
    // 单量用账单数（bills），与汇总卡「订单数」同一口径；旧接口退回行数。
    const bills = bucket.hours.reduce((sum, h) => sum + Number(hourMap[h]?.bills ?? hourMap[h]?.order_lines ?? 0), 0)
    return { ...bucket, revenue, bills }
  })
})
</script>

<template>
  <div v-if="error" class="empty-state period-empty">{{ error }}</div>
  <div v-else-if="!hasData" class="empty-state period-empty">暂无时段数据</div>
  <template v-else>
    <div class="period-grid">
      <div v-for="card in cards" :key="card.id" class="period-card">
        <div class="name">{{ card.name }}</div>
        <div class="amount">¥{{ card.revenue.toFixed(2) }}</div>
        <div class="meta">{{ card.bills }} 单 · {{ card.hours[0] }}-{{ card.hours[card.hours.length - 1] }}点</div>
      </div>
    </div>
    <p class="period-note">跨时段账单会在各时段分别计入</p>
  </template>

</template>

<style scoped>
/* R47：空数据不再渲染三个 ¥0 卡片；金额保留两位（R41）。 */
.period-empty {
  padding: 28px 0;
}

.period-note {
  margin: 4px 0 0;
  font-size: 10.5px;
  color: var(--text-dim);
}
</style>
