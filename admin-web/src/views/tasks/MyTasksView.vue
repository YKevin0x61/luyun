<script setup>
import { computed, onMounted, ref } from 'vue'
import { getMyTasks } from '../../api/tasks'
import { useNudgePull } from '../../composables/useNudgePull'
import TaskCard from '../../components/tasks/TaskCard.vue'
import TaskDetailDialog from '../../components/tasks/TaskDetailDialog.vue'
import TaskPublishDialog from '../../components/tasks/TaskPublishDialog.vue'
import TaskWorkSheet from '../../components/tasks/TaskWorkSheet.vue'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { hasCap } from '../../utils/adminCaps'

// 员工端 ·「我的任务」（票 02）：待我做 / 待我验收 / 我发布的。
//
// 待我验收与我发布的只在花名册勾了「任务管理」（`admin_caps` 含 `task`）时出现；
// 发布/上传走 `?identity=staff`（共用电脑上管理端 cookie 优先，不声明会读成管理端），
// `GET /mine` 本身只认员工会话。底部 tab 不新增格 —— 这页挂在「我的」组里。

const identityStore = useWorkbenchIdentityStore()
const canManage = computed(() => hasCap(identityStore.staffCaps, 'task'))

const data = ref({
  groups: { todo: [], review: [], published: [] },
  counts: { todo: 0, review: 0, published: 0 },
})
const loading = ref(false)
const error = ref('')
const publishOpen = ref(false)
const editTarget = ref(null)
const detailOpen = ref(false)
const detailTask = ref(null)
const workOpen = ref(false)
const workTask = ref(null)

// 后端 `groups.todo` 同时带「待交」和「已交等验收」两种状态的实例
// （等验收的也要让同组其他人看见「已被 XX 完成」），这里按状态拆成两段，
// 免得「待我做」角标数着 0 却挂着一张卡。
const todoItems = computed(() =>
  (data.value.groups?.todo || []).filter((item) => item.status === 'todo'),
)
const waitingItems = computed(() =>
  (data.value.groups?.todo || []).filter((item) => item.status === 'pending'),
)
const groups = computed(() => data.value.groups || {})
const counts = computed(() => data.value.counts || {})

const segments = computed(() =>
  [
    { key: 'todo', label: '待我做', items: todoItems.value, count: todoItems.value.length, empty: '今天没有派给你的任务。' },
    { key: 'waiting', label: '等验收', items: waitingItems.value, count: waitingItems.value.length, empty: '还没有提交等验收的任务。' },
    { key: 'review', label: '待我验收', items: groups.value.review || [], count: counts.value.review || 0, empty: '没有等你验收的任务。', managerOnly: true },
    { key: 'published', label: '我发布的', items: groups.value.published || [], count: counts.value.published || 0, empty: '你还没有发布过任务。', managerOnly: true },
  ].filter((segment) => !segment.managerOnly || canManage.value),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await getMyTasks()
  } catch (err) {
    error.value = err?.detail || '任务没读出来，请刷新重试'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!identityStore.probed) await identityStore.refresh()
  load()
})

// 组里别人交了 / 验收人通过了，本页的「待我做 / 等验收 / 待我验收」跟着变。
useNudgePull({ id: 'my-tasks', topics: ['tasks'], pull: () => { void load() } })

function openPublish() {
  editTarget.value = null
  publishOpen.value = true
}

function openDetail(task) {
  detailTask.value = task
  detailOpen.value = true
}

function onPublished() {
  publishOpen.value = false
  editTarget.value = null
  load()
}

function onEdit(task) {
  detailOpen.value = false
  editTarget.value = task
  publishOpen.value = true
}

function onWork(task) {
  detailOpen.value = false
  workTask.value = task
  workOpen.value = true
}

function onWorkSubmitted() {
  workOpen.value = false
  workTask.value = null
  load()
}
</script>

<template>
  <section class="tasks-page">
    <header class="tasks-head">
      <div>
        <p class="tasks-kicker">工作台 · 我的</p>
        <h1>我的任务</h1>
        <p class="tasks-sub">做完了先拍照，再提交；驳回后可以重拍重交。</p>
      </div>
      <button v-if="canManage" class="tasks-publish" type="button" @click="openPublish">发布任务</button>
    </header>

    <p v-if="error" class="tasks-error" role="alert">{{ error }}</p>

    <div class="tasks-panels">
      <section v-for="segment in segments" :key="segment.key" class="tasks-panel">
        <header class="tasks-panel-head">
          <h2>{{ segment.label }}</h2>
          <span class="tasks-chip">{{ segment.count }}</span>
        </header>
        <TaskCard
          v-for="task in segment.items"
          :key="task.id"
          :task="task"
          @select="openDetail"
        />
        <p v-if="!segment.items.length && !loading" class="tasks-empty">{{ segment.empty }}</p>
      </section>
    </div>

    <TaskPublishDialog
      :open="publishOpen"
      :instance="editTarget"
      staff
      @close="publishOpen = false"
      @saved="onPublished"
    />
    <TaskDetailDialog
      :open="detailOpen"
      :instance="detailTask"
      staff
      @close="detailOpen = false"
      @edit="onEdit"
      @changed="load"
      @work="onWork"
    />
    <TaskWorkSheet
      :open="workOpen"
      :task="workTask"
      @close="workOpen = false"
      @submitted="onWorkSubmitted"
    />
  </section>
</template>

<style scoped>
.tasks-page {
  max-width: 720px;
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
.tasks-publish {
  flex: none;
  min-height: var(--tap);
  padding: 7px 16px;
  border: 1px solid var(--action);
  border-radius: var(--r-1);
  background: var(--action);
  color: var(--action-ink);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.tasks-error {
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: var(--r-1);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 13px;
  line-height: 1.6;
}
.tasks-panels {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.tasks-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  padding: 14px;
  min-height: 120px;
}
.tasks-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.tasks-panel-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.tasks-chip {
  min-width: 22px;
  padding: 1px 7px;
  border-radius: var(--r-full);
  background: var(--action-soft);
  color: var(--action);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}
.tasks-empty {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-faint);
}
</style>
