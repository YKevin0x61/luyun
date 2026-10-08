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

/**
 * 校验说明的比较口径：**按空白规范化后比**。
 * 后端同一条说明在不同备份点上可能带不同的缩进 / 换行 / 连续空格（尤其人工录入过的
 * `basic_check.messages`），只比原串会把"同一段话"当成两段，去重就漏了。
 */
function normalizeMessage(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim()
}

/**
 * 后端对每个备份点各返回一份基础校验说明，其中"业务数据是 PostgreSQL 整库备份……"
 * 这类**每份备份都成立**的机制解释会在每个点上各来一遍（实测 4 个点 = 同一段 48 字说
 * 4 遍）。这里把"本组有 ≥2 个点共有"的说明提到**组级**只留一处（默认收起的折叠），
 * 各点行只留自己独有的那条；组里只有一个点时不合并（消息留在它自己那一行）。
 * 逐点判断与合并都在这个组件里做，composable 的校验逻辑与返回结构不动。
 */
function splitGroupMessages(items) {
  const pointCount = new Map()
  for (const { row } of items) {
    for (const key of new Set(row.checkMessages.map(normalizeMessage))) {
      if (key) pointCount.set(key, (pointCount.get(key) || 0) + 1)
    }
  }
  const sharedKeys = new Set(
    [...pointCount].filter(([, count]) => count > 1).map(([key]) => key),
  )
  if (!sharedKeys.size) return { sharedMessages: [], items }

  const sharedMessages = []
  const seen = new Set()
  for (const { row } of items) {
    for (const message of row.checkMessages) {
      const key = normalizeMessage(message)
      if (!sharedKeys.has(key) || seen.has(key)) continue
      seen.add(key)
      // 保留首次出现时的原始写法（含它自己的标点与大小写）
      sharedMessages.push(message)
    }
  }
  return {
    sharedMessages,
    items: items.map(({ point, row }) => ({
      point,
      row: { ...row, checkMessages: row.checkMessages.filter((m) => !sharedKeys.has(normalizeMessage(m))) },
    })),
  }
}

const groups = computed(() => {
  const grouped = groupBackupPoints(props.points)
  return GROUP_META.map((meta) => {
    const items = (grouped[meta.key] || []).map((point) => ({ point, row: toRow(point) }))
    return { ...meta, ...splitGroupMessages(items) }
  })
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
        <!-- 后端对每个备份点各返回一份基础校验说明，其中"每份备份都成立"的机制解释
             （如"业务数据是 PostgreSQL 整库备份……"）会在每个点上各来一遍。本组有 ≥2 个点
             共有的说明收进这里的一条折叠，只出现一次；各点行只留自己独有的那条。
             收进折叠而不是直接铺在组头：它是机制解释（同一条后果在回滚确认弹窗里还会在
             动作发生前再说一次），默认态不占版面、也不逐点重复。 -->
        <details v-if="g.sharedMessages.length" class="points__shared">
          <summary>共同的校验说明（{{ g.sharedMessages.length }} 条）</summary>
          <ul>
            <li v-for="(message, i) in g.sharedMessages" :key="`shared-${i}`">{{ message }}</li>
          </ul>
        </details>
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

/* 本组共有的校验说明：一条折叠，默认收起（逐点重复的那段话只在这里出现一次）。
   窄屏（320/390）下长消息要能换行，不许把卡片或页面撑出横向滚动条。 */
.points__shared {
  margin: 0 0 8px; font-size: 11px; line-height: 1.6; color: var(--text-dim);
  background: rgba(10, 13, 22, 0.45);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 2px 10px 4px;
}
.points__shared > summary {
  cursor: pointer; min-height: 36px; display: flex; align-items: center;
  overflow-wrap: anywhere;
}
.points__shared > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.points__shared ul { margin: 2px 0 4px; padding-left: 18px; }
.points__shared li { margin-bottom: 4px; overflow-wrap: anywhere; }

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
  /* 校验说明 / 提示里可能出现长路径或长连写串：overflow-wrap 是可继承属性，
     设在这里匿名 flex 项也吃得到，320px 下长消息换行而不是把卡片撑破。 */
  overflow-wrap: anywhere;
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
