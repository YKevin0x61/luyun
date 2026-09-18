<script setup>
import { computed } from 'vue'
import { formatTs } from '../../utils/backupProgress'
import { stepSymbol, updateStageSteps } from '../../utils/updateProgress'

// 更新作业阶段进度：把后端「当前 stage」还原成一条固定步骤链。
// 状态用符号 + 文字表达（✓ / ● / ○ / ✗），颜色只是辅助；失败时只用作业里确实
// 落下的时间戳判断哪些步骤完成过，不假装知道失败发生在哪一步。
const props = defineProps({
  job: { type: Object, default: null },
})

const view = computed(() => updateStageSteps(props.job))

/** 备份快照时间戳不是 ISO（20260919-0359），原样展示；其余按统一格式截断。 */
function formatAt(value) {
  if (!value) return ''
  return /^\d{8}-\d{4}$/.test(value) ? value : formatTs(value)
}
</script>

<template>
  <div class="stages" :class="`is-${view.outcome}`">
    <ol class="stages__list" :aria-label="`更新阶段：${view.summary}`">
      <li
        v-for="item in view.items"
        :key="item.key"
        class="stage"
        :class="`is-${item.state}`"
      >
        <span class="stage__symbol" aria-hidden="true">{{ stepSymbol(item.state) }}</span>
        <span class="stage__label">{{ item.label }}</span>
        <span v-if="item.at" class="stage__at">{{ formatAt(item.at) }}</span>
      </li>
    </ol>
    <p class="stages__summary">{{ view.summary }}</p>
  </div>
</template>

<style scoped>
.stages { display: grid; gap: 8px; }
.stages__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 6px;
  margin: 0; padding: 0; list-style: none;
}
.stage {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: rgba(10, 13, 22, 0.55);
  border: 1px solid var(--border);
}
.stage__symbol { font-size: 13px; font-weight: 700; line-height: 1; color: var(--text-dim); }
.stage__label { color: var(--text-dim); min-width: 0; }
.stage__at {
  grid-column: 1 / -1;
  font-size: 11px; color: var(--text-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}

.stage.is-done { border-color: rgba(34, 197, 94, 0.3); }
.stage.is-done .stage__symbol,
.stage.is-done .stage__label { color: var(--green); }

.stage.is-active {
  border-color: rgba(99, 102, 241, 0.5);
  background: rgba(99, 102, 241, 0.14);
}
.stage.is-active .stage__symbol,
.stage.is-active .stage__label { color: var(--text); font-weight: 700; }
.stage.is-active .stage__symbol { color: var(--accent); }

.stage.is-failed {
  border-color: rgba(239, 68, 68, 0.45);
  background: rgba(239, 68, 68, 0.12);
}
.stage.is-failed .stage__symbol,
.stage.is-failed .stage__label { color: #fca5a5; font-weight: 700; }

.stages__summary {
  margin: 0; font-size: 12px; line-height: 1.6; color: var(--text-dim);
}
.stages.is-failed .stages__summary { color: #fca5a5; }
.stages.is-unhealthy .stages__summary { color: var(--yellow); }
.stages.is-succeeded .stages__summary { color: var(--green); }

@media (prefers-reduced-motion: reduce) {
  .stage { transition: none; }
}
</style>
