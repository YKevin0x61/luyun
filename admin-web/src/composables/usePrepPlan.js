import { ref } from 'vue'
import { api } from '../api/client'
import {
  CLOSED_DAY_FALLBACK,
  REFRESHING_LABEL,
  UNCATEGORIZED_STATION,
} from '../utils/prepPlanCopy'
import {
  PRESET_CUSTOM,
  PRESET_FUTURE_24H,
  formatPrepTime,
  resolvePrepWindow,
  toDatetimeLocalValue,
} from '../utils/prepPlanWindow'

function cloneItems(items) {
  return (items || []).map((item) => ({
    ...item,
    slots: [...(item.slots || [])],
  }))
}

/** 备货计划的读路径。写路径（登记 / 报废 / 撤销 / 生成计划 / 临期批次）随「不登记备货数量、
 *  每次计算都按 0 库存」的产品模型一起去掉了 —— 这一页现在只有 `forecast` 一个接口。 */
export function usePrepPlan() {
  const preset = ref(PRESET_FUTURE_24H)
  const customStart = ref(toDatetimeLocalValue(new Date()))
  const customEnd = ref(toDatetimeLocalValue(new Date(Date.now() + 24 * 3600 * 1000)))
  const showCustom = ref(false)
  const stationFilter = ref('')
  const busy = ref(false)
  const statusText = ref('')
  const errorText = ref('')
  const items = ref([])
  const missingRules = ref([])
  const lowConfidence = ref([])
  const windowStart = ref('')
  const windowEnd = ref('')
  const closedReason = ref('')
  const closedDays = ref([])
  const newClosedDay = ref('')
  const newClosedNote = ref('')
  const closedError = ref('')
  const exportOpen = ref(false)

  function windowRange() {
    if (preset.value === PRESET_CUSTOM) {
      return resolvePrepWindow(PRESET_CUSTOM, new Date(), customStart.value, customEnd.value)
    }
    return resolvePrepWindow(preset.value, new Date())
  }

  function applyForecast(data) {
    items.value = cloneItems(data.items || [])
    missingRules.value = data.missing_rules || []
    lowConfidence.value = data.low_confidence || []
    // 整段落在歇业日里时后端不给清单、只给一句原因（prep_closed_days）
    closedReason.value = data.closed ? data.closed_reason || CLOSED_DAY_FALLBACK : ''
    windowStart.value = data.target_window?.start || windowRange().start.toISOString()
    windowEnd.value = data.target_window?.end || windowRange().end.toISOString()
  }

  async function refresh() {
    errorText.value = ''
    statusText.value = REFRESHING_LABEL
    const { start, end } = windowRange()
    const data = await api.get('/api/prep-plan/forecast', {
      target_start: start.toISOString(),
      target_end: end.toISOString(),
    })
    applyForecast(data)
    statusText.value = `已更新 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  }

  // 歇业日历：页面上那块维护区用。读面任一身份都能读，写面只有管理端过得去
  // （后端写端点挂 verify_admin_token），所以员工那一档按钮根本不渲染。
  async function loadClosedDays() {
    try {
      const data = await api.get('/api/prep-plan/closed-days')
      closedDays.value = data.days || []
    } catch (err) {
      closedError.value = err.message || '歇业日读不出来'
    }
  }

  async function addClosedDay() {
    if (!newClosedDay.value) return
    closedError.value = ''
    try {
      await api.post('/api/prep-plan/closed-days', {
        day: newClosedDay.value,
        note: newClosedNote.value,
      })
      newClosedDay.value = ''
      newClosedNote.value = ''
      await loadClosedDays()
    } catch (err) {
      closedError.value = err.message || '加不上'
    }
  }

  async function removeClosedDay(day) {
    closedError.value = ''
    try {
      await api.delete(`/api/prep-plan/closed-days/${day}`)
      await loadClosedDays()
    } catch (err) {
      closedError.value = err.message || '取消失败'
    }
  }

  async function safelyRun(taskFn) {
    busy.value = true
    errorText.value = ''
    try {
      await taskFn()
    } catch (err) {
      errorText.value = err.message || '操作失败'
      statusText.value = `失败：${err.message || '请重试'}`
    } finally {
      busy.value = false
    }
  }

  return {
    preset,
    customStart,
    customEnd,
    showCustom,
    stationFilter,
    busy,
    statusText,
    errorText,
    items,
    missingRules,
    lowConfidence,
    windowStart,
    windowEnd,
    closedReason,
    closedDays,
    newClosedDay,
    newClosedNote,
    closedError,
    exportOpen,
    loadClosedDays,
    addClosedDay,
    removeClosedDay,
    windowRange,
    UNCATEGORIZED_STATION,
    formatPrepTime,
    refresh: () => safelyRun(refresh),
  }
}
