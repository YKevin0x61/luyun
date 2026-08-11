import { inject, onBeforeUnmount, watch } from 'vue'

export const FALLBACK_POLL_MS = 30000
const FALLBACK_GRACE_MS = 8000

/**
 * WS 断线低频轮询兜底：可靠性兜底，不是常态轮询。
 * 依赖 App.vue 通过 provide('wsConnected', connected) 下发的连接状态：
 * 断开状态持续超过 FALLBACK_GRACE_MS（避免瞬时抖动误触发）后，按
 * FALLBACK_POLL_MS 低频调用一次 refreshFn；WS 一旦恢复连接，立即
 * clearInterval 停止轮询，回到 nudge 驱动。组件/composable 卸载时自动清理。
 */
export function useConnectionFallback(refreshFn) {
  const wsConnected = inject('wsConnected', null)
  let pollTimer = null
  let graceTimer = null

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function stopGrace() {
    if (graceTimer) {
      clearTimeout(graceTimer)
      graceTimer = null
    }
  }

  function startPolling() {
    if (pollTimer) return
    pollTimer = setInterval(() => refreshFn(), FALLBACK_POLL_MS)
  }

  function handleConnectedChange(isConnected) {
    stopGrace()
    if (isConnected) {
      stopPolling()
    } else {
      graceTimer = setTimeout(startPolling, FALLBACK_GRACE_MS)
    }
  }

  let stopWatch = null
  if (wsConnected) {
    handleConnectedChange(wsConnected.value)
    stopWatch = watch(wsConnected, handleConnectedChange)
  }

  onBeforeUnmount(() => {
    stopGrace()
    stopPolling()
    stopWatch?.()
  })
}
