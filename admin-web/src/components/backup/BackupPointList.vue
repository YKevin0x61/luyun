<script setup>
import { computed } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import StatusPill from '../ui/StatusPill.vue'
import { formatTs } from '../../utils/backupProgress'
import {
  backupPointRow,
  backupPointsEmptyHint,
  groupBackupPoints,
} from '../../utils/backupPoints'

// 备份点列表：三种介质分组展示，每组一张卡片式列表。
// 之前是一张 8 列表格，内容区只有 ~660px 必然挤成横向滚动，而且冷备会在表格
// 和下方列表里各出现一次；分组卡片在任何宽度下都读得完，也不再重复。
const props = defineProps({
  points: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  mediumLabels: { type: Object, default: () => ({}) },
  mediumPurposes: { type: Object, default: () => ({}) },
  validatingId: { type: String, default: '' },
  /** 正在回滚的快照时间戳；用于按钮的忙碌态。 */
  rollingBackTs: { type: String, default: '' },
  /** { [pointId]: { ok, messages, recoverable, checked_at } } —— 刚跑完的手动校验。 */
  validateResults: { type: Object, default: () => ({}) },
  notBackedUp: { type: Array, default: () => [] },
})

defineEmits(['validate', 'rollback', 'refresh'])

const GROUP_META = [
  {
    key: 'snapshots',
    title: '本机回滚快照',
    hint: '用于快速的数据回滚；每次回滚前会自动再建一份。',
    readonly: false,
  },
  {
    key: 'exports',
    title: '导出备份',
    hint: '口令加密的 .luyunbak，可下载到浏览器外保存，也可在本页直接恢复。',
    readonly: false,
  },
  {
    key: 'cold',
    title: '冷备',
    hint: '宿主机定时产出，页面只读；要恢复请在宿主机上操作。',
    readonly: true,
  },
]

function toRow(point) {
  return backupPointRow(point, {
    mediumLabels: props.mediumLabels,
    mediumPurposes: props.mediumPurposes,
    validateResult: props.validateResults[point.id] || null,
  })
}

const groups = computed(() => {
  const grouped = groupBackupPoints(props.points)
  return GROUP_META.map((meta) => ({
    ...meta,
    items: (grouped[meta.key] || []).map((point) => ({ point, row: toRow(point) })),
  }))
})

const recoverableCount = computed(
  () => groups.value.find((g) => g.key === 'snapshots').items.length
    + groups.value.find((g) => g.key === 'exports').items.length,
)
const emptyHint = computed(() => backupPointsEmptyHint(props.points))

function photoText(row) {
  return row.photoLines.map((line) => `${line.label} ${line.text}`).join('；')
}
</script>

<template>
  <section class="points" aria-labelledby="backup-points-title">
    <div class="points__head">
      <h2 id="backup-points-title" class="points__title">
        <SvgIcon name="package" :size="15" />备份点
      </h2>
      <button type="button" class="btn" :disabled="loading" @click="$emit('refresh')">
        <SvgIcon name="refresh-cw" :size="14" />{{ loading ? '刷新中…' : '刷新列表' }}
      </button>
    </div>

    <p v-if="error" class="points__note is-error">加载失败：{{ error }}</p>
    <p v-else-if="loading && !points.length" class="points__note">加载中…</p>
    <p v-else-if="!points.length" class="points__note">{{ emptyHint }}</p>

    <template v-else>
      <p v-if="recoverableCount === 0 && emptyHint" class="points__note is-warn">{{ emptyHint }}</p>

      <div v-for="g in groups" :key="g.key" class="points__group">
        <div class="points__group-head">
          <h3 class="points__group-title">{{ g.title }}</h3>
          <span class="points__count">{{ g.items.length }} 份</span>
          <StatusPill v-if="g.readonly" tone="neutral" label="只读" />
        </div>
        <p class="points__group-hint">{{ g.hint }}</p>
        <p v-if="!g.items.length" class="points__note">暂无</p>
        <ul v-else class="points__list">
          <li v-for="{ point, row } in g.items" :key="point.id" class="point">
            <div class="point__main">
              <div class="point__title">
                <span class="point__ts">{{ row.ts }}</span>
                <StatusPill :tone="row.checkTone || 'neutral'" :label="row.checkLabel" />
              </div>
              <p class="point__meta">
                {{ row.createdLabel }} · {{ row.provenanceLabel }} · {{ row.sizeLabel }}
              </p>
              <p class="point__line">
                <span class="point__key">内容</span>{{ row.contentsLabels.join('、') || '—' }}
              </p>
              <p v-if="row.missingLabels.length" class="point__line is-warn">
                <span class="point__key">缺少</span>{{ row.missingLabels.join('、') }}
              </p>
              <p v-if="row.photoLines.length" class="point__line">
                <span class="point__key">照片</span>{{ photoText(row) }}
              </p>
              <p v-if="row.photosUnclassified" class="point__line is-warn">
                <span class="point__key">照片</span>未能按库引用分类（psql 不可用），全部计入「其它照片」
              </p>
              <p v-for="(warning, wi) in row.checkWarnings" :key="`warn-${wi}`" class="point__line is-warn">
                <span class="point__key">提示</span>{{ warning }}
              </p>
              <p v-if="row.checkMessages.length" class="point__line is-dim">
                {{ row.checkMessages.join('；') }}
              </p>
              <p v-if="row.checkAt" class="point__line is-dim">校验于 {{ formatTs(row.checkAt) }}</p>
            </div>
            <div class="point__actions">
              <button
                v-if="!g.readonly"
                type="button"
                class="btn btn-sm"
                :disabled="validatingId === point.id"
                @click="$emit('validate', point.id)"
              >{{ validatingId === point.id ? '校验中…' : '校验' }}</button>
              <button
                v-if="point.medium === 'local_snapshot' && row.recoverable"
                type="button"
                class="btn btn-sm btn-danger"
                :disabled="rollingBackTs === row.ts"
                @click="$emit('rollback', point)"
              >{{ rollingBackTs === row.ts ? '回滚中…' : row.rollbackLabel }}</button>
            </div>
          </li>
        </ul>
      </div>
    </template>

    <details v-if="notBackedUp.length" class="points__excluded">
      <summary>不备份清单（{{ notBackedUp.length }}）——这些内容不会进入任何备份点</summary>
      <ul>
        <li v-for="item in notBackedUp" :key="item.name">
          <strong>{{ item.name }}</strong>：{{ item.reason }}
        </li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.points { margin-bottom: 16px; }
.points__head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 6px;
}
.points__title { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 15px; }
.points__head .btn { margin-left: auto; min-height: 34px; }

.points__note { margin: 0 0 10px; font-size: 12px; line-height: 1.6; color: var(--text-dim); }
.points__note.is-error { color: #fca5a5; }
.points__note.is-warn { color: var(--yellow); }

.points__group { margin-top: 14px; }
.points__group-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.points__group-title { margin: 0; font-size: 13px; color: var(--text); }
.points__count {
  padding: 1px 8px; border-radius: 999px; font-size: 11px;
  background: rgba(10, 13, 22, 0.6); border: 1px solid var(--border); color: var(--text-dim);
}
.points__group-hint { margin: 4px 0 8px; font-size: 11px; line-height: 1.5; color: var(--text-dim); }

.points__list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.point {
  display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px;
  background: rgba(10, 13, 22, 0.55);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.point__main { flex: 1 1 320px; min-width: 0; display: grid; gap: 4px; }
.point__title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.point__ts {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; font-weight: 700; color: var(--text);
  overflow-wrap: anywhere;
}
.point__meta { margin: 0; font-size: 11px; color: var(--text-dim); line-height: 1.5; }
.point__line {
  margin: 0; font-size: 11px; line-height: 1.55; color: var(--text);
  display: flex; gap: 8px; min-width: 0;
}
.point__line.is-dim { color: var(--text-dim); }
.point__line.is-warn { color: var(--yellow); }
.point__key { flex: 0 0 30px; color: var(--text-dim); }

.point__actions { display: flex; gap: 6px; flex-wrap: wrap; margin-left: auto; align-items: center; }
.point__actions .btn { min-height: 30px; }

.points__excluded { margin-top: 14px; font-size: 11px; color: var(--text-dim); line-height: 1.6; }
.points__excluded > summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.points__excluded ul { margin: 4px 0 0; padding-left: 18px; }
.points__excluded li { margin-bottom: 3px; }

@media (max-width: 560px) {
  .point__actions { margin-left: 0; width: 100%; }
  .point__actions .btn { flex: 1 1 0; justify-content: center; min-height: 36px; }
  .points__head .btn { margin-left: 0; width: 100%; justify-content: center; min-height: 36px; }
}
</style>
