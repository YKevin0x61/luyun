<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getTaskBoard } from '../../api/tasks'
import { useNudgePull } from '../../composables/useNudgePull'
import TaskCard from '../../components/tasks/TaskCard.vue'
import TaskDetailDialog from '../../components/tasks/TaskDetailDialog.vue'
import TaskPublishDialog from '../../components/tasks/TaskPublishDialog.vue'

// 工作台 ·「任务」（管理端，票 02）：进行中 / 待验收 / 已结束三段。
//
// 数据一次拉 `GET /api/tasks/board`（三段 + 计数），发布/编辑/撤回后重拉。详情与
// 发布各自是弹层，不新开路由。员工端有「任务管理」能力的人走「我的任务」页发布，
// 这一页只服务管理端会话（管理端共享账号只发布/验收，不提交照片 —— ADR 0131）。

const route = useRoute()

const SEGMENTS = [
  { key: 'active', label: '进行中', countKey: 'active', empty: '还没有进行中的任务。' },
  { key: 'pending', label: '待验收', countKey: 'pending_review', empty: '没有待验收的任务。' },
  { key: 'finished', label: '已结束', countKey: 'finished', empty: '还没有已结束的任务。' },
]

const emptyBoard = () => ({
  groups: { active: [], pending: [], finished: [] },
  counts: { active: 0, pending_review: 0, overdue: 0, needs_reassign: 0, finished: 0 },
})

const board = ref(emptyBoard())
const loading = ref(false)
const error = ref('')
const publishOpen = ref(false)
const editTarget = ref(null)
const detailOpen = ref(false)
const detailTask = ref(null)

const groups = computed(() => board.value.groups || emptyBoard().groups)
const counts = computed(() => board.value.counts || emptyBoard().counts)
const overdueCount = computed(() => Number(counts.value.overdue) || 0)
const needsReassignCount = computed(() => Number(counts.value.needs_reassign) || 0)
const onlyNeedsReassign = ref(false)

function segmentTasks(segment) {
  const rows = groups.value[segment.key] || []
  if (segment.key === 'active' && onlyNeedsReassign.value) {
    return rows.filter((task) => task.needs_reassign)
  }
  return rows
}

function toggleNeedsReassignFilter() {
  onlyNeedsReassign.value = !onlyNeedsReassign.value
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    board.value = await getTaskBoard()
  } catch (err) {
    error.value = err?.detail || '任务列表没读出来，请刷新重试'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  // 首页那张「待验收任务」卡带 `?seg=pending` 进来：直接落到待验收那段。
  if (route.query.seg === 'pending') {
    await nextTick()
    document.querySelector('.tasks-segment[data-seg="pending"]')?.scrollIntoView({ block: 'start' })
  }
})

// 别人提交 / 验收 / 撤回后，本页的段与计数跟着变（nudge 不带数据，重拉一次 board）。
useNudgePull({ id: 'tasks-board', topics: ['tasks'], pull: () => { void load() } })

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

function reloadAfterChange() {
  load()
}
</script>

<template>
  <section class="tasks-page">
    <header class="tasks-head">
      <div>
        <p class="tasks-kicker">工作台 · 任务</p>
        <h1>任务</h1>
        <p class="tasks-sub">发布临时任务，指派到人或池；完成后拍照提交，由店长验收。</p>
      </div>
      <button class="tasks-publish" type="button" @click="openPublish">发布任务</button>
    </header>

    <p v-if="error" class="tasks-error" role="alert">{{ error }}</p>
    <p v-else-if="loading && !groups.active.length && !groups.pending.length && !groups.finished.length" class="tasks-hint">
      正在读取任务…
    </p>

    <p v-if="overdueCount" class="tasks-overdue">有 {{ overdueCount }} 个任务已经逾期，点开可以看截止时间或撤回。</p>
    <p v-if="needsReassignCount" class="tasks-reassign">
      有 {{ needsReassignCount }} 个任务需改派（负责人已离职）。
      <button class="tasks-reassign-filter" type="button" @click="toggleNeedsReassignFilter">
        {{ onlyNeedsReassign ? '显示全部进行中' : '只看需改派' }}
      </button>
    </p>

    <div class="tasks-segments" role="list">
      <section v-for="segment in SEGMENTS" :key="segment.key" class="tasks-segment" :data-seg="segment.key" role="listitem">
        <header class="tasks-segment-head">
          <h2>{{ segment.label }}</h2>
          <span class="tasks-chip">{{ counts[segment.countKey] || 0 }}</span>
        </header>
        <TaskCard
          v-for="task in segmentTasks(segment)"
          :key="task.id"
          :task="task"
          @select="openDetail"
        />
        <p v-if="!segmentTasks(segment).length && !loading" class="tasks-empty">
          {{ segment.key === 'active' && onlyNeedsReassign ? '没有需改派的任务。' : segment.empty }}
        </p>
      </section>
    </div>

    <TaskPublishDialog
      :open="publishOpen"
      :instance="editTarget"
      @close="publishOpen = false"
      @saved="onPublished"
    />
    <TaskDetailDialog
      :open="detailOpen"
      :instance="detailTask"
      @close="detailOpen = false"
      @edit="onEdit"
      @changed="reloadAfterChange"
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
.tasks-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--ink-faint);
}
.tasks-overdue {
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: var(--r-1);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: 13px;
  line-height: 1.6;
}
.tasks-segments {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
  gap: 14px;
}
@media (max-width: 720px) {
  .tasks-segments {
    grid-template-columns: minmax(0, 1fr);
  }
}
.tasks-segment {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  padding: 14px;
  min-height: 180px;
}
.tasks-segment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.tasks-segment-head h2 {
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
.tasks-reassign {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: var(--r-2);
  background: var(--warn-soft);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.6;
}
.tasks-reassign-filter {
  min-height: var(--tap);
  padding: 2px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-full);
  background: var(--surface-1);
  color: var(--action);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
</style>
