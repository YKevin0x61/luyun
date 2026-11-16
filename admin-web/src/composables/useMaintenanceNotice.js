import { computed, onMounted, onUnmounted, ref } from 'vue'

// 维护态是低频、只读的一条探针：15s 一次足够让「正在更新」在切包前被看到，
// 又不会在正常营业时给服务端添麻烦。
const POLL_INTERVAL_MS = 15000

/**
 * 轮询 /api/system/maintenance，得到「现在是不是在更新 / 恢复」。
 *
 * 服务短暂不可达时**不隐藏已知的维护态**：切包那一刻请求本来就会失败，
 * 这时候把横条收掉正好把最需要提示的几秒藏了。反过来，从没读到过维护态时
 * 网络错误也不弹提示——那可能只是店里 Wi-Fi 抖了一下，不能谎报系统在升级。
 */
export function createMaintenanceNotice({
  fetchImpl = globalThis.fetch,
  setTimer = setInterval,
  clearTimer = clearInterval,
  intervalMs = POLL_INTERVAL_MS,
} = {}) {
  const notice = ref(null)
  const unreachable = ref(false)
  let timer = null

  async function refresh() {
    if (typeof fetchImpl !== 'function') return
    try {
      const response = await fetchImpl('/api/system/maintenance', {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) throw new Error(String(response.status || 'HTTP error'))
      const payload = await response.json()
      unreachable.value = false
      notice.value = payload && payload.active
        ? {
            kind: payload.kind || 'maintenance',
            message: String(payload.message || '系统正在维护，请稍后再试。'),
            startedAt: payload.started_at || null,
          }
        : null
    } catch {
      unreachable.value = true
    }
  }

  function start() {
    if (timer) return
    void refresh()
    timer = setTimer(refresh, intervalMs)
  }

  function stop() {
    if (timer) {
      clearTimer(timer)
      timer = null
    }
  }

  const visible = computed(() => notice.value !== null)
  const message = computed(() => {
    if (!notice.value) return ''
    const base = notice.value.message
    if (!unreachable.value) return base
    // 已经知道在维护、又恰好连不上：这说明正在切服务，不是用户网络坏了。
    return base + '（暂时连接不上服务器，恢复后会自动消失）'
  })

  return { notice, unreachable, visible, message, refresh, start, stop }
}

export function useMaintenanceNotice(options = {}) {
  const controller = createMaintenanceNotice(options)
  onMounted(controller.start)
  onUnmounted(controller.stop)
  return controller
}
