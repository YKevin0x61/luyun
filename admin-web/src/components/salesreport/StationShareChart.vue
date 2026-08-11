<script setup>
import { computed } from 'vue'
import { useStationsStore } from '../../stores/stations'

const props = defineProps({
  dishSales: { type: Array, default: () => [] },
})
const stationsStore = useStationsStore()

const rows = computed(() => {
  const totals = {}
  for (const item of props.dishSales) {
    const st = item.station || '未知'
    totals[st] = (totals[st] || 0) + Number(item.total_amount || 0)
  }
  return Object.entries(totals)
    .filter(([, amount]) => amount > 0)
    .sort((a, b) => b[1] - a[1])
})
const total = computed(() => rows.value.reduce((s, [, amt]) => s + amt, 0))
</script>

<template>
  <div>
    <div v-if="!rows.length" class="empty-state" style="padding:28px 0">暂无数据</div>
    <div v-else v-for="[station, amount] in rows" :key="station" class="bar-row">
      <div class="bar-label">{{ station === '未知' ? '未分类' : stationsStore.nameOf(station) }}</div>
      <div class="bar-track">
        <div
          class="bar-fill"
          :style="{ width: `${total ? (amount / total * 100).toFixed(1) : 0}%`, background: stationsStore.colorOf(station) }"
        ></div>
      </div>
      <div class="bar-value">{{ total ? (amount / total * 100).toFixed(1) : '0.0' }}%</div>
    </div>
  </div>
</template>
