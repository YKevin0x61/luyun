import { ref } from 'vue'
import { api } from '../api/client'
import { useNudgePull } from './useNudgePull'

/**
 * 仪表盘数据走 nudge + pull：后端 realtime hub 只发"有变"nudge（不带数据），
 * 客户端挂载时先 HTTP 拉取一次，之后收到 `dashboard` topic 的 nudge 再重新拉取。
 * WS 断线期间由 useNudgePull 的 fallback 低频轮询兜底。
 */
export function useDashboardData() {
  const summary = ref(null)
  const loading = ref(true)
  const error = ref('')

  async function refresh() {
    try {
      summary.value = await api.get('/api/dashboard/summary')
      error.value = ''
    } catch (e) {
      error.value = e.message || '加载仪表盘数据失败'
    } finally {
      loading.value = false
    }
  }

  useNudgePull({
    id: 'admin-dashboard',
    topics: ['dashboard'],
    pull: refresh,
    immediate: true,
  })

  return { summary, loading, error, refresh }
}
