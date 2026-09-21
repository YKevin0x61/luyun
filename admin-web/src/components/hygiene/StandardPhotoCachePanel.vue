<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import { formatChinaSyncTime } from '../../utils/standardPhotoCache'

const store = useStandardPhotoCacheStore()
let hiddenAt = null
const progressCollapsed = ref(false)

const progressPercent = computed(() => {
  const progress = store.progress
  if (!progress || !progress.total) return 0
  return Math.round((progress.completed / progress.total) * 100)
})

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function onVisibilityChange() {
  if (document.hidden) {
    hiddenAt = Date.now()
    return
  }
  if (hiddenAt && Date.now() - hiddenAt >= 15 * 60 * 1000) {
    store.checkForUpdates()
  }
  hiddenAt = null
}

function onOnlineChange() {
  const online = navigator.onLine !== false
  store.setOnline(online)
  if (online) store.checkForUpdates()
}

function clearCache() {
  if (window.confirm('清除本机标准图缓存？不会退出登录或删除卫生数据。')) {
    store.clearCache()
  }
}

function checkUpdates() {
  if (!store.stats.totalCount) return
  if (!store.stats.baselineReady) {
    store.firstPromptOpen = true
    return
  }
  store.checkForUpdates()
}

watch(
  () => Boolean(store.progress),
  (visible) => {
    if (visible) progressCollapsed.value = false
  },
)

onMounted(() => {
  store.initialize()
  document.addEventListener('visibilitychange', onVisibilityChange)
  window.addEventListener('online', onOnlineChange)
  window.addEventListener('offline', onOnlineChange)
  if (navigator.connection?.addEventListener) {
    navigator.connection.addEventListener('change', onOnlineChange)
  }
})

onBeforeUnmount(() => {
  store.cancelDownload()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('online', onOnlineChange)
  window.removeEventListener('offline', onOnlineChange)
  if (navigator.connection?.removeEventListener) {
    navigator.connection.removeEventListener('change', onOnlineChange)
  }
})
</script>

<template>
  <div class="std-cache-ui">
    <button
      type="button"
      class="std-cache-trigger"
      aria-label="标准图缓存状态"
      @click="store.managementOpen = true"
    >
      标准图缓存
      <span v-if="store.stats.missingCount">{{ store.stats.missingCount }}</span>
    </button>

    <div
      v-if="store.firstPromptOpen && store.stats.totalCount > 0 && store.stats.missingCount > 0"
      class="std-cache-mask is-first-prompt"
      role="presentation"
    >
      <section class="std-cache-card" role="dialog" aria-modal="true" aria-label="下载全部标准图">
        <p class="std-cache-kicker">STANDARD PHOTOS</p>
        <h2>先下载标准图</h2>
        <p>
          本机还有 {{ store.stats.missingCount }} 张标准图未缓存，
          共约 {{ formatBytes(store.stats.missingBytes) }}。
          下载后可更快打开，并在短时断网时继续查看。
        </p>
        <p v-if="store.errorText" class="std-cache-error">{{ store.errorText }}</p>
        <div class="std-cache-actions">
          <button type="button" class="btn btn-primary" :disabled="store.busy" @click="store.startFirstDownload()">开始下载</button>
          <button type="button" class="btn" :disabled="store.busy" @click="store.skipFirstRun()">稍后下载</button>
        </div>
      </section>
    </div>

    <section
      v-if="store.progress"
      class="std-cache-progress"
      :class="{ 'is-collapsed': progressCollapsed }"
      aria-live="polite"
    >
      <div class="std-cache-progress-head">
        <strong>正在缓存标准图 {{ store.progress.completed }}/{{ store.progress.total }}</strong>
        <span>{{ formatBytes(store.progress.downloadedBytes) }}</span>
        <button
          type="button"
          :aria-expanded="!progressCollapsed"
          @click="progressCollapsed = !progressCollapsed"
        >{{ progressCollapsed ? '展开' : '收起' }}</button>
      </div>
      <progress :value="progressPercent" max="100">{{ progressPercent }}%</progress>
      <template v-if="!progressCollapsed">
        <small v-if="store.progress.current">
          {{ store.progress.current.item_name || `标准图 ${store.progress.current.item_id}` }}
        </small>
      </template>
    </section>

    <button
      v-if="store.deferred"
      type="button"
      class="std-cache-banner"
      @click="store.checkForUpdates({ download: true })"
    >
      有 {{ store.deferred.missing.length }} 张标准图待更新 · 立即更新
    </button>

    <div v-if="store.notice" class="std-cache-notice" :class="`is-${store.notice.type}`" role="status">
      <span>{{ store.notice.message }}</span>
      <button type="button" aria-label="关闭更新提示" @click="store.dismissNotice()">关闭</button>
    </div>

    <div v-if="store.managementOpen" class="std-cache-mask" role="presentation">
      <section class="std-cache-card" role="dialog" aria-modal="true" aria-label="标准图缓存管理">
        <p class="std-cache-kicker">CACHE · 本机</p>
        <h2>标准图缓存</h2>
        <dl class="std-cache-stats">
          <div><dt>已缓存</dt><dd>{{ store.stats.entryCount }} 张</dd></div>
          <div><dt>占用</dt><dd>{{ formatBytes(store.stats.byteSize) }}</dd></div>
          <div><dt>缺图</dt><dd>{{ store.stats.missingCount }} 张</dd></div>
          <div><dt>失败</dt><dd>{{ store.stats.failureCount }} 张</dd></div>
          <div>
            <dt>最后同步</dt>
            <dd>{{ formatChinaSyncTime(store.stats.lastSyncAt) || '尚未同步' }}</dd>
          </div>
        </dl>
        <p v-if="store.errorText" class="std-cache-error">{{ store.errorText }}</p>
        <div class="std-cache-actions">
          <button type="button" class="btn btn-primary" :disabled="store.busy" @click="checkUpdates">检查更新</button>
          <button type="button" class="btn" :disabled="store.busy || !store.stats.failureCount" @click="store.retryFailed()">重试失败</button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="store.busy"
            @click="clearCache"
          >清除缓存</button>
          <button type="button" class="btn" @click="store.managementOpen = false">关闭</button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.std-cache-ui {
  position: relative;
  z-index: 40;
}
.std-cache-trigger {
  position: fixed;
  right: 14px;
  bottom: 76px;
  z-index: 41;
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  background: var(--hy-surface);
  color: var(--hy-muted);
  padding: 7px 11px;
  font: 700 11px/1 var(--font-mono);
  box-shadow: 0 8px 20px rgba(0, 0, 0, .24);
}
.std-cache-trigger span {
  margin-left: 5px;
  color: var(--hy-mint-bright);
}
.std-cache-mask {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(3, 11, 10, .72);
  backdrop-filter: blur(8px);
}
/* 首次下载提示必须低于拍摄/对照弹层（.staff-preview 是 60）：它是启动时自动弹的，
   z-index 高于相机时会直接压在取景画面上，员工点不到快门。管理面板是用户主动打开
   的，保持在上层。 */
.std-cache-mask.is-first-prompt {
  z-index: 55;
}
.std-cache-card {
  width: min(520px, 100%);
  max-height: min(86vh, 720px);
  overflow: auto;
  border: 1px solid var(--hy-line);
  border-radius: 10px;
  background: var(--hy-surface);
  color: var(--hy-ink);
  padding: 20px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, .38);
}
.std-cache-card h2 {
  margin: 0 0 10px;
  font-size: 22px;
}
.std-cache-card p {
  margin: 0 0 16px;
  color: var(--hy-muted);
  line-height: 1.65;
}
.std-cache-kicker {
  margin-bottom: 6px !important;
  color: var(--hy-mint) !important;
  font: 700 10px/1.4 var(--font-mono);
  letter-spacing: .16em;
}
.std-cache-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.std-cache-progress,
.std-cache-notice,
.std-cache-banner {
  position: fixed;
  left: 50%;
  z-index: 42;
  transform: translateX(-50%);
  width: min(620px, calc(100vw - 24px));
  border: 1px solid var(--hy-line);
  border-radius: 8px;
  background: var(--hy-surface);
  box-shadow: 0 16px 36px rgba(0, 0, 0, .3);
}
.std-cache-progress {
  bottom: 14px;
  padding: 10px 12px;
}
.std-cache-progress-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}
.std-cache-progress-head button {
  border: 0;
  background: transparent;
  color: var(--hy-mint);
  font: inherit;
}
.std-cache-progress progress {
  width: 100%;
  margin-top: 7px;
}
.std-cache-progress small {
  display: block;
  margin-top: 4px;
  color: var(--hy-muted);
}
.std-cache-banner {
  bottom: 70px;
  padding: 10px 12px;
  color: var(--hy-mint-bright);
  font-size: 12px;
}
.std-cache-notice {
  bottom: 14px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  font-size: 12px;
}
.std-cache-notice.is-success { border-color: var(--hy-mint-line); }
.std-cache-notice.is-warning { border-color: var(--yellow); }
.std-cache-notice button {
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
}
.std-cache-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 16px;
}
.std-cache-stats div {
  border: 1px solid var(--hy-line);
  border-radius: 7px;
  padding: 9px;
}
.std-cache-stats dt {
  color: var(--hy-faint);
  font-size: 11px;
}
.std-cache-stats dd {
  margin: 3px 0 0;
  font-weight: 700;
}
.std-cache-error { color: var(--red) !important; }
@media (max-width: 720px) {
  .std-cache-trigger { bottom: 86px; }
  .std-cache-stats { grid-template-columns: 1fr; }
}
</style>
