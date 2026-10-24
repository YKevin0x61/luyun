import { ref } from 'vue'
import { api } from '../api/client'

/** 复盘 tab（ADR 0118）：昨天每个品的建议 / 实际做 / 卖出 / 剩 / 报废。只读。 */
export function usePrepReview() {
  const state = ref('idle')
  const errorText = ref('')
  const day = ref('')
  const rows = ref([])
  const summary = ref({ over: 0, under: 0, waste_total: 0 })

  async function load(targetDay = '') {
    state.value = 'loading'
    errorText.value = ''
    try {
      const query = targetDay ? `?day=${encodeURIComponent(targetDay)}` : ''
      const data = await api.get(`/api/prep-plan/review${query}`)
      day.value = data.day || ''
      rows.value = data.rows || []
      summary.value = data.summary || { over: 0, under: 0, waste_total: 0 }
      state.value = 'ready'
    } catch (err) {
      state.value = 'error'
      errorText.value = err.message || '复盘读不出来'
    }
  }

  return { state, errorText, day, rows, summary, load }
}
