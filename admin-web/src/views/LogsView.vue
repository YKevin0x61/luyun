<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useLogs } from '../composables/useLogs'
import SvgIcon from '../components/SvgIcon.vue'
import LuyunNumberInput from '../components/ui/LuyunNumberInput.vue'
import { buildLogsCopyText, selectLogsForCopy } from '../utils/logCopy'

const {
  mode, playing, filters, items, resultCountText, facets, stats, history, filterLabel,
  setMode, setPlaying, applyFilters, resetFilters, loadHistoryMore, cleanup, init,
} = useLogs()

const qInput = ref(filters.q)
const sinceInput = ref('')
const untilInput = ref('')
const logWrapRef = ref(null)
const showScrollBtn = ref(false)
const expandedIds = ref(new Set())

// 移动端筛选抽屉，对齐旧页 public/logs.html 的 filterToggleBtn（760px 断点，见 public/logs.html:664-674）。
const MOBILE_FILTER_QUERY = '(max-width: 760px)'
const sidebarOpen = ref(false)
let mobileFilterMql = null

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
}
function closeSidebar() {
  sidebarOpen.value = false
}

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const COPY_LABEL_RESET_MS = 1200
const COPY_LABELS = {
  all: '复制全部',
  error: '仅复制错误日志',
  warning: '仅复制警告日志',
}
const COPY_EMPTY_MESSAGES = {
  error: '当前视图暂无错误日志',
  warning: '当前视图暂无警告日志',
}

const copyLabels = reactive({ ...COPY_LABELS })
const copyLabelTimers = new Map()

const statRangeText = computed(() => {
  if (!stats.value?.earliest) return '—'
  return `${fmtTs(stats.value.earliest)} → ${fmtTs(stats.value.latest)}`
})

const hasActiveFilters = computed(() => (
  filters.level !== 'ALL'
  || !!filters.logger
  || !!filters.q
  || !!filters.sinceMin
  || !!filters.untilMin
))

const emptyStateText = computed(() => {
  if (resultCountText.value === '加载失败') return '日志加载失败，请稍后重试'
  if (hasActiveFilters.value) return '没有符合当前筛选条件的日志'
  if (mode.value === 'history') return '当前时间范围内没有日志'
  if (!playing.value) return '日志接收已暂停'
  return '等待新日志…'
})

const statsDbPathText = computed(() => {
  // 日志已随 ADR 0089 进 PostgreSQL：不再有 logs.db 路径，只显示后端。
  const backend = stats.value?.backend
  return backend ? String(backend) : ''
})

function fmtTs(ts) {
  if (!ts) return ''
  if (typeof ts === 'number') return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

function highlightParts(text, q) {
  const safe = String(text ?? '')
  if (!q) return [{ text: safe, hit: false }]
  const lower = safe.toLowerCase()
  const qLower = q.toLowerCase()
  const parts = []
  let idx = 0
  while (idx < safe.length) {
    const found = lower.indexOf(qLower, idx)
    if (found === -1) {
      parts.push({ text: safe.slice(idx), hit: false })
      break
    }
    if (found > idx) parts.push({ text: safe.slice(idx, found), hit: false })
    parts.push({ text: safe.slice(found, found + q.length), hit: true })
    idx = found + q.length
  }
  return parts.length ? parts : [{ text: safe, hit: false }]
}

function isAtTop() {
  const el = logWrapRef.value
  if (!el) return true
  return el.scrollTop < 80
}

function isAtBottom() {
  const el = logWrapRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function scrollToTop() {
  const el = logWrapRef.value
  if (el) el.scrollTop = 0
  showScrollBtn.value = false
}

function scrollToBottom() {
  const el = logWrapRef.value
  if (el) el.scrollTop = el.scrollHeight
  showScrollBtn.value = false
}

function handleScroll() {
  const el = logWrapRef.value
  if (!el) return
  if (mode.value === 'history' && el.scrollTop < 50 && history.hasMore && !history.loading) {
    loadHistoryMore()
  }
  showScrollBtn.value = mode.value === 'realtime' ? !isAtTop() : !isAtBottom()
}

function logItemKey(item, index = 0) {
  return item?.id ?? item?.ts ?? item?.timestamp ?? `row-${index}`
}

function isExpanded(item, index) {
  return expandedIds.value.has(logItemKey(item, index))
}

function toggleExpand(item, index) {
  const key = logItemKey(item, index)
  const next = new Set(expandedIds.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedIds.value = next
}

function clickLogger(logger) {
  filters.logger = logger
  applyFilters({ logger })
}

function applyFilterForm() {
  applyFilters({
    q: qInput.value.trim(),
    sinceMin: parseInt(sinceInput.value, 10) || 0,
    untilMin: parseInt(untilInput.value, 10) || 0,
  })
}

function resetFilterForm() {
  qInput.value = ''
  sinceInput.value = ''
  untilInput.value = ''
  resetFilters()
}

async function handleCleanup() {
  if (!window.confirm('确定要删除 7 天前的所有日志？此操作不可恢复。')) return
  const data = await cleanup(7)
  window.alert(`已删除 ${data.deleted} 条日志`)
}

function flashCopied(scope) {
  copyLabels[scope] = '已复制'
  if (copyLabelTimers.has(scope)) clearTimeout(copyLabelTimers.get(scope))
  copyLabelTimers.set(scope, setTimeout(() => {
    copyLabels[scope] = COPY_LABELS[scope]
    copyLabelTimers.delete(scope)
  }, COPY_LABEL_RESET_MS))
}

async function copySelection(scope) {
  const selected = selectLogsForCopy(items.value, scope)
  if (scope !== 'all' && !selected.length) {
    window.alert(COPY_EMPTY_MESSAGES[scope])
    return
  }

  const text = buildLogsCopyText(selected, fmtTs)
  try {
    await navigator.clipboard.writeText(text)
    flashCopied(scope)
  } catch (e) {
    window.alert('复制失败，请手动选择')
  }
}

// 实时：最新在顶部，跟随时滚到顶；历史：滚到底查看较新条目
watch(items, () => {
  nextTick(() => {
    if (mode.value === 'realtime') {
      if (playing.value && (isAtTop() || items.value.length <= 1)) scrollToTop()
    } else if (isAtBottom() || items.value.length <= 1) {
      scrollToBottom()
    }
  })
})

onMounted(() => {
  init()
  mobileFilterMql = window.matchMedia(MOBILE_FILTER_QUERY)
  mobileFilterMql.addEventListener('change', closeSidebar)
})

onBeforeUnmount(() => {
  for (const timer of copyLabelTimers.values()) clearTimeout(timer)
  copyLabelTimers.clear()
  mobileFilterMql?.removeEventListener('change', closeSidebar)
})
</script>

<template>
  <div class="logs-page" :class="{ 'sidebar-open': sidebarOpen }">
    <div class="logs-sidebar-backdrop" aria-hidden="true" @click="closeSidebar"></div>
    <aside class="logs-sidebar card">
      <div class="logs-panel logs-panel-hint">
        实时模式下侧栏筛选会同步应用到增量拉取（级别 / logger / 关键词）。
      </div>
      <div class="logs-panel">
        <div class="logs-panel-title">模式</div>
        <div class="logs-toggle">
          <button :class="{ active: mode === 'realtime' }" @click="setMode('realtime')">实时</button>
          <button :class="{ active: mode === 'history' }" @click="setMode('history')">历史</button>
        </div>
      </div>

      <div class="logs-panel">
        <div class="logs-panel-title">级别</div>
        <div class="logs-chip-group">
          <span
            v-for="lvl in LEVELS"
            :key="lvl"
            class="logs-chip"
            :class="{ active: filters.level === lvl }"
            @click="applyFilters({ level: lvl })"
          >{{ lvl === 'ALL' ? '全部' : lvl }}</span>
        </div>
      </div>

      <div class="logs-panel">
        <div class="logs-panel-title">logger</div>
        <select class="select" style="width:100%" :value="filters.logger" @change="applyFilters({ logger: $event.target.value })">
          <option value="">全部</option>
          <option v-for="l in facets.loggers || []" :key="l.value" :value="l.value">{{ l.value }} ({{ l.count }})</option>
        </select>
      </div>

      <div class="logs-panel">
        <div class="logs-panel-title">关键词 / 时间</div>
        <div class="form-row">
          <label>搜索</label>
          <input class="input" v-model="qInput" placeholder="模糊匹配 message / 异常" @keydown.enter="applyFilterForm" />
        </div>
        <div style="display:flex;gap:6px">
          <div class="form-row" style="flex:1">
            <label>起始（分钟前）</label>
            <LuyunNumberInput v-model="sinceInput" :min="0" placeholder="0 = 不限" />
          </div>
          <div class="form-row" style="flex:1">
            <label>结束（分钟前）</label>
            <LuyunNumberInput v-model="untilInput" :min="0" placeholder="0 = 不限" />
          </div>
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-primary btn-sm" style="flex:1" @click="applyFilterForm">应用筛选</button>
          <button class="btn btn-sm" style="flex:1" @click="resetFilterForm">重置</button>
        </div>
      </div>

      <div class="logs-panel">
        <div class="logs-panel-title">存储 <span v-if="statsDbPathText" class="logs-db-path">{{ statsDbPathText }}</span></div>
        <div class="logs-stat-grid">
          <div class="logs-stat-mini"><div class="v">{{ stats?.total ?? '—' }}</div><div class="l">总数</div></div>
          <div class="logs-stat-mini"><div class="v">{{ stats?.queue_size ?? '—' }}</div><div class="l">队列</div></div>
          <div class="logs-stat-mini warn"><div class="v">{{ stats?.last_hour?.WARNING ?? 0 }}</div><div class="l">近 1h 警告</div></div>
          <div class="logs-stat-mini danger"><div class="v">{{ stats?.last_hour?.ERROR ?? 0 }}</div><div class="l">近 1h 错误</div></div>
          <div class="logs-stat-mini" style="grid-column:1 / -1"><div class="v" style="font-size:11px">{{ statRangeText }}</div><div class="l">时间范围</div></div>
        </div>
        <button class="btn btn-sm" style="width:100%;margin-top:8px" @click="handleCleanup"><SvgIcon name="trash-2" :size="12" /> 清理 7 天前</button>
      </div>
    </aside>

    <div class="logs-content">
      <div class="logs-toolbar">
        <div class="logs-toolbar-left">
          <span
            class="logs-auto-state"
            :class="{ paused: !playing }"
            :title="playing ? '实时接收中，新日志会显示在顶部' : '已暂停接收新日志'"
          ><span class="dot"></span>{{ playing ? '实时跟踪中' : '已暂停' }}</span>
          <span style="color:#4b5563">·</span>
          <span>{{ resultCountText }}</span>
          <span style="color:#4b5563">·</span>
          <span>{{ mode === 'realtime' ? '实时跟踪' : '历史查询' }}</span>
          <span style="color:var(--text-dim)">{{ filterLabel }}</span>
        </div>
        <div class="logs-toolbar-actions">
          <button type="button" class="btn btn-sm logs-mobile-filter-toggle" @click="toggleSidebar">
            <SvgIcon name="menu" :size="14" />
            筛选
          </button>
          <button class="btn btn-sm" @click="setPlaying(!playing)">
            <SvgIcon :name="playing ? 'pause' : 'play'" :size="13" /> {{ playing ? '暂停' : '继续' }}
          </button>
          <button class="btn btn-sm" @click="items.length = 0" title="清空当前视图（不影响数据库）">清空视图</button>
          <button class="btn btn-sm" @click="copySelection('all')">{{ copyLabels.all }}</button>
          <button class="btn btn-sm" @click="copySelection('error')" title="复制当前视图中的错误日志（含 CRITICAL）">{{ copyLabels.error }}</button>
          <button class="btn btn-sm" @click="copySelection('warning')" title="复制当前视图中的警告日志">{{ copyLabels.warning }}</button>
        </div>
      </div>

      <div ref="logWrapRef" class="logs-wrap luyun-scrollbar" @scroll="handleScroll">
        <div v-if="!items.length" class="logs-empty empty-state">{{ emptyStateText }}</div>
        <div v-else class="logs-columns" aria-hidden="true">
          <span>时间</span>
          <span>级别</span>
          <span>Logger</span>
          <span>消息</span>
          <span></span>
        </div>
        <div
          v-for="(it, index) in items"
          :key="logItemKey(it, index)"
          class="logs-line"
          :class="`lvl-${(it.level || 'INFO').toUpperCase()}`"
        >
          <time class="ts" :datetime="it.timestamp || it.ts" :title="it.timestamp || it.ts">{{ fmtTs(it.timestamp || it.ts) }}</time>
          <span class="lvl">{{ (it.level || 'INFO').toUpperCase() }}</span>
          <button
            type="button"
            class="lg"
            :title="`按 logger ${it.logger || '未标注'} 筛选`"
            :aria-label="`按 logger ${it.logger || '未标注'} 筛选`"
            @click="clickLogger(it.logger)"
          >{{ it.logger || '未标注' }}</button>
          <div class="msg">
            <template v-for="(part, i) in highlightParts(it.message, filters.q)" :key="i">
              <span :class="{ hit: part.hit }">{{ part.text }}</span>
            </template>
          </div>
          <div class="log-actions">
            <button
              v-if="it.exception"
              type="button"
              class="ex-toggle"
              :class="{ active: isExpanded(it, index) }"
              :aria-expanded="isExpanded(it, index)"
              :aria-controls="`log-exception-${index}`"
              @click="toggleExpand(it, index)"
            >
              <SvgIcon :name="isExpanded(it, index) ? 'chevron-up' : 'chevron-down'" :size="12" />
              {{ isExpanded(it, index) ? '收起' : '异常' }}
            </button>
          </div>
          <div v-if="it.exception && isExpanded(it, index)" :id="`log-exception-${index}`" class="ex">
            <div class="ex-title">异常堆栈</div>
            <div class="ex-body">
              <template v-for="(part, i) in highlightParts(it.exception, filters.q)" :key="i">
                <span :class="{ hit: part.hit }">{{ part.text }}</span>
              </template>
            </div>
          </div>
        </div>
      </div>
      <button
        v-if="showScrollBtn"
        class="logs-scroll-jump"
        :title="mode === 'realtime' ? '回到最新' : '滚到底部'"
        @click="mode === 'realtime' ? scrollToTop() : scrollToBottom()"
      ><SvgIcon :name="mode === 'realtime' ? 'chevron-up' : 'chevron-down'" :size="16" /></button>
    </div>
  </div>
</template>

<style scoped>
.logs-page { display: flex; gap: 12px; height: calc(100vh - 90px); position: relative; }
.logs-sidebar { width: 260px; flex-shrink: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0; padding: 0; }
.logs-sidebar-backdrop { display: none; }
.logs-panel { padding: 12px 14px; border-bottom: 1px solid var(--border); }
.logs-panel:last-child { border-bottom: none; }
.logs-panel-hint { font-size: 11px; color: #9ca3af; line-height: 1.5; }
.logs-panel-title { font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--text-dim); margin-bottom: 8px; }
.logs-toggle { display: inline-flex; background: var(--bg); border: 1px solid var(--border); border-radius: 5px; overflow: hidden; width: 100%; }
.logs-toggle button { flex: 1; background: transparent; border: none; color: var(--text-dim); padding: 5px 12px; font-size: 11.5px; cursor: pointer; font-family: inherit; }
.logs-toggle button.active { background: var(--accent); color: #fff; }
.logs-chip-group { display: flex; flex-wrap: wrap; gap: 4px; }
.logs-chip { background: var(--bg); border: 1px solid var(--border); border-radius: 99px; padding: 2px 8px; font-size: 10.5px; color: var(--text-dim); cursor: pointer; user-select: none; }
.logs-chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.logs-stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.logs-stat-mini { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.logs-stat-mini .v { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.logs-stat-mini .l { font-size: 10px; color: var(--text-dim); margin-top: 1px; }
.logs-stat-mini.warn .v { color: var(--yellow); }
.logs-stat-mini.danger .v { color: var(--red); }
.logs-db-path { font-size: 9.5px; color: #4b5563; text-transform: none; letter-spacing: 0; }

.logs-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; position: relative; }
.logs-toolbar { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; padding: 8px 14px; background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 10px 10px 0 0; gap: 8px 10px; flex-shrink: 0; font-size: 11.5px; color: var(--text-dim); }
.logs-toolbar-left, .logs-toolbar-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.logs-toolbar-actions { justify-content: flex-end; margin-left: auto; }
.logs-auto-state { display: inline-flex; align-items: center; gap: 5px; }
.logs-auto-state .dot { width: 6px; height: 6px; border-radius: 99px; background: var(--green); box-shadow: 0 0 6px var(--green); }
.logs-auto-state.paused .dot { background: var(--text-dim); box-shadow: none; }
.logs-mobile-filter-toggle { display: none; }
.logs-wrap { flex: 1; overflow-y: auto; background: #07090f; font-family: "SF Mono", Menlo, Consolas, "Courier New", monospace; font-size: 11.5px; line-height: 1.55; border: 1px solid var(--border); border-top: none; border-radius: 0 0 10px 10px; }
.logs-empty { min-height: 180px; display: grid; place-items: center; }
.logs-columns { position: sticky; top: 0; z-index: 2; display: grid; grid-template-columns: 166px 58px minmax(120px, 190px) minmax(0, 1fr) 58px; gap: 10px; padding: 6px 14px; background: rgba(11,16,26,0.98); border-bottom: 1px solid var(--border); color: #7f8a9b; font-family: system-ui, sans-serif; font-size: 10px; letter-spacing: .04em; }
.logs-line { display: grid; grid-template-columns: 166px 58px minmax(120px, 190px) minmax(0, 1fr) 58px; gap: 10px; padding: 4px 14px; border-bottom: 1px solid rgba(31,41,55,0.4); align-items: start; word-break: break-word; white-space: pre-wrap; }
.logs-line.lvl-WARNING { background: rgba(245,158,11,0.025); }
.logs-line.lvl-ERROR { background: rgba(239,68,68,0.035); }
.logs-line.lvl-CRITICAL { background: rgba(239,68,68,0.065); }
.logs-line:hover { background: rgba(99,102,241,0.08); }
.logs-line .ts { color: #8b95a5; font-size: 10.5px; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.logs-line .lvl { min-width: 58px; font-size: 10.5px; font-weight: 600; text-align: center; border-radius: 4px; padding: 0 5px; line-height: 18px; align-self: center; }
.logs-line .lg { min-width: 0; padding: 0; border: 0; background: transparent; color: #9ca3af; font-family: inherit; font-size: 10.5px; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.logs-line .lg:hover { color: var(--accent); }
.logs-line .lg:focus-visible, .logs-line .ex-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.logs-line .msg { color: #d7dee8; font-size: 12px; overflow-wrap: anywhere; }
.logs-line .msg .hit { background: rgba(245,158,11,0.25); color: #fde68a; border-radius: 2px; padding: 0 2px; }
.logs-line.lvl-INFO .lvl { color: #22c55e; background: rgba(34,197,94,0.12); }
.logs-line.lvl-WARNING .lvl { color: #f59e0b; background: rgba(245,158,11,0.12); }
.logs-line.lvl-ERROR .lvl { color: #ef4444; background: rgba(239,68,68,0.12); }
.logs-line.lvl-CRITICAL .lvl { color: #fff; background: var(--red); }
.logs-line.lvl-DEBUG .lvl { color: #9ca3af; background: rgba(107,114,128,0.16); }
.logs-line .log-actions { display: flex; justify-content: flex-end; align-items: center; min-height: 20px; }
.logs-line .ex-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 3px; min-height: 20px; padding: 1px 5px; border: 1px solid rgba(239,68,68,0.3); border-radius: 4px; background: rgba(239,68,68,0.07); color: #fca5a5; font-family: inherit; font-size: 10px; cursor: pointer; }
.logs-line .ex-toggle:hover, .logs-line .ex-toggle.active { border-color: rgba(239,68,68,0.55); background: rgba(239,68,68,0.13); color: #fecaca; }
.logs-line .ex { grid-column: 1 / -1; overflow: hidden; margin: 2px 0 4px; border: 1px solid rgba(239,68,68,0.18); border-radius: 6px; background: rgba(127,29,29,0.12); }
.logs-line .ex-title { padding: 5px 10px; border-bottom: 1px solid rgba(239,68,68,0.15); color: #fca5a5; font-family: system-ui, sans-serif; font-size: 10px; font-weight: 600; }
.logs-line .ex-body { padding: 7px 10px; color: #fca5a5; font-size: 10.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.logs-line .ex-body .hit { background: rgba(245,158,11,0.25); color: #fde68a; border-radius: 2px; padding: 0 2px; }

.logs-scroll-jump { position: absolute; right: 18px; bottom: 18px; background: var(--accent); color: #fff; border: none; border-radius: 99px; width: 34px; height: 34px; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; }

/* 移动端：侧栏筛选折叠为滑出抽屉（对齐旧页 760px 断点，见 public/logs.html:664-674） */
@media (max-width: 760px) {
  .logs-mobile-filter-toggle { display: inline-flex; }
  .logs-toolbar-left, .logs-toolbar-actions { width: 100%; }
  .logs-toolbar-actions { justify-content: flex-start; margin-left: 0; }

  .logs-sidebar {
    position: fixed;
    top: var(--global-nav-height, 46px);
    left: 0;
    bottom: 0;
    width: min(280px, 86vw);
    z-index: 60;
    border-radius: 0;
    transform: translateX(-105%);
    transition: transform .25s ease-out, box-shadow .25s ease-out;
    box-shadow: none;
  }
  .logs-page.sidebar-open .logs-sidebar {
    transform: translateX(0);
    box-shadow: 4px 0 28px rgba(0, 0, 0, 0.45);
  }
  .logs-sidebar-backdrop {
    display: block;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    top: var(--global-nav-height, 46px);
    background: rgba(0, 0, 0, 0.55);
    z-index: 55;
    opacity: 0;
    pointer-events: none;
    transition: opacity .25s ease-out;
  }
  .logs-page.sidebar-open .logs-sidebar-backdrop {
    opacity: 1;
    pointer-events: auto;
  }

  .logs-columns { display: none; }
  .logs-line { grid-template-columns: minmax(0, 1fr) auto auto; gap: 5px 8px; padding: 8px 10px; }
  .logs-line .ts { grid-column: 1; grid-row: 1; align-self: center; }
  .logs-line .lvl { grid-column: 2; grid-row: 1; align-self: center; }
  .logs-line .log-actions { grid-column: 3; grid-row: 1; }
  .logs-line .lg { grid-column: 1 / -1; grid-row: 2; display: block; color: #8994a6; }
  .logs-line .msg { grid-column: 1 / -1; grid-row: 3; font-size: 12px; line-height: 1.6; }
  .logs-line .ex { grid-column: 1 / -1; grid-row: 4; }
}
</style>
