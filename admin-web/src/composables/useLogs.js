import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { api } from '../api/client'
import { useNudgePull } from './useNudgePull'

const MAX_VISIBLE = 1500
const HISTORY_PAGE_SIZE = 100
const LOGS_SUBSCRIPTION_ID = 'admin-logs'
const REALTIME_PULL_LIMIT = 500 // 与 /api/logs/recent 的 le=500 上限一致

/**
 * 实时日志走 nudge + pull：subscribe `logs`（带 level/logger/q 过滤，供后端
 * 未来按 scope 精确派发），挂载/切换过滤时先拉一次 `/api/logs/recent`，
 * 之后收到 `{type:'nudge', topic:'logs'}` 再增量拉取（`after_id`）。
 * 同一个 nudge 事件也驱动 stats/facets 刷新，以及历史模式首页的重新拉取，
 * 替代旧页的 5s/10s/30s 定时轮询。不加任何定时轮询。
 */
export function useLogs() {
  const mode = ref('realtime') // 'realtime' | 'history'
  const playing = ref(true)
  const filters = reactive({ level: 'ALL', logger: '', q: '', sinceMin: 0, untilMin: 0 })
  const items = ref([])
  const resultCountText = ref('—')
  const facets = ref({ levels: [], loggers: [] })
  const stats = ref(null)

  const history = reactive({ page: 0, hasMore: true, loading: false })
  const realtime = reactive({ lastId: 0 })

  const filterLabel = computed(() => {
    const parts = []
    if (filters.level && filters.level !== 'ALL') parts.push(filters.level)
    if (filters.logger) parts.push(filters.logger)
    if (filters.q) parts.push(`"${filters.q}"`)
    if (filters.sinceMin) parts.push(`起 ${filters.sinceMin}m 前`)
    if (filters.untilMin) parts.push(`至 ${filters.untilMin}m 前`)
    return parts.length ? '· 过滤: ' + parts.join(' / ') : ''
  })

  function buildLogsFilters() {
    const f = {}
    if (filters.level && filters.level !== 'ALL') f.level = filters.level
    if (filters.logger) f.logger = filters.logger
    if (filters.q) f.q = filters.q
    return f
  }

  function trim() {
    if (items.value.length <= MAX_VISIBLE) return
    // 实时：最新在顶部，裁掉末尾最旧条目；历史：向上分页加载旧日志，裁掉顶部最旧条目
    items.value = mode.value === 'realtime'
      ? items.value.slice(0, MAX_VISIBLE)
      : items.value.slice(items.value.length - MAX_VISIBLE)
  }

  function clearView() {
    items.value = []
  }

  function matchesFilters(item) {
    if (filters.level && filters.level !== 'ALL' && item.level !== filters.level) return false
    if (filters.logger && item.logger !== filters.logger) return false
    if (filters.q && !String(item.message || '').toLowerCase().includes(filters.q.toLowerCase())) return false
    return true
  }

  async function pullRealtime(reset = false) {
    if (mode.value !== 'realtime') return
    try {
      const afterId = reset ? 0 : realtime.lastId
      const data = await api.get('/api/logs/recent', { limit: REALTIME_PULL_LIMIT, after_id: afterId })
      if (!data.success) return
      const matched = (data.items || []).filter(matchesFilters)
      if (reset) {
        items.value = [...matched].reverse()
      } else if (matched.length) {
        items.value = [...matched].reverse().concat(items.value)
      }
      trim()
      realtime.lastId = data.latest_id || realtime.lastId
      resultCountText.value = `实时 · ${items.value.length} 条`
    } catch (e) {
      resultCountText.value = '加载失败'
    }
  }

  function buildHistoryParams() {
    const p = { limit: HISTORY_PAGE_SIZE, offset: history.page * HISTORY_PAGE_SIZE }
    if (filters.level !== 'ALL') p.level = filters.level
    if (filters.logger) p.logger = filters.logger
    if (filters.q) p.q = filters.q
    const now = Date.now() / 1000
    if (filters.sinceMin) p.since = (now - filters.sinceMin * 60).toFixed(0)
    if (filters.untilMin) p.until = (now - filters.untilMin * 60).toFixed(0)
    return p
  }

  async function loadHistory(reset = false) {
    if (reset) {
      history.page = 0
      history.hasMore = true
      clearView()
    }
    if (history.loading || !history.hasMore) return
    history.loading = true
    resultCountText.value = '加载中…'
    try {
      const data = await api.get('/api/logs', buildHistoryParams())
      if (data.success) {
        const pageItems = data.items || []
        const ordered = [...pageItems].reverse()
        if (history.page === 0) {
          items.value = ordered
        } else {
          items.value = [...ordered, ...items.value]
        }
        trim()
        history.hasMore = pageItems.length === HISTORY_PAGE_SIZE
        resultCountText.value = `共 ${data.total} 条（已加载 ${history.page * HISTORY_PAGE_SIZE + pageItems.length}）`
      }
    } catch (e) {
      resultCountText.value = '加载失败'
    } finally {
      history.loading = false
    }
  }

  async function loadHistoryMore() {
    history.page += 1
    await loadHistory(false)
  }

  /** nudge / 断线兜底共用：stats/facets + 按模式增量或刷新首页 */
  async function pullFromNudge() {
    refreshStats()
    refreshFacets()
    if (mode.value === 'realtime') {
      if (playing.value) await pullRealtime(false)
    } else if (mode.value === 'history' && history.page === 0) {
      await loadHistory(true)
    }
  }

  const logsNudge = useNudgePull({
    id: LOGS_SUBSCRIPTION_ID,
    topics: ['logs'],
    pull: pullFromNudge,
    manual: true,
    fallback: { when: () => mode.value === 'realtime' },
  })

  function startRealtime() {
    logsNudge.bind()
    logsNudge.setFilters(buildLogsFilters())
    pullRealtime(true)
  }

  function stopRealtime() {
    logsNudge.teardown()
  }

  function scheduleRealtimeReset() {
    if (mode.value !== 'realtime') return
    clearView()
    realtime.lastId = 0
    logsNudge.setFilters(buildLogsFilters())
    pullRealtime(true)
  }

  function setMode(next) {
    if (mode.value === next) return
    mode.value = next
    clearView()
    realtime.lastId = 0
    if (next === 'realtime') {
      startRealtime()
    } else {
      stopRealtime()
      loadHistory(true)
    }
  }

  function setPlaying(value) {
    playing.value = value
    if (value && mode.value === 'realtime') {
      scheduleRealtimeReset()
    }
  }

  function applyFilters(next) {
    Object.assign(filters, next)
    if (mode.value === 'realtime') scheduleRealtimeReset()
    else loadHistory(true)
  }

  function resetFilters() {
    Object.assign(filters, { level: 'ALL', logger: '', q: '', sinceMin: 0, untilMin: 0 })
    if (mode.value === 'realtime') scheduleRealtimeReset()
    else loadHistory(true)
  }

  async function refreshFacets() {
    try {
      const data = await api.get('/api/logs/facets')
      if (data.success) facets.value = data
    } catch (e) { /* ignore */ }
  }

  async function refreshStats() {
    try {
      const data = await api.get('/api/logs/stats')
      if (data.success) stats.value = data
    } catch (e) { /* ignore */ }
  }

  async function cleanup(days = 7) {
    const data = await api.post(`/api/logs/cleanup?days=${days}`, {})
    await refreshStats()
    if (mode.value === 'realtime') scheduleRealtimeReset()
    else loadHistory(true)
    return data
  }

  function init() {
    refreshFacets()
    refreshStats()
    if (mode.value === 'realtime') {
      startRealtime()
    }
  }

  onBeforeUnmount(() => {
    stopRealtime()
  })

  return {
    mode, playing, filters, items, resultCountText, facets, stats, history, filterLabel,
    setMode, setPlaying, applyFilters, resetFilters, loadHistoryMore, cleanup, clearView, init,
  }
}
