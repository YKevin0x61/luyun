import { onBeforeUnmount, onMounted } from 'vue'
import { useRealtimeStore } from '../stores/realtime.js'

/** Align with hub DASHBOARD_DEBOUNCE_SECONDS (0.3s). Shared strategy with admin-web. */
export const NUDGE_PULL_COALESCE_MS = 300

/**
 * nudge + pull 订阅深模块（KDS）：订 topic → 收 nudge →（可选 debounce/match）→ pull。
 * 与 admin-web 同形，底层走 Pinia `useRealtimeStore`（非 provide/inject）。
 *
 * 默认对 nudge 做 NUDGE_PULL_COALESCE_MS 合并；reconcile / immediate / 显式 debounceMs:0 不进窗口。
 *
 * fallback 默认 `'reconcile'`：bind 时 `init()`，复用 store 内置 60s 对账
 * （dispatch orders + `{ reconcile: true }` 到同一批 handler）。
 *
 * @param {object} options
 * @param {string} options.id 订阅 id（连接内唯一；各页请用不同 id，便于卸载时 unsubscribe）
 * @param {string[]} options.topics
 * @param {() => void|Promise<void>} options.pull
 * @param {object} [options.filters]
 * @param {(ev: object) => boolean} [options.match] 默认 type===nudge 且 topic ∈ topics
 * @param {number} [options.debounceMs=NUDGE_PULL_COALESCE_MS] 设 0 关闭合并
 * @param {boolean|'reconcile'|{when?: () => boolean}} [options.fallback='reconcile']
 * @param {boolean} [options.immediate=false] bind 时是否先 pull 一次
 * @param {boolean} [options.manual=false] true 时不自动挂载，返回 bind/teardown
 */
export function useNudgePull(options) {
  const {
    id,
    topics,
    pull,
    filters: initialFilters = {},
    match,
    debounceMs = NUDGE_PULL_COALESCE_MS,
    fallback = 'reconcile',
    immediate = false,
    manual = false,
  } = options

  const realtimeStore = useRealtimeStore()

  let currentFilters = { ...initialFilters }
  /** @type {Array<() => void>} */
  let topicUnsubs = []
  let debounceTimer = null
  let bound = false

  const topicSet = () => new Set(topics)

  function defaultMatch(ev) {
    return ev?.type === 'nudge' && topicSet().has(ev.topic)
  }

  function matches(ev) {
    return (match || defaultMatch)(ev)
  }

  const fallbackOff = fallback === false || fallback == null
  const fallbackWhen = typeof fallback === 'object' && fallback && typeof fallback.when === 'function'
    ? fallback.when
    : null
  // true / 'reconcile' / { when } 都接受 store 的 reconcile 派发；仅 false 时忽略
  const acceptReconcile = !fallbackOff

  function runPull() {
    return pull()
  }

  /** Immediate pull; cancels any pending coalesce (reconcile / teardown paths). */
  function runPullNow() {
    clearTimeout(debounceTimer)
    debounceTimer = null
    return runPull()
  }

  function schedulePull() {
    if (debounceMs > 0) {
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        debounceTimer = null
        runPull()
      }, debounceMs)
      return
    }
    runPull()
  }

  function handleEvent(ev) {
    // reconcile 派发：fallback:false 忽略；{ when } 仅约束这条兜底路径（对齐 admin）
    // 对账立即 pull，不消耗 coalesce 窗口
    if (ev?.scope?.reconcile) {
      if (!acceptReconcile) return
      if (fallbackWhen && !fallbackWhen()) return
      if (!matches(ev)) return
      runPullNow()
      return
    }
    if (!matches(ev)) return
    schedulePull()
  }

  function setFilters(next = {}) {
    currentFilters = { ...next }
    if (bound) {
      realtimeStore.subscribe(id, topics, currentFilters)
    }
  }

  function bind() {
    if (bound) {
      realtimeStore.subscribe(id, topics, currentFilters)
      return
    }
    bound = true
    // 连接 +（若尚未启动）60s reconcile 循环；init 幂等
    realtimeStore.init()
    realtimeStore.subscribe(id, topics, currentFilters)
    topicUnsubs = topics.map((topic) =>
      realtimeStore.on(topic, (scope = {}) => {
        handleEvent({ type: 'nudge', topic, scope })
      })
    )
    if (immediate) runPullNow()
  }

  function teardown() {
    clearTimeout(debounceTimer)
    debounceTimer = null
    if (!bound) return
    bound = false
    for (const unsub of topicUnsubs) unsub()
    topicUnsubs = []
    realtimeStore.unsubscribe(id)
  }

  if (!manual) {
    onMounted(bind)
    onBeforeUnmount(teardown)
  }

  return { bind, teardown, setFilters }
}
