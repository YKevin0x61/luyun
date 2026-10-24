<script setup>
import { computed, ref } from 'vue'
import ConfirmDialog from './admin/ConfirmDialog.vue'
import SvgIcon from './SvgIcon.vue'
import { useImageUploadQueueStore } from '../stores/imageUploadQueue'

const store = useImageUploadQueueStore()
// 默认收起（2026-10-19 用户裁定）：上传在后台跑，不自动弹面板打断正在做的事；
// 需要看进度/重试时点那颗胶囊展开。
const collapsed = ref(true)
// 待确认「移除」的任务。只有失败项才拦——成功项的照片已经在服务端了，移除只是清
// 列表；失败项的移除等于当场放弃这张照片，且草稿也会一起删掉。
const removeTarget = ref(null)

function askRemove(task) {
  removeTarget.value = task
}

function confirmRemove() {
  if (removeTarget.value) store.remove(removeTarget.value.id)
  removeTarget.value = null
}

const headline = computed(() => {
  if (store.activeTasks.length) {
    return `图片上传 ${store.completedTasks.length + store.failedTasks.length}/${store.tasks.length}`
  }
  if (store.failedTasks.length) return `${store.failedTasks.length} 张上传失败`
  return `${store.completedTasks.length} 张上传完成`
})

const chipLabel = computed(() => {
  if (store.activeTasks.length) return `上传中 ${store.activeTasks.length}`
  if (store.failedTasks.length) return `上传失败 ${store.failedTasks.length}`
  return `图片已上传 ${store.completedTasks.length}`
})

// 不再「有任务就自动展开」：原来上传一开始就把面板顶出来，正在填表的人被打断。
// 现在一律由用户点胶囊展开（失败也一样，胶囊会变红并报数）。

function statusText(task) {
  if (task.status === 'queued') return '排队中'
  if (task.status === 'uploading') return `上传 ${task.percent}%`
  if (task.status === 'processing') return '服务端处理中'
  if (task.status === 'success') return '已完成'
  return '失败'
}

function statusClass(task) {
  return `is-${task.status}`
}

function closePanel() {
  if (store.activeTasks.length) {
    collapsed.value = true
    return
  }
  store.clearCompleted()
}
</script>

<template>
  <div v-if="store.tasks.length" class="image-upload-queue" :class="{ 'is-collapsed': collapsed }">
    <button
      v-if="collapsed"
      type="button"
      class="image-upload-chip"
      :aria-label="`${chipLabel}，展开上传队列`"
      @click="collapsed = false"
    >
      <span class="image-upload-chip-dot" :class="{ 'is-error': store.failedTasks.length }"></span>
      {{ chipLabel }}
    </button>

    <section v-else class="image-upload-panel" aria-live="polite">
      <header class="image-upload-head">
        <span class="image-upload-head-icon">
          <SvgIcon name="upload" :size="17" />
        </span>
        <div>
          <strong>{{ headline }}</strong>
          <span v-if="store.activeTasks.length">
            {{ store.uploadingCount }} 张上传中<template v-if="store.pendingCount"> · {{ store.pendingCount }} 张排队</template>
          </span>
          <span v-else-if="store.failedTasks.length">可重试，失败原图仅在本次页面保留</span>
          <span v-else>可以继续操作其他页面</span>
        </div>
        <button type="button" class="image-upload-icon-btn" aria-label="收起上传队列" @click="collapsed = true">
          <SvgIcon name="chevron-down" :size="17" />
        </button>
      </header>

      <ul class="image-upload-list">
        <li v-for="task in store.tasks" :key="task.id" class="image-upload-task" :class="statusClass(task)">
          <div class="image-upload-task-copy">
            <strong>{{ task.label }}</strong>
            <span v-if="task.detail">{{ task.detail }}</span>
            <small>{{ statusText(task) }}</small>
          </div>

          <div
            v-if="task.status === 'queued' || task.status === 'uploading' || task.status === 'processing'"
            class="image-upload-bar"
            role="progressbar"
            :aria-label="`${task.label} 上传进度`"
            :aria-valuenow="task.percent"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span :style="{ width: `${task.percent}%` }"></span>
          </div>

          <p v-if="task.status === 'error'" class="image-upload-error" role="alert">{{ task.error }}</p>

          <div class="image-upload-task-actions">
            <button
              v-if="task.status === 'error'"
              type="button"
              class="image-upload-retry"
              @click="store.retry(task.id)"
            >
              <SvgIcon name="refresh-cw" :size="14" />
              重试
            </button>
            <button
              v-if="task.status === 'success' || task.status === 'error'"
              type="button"
              class="image-upload-icon-btn"
              :aria-label="`移除 ${task.label} 上传记录`"
              @click="task.status === 'error' ? askRemove(task) : store.remove(task.id)"
            >
              <SvgIcon name="x" :size="15" />
            </button>
          </div>
        </li>
      </ul>

      <footer class="image-upload-foot">
        <button
          v-if="store.completedTasks.length"
          type="button"
          class="image-upload-clear"
          @click="store.clearCompleted()"
        >清除已完成</button>
        <button type="button" class="image-upload-clear" @click="closePanel">
          {{ store.activeTasks.length ? '收起' : (store.failedTasks.length ? '保留失败项' : '关闭') }}
        </button>
      </footer>
    </section>
  </div>

  <ConfirmDialog
    v-if="removeTarget"
    title="放弃这张照片？"
    :message="`「${removeTarget.label}」还没传上去，移除后本地草稿也会一起删掉，需要重新拍。`"
    confirm-label="放弃"
    danger
    @confirm="confirmRemove"
    @cancel="removeTarget = null"
  />
</template>

<style scoped>
/* B 案面板语言（2026-10-19）：与工作台的卡片/行同一套 —— 语义令牌、1px 细边、方角小圆角、
   纯色 3px 进度条、9.5px mono 状态。原来这里是后台旧皮肤（10px 圆角 + rgba 深底 +
   渐变进度条 + 旧令牌 --border/--text/--accent），所以它在改造过的页面里显得没改。 */
.image-upload-queue {
  position: fixed;
  top: calc(var(--global-nav-height, 46px) + 12px);
  right: 14px;
  /* 仍低于拍摄/对照弹层（.staff-preview = 60），高于底部 tabbar：收起态也点得到。 */
  z-index: var(--z-dialog);
  width: min(340px, calc(100vw - 28px));
}
.image-upload-queue.is-collapsed { width: auto; }
.image-upload-panel,
.image-upload-chip {
  border: 1px solid var(--line-strong);
  border-radius: var(--r-2);
  background: var(--surface-1);
  color: var(--ink);
  /* 不挂模糊阴影：B 案浮层靠 1px 边 + 实底分层（设计护栏也不允许新增 --hy-* 引用）。 */
}
.image-upload-panel { overflow: hidden; }
.image-upload-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 0 12px;
  font: 500 11px/1 var(--font-sans, inherit);
  letter-spacing: .04em;
  cursor: pointer;
}
.image-upload-chip-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--action); }
.image-upload-chip-dot.is-error { background: var(--danger); }
.image-upload-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 6px 12px;
  border-bottom: 1px solid var(--line);
}
/* 图标块改小：B 案的面板头是「一个记号 + 标题」，不做彩色图标砖。 */
.image-upload-head-icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  color: var(--action);
}
.image-upload-head strong,
.image-upload-head span { display: block; min-width: 0; }
.image-upload-head strong {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: .04em;
  color: var(--ink);
}
.image-upload-head div > span {
  margin-top: 3px;
  color: var(--ink-dim);
  font-size: 10.5px;
  line-height: 1.4;
}
.image-upload-icon-btn {
  display: inline-grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 0;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink-dim);
  cursor: pointer;
}
.image-upload-icon-btn:hover,
.image-upload-icon-btn:focus-visible { background: var(--surface-2); color: var(--ink); }
.image-upload-list {
  max-height: min(48vh, 380px);
  overflow-y: auto;
  margin: 0;
  padding: 0 12px;
  list-style: none;
}
.image-upload-task {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px 10px;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.image-upload-task:first-child { border-top: 0; }
.image-upload-task-copy { min-width: 0; }
.image-upload-task-copy strong,
.image-upload-task-copy span,
.image-upload-task-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.image-upload-task-copy strong { font-size: 12px; font-weight: 500; color: var(--ink); }
.image-upload-task-copy span {
  margin-top: 2px;
  color: var(--ink-dim);
  font-size: 10.5px;
}
.image-upload-task-copy small {
  margin-top: 4px;
  color: var(--ink-faint);
  font: 400 9.5px/1.2 var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* 状态也用语义色，不再用后台那套 --accent/--cyan/--green/--red。 */
.image-upload-task.is-processing .image-upload-task-copy small { color: var(--info); }
.image-upload-task.is-success .image-upload-task-copy small { color: var(--ok); }
.image-upload-task.is-error .image-upload-task-copy small { color: var(--danger-bright); }
.image-upload-bar {
  grid-column: 1 / -1;
  height: 3px;
  overflow: hidden;
  background: var(--line);
}
.image-upload-bar span {
  display: block;
  height: 100%;
  background: var(--action);
  transition: width .18s ease-out;
}
.image-upload-task.is-processing .image-upload-bar span {
  width: 100% !important;
  animation: image-upload-pulse 1.2s ease-in-out infinite;
}
.image-upload-error {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--danger-bright);
  font-size: 10.5px;
  line-height: 1.5;
}
.image-upload-task-actions {
  grid-column: 2;
  grid-row: 1;
  display: flex;
  align-items: center;
  gap: 4px;
}
.image-upload-retry {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink);
  font: 500 11px/1 var(--font-sans, inherit);
  cursor: pointer;
}
.image-upload-retry:hover,
.image-upload-retry:focus-visible { border-color: var(--action); }
.image-upload-foot {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  padding: 2px 6px;
  border-top: 1px solid var(--line);
}
.image-upload-clear {
  min-height: 44px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink-dim);
  font: 500 11px/1 var(--font-sans, inherit);
  cursor: pointer;
}
.image-upload-clear:hover,
.image-upload-clear:focus-visible { color: var(--ink); background: var(--surface-2); }
@keyframes image-upload-pulse {
  0%, 100% { opacity: .55; }
  50% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .image-upload-bar span { transition: none; }
  .image-upload-task.is-processing .image-upload-bar span { animation: none; }
}
@media (max-width: 900px) {
  .image-upload-queue {
    top: auto;
    right: 10px;
    bottom: calc(122px + env(safe-area-inset-bottom));
    left: 10px;
    width: auto;
  }
  .image-upload-queue.is-collapsed { right: 10px; left: auto; width: auto; }
  .image-upload-list { max-height: min(42vh, 320px); }
}
</style>
