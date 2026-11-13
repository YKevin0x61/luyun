<script setup>
import { computed } from 'vue'
import { assigneeEmptyHint, assigneeText, formatDeadlineLabel, photoHint, statusLabel, statusTone } from '../../utils/tasks'

// 任务卡（票 02）：列表里一眼看清「做什么、谁做、什么时候要、要几张照」。
// 点开由父级打开发布者/执行人的详情弹层；这里不发请求。

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['select'])

const tone = computed(() => statusTone(props.task))
const emptyAssignees = computed(() => assigneeEmptyHint(props.task))
const claimText = computed(() => {
  if (props.task.status !== 'pending') return ''
  if (props.task.claimed_by_me) return '已提交，等验收结果'
  if (props.task.pending_submitter_name) return `已被 ${props.task.pending_submitter_name} 完成`
  return '已提交，等验收结果'
})
const rejectText = computed(() => {
  if (props.task.status !== 'todo') return ''
  return String(props.task.last_reject_reason || '')
})

function open() {
  emit('select', props.task)
}
</script>

<template>
  <article
    class="task-card"
    role="button"
    tabindex="0"
    :aria-label="`查看任务：${task.title}`"
    @click="open"
    @keydown.enter.prevent="open"
    @keydown.space.prevent="open"
  >
    <header class="task-card-head">
      <h3 class="task-card-title">{{ task.title }}</h3>
      <span class="task-badge" :class="`is-${tone}`">{{ statusLabel(task) }}</span>
    </header>

    <dl class="task-card-meta">
      <div>
        <dt>执行人</dt>
        <dd>{{ assigneeText(task) }}</dd>
      </div>
      <div>
        <dt>截止</dt>
        <dd>{{ formatDeadlineLabel(task.deadline_at) }}</dd>
      </div>
      <div>
        <dt>照片</dt>
        <dd>{{ photoHint(task) }}</dd>
      </div>
    </dl>

    <p v-if="emptyAssignees" class="task-card-warn">该日无可指派人员</p>
    <p v-if="task.needs_reassign" class="task-card-warn">有负责人已离职，需改派</p>
    <p v-if="claimText" class="task-card-foot">{{ claimText }}</p>
    <p v-if="rejectText" class="task-card-reject">上次被驳回：{{ rejectText }}</p>
  </article>
</template>

<style scoped>
.task-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  margin-bottom: 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
  cursor: pointer;
  transition: border-color 120ms ease;
}
.task-card:hover,
.task-card:focus-visible {
  border-color: var(--line-strong);
  outline: none;
}
.task-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.task-card-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.task-badge {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--r-full);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.7;
  background: var(--action-soft);
  color: var(--action);
}
.task-badge.is-danger {
  background: var(--danger-soft);
  color: var(--danger-bright);
}
.task-badge.is-warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.task-badge.is-ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.task-badge.is-muted {
  background: var(--surface-2);
  color: var(--ink-faint);
}
.task-card-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0;
}
.task-card-meta > div {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.6;
}
.task-card-meta dt {
  flex: none;
  width: 42px;
  color: var(--ink-faint);
}
.task-card-meta dd {
  margin: 0;
  color: var(--ink-dim);
  overflow-wrap: anywhere;
}
.task-card-warn {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--warn);
}
.task-card-foot {
  margin: 0;
  font-size: 12px;
  color: var(--ink-faint);
}
.task-card-reject {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--danger-bright);
}
</style>
