import { reactive, ref } from 'vue'
import { api } from '../api/client'

const DONE_STORAGE_KEY = 'prepDone'

function loadDoneKeys() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(DONE_STORAGE_KEY) || '[]'))
  } catch (e) {
    return new Set()
  }
}

function toIsoInputValue(date) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 阶段三：备货计划页面状态管理，1:1 迁移自原 public/prep-plan.html。 */
export function usePrepPlan() {
  const now = new Date()
  const targetStart = ref(toIsoInputValue(now))
  const targetEnd = ref(toIsoInputValue(new Date(now.getTime() + 24 * 3600 * 1000)))
  const station = ref('')

  const busy = ref(false)
  const statusText = ref('')
  const steps = reactive({
    step1: '设置时间窗（默认未来 24 小时）',
    step2: '点击"一键生成执行清单"',
    step3: '按下方档口卡片逐项执行',
  })

  const summary = reactive({
    item_count: 0, missing_rule_count: 0, high_risk_count: 0,
    expiry_risk_count: 0, waste_risk_count: 0,
  })
  const items = ref([])
  const lowConfidence = ref([])
  const missingRules = ref([])
  const doneKeys = ref(loadDoneKeys())

  function toggleDone(key, checked) {
    const next = new Set(doneKeys.value)
    if (checked) next.add(key)
    else next.delete(key)
    doneKeys.value = next
    sessionStorage.setItem(DONE_STORAGE_KEY, JSON.stringify([...next]))
  }

  function windowParams() {
    const p = {}
    if (targetStart.value) p.target_start = new Date(targetStart.value).toISOString()
    if (targetEnd.value) p.target_end = new Date(targetEnd.value).toISOString()
    if (station.value) p.station = station.value
    return p
  }

  function applyResult(result) {
    Object.assign(summary, result.summary || {})
    items.value = result.items || []
    lowConfidence.value = result.low_confidence || []
    missingRules.value = result.missing_rules || []
  }

  async function runForecast() {
    statusText.value = '正在计算预测...'
    steps.step1 = '已完成：时间窗设置'; steps.step2 = '进行中：预测计算'; steps.step3 = '等待生成计划'
    const data = await api.get('/api/prep-plan/forecast', windowParams())
    applyResult(data)
    statusText.value = `预测完成：${new Date().toLocaleTimeString()}`
    return data
  }

  async function runGenerate() {
    statusText.value = '正在生成计划...'
    steps.step2 = '已完成：预测计算'; steps.step3 = '进行中：保存计划'
    const data = await api.post('/api/prep-plan/generate', {
      target_start: windowParams().target_start,
      target_end: windowParams().target_end,
      station: station.value || null,
      method: 'weighted_history',
      created_by: 'web_user',
    })
    statusText.value = `生成成功，run_id=${data.run_id}`
    return data
  }

  async function runCurrent() {
    statusText.value = '读取最近一次计划...'
    const data = await api.get('/api/prep-plan/current')
    if (data.run) {
      Object.assign(summary, {
        item_count: data.run.item_count,
        missing_rule_count: data.run.missing_rule_count,
        high_risk_count: data.run.high_risk_count,
        expiry_risk_count: data.run.expiry_risk_count,
        waste_risk_count: data.run.waste_risk_count,
      })
      statusText.value = `读取完成 run #${data.run.id || data.run.run_id || '—'} · ${data.run.created_at || ''} · ${new Date().toLocaleTimeString()}`
    } else {
      Object.assign(summary, { item_count: 0, missing_rule_count: 0, high_risk_count: 0, expiry_risk_count: 0, waste_risk_count: 0 })
      statusText.value = `读取完成：${new Date().toLocaleTimeString()}`
    }
    items.value = data.items || []
    lowConfidence.value = []
    missingRules.value = data.missing_rules || []
    steps.step1 = '已完成：读取历史计划'; steps.step2 = '按档口展开卡片'; steps.step3 = '按建议量从上到下执行'
    return data
  }

  async function safelyRun(taskFn) {
    busy.value = true
    try {
      await taskFn()
    } catch (e) {
      statusText.value = `失败：${e.message}`
      steps.step1 = '设置时间窗（默认未来 24 小时）'
      steps.step2 = '点击"一键生成执行清单"'
      steps.step3 = '失败后重试，或读取最近一次'
    } finally {
      busy.value = false
    }
  }

  async function runAll() {
    statusText.value = '开始一键流程...'
    await runForecast()
    await runGenerate()
    await runCurrent()
    statusText.value = `一键流程完成：${new Date().toLocaleTimeString()}`
    steps.step1 = '已完成：时间窗设置'; steps.step2 = '已完成：预测+生成'; steps.step3 = '执行中：按档口卡片操作'
  }

  return {
    targetStart, targetEnd, station, busy, statusText, steps, summary, items,
    lowConfidence, missingRules, doneKeys, toggleDone,
    runAll: () => safelyRun(runAll),
    runCurrent: () => safelyRun(runCurrent),
  }
}
