<script setup>
import { computed } from 'vue'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const KDS_MEDIUM = 8
const KDS_HIGH = 15

const props = defineProps({
  backlog: { type: Object, default: null },
})

const stationsStore = useStationsStore()

const stations = computed(() => props.backlog?.stations || [])

function loadClass(level) {
  if (level === 'high') return 'red'
  if (level === 'medium') return 'yellow'
  return ''
}

function loadLabel(pending) {
  if (pending >= KDS_HIGH) return '高负载'
  if (pending >= KDS_MEDIUM) return '繁忙'
  return ''
}
</script>

<template>
  <div class="card">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="chef-hat" :size="16" /> 出餐压力</span>
      <span class="badge">≥{{ KDS_MEDIUM }} 繁忙 · ≥{{ KDS_HIGH }} 高负载</span>
    </div>
    <div v-if="!stations.length" class="empty-state">当前无待出餐</div>
    <div v-else class="kds-backlog-list luyun-scrollbar">
      <div
        v-for="item in stations"
        :key="item.station_id || 'unknown'"
        class="kds-backlog-row"
      >
        <span class="kds-backlog-name">{{ stationsStore.nameOf(item.station_id) }}</span>
        <span v-if="loadLabel(item.pending)" class="kds-backlog-tag" :class="loadClass(item.load_level)">
          {{ loadLabel(item.pending) }}
        </span>
        <div class="bar-track kds-backlog-bar">
          <div
            class="bar-fill"
            :class="loadClass(item.load_level)"
            :style="{ width: Math.min(100, (item.pending / KDS_HIGH) * 100) + '%' }"
          ></div>
        </div>
        <span class="kds-backlog-pending" :class="loadClass(item.load_level)">{{ item.pending }}</span>
        <span class="kds-backlog-wait">最久 {{ item.oldest_wait_minutes ?? 0 }} 分</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kds-backlog-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 360px;
  overflow-y: auto;
}
.kds-backlog-row {
  display: grid;
  grid-template-columns: 72px auto 1fr 28px 72px;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--card2);
  font-size: 12px;
}
.kds-backlog-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
.kds-backlog-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.15);
  color: var(--yellow);
  white-space: nowrap;
}
.kds-backlog-tag.red {
  background: rgba(239, 68, 68, 0.15);
  color: var(--red);
}
.kds-backlog-bar { min-width: 0; }
.kds-backlog-pending {
  text-align: right;
  font-weight: 700;
}
.kds-backlog-wait {
  text-align: right;
  color: var(--text-dim);
  font-size: 11px;
}
.yellow { color: var(--yellow); }
.red { color: var(--red); }
.bar-fill.yellow { background: var(--yellow); }
.bar-fill.red { background: var(--red); }
</style>
