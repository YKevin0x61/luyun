<script setup>
import { ref } from 'vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import SvgIcon from '../SvgIcon.vue'

const tables = ref([])
const loading = ref(true)
const error = ref('')

async function refresh() {
  try {
    const result = await api.get('/api/tables/live')
    tables.value = result.tables || []
    error.value = ''
  } catch (e) {
    error.value = e.message || '桌台实况加载失败'
  } finally {
    loading.value = false
  }
}

// 订 tables + dashboard，避免仅依赖同页其它订阅才收到桌态相关 nudge
useNudgePull({
  id: 'dashboard-table-live',
  topics: ['tables', 'dashboard'],
  pull: refresh,
  immediate: true,
})

function formatAmount(value) {
  return `¥${Number(value || 0).toFixed(0)}`
}
</script>

<template>
  <div class="card table-live-panel">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="armchair" :size="16" /> 桌台实况</span>
      <span class="badge">{{ tables.length }} 桌占用</span>
    </div>
    <div v-if="loading && !tables.length" class="empty-state">加载桌台中...</div>
    <div v-else-if="error && !tables.length" class="empty-state">{{ error }}</div>
    <div v-else-if="!tables.length" class="empty-state">暂无占用桌台</div>
    <div v-else class="table-live-list luyun-scrollbar">
      <div class="table-live-head">
        <span>桌号</span>
        <span>人数</span>
        <span>金额</span>
        <span>时长</span>
      </div>
      <div v-for="row in tables" :key="row.table_number" class="table-live-row">
        <span class="table-live-number">{{ row.table_number }}</span>
        <span>{{ row.people ?? 0 }}</span>
        <span>{{ formatAmount(row.amount) }}</span>
        <span>{{ row.duration_minutes ?? 0 }} 分</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.table-live-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 360px;
  overflow-y: auto;
}
.table-live-head,
.table-live-row {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr 1fr 0.9fr;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  padding: 4px 8px;
}
.table-live-head {
  color: var(--text-dim);
  font-size: 11px;
}
.table-live-row {
  border-radius: 8px;
  background: var(--card2);
}
.table-live-number {
  font-weight: 600;
}
</style>
