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

// KDS 数据源健康（后端 kds_backlog.kds_active）：营业日窗口内「已制作待上菜」占比不足
// 2% 说明 dish_status 没人回写，档口的红/黄负载是失真告警，明细保留但去掉负载标签与
// 红条高亮。旧接口没有该字段时按 true 处理，保持原有渲染。
const kdsActive = computed(() => props.backlog?.kds_active !== false)

function loadClass(level) {
  if (!kdsActive.value) return ''
  if (level === 'high') return 'red'
  if (level === 'medium') return 'yellow'
  return ''
}

function loadLabel(pending) {
  if (!kdsActive.value) return ''
  if (pending >= KDS_HIGH) return '高负载'
  if (pending >= KDS_MEDIUM) return '繁忙'
  return ''
}
</script>

<template>
  <div class="card">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="chef-hat" :size="16" /> 出餐压力</span>
      <span v-if="kdsActive" class="badge">≥{{ KDS_MEDIUM }} 繁忙 · ≥{{ KDS_HIGH }} 高负载</span>
      <span v-else class="badge muted">KDS 未启用</span>
    </div>
    <!-- kds_active=false：dish_status 几乎没人回写，红/黄档口负载是失真告警，只给中性提示 -->
    <div v-if="!kdsActive" class="kds-inactive-hint">KDS 未启用，出餐状态没人回写</div>
    <div v-if="!stations.length" class="empty-state">当前无待出餐</div>
    <div v-else class="kds-backlog-list luyun-scrollbar">
      <div
        v-for="item in stations"
        :key="item.station_id || 'unknown'"
        class="kds-backlog-row"
        :class="{ 'is-inactive': !kdsActive }"
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
/* kds_active=false 时负载标签不渲染，这一行只剩 4 个格子，会按原模板的 5 列依次错位
   （进度条挤进 auto、待出数占 1fr、「最久 X 分」落到 28px 列里折行）。这里换成 4 列。 */
.kds-backlog-row.is-inactive {
  grid-template-columns: 72px 1fr 40px 96px;
}
.kds-backlog-wait {
  text-align: right;
  color: var(--text-dim);
  font-size: 11px;
  white-space: nowrap;
}
/* kds_active=false 的降级提示：左侧橙色竖线 + 橙字，不铺底色（设计护栏：不新增裸 rgba）。 */
.kds-inactive-hint {
  margin-bottom: 8px;
  padding: 2px 0 2px 8px;
  border-left: 3px solid var(--yellow);
  color: var(--yellow);
  font-size: 12px;
}
/* 去掉 .badge 自带的 card2 底，只留边框与次要字色：不再暗示"繁忙/高负载"。 */
.badge.muted {
  background: transparent;
  color: var(--text-dim);
}
.yellow { color: var(--yellow); }
.red { color: var(--red); }
.bar-fill.yellow { background: var(--yellow); }
.bar-fill.red { background: var(--red); }
</style>
