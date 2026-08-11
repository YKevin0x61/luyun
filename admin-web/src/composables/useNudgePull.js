import { inject, onBeforeUnmount, onMounted } from 'vue'
import { useConnectionFallback } from './useConnectionFallback'

/** Align with hub DASHBOARD_DEBOUNCE_SECONDS (0.3s). Shared strategy with kds. */
export const NUDGE_PULL_COALESCE_MS = 300

/**
 * nudge + pull 订阅深模块：订 topic → 收 nudge →（可选 debounce/match）→ pull，
 * 并按需挂上断线低频兜底。隐藏 App.vue 的 inject 细节与生命周期清理。
 *
 * 默认对 nudge 做 NUDGE_PULL_COALESCE_MS 合并；断线 fallback / immediate / debounceMs:0 不进窗口。
 *
 * @param {object} options
 * @param {string} options.id 订阅 id（连接内唯一）
 * @param {string[]} options.topics
 * @param {() => void|Promise<void>} options.pull
 * @param {object} [options.filters]
 * @param {(ev: object) => boolean} [options.match] 默认 type===nudge 且 topic ∈ topics
 * @param {number} [options.debounceMs=NUDGE_PULL_COALESCE_MS] 设 0 关闭合并
 * @param {boolean|{when?: () => boolean}} [options.fallback=true]
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
    fallback = true,
    immediate = false,
    manual = false,
  } = options

  const onRealtimeEvent = inject('onRealtimeEvent', null)
  const wsSubscribe = inject('wsSubscribe', null)
  const wsUnsubscribe = inject('wsUnsubscribe', null)

  let currentFilters = { ...initialFilters }
  let unsubEvent = null
  let debounceTimer = null
  let bound = false

  const topicSet = () => new Set(topics)

  function defaultMatch(ev) {
    return ev?.type === 'nudge' && topicSet().has(ev.topic)
  }

  function matches(ev) {
    return (match || defaultMatch)(ev)
  }

  function runPull() {
    return pull()
  }

  /** Immediate pull; cancels any pending coalesce. */
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
    if (!matches(ev)) return
    schedulePull()
  }

  function setFilters(next = {}) {
    currentFilters = { ...next }
    if (bound) {
      wsSubscribe?.(id, topics, currentFilters)
    }
  }

  function bind() {
    if (bound) {
      wsSubscribe?.(id, topics, currentFilters)
      return
    }
    bound = true
    wsSubscribe?.(id, topics, currentFilters)
    if (onRealtimeEvent) {
      unsubEvent = onRealtimeEvent(handleEvent)
    }
    if (immediate) runPullNow()
  }

  function teardown() {
    clearTimeout(debounceTimer)
    debounceTimer = null
    if (!bound) return
    bound = false
    wsUnsubscribe?.(id)
    unsubEvent?.()
    unsubEvent = null
  }

  const fallbackEnabled = fallback !== false && fallback != null
  const fallbackWhen = typeof fallback === 'object' && fallback && typeof fallback.when === 'function'
    ? fallback.when
    : null

  if (fallbackEnabled) {
    // 断线低频兜底立即 pull，不进 coalesce（对齐 kds reconcile）
    useConnectionFallback(() => {
      if (fallbackWhen && !fallbackWhen()) return
      runPullNow()
    })
  }

  if (!manual) {
    onMounted(bind)
    onBeforeUnmount(teardown)
  }

  return { bind, teardown, setFilters }
}
