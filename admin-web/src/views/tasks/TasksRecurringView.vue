<script setup>
import { onMounted, ref } from 'vue'
import { getTaskDefinitions, stopTaskDefinition } from '../../api/tasks'
import { useNudgePull } from '../../composables/useNudgePull'
import TaskPublishDialog from '../../components/tasks/TaskPublishDialog.vue'
import {
  assigneeEmptyHint,
  assigneeText,
  frequencyText,
  photoHint,
  recentPeriodsText,
} from '../../utils/tasks'

// 工作台 ·「定期任务」（票 06）：定义列表 + 新建 / 编辑 / 停用。
//
// 每个定义一行：频率与起止、指派摘要、每期截止、近 N 期完成情况。编辑只改定义，
// 已经生成的期保留当时的快照；停用后不再生成新期，历史还在。

const items = ref([])
const loading = ref(false)
const error = ref('')
const publishOpen = ref(false)
const editTarget = ref(null)
const stoppingId = ref(0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await getTaskDefinitions()
    items.value = Array.isArray(data?.items) ? data.items : []
  } catch (err) {
    error.value = err?.detail || '定期任务列表没读出来，请刷新重试'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editTarget.value = null
  publishOpen.value = true
}

function openEdit(definition) {
  editTarget.value = definition
  publishOpen.value = true
}

function onSaved() {
  publishOpen.value = false
  editTarget.value = null
  load()
}

async function stop(definition) {
  if (stoppingId.value !== definition.id) {
    stoppingId.value = definition.id
    return
  }
  stoppingId.value = 0
  error.value = ''
  try {
    await stopTaskDefinition(definition.id)
    await load()
  } catch (err) {
    error.value = err?.detail || '停用失败，请重试'
  }
}

function dateRangeText(definition) {
  const start = definition.start_date || ''
  const end = definition.end_date || ''
  if (!start) return ''
  return end ? `${start} 至 ${end}` : `${start} 起`
}

onMounted(load)

// 物化循环每 30 秒可能新建本期实例（nudge 原因 materialized）：近 N 期数字跟着刷。
useNudgePull({ id: 'tasks-recurring', topics: ['tasks'], pull: () => { void load() },  })
</script>

<template>
  <section class="tasks-page">
    <header class="tasks-head">
      <div>
        <p class="tasks-kicker">工作台 · 任务</p>
        <h1>定期任务</h1>
        <p class="tasks-sub">按每天 / 每周几 / 每月几号生成一期，每期到点拍照交，逐期验收。</p>
      </div>
      <button class="task-primary" type="button" @click="openCreate">新建定期任务</button>
    </header>

    <p v-if="error" class="task-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="tasks-hint">正在读取定期任务…</p>

    <div v-if="!loading && !items.length" class="tasks-panel">
      <p class="tasks-empty">
        还没有定期任务。新建后每期会自动生成一条任务，员工到点拍照提交，逐期验收。
      </p>
    </div>

    <ul v-else-if="items.length" class="task-recurring-list">
      <li v-for="definition in items" :key="definition.id" class="task-recurring">
        <div class="task-recurring-head">
          <h2>{{ definition.title }}</h2>
          <span
            class="task-recurring-status"
            :class="definition.status === 'stopped' ? 'is-stopped' : 'is-active'"
          >{{ definition.status === 'stopped' ? '已停用' : '启用中' }}</span>
        </div>
        <p class="task-recurring-freq">
          {{ frequencyText(definition) }} · 每期 {{ definition.deadline_hhmm }} 截止
          <span class="task-recurring-range">{{ dateRangeText(definition) }}</span>
        </p>
        <p class="task-recurring-meta">
          <span>执行人 {{ assigneeText(definition) }}</span>
          <span>{{ photoHint(definition) }}</span>
          <span class="task-recurring-rate">{{ recentPeriodsText(definition) }}</span>
        </p>
        <p v-if="assigneeEmptyHint(definition)" class="task-recurring-warn">
          这个池在最近一期没有解析出可指派人员，发布后每期按排班实时解析。
        </p>
        <div class="task-recurring-actions">
          <button class="task-secondary" type="button" @click="openEdit(definition)">编辑</button>
          <button
            v-if="definition.status !== 'stopped'"
            class="task-danger"
            type="button"
            @click="stop(definition)"
          >{{ stoppingId === definition.id ? '确定停用？' : '停用' }}</button>
        </div>
      </li>
    </ul>

    <TaskPublishDialog
      :open="publishOpen"
      :definition="editTarget"
      default-kind="recurring"
      @close="publishOpen = false"
      @saved="onSaved"
    />
  </section>
</template>

<style scoped>
.tasks-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 16px 40px;
}
.tasks-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.tasks-kicker {
  margin: 0 0 4px;
  font-size: 12px;
  letter-spacing: 0.08em;
  color: var(--ink-faint);
}
.tasks-head h1 {
  margin: 0;
  font-family: var(--font-song);
  font-size: 25px;
  font-weight: 700;
  color: var(--ink);
}
.tasks-sub {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-dim);
}
.tasks-hint {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--ink-faint);
}
.tasks-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  padding: 28px 18px;
}
.tasks-empty {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-faint);
  text-align: center;
}
.task-primary {
  min-height: var(--tap);
  padding: 0 18px;
  border: 0;
  border-radius: var(--r-2);
  background: var(--action);
  color: var(--action-ink);
  font-size: 14px;
  cursor: pointer;
}
.task-secondary {
  min-height: var(--tap);
  padding: 0 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-2);
  background: var(--surface-1);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
}
.task-danger {
  min-height: var(--tap);
  padding: 0 14px;
  border: 1px solid var(--danger-soft);
  border-radius: var(--r-2);
  background: var(--surface-1);
  color: var(--danger-bright);
  font-size: 13px;
  cursor: pointer;
}
.task-error {
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: var(--r-1);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 13px;
}
.task-recurring-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}
.task-recurring {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  padding: 16px 18px;
}
.task-recurring-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.task-recurring-head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}
.task-recurring-status {
  padding: 2px 8px;
  border-radius: var(--r-full);
  font-size: 12px;
}
.task-recurring-status.is-active {
  background: var(--action-soft);
  color: var(--action);
}
.task-recurring-status.is-stopped {
  background: var(--surface-2);
  color: var(--ink-faint);
}
.task-recurring-freq {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--ink);
}
.task-recurring-range {
  margin-left: 8px;
  color: var(--ink-faint);
}
.task-recurring-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--ink-dim);
}
.task-recurring-rate {
  color: var(--ink);
}
.task-recurring-warn {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--danger-bright);
}
.task-recurring-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
