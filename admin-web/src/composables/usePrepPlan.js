import { reactive, ref } from 'vue'
import { api } from '../api/client'
import {
  NO_MASTER_REASON,
  REFRESHING_LABEL,
  UNCATEGORIZED_STATION,
} from '../utils/prepPlanCopy'
import {
  PRESET_CUSTOM,
  PRESET_FUTURE_24H,
  formatPrepTime,
  itemKey,
  resolvePrepWindow,
  toDatetimeLocalValue,
} from '../utils/prepPlanWindow'

const NEAR_MS = 4 * 3600 * 1000

function cloneItems(items) {
  return (items || []).map((item) => ({
    ...item,
    slots: [...(item.slots || [])],
    batches: (item.batches || []).map((batch) => ({ ...batch })),
  }))
}

function isNearExpiry(expiresAt, now = Date.now()) {
  const ts = new Date(expiresAt).getTime()
  if (!Number.isFinite(ts)) return false
  return ts > now && ts <= now + NEAR_MS
}

function patchItem(items, key, updater) {
  return items.map((item) => (itemKey(item) === key ? updater({ ...item, batches: [...(item.batches || [])] }) : item))
}

export function usePrepPlan() {
  const preset = ref(PRESET_FUTURE_24H)
  const customStart = ref(toDatetimeLocalValue(new Date()))
  const customEnd = ref(toDatetimeLocalValue(new Date(Date.now() + 24 * 3600 * 1000)))
  const showCustom = ref(false)
  const stationFilter = ref('')
  const extraRecordOpen = reactive({})
  const registerQty = reactive({})
  const busy = ref(false)
  const statusText = ref('')
  const errorText = ref('')
  const items = ref([])
  const missingRules = ref([])
  const lowConfidence = ref([])
  const expiring = ref([])
  const summary = reactive({
    item_count: 0,
    todo_count: 0,
    missing_rule_count: 0,
    high_risk_count: 0,
    expiry_risk_count: 0,
    waste_risk_count: 0,
  })
  const windowStart = ref('')
  const windowEnd = ref('')
  const exportOpen = ref(false)
  const discardTarget = ref(null)

  function windowRange() {
    if (preset.value === PRESET_CUSTOM) {
      return resolvePrepWindow(PRESET_CUSTOM, new Date(), customStart.value, customEnd.value)
    }
    return resolvePrepWindow(preset.value, new Date())
  }

  function resetRegisterDefaults(nextItems) {
    for (const item of nextItems) {
      const key = itemKey(item)
      const recommended = Number(item.recommended_qty || 0)
      if (registerQty[key] === undefined || registerQty[key] === '') {
        registerQty[key] = recommended > 0 ? recommended : ''
      }
    }
  }

  function applyForecast(data) {
    const nextItems = cloneItems(data.items || [])
    items.value = nextItems
    missingRules.value = data.missing_rules || []
    lowConfidence.value = data.low_confidence || []
    expiring.value = data.expiring || []
    Object.assign(summary, {
      item_count: 0,
      todo_count: 0,
      missing_rule_count: 0,
      high_risk_count: 0,
      expiry_risk_count: 0,
      waste_risk_count: 0,
      ...(data.summary || {}),
    })
    windowStart.value = data.target_window?.start || windowRange().start.toISOString()
    windowEnd.value = data.target_window?.end || windowRange().end.toISOString()
    resetRegisterDefaults(nextItems)
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

  async function recordItem(item) {
    const key = itemKey(item)
    const qty = Number(registerQty[key])
    if (!Number.isFinite(qty) || qty <= 0) {
      throw new Error('登记数量必须大于 0')
    }
    if (!item.can_record) {
      throw new Error(NO_MASTER_REASON)
    }
    const data = await api.post('/api/prep-plan/batches', {
      item_name: item.item_name,
      unit: item.unit || '',
      produced_qty: qty,
      operator: '后厨',
    })
    const near = isNearExpiry(data.expires_at)
    const nextRecommended = Math.max(0, Number(item.recommended_qty || 0) - Math.ceil(qty))
    items.value = patchItem(items.value, key, (current) => {
      const next = {
        ...current,
        produced_qty: Number(current.produced_qty || 0) + qty,
        undo_batch_id: data.batch_id,
        available_fresh_qty: Number(current.available_fresh_qty || 0) + (near ? 0 : qty),
        available_near_expiry_qty: Number(current.available_near_expiry_qty || 0) + (near ? qty : 0),
        recommended_qty: nextRecommended,
      }
      next.available_qty = Number(next.available_fresh_qty) + Number(next.available_near_expiry_qty)
      next.batches = [
        {
          batch_id: data.batch_id,
          produced_qty: qty,
          remaining_qty: Number(data.remaining_qty || qty),
          produced_at: new Date().toISOString(),
          expires_at: data.expires_at,
          near_expiry: near,
        },
        ...(current.batches || []),
      ]
      return next
    })
    extraRecordOpen[key] = false
    registerQty[key] = nextRecommended > 0 ? nextRecommended : ''
    statusText.value = `已登记 ${item.item_name} ${qty}${item.unit || ''}`
  }

  async function undoItem(item) {
    const batchId = item.undo_batch_id
    if (!batchId) return
    await api.post(`/api/prep-plan/batches/${batchId}/undo`)
    const key = itemKey(item)
    items.value = patchItem(items.value, key, (current) => {
      const batch = (current.batches || []).find((row) => row.batch_id === batchId)
      const qty = Number(batch?.remaining_qty || batch?.produced_qty || 0)
      const near = Boolean(batch?.near_expiry)
      const next = {
        ...current,
        produced_qty: Math.max(0, Number(current.produced_qty || 0) - qty),
        undo_batch_id: null,
        available_fresh_qty: Math.max(0, Number(current.available_fresh_qty || 0) - (near ? 0 : qty)),
        available_near_expiry_qty: Math.max(0, Number(current.available_near_expiry_qty || 0) - (near ? qty : 0)),
        recommended_qty: Number(current.recommended_qty || 0) + Math.ceil(qty),
        batches: (current.batches || []).filter((row) => row.batch_id !== batchId),
      }
      next.available_qty = Number(next.available_fresh_qty) + Number(next.available_near_expiry_qty)
      return next
    })
    expiring.value = expiring.value.filter((row) => row.batch_id !== batchId)
    statusText.value = `已撤销 ${item.item_name} 刚才那笔`
  }

  async function discardBatch(target) {
    if (!target?.batch_id) return
    await api.post(`/api/prep-plan/batches/${target.batch_id}/discard`)
    const qty = Number(target.remaining_qty || 0)
    const near = target.near_expiry !== false
    const key = itemKey(target)
    items.value = patchItem(items.value, key, (current) => {
      const next = {
        ...current,
        available_fresh_qty: Math.max(0, Number(current.available_fresh_qty || 0) - (near ? 0 : qty)),
        available_near_expiry_qty: Math.max(0, Number(current.available_near_expiry_qty || 0) - (near ? qty : 0)),
        recommended_qty: Number(current.recommended_qty || 0) + Math.ceil(qty),
        batches: (current.batches || []).filter((row) => row.batch_id !== target.batch_id),
        undo_batch_id: current.undo_batch_id === target.batch_id ? null : current.undo_batch_id,
      }
      next.available_qty = Number(next.available_fresh_qty) + Number(next.available_near_expiry_qty)
      return next
    })
    expiring.value = expiring.value.filter((row) => row.batch_id !== target.batch_id)
    discardTarget.value = null
    statusText.value = `已报废 ${target.item_name}`
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
    extraRecordOpen,
    registerQty,
    busy,
    statusText,
    errorText,
    items,
    missingRules,
    lowConfidence,
    expiring,
    summary,
    windowStart,
    windowEnd,
    exportOpen,
    discardTarget,
    windowRange,
    UNCATEGORIZED_STATION,
    formatPrepTime,
    itemKey,
    refresh: () => safelyRun(refresh),
    recordItem: (item) => safelyRun(() => recordItem(item)),
    undoItem: (item) => safelyRun(() => undoItem(item)),
    discardBatch: (target) => safelyRun(() => discardBatch(target)),
  }
}
