<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLogs } from '../composables/useLogs'
import SvgIcon from '../components/SvgIcon.vue'
import LuyunNumberInput from '../components/ui/LuyunNumberInput.vue'

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
const COPY_LABEL_DEFAULT = '复制全部'
const COPY_LABEL_RESET_MS = 1200

const copyLabel = ref(COPY_LABEL_DEFAULT)
let copyLabelTimer = null

const statRangeText = computed(() => {
  if (!stats.value?.earliest) return '—'
  return `${fmtTs(stats.value.earliest)} → ${fmtTs(stats.value.latest)}`
})

const statsDbPathText = computed(() => {
  const dbPath = stats.value?.db_path
  if (!dbPath) return ''
  return String(dbPath).split('/').slice(-2).join('/')
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

function toggleExpand(id) {
  if (!id) return
  const next = new Set(expandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
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

async function copyAll() {
  const text = items.value.map((it) => {
    const lvl = (it.level || 'INFO').toUpperCase()
    return `${fmtTs(it.timestamp || it.ts)} [${lvl}] ${it.logger || ''} ${it.message || ''}`
  }).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    copyLabel.value = '已复制'
    if (copyLabelTimer) clearTimeout(copyLabelTimer)
    copyLabelTimer = setTimeout(() => { copyLabel.value = COPY_LABEL_DEFAULT }, COPY_LABEL_RESET_MS)
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
  if (copyLabelTimer) clearTimeout(copyLabelTimer)
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
        <div style="display:flex;gap:6px;align-items:center">
          <button type="button" class="btn btn-sm logs-mobile-filter-toggle" @click="toggleSidebar">
            <SvgIcon name="menu" :size="14" />
            筛选
          </button>
          <button class="btn btn-sm" @click="setPlaying(!playing)">
            <SvgIcon :name="playing ? 'pause' : 'play'" :size="13" /> {{ playing ? '暂停' : '继续' }}
          </button>
          <button class="btn btn-sm" @click="items.length = 0" title="清空当前视图（不影响数据库）">清空视图</button>
          <button class="btn btn-sm" @click="copyAll">{{ copyLabel }}</button>
        </div>
      </div>

      <div ref="logWrapRef" class="logs-wrap luyun-scrollbar" @scroll="handleScroll">
        <div v-if="!items.length" class="empty-state">等待日志…</div>
        <div
          v-for="it in items"
          :key="it.id || it.ts || it.timestamp"
          class="logs-line"
          :class="[`lvl-${(it.level || 'INFO').toUpperCase()}`, { 'has-exception': !!it.exception }]"
          @click="it.exception && toggleExpand(it.id)"
        >
          <div class="ts" :title="it.timestamp || it.ts">{{ fmtTs(it.timestamp || it.ts) }}</div>
          <div class="lvl">{{ (it.level || 'INFO').toUpperCase() }}</div>
          <div class="lg" :title="it.logger" @click.stop="clickLogger(it.logger)">{{ it.logger }}</div>
          <div class="msg">
            <template v-for="(part, i) in highlightParts(it.message, filters.q)" :key="i">
              <span :class="{ hit: part.hit }">{{ part.text }}</span>
            </template>
          </div>
          <div v-if="it.exception && expandedIds.has(it.id)" class="ex">{{ it.exception }}</div>
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
.logs-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 10px 10px 0 0; gap: 10px; flex-shrink: 0; font-size: 11.5px; color: var(--text-dim); }
.logs-auto-state { display: inline-flex; align-items: center; gap: 5px; }
.logs-auto-state .dot { width: 6px; height: 6px; border-radius: 99px; background: var(--green); box-shadow: 0 0 6px var(--green); }
.logs-auto-state.paused .dot { background: var(--text-dim); box-shadow: none; }
.logs-mobile-filter-toggle { display: none; }
.logs-wrap { flex: 1; overflow-y: auto; background: #07090f; font-family: "SF Mono", Menlo, Consolas, "Courier New", monospace; font-size: 11.5px; line-height: 1.55; border: 1px solid var(--border); border-top: none; border-radius: 0 0 10px 10px; }
.logs-line { display: grid; grid-template-columns: 170px 60px 200px 1fr; gap: 10px; padding: 2px 14px; border-bottom: 1px solid rgba(31,41,55,0.4); align-items: start; word-break: break-word; white-space: pre-wrap; }
.logs-line:hover { background: rgba(99,102,241,0.06); }
.logs-line .ts { color: #6b7280; font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.logs-line .lvl { font-size: 10.5px; font-weight: 600; text-align: center; border-radius: 3px; padding: 0 4px; line-height: 18px; align-self: center; }
.logs-line .lg { color: #9ca3af; font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.logs-line .lg:hover { color: var(--accent); }
.logs-line .msg { color: #d1d5db; white-space: pre-wrap; word-break: break-word; }
.logs-line .msg .hit { background: rgba(245,158,11,0.25); color: #fde68a; border-radius: 2px; padding: 0 2px; }
.logs-line.lvl-INFO .lvl { color: #22c55e; background: rgba(34,197,94,0.12); }
.logs-line.lvl-WARNING .lvl { color: #f59e0b; background: rgba(245,158,11,0.12); }
.logs-line.lvl-ERROR .lvl { color: #ef4444; background: rgba(239,68,68,0.12); }
.logs-line.lvl-CRITICAL .lvl { color: #fff; background: var(--red); }
.logs-line.lvl-DEBUG .lvl { color: #6b7280; background: rgba(107,114,128,0.12); }
.logs-line .ex { grid-column: 1 / -1; padding: 6px 10px; background: rgba(239,68,68,0.06); color: #fca5a5; font-size: 10.5px; border-top: 1px solid rgba(239,68,68,0.15); white-space: pre-wrap; }
.logs-line.has-exception { cursor: help; }
.logs-line.has-exception .msg { border-bottom: 1px dashed var(--border); }

.logs-scroll-jump { position: absolute; right: 18px; bottom: 18px; background: var(--accent); color: #fff; border: none; border-radius: 99px; width: 34px; height: 34px; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; }

/* 移动端：侧栏筛选折叠为滑出抽屉（对齐旧页 760px 断点，见 public/logs.html:664-674） */
@media (max-width: 760px) {
  .logs-mobile-filter-toggle { display: inline-flex; }

  .logs-sidebar {
    position: fixed;
    top: clamp(38px, 4.4vh, 46px);
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
    top: clamp(38px, 4.4vh, 46px);
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

  .logs-line { grid-template-columns: 120px 50px 1fr; }
  .logs-line .lg { display: none; }
}
</style>
