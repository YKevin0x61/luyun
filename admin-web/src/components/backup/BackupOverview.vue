<script setup>
import { computed } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import StatusPill from '../ui/StatusPill.vue'
import { formatBytes, formatTs } from '../../utils/backupProgress'

// 备份中心顶部总览：一句话结论 + 四个关键事实 + 重跑入口。
// 结论同时以文字给出（summary），颜色只是辅助；下一句「下一步」告诉操作者该做什么。
const props = defineProps({
  /** useBackupCenter 的 healthView；未计算时为 null。 */
  health: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

defineEmits(['refresh'])

/** 后端 status：ok / degraded / legacy_only / unusable / no_backup。 */
const TONES = {
  ok: 'ok',
  degraded: 'warn',
  legacy_only: 'warn',
  unusable: 'error',
  no_backup: 'error',
}
const tone = computed(() => TONES[props.health?.status] || 'neutral')

const lastSuccessText = computed(
  () => formatTs(props.health?.last_success_at) || '（无可用备份）',
)
const countsText = computed(() => {
  const counts = props.health?.counts
  if (!counts) return '—'
  return `${counts.usable} / ${counts.total}`
})
const totalBytesText = computed(() => formatBytes(props.health?.total_bytes))
const coverageText = computed(() => (props.health?.coverage_labels || []).join('、') || '—')
</script>

<template>
  <section class="backup-overview" aria-labelledby="backup-overview-title">
    <div class="backup-overview__head">
      <h2 id="backup-overview-title" class="backup-overview__title">
        <SvgIcon name="database" :size="15" />备份状态
      </h2>
      <StatusPill
        :tone="tone"
        :label="health ? health.summary : (loading ? '正在计算备份健康…' : '尚未计算备份健康')"
      />
      <button
        type="button"
        class="btn backup-overview__refresh"
        :disabled="loading"
        @click="$emit('refresh')"
      >
        <SvgIcon name="refresh-cw" :size="14" />
        {{ loading ? '重跑中…' : '重跑备份健康' }}
      </button>
    </div>

    <p v-if="error" class="backup-overview__error">加载失败：{{ error }}</p>

    <dl v-if="health" class="backup-overview__facts">
      <div><dt>最近一次可用备份</dt><dd>{{ lastSuccessText }}</dd></div>
      <div><dt>介质</dt><dd>{{ health.last_success_medium_label || '—' }}</dd></div>
      <div><dt>可恢复备份点</dt><dd>{{ countsText }}</dd></div>
      <div><dt>总体积</dt><dd>{{ totalBytesText }}</dd></div>
      <div class="is-wide"><dt>覆盖内容</dt><dd>{{ coverageText }}</dd></div>
    </dl>

    <p v-if="health?.next_step" class="backup-overview__next">下一步：{{ health.next_step }}</p>
  </section>
</template>

<style scoped>
.backup-overview {
  padding: 14px 16px;
  margin-bottom: 14px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.backup-overview__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
}
.backup-overview__title {
  display: flex; align-items: center; gap: 6px;
  margin: 0; font-size: 15px;
}
.backup-overview__refresh {
  margin-left: auto;
  min-height: 40px; padding: 0 14px; font-size: 13px; justify-content: center;
}
.backup-overview__refresh:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.backup-overview__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 4px 18px;
  margin: 12px 0 0;
  font-size: 12px;
}
.backup-overview__facts > div { display: flex; gap: 8px; min-width: 0; }
.backup-overview__facts dt { color: var(--text-dim); white-space: nowrap; }
.backup-overview__facts dd {
  margin: 0; min-width: 0; margin-left: auto; text-align: right;
  color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}
.backup-overview__facts .is-wide { grid-column: 1 / -1; }

.backup-overview__next {
  margin: 12px 0 0; padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px; line-height: 1.6; color: var(--text-dim);
}
.backup-overview__error {
  margin: 12px 0 0; padding: 8px 10px; border-radius: 8px;
  font-size: 12px; line-height: 1.5;
  background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5;
}

@media (max-width: 560px) {
  .backup-overview__refresh { margin-left: 0; width: 100%; }
}
</style>
