<script setup>
import { computed, onMounted, ref } from 'vue'
import LuyunDatePicker from '../components/ui/LuyunDatePicker.vue'
import { api } from '../api/client'
import { useStationsStore } from '../stores/stations'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../utils/workbenchIdentity'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'
import {
  PREP_REVIEW_CONTEXT_HOLIDAY,
  PREP_REVIEW_CONTEXT_WEATHER,
  PREP_REVIEW_COST_AMOUNT,
  PREP_REVIEW_COST_ESTIMATED,
  PREP_REVIEW_COST_ESTIMATED_HINT,
  PREP_REVIEW_COST_HINT,
  PREP_REVIEW_COST_LOSS_LINK,
  PREP_REVIEW_COST_RECORDED_HINT,
  PREP_REVIEW_COST_ROWS,
  PREP_REVIEW_COST_TITLE,
  PREP_REVIEW_COST_TOTAL,
  PREP_REVIEW_DETAIL_HINT,
  PREP_REVIEW_DIR_EVEN,
  PREP_REVIEW_DIR_OVER,
  PREP_REVIEW_DIR_UNDER,
  PREP_REVIEW_EMPTY,
  PREP_REVIEW_EXPORT,
  PREP_REVIEW_EXPORT_EMPTY,
  PREP_REVIEW_KICKER,
  PREP_REVIEW_LOADING,
  PREP_REVIEW_LOAD_FAILED,
  PREP_REVIEW_METRIC_ABS,
  PREP_REVIEW_METRIC_ABS_HINT,
  PREP_REVIEW_METRIC_COVERAGE,
  PREP_REVIEW_METRIC_COVERAGE_HINT,
  PREP_REVIEW_METRIC_DIR,
  PREP_REVIEW_METRIC_DIR_HINT,
  PREP_REVIEW_NOTE_CANCEL,
  PREP_REVIEW_NOTE_EDIT,
  PREP_REVIEW_NOTE_FAILED,
  PREP_REVIEW_NOTE_LABEL,
  PREP_REVIEW_NOTE_PLACEHOLDER,
  PREP_REVIEW_NOTE_PREFIX,
  PREP_REVIEW_NOTE_SAVE,
  PREP_REVIEW_REFUND_APPROX,
  PREP_REVIEW_SAMPLE_SHORT,
  PREP_REVIEW_SUMMARY_FAILED,
  PREP_REVIEW_TH_AFTER_1H,
  PREP_REVIEW_TH_AFTER_2H,
  PREP_REVIEW_TH_NEXT_DAY,
  PREP_REVIEW_SOLD_OUT_HINT,
  PREP_REVIEW_SOLD_OUT_SUSPECT,
  PREP_REVIEW_SOLD_OUT_TRUSTED,
  PREP_REVIEW_TH_REFUND,
  PREP_REVIEW_TH_SOLD_OUT,
  PREP_REVIEW_TITLE,
  PREP_REVIEW_WHY_DIMENSIONS,
  PREP_REVIEW_WHY_HINT,
  PREP_REVIEW_WHY_TITLE,
  WEATHER_SETTINGS_CITY_LABEL,
  WEATHER_SETTINGS_CITY_PLACEHOLDER,
  WEATHER_SETTINGS_CITY_REQUIRED,
  WEATHER_SETTINGS_CLOSE,
  WEATHER_SETTINGS_HINT,
  WEATHER_SETTINGS_HOST_LABEL,
  WEATHER_SETTINGS_HOST_PLACEHOLDER,
  WEATHER_SETTINGS_KEY_LABEL,
  WEATHER_SETTINGS_OPEN,
  WEATHER_SETTINGS_READ_FAILED,
  WEATHER_SETTINGS_SAVE,
  WEATHER_SETTINGS_SAVE_FAILED,
  WEATHER_SETTINGS_SAVING,
  WEATHER_SETTINGS_TEST,
  WEATHER_SETTINGS_TEST_FAILED,
  WEATHER_SETTINGS_TESTING,
  WEATHER_SETTINGS_TITLE,
  prepReviewContextStaff,
  weatherSettingsKeyPlaceholder,
  weatherSettingsTestOk,
  prepReviewPendingHint,
  prepReviewPeriodHint,
  prepReviewQualityLine,
  prepReviewSoldOutLine,
} from '../utils/prepPlanCopy'
import { qualityReasons } from '../utils/prepReviewQuality'
import {
  buildReviewCsv,
  reviewAfterWindow,
  reviewCsvFilename,
  reviewSoldOutTime,
} from '../utils/prepReviewCsv'
import { REVIEW_CLOCK_FALLBACK } from '../utils/reviewDay'

/**
 * 备货复盘（ADR 0123 / 0129）。
 *
 * 只回答一个问题：**那一次给的参考值，事后看准不准。**
 *
 * 口径的关键：一条快照 = 一个 24 小时窗口的一次预测，只能跟**它自己那个窗口**的实际
 * 销量比。同一天的 24 条快照窗口高度重叠（相邻两条差 1 小时），把它们平均起来误差会
 * 互相抵消——逐条全错也能平均出「偏差很小」。所以这一页按**快照钟点**分组，
 * 默认看 07:00（师傅真正下单备货的那一次），其余钟点收在下面，用来观察「越晚越不准」。
 *
 * 上方三张新卡与「为什么不准 / 代价」读 `/review/summary`：近 90 天、07:00 主口径。
 * 那里的比例字段 null = 分母为 0，页面显示「—」；分组 `insufficient` = 样本不足，
 * 不允许用 0 冒充「很准」。
 */

workbenchDocumentTitle('备货复盘')

const REVIEW_DAYS = 90

const stations = useStationsStore()
// 2026-11-10 用户裁定：这一页开只读给勾了「备货复盘」的管理员（员工会话）。
// 备注 / 天气设置是写面，只给管理端会话；员工会话能看全部只读内容（后端也按这条判）。
const identityStore = useWorkbenchIdentityStore()
const canWrite = computed(() => identityStore.identity === IDENTITY_ADMIN)
void identityStore.refresh()

const day = ref(new Date().toLocaleDateString('sv-SE'))
const state = ref('idle')
const errorText = ref('')
const snapshots = ref([])
const primaryClock = ref(null)
const clock = ref(null)
const showOtherClocks = ref(false)

const context = ref(null)
const summary = ref(null)
const summaryState = ref('idle')
const summaryError = ref('')

const noteEditing = ref(false)
const noteSaving = ref(false)
const noteDraft = ref('')
const noteError = ref('')

const weatherOpen = ref(false)
const weatherLoading = ref(false)
const weatherSaving = ref(false)
const weatherTesting = ref(false)
const weatherConfig = ref({
  configured: false,
  api_key_masked: '',
  city: '',
  api_host: '',
  updated_at: '',
})
const weatherKeyDraft = ref('')
const weatherCityDraft = ref('')
const weatherHostDraft = ref('')
const weatherStatus = ref('')
const weatherStatusBad = ref(false)

const current = computed(() => snapshots.value.find((g) => g.clock === clock.value) || null)

const currentItems = computed(() => current.value?.items || [])

const otherClocks = computed(() => snapshots.value.filter((g) => g.clock !== clock.value))

const note = computed(() => context.value?.note || '')

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

/** 兼容两种后端形状：合并后的接口给的是已解析的 `weather` / `staff` 对象，
 *  票面描述的是 `weather_json` / `staff_json` 文本。坏 JSON 一律当空对象。 */
function parseJsonObject(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return {}
  try {
    return asObject(JSON.parse(raw))
  } catch (_) {
    return {}
  }
}

function contextObject(direct, raw) {
  const parsed = asObject(direct)
  return Object.keys(parsed).length ? parsed : parseJsonObject(raw)
}

const weatherInfo = computed(() => contextObject(context.value?.weather, context.value?.weather_json))
const staffInfo = computed(() => contextObject(context.value?.staff, context.value?.staff_json))

const weatherKeyPlaceholder = computed(() =>
  weatherSettingsKeyPlaceholder(weatherConfig.value?.api_key_masked),
)

const contextLine = computed(() => {
  const ctx = context.value
  if (!ctx) return ''
  const parts = []
  if (ctx.holiday_name) parts.push(`${PREP_REVIEW_CONTEXT_HOLIDAY}：${ctx.holiday_name}`)
  const weather = weatherInfo.value
  if (weather.text) {
    const temp =
      weather.temp_max === null || weather.temp_max === undefined ? '' : ` ${num(weather.temp_max, 0)}°`
    parts.push(`${PREP_REVIEW_CONTEXT_WEATHER}：${weather.text}${temp}`)
  }
  const staffTotal = Object.values(staffInfo.value).reduce((sum, value) => sum + (Number(value) || 0), 0)
  if (staffTotal > 0) parts.push(prepReviewContextStaff(staffTotal))
  return parts.join(' · ')
})

/** 数据质量提示（票 09）：采集断档的日子，别把「没卖多少」读成「备多了」。
 *  只读 `context.data_quality_json`（票 08）；字段缺失 / 坏 JSON 一律不提示。 */
const qualityLine = computed(() => {
  const ctx = context.value
  if (!ctx) return ''
  const raw = ctx.data_quality_json ?? ctx.data_quality
  const reasons = qualityReasons(raw, {
    runtime: ctx.runtime,
    isToday: day.value === new Date().toLocaleDateString('sv-SE'),
    nowHour: new Date().getHours(),
  })
  return reasons.length ? prepReviewQualityLine(reasons) : ''
})

const summaryPeriodHint = computed(() =>
  prepReviewPeriodHint(
    summary.value?.period?.days || REVIEW_DAYS,
    summary.value?.primary_clock || primaryClock.value || REVIEW_CLOCK_FALLBACK,
  ),
)

function pct(value) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function signedPct(value) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
}

function directionWord(value) {
  if (value === null || value === undefined) return ''
  const n = Number(value)
  if (n > 0) return PREP_REVIEW_DIR_OVER
  if (n < 0) return PREP_REVIEW_DIR_UNDER
  return PREP_REVIEW_DIR_EVEN
}

function directionClass(value) {
  if (value === null || value === undefined) return ''
  const n = Number(value)
  if (n > 0) return 'is-over'
  if (n < 0) return 'is-short'
  return ''
}

function num(value, digits = 1) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(digits)
}

function signed(value) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`
}

function money(value) {
  if (value === null || value === undefined) return '—'
  return `¥${Number(value).toFixed(2)}`
}

function sharePct(value) {
  const total = Number(summary.value?.cost?.overfill_qty || 0)
  if (!total || value === null || value === undefined) return '—'
  return `${((Number(value) / total) * 100).toFixed(1)}%`
}

function soldOutLabel(item) {
  return reviewSoldOutTime(item?.sold_out_at) || '—'
}

function afterWindowLabel(item, key) {
  const value = reviewAfterWindow(item, key)
  return value === null ? '—' : num(value)
}

const BASELINE_LABELS = [
  ['reference', '参考值'],
  ['planned', '下达量'],
]

/** 同一份汇总里的两条基线，顺序固定（参考在前，下达在后）。 */
function baselineRows(data) {
  return BASELINE_LABELS.map(([key, label]) => ({ key, label, data: data?.[key] || null }))
}

/** 下达量是不是店长手改的；`suggested` = 未手改（回落参考值）。 */
function plannedTag(item) {
  return item?.planned_source === 'manual' ? '手改' : '未手改'
}

/** 明细里「对参考 / 对下达」：卖多了记负（缺货红），卖少了记正（多出黄）。 */
function gapVs(item, key) {
  if (item?.sold_qty === null || item?.sold_qty === undefined) return '—'
  return signed(Number(item.sold_qty) - Number(item[key] || 0))
}

function gapClass(item, key) {
  if (item?.sold_qty === null || item?.sold_qty === undefined) return ''
  return Number(item.sold_qty) > Number(item[key] || 0) ? 'is-short' : 'is-over'
}

function stationName(id) {
  return id ? stations.nameOf(id) : '未分类'
}

function tier(item) {
  if (item.quantile === null || item.quantile === undefined) return '—'
  const label = `p${Math.round(item.quantile * 100)}`
  return item.is_major ? `${label} 大品` : label
}

async function load({ keepClock = false } = {}) {
  const previousClock = clock.value
  state.value = 'loading'
  errorText.value = ''
  try {
    const data = await api.get(`/api/prep-plan/snapshots?day=${day.value}`)
    snapshots.value = data.snapshots || []
    context.value = data.context || null
    primaryClock.value = data.primary_clock || snapshots.value[0]?.clock || null
    const keep = keepClock && snapshots.value.some((group) => group.clock === previousClock)
    clock.value = keep ? previousClock : primaryClock.value
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorText.value = err.message || PREP_REVIEW_LOAD_FAILED
  }
}

async function loadSummary() {
  summaryState.value = 'loading'
  summaryError.value = ''
  try {
    const data = await api.get(`/api/prep-plan/review/summary?end=${day.value}&days=${REVIEW_DAYS}`)
    summary.value = data
    summaryState.value = 'ready'
  } catch (err) {
    summaryState.value = 'error'
    summaryError.value = err.message || PREP_REVIEW_SUMMARY_FAILED
  }
}

function reload() {
  load()
  loadSummary()
}

function startNoteEdit() {
  noteDraft.value = note.value
  noteEditing.value = true
  noteError.value = ''
}

function cancelNote() {
  noteEditing.value = false
  noteError.value = ''
}

async function saveNote() {
  if (noteSaving.value) return
  noteSaving.value = true
  noteError.value = ''
  const text = noteDraft.value.trim()
  try {
    const data = await api.put('/api/prep-plan/context/note', { day: day.value, note: text })
    context.value = data?.context || { ...(context.value || {}), note: text }
    noteEditing.value = false
    await load({ keepClock: true })
  } catch (err) {
    noteError.value = err.message || PREP_REVIEW_NOTE_FAILED
  } finally {
    noteSaving.value = false
  }
}

async function openWeatherSettings() {
  weatherOpen.value = true
  weatherLoading.value = true
  weatherStatus.value = ''
  weatherStatusBad.value = false
  weatherKeyDraft.value = ''
  try {
    const data = await api.get('/api/prep-plan/context/weather')
    weatherConfig.value = data || {}
    weatherCityDraft.value = data?.city || ''
    weatherHostDraft.value = data?.api_host || ''
  } catch (err) {
    weatherStatus.value = err.message || WEATHER_SETTINGS_READ_FAILED
    weatherStatusBad.value = true
  } finally {
    weatherLoading.value = false
  }
}

function closeWeatherSettings() {
  weatherOpen.value = false
  weatherStatus.value = ''
  weatherStatusBad.value = false
}

/** 表单 → 请求体：空 Key 不带字段（后端据此保留原 Key），城市去空白后必填。 */
function weatherPayload() {
  const city = weatherCityDraft.value.trim()
  if (!city) {
    weatherStatus.value = WEATHER_SETTINGS_CITY_REQUIRED
    weatherStatusBad.value = true
    return null
  }
  const payload = { city, api_host: weatherHostDraft.value.trim() }
  const key = weatherKeyDraft.value.trim()
  if (key) payload.api_key = key
  return payload
}

async function saveWeatherSettings() {
  if (weatherSaving.value || weatherTesting.value || weatherLoading.value) return
  const payload = weatherPayload()
  if (!payload) return
  weatherSaving.value = true
  weatherStatus.value = ''
  weatherStatusBad.value = false
  try {
    await api.put('/api/prep-plan/context/weather', payload)
    weatherOpen.value = false
    // 只重拉快照，让头部天气行跟着新配置走；不整页刷新。
    await load({ keepClock: true })
  } catch (err) {
    weatherStatus.value = err.message || WEATHER_SETTINGS_SAVE_FAILED
    weatherStatusBad.value = true
  } finally {
    weatherSaving.value = false
  }
}

async function testWeatherSettings() {
  if (weatherTesting.value || weatherSaving.value || weatherLoading.value) return
  const payload = weatherPayload()
  if (!payload) return
  weatherTesting.value = true
  weatherStatus.value = ''
  weatherStatusBad.value = false
  try {
    const data = await api.post('/api/prep-plan/context/weather/test', payload)
    if (data?.ok) {
      weatherStatus.value = weatherSettingsTestOk(data.sample)
      weatherStatusBad.value = false
    } else {
      weatherStatus.value = data?.error || WEATHER_SETTINGS_TEST_FAILED
      weatherStatusBad.value = true
    }
  } catch (err) {
    weatherStatus.value = err.message || WEATHER_SETTINGS_TEST_FAILED
    weatherStatusBad.value = true
  } finally {
    weatherTesting.value = false
  }
}

function exportCsv() {
  const items = currentItems.value
  if (!items.length) return
  const text = buildReviewCsv({ items })
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = reviewCsvFilename(day.value, clock.value || primaryClock.value || REVIEW_CLOCK_FALLBACK)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  // 档口名只是分组卡上的标签。`/api/stations` 是管理端门（员工会话 401），
  // 员工会话不拉它、退回显示 id（就是档口中文名），别让刚开放给管理员的只读页
  // 一进来就挂一条 401（2026-11-10 走查）。
  if (canWrite.value) stations.load().catch(() => {})
  reload()
})
</script>

<template>
  <div class="pr">
    <header class="pr-head">
      <div class="pr-heading">
        <p class="pr-kicker">{{ PREP_REVIEW_KICKER }}</p>
        <h1 class="pr-title">{{ PREP_REVIEW_TITLE }}</h1>
      </div>
      <div class="pr-actions">
        <label class="pr-date">
          <span>日期</span>
          <LuyunDatePicker v-model="day" placeholder="选择日期" aria-label="复盘日期" @update:model-value="reload" />
        </label>
        <button v-if="canWrite" type="button" class="pr-note-btn" :aria-expanded="noteEditing" @click="startNoteEdit">
          {{ note ? PREP_REVIEW_NOTE_EDIT : PREP_REVIEW_NOTE_LABEL }}
        </button>
        <button
          type="button"
          class="pr-export"
          :disabled="!currentItems.length"
          :title="currentItems.length ? '' : PREP_REVIEW_EXPORT_EMPTY"
          @click="exportCsv"
        >
          {{ PREP_REVIEW_EXPORT }}
        </button>
      </div>
    </header>

    <div class="pr-context-row">
      <p v-if="contextLine" class="pr-context">{{ contextLine }}</p>
      <button v-if="canWrite" type="button" class="pr-weather-btn" aria-label="天气设置" @click="openWeatherSettings">
        {{ WEATHER_SETTINGS_OPEN }}
      </button>
    </div>
    <p v-if="qualityLine" class="pr-quality" role="status">{{ qualityLine }}</p>

    <div v-if="canWrite && noteEditing" class="pr-note-edit">
      <input
        v-model="noteDraft"
        class="pr-note-input"
        type="text"
        :placeholder="PREP_REVIEW_NOTE_PLACEHOLDER"
        aria-label="今日备注"
        @keyup.enter="saveNote"
      />
      <button type="button" class="pr-note-save" :disabled="noteSaving" @click="saveNote">
        {{ PREP_REVIEW_NOTE_SAVE }}
      </button>
      <button type="button" class="pr-note-cancel" @click="cancelNote">{{ PREP_REVIEW_NOTE_CANCEL }}</button>
    </div>
    <p v-else-if="note" class="pr-note-view">{{ PREP_REVIEW_NOTE_PREFIX }}{{ note }}</p>
    <p v-if="noteError" class="pr-error" role="status">{{ noteError }}</p>

    <p v-if="errorText" class="pr-error" role="status">{{ errorText }}</p>
    <p v-else-if="state === 'loading'" class="pr-empty">{{ PREP_REVIEW_LOADING }}</p>
    <p v-else-if="!snapshots.length" class="pr-empty">{{ PREP_REVIEW_EMPTY }}</p>

    <div v-if="snapshots.length" class="pr-clocks" role="tablist" aria-label="快照钟点">
      <button
        v-for="group in snapshots"
        :key="group.clock"
        type="button"
        role="tab"
        class="pr-clock"
        :class="{ 'is-on': group.clock === clock, 'is-primary': group.clock === primaryClock }"
        :aria-selected="group.clock === clock"
        @click="clock = group.clock"
      >
        {{ group.clock }}
      </button>
    </div>

    <template v-if="current">
      <p class="pr-line">
        <b>{{ current.clock }}</b> 这一版快照：实际卖出 {{ num(current.summary.sold_total) }}，参考合计
        {{ num(current.summary.reference?.qty_total) }}，下达合计
        {{ num(current.summary.planned?.qty_total) }}
        <span v-if="current.summary.pending" class="pr-dim">
          · 还有 {{ current.summary.pending }} 个窗口没结束（要等 24 小时）
        </span>
        <span v-if="current.summary.drifted" class="pr-warn">
          · {{ current.summary.drifted }} 行的配方在这之后改过，实际销量按现在的配方重算
        </span>
        <span v-if="current.summary.sold_out" class="pr-dim">
          · {{ prepReviewSoldOutLine(current.summary.sold_out, current.summary.sold_out_reliable ?? 0) }}
        </span>
      </p>

      <!-- 这个钟点的窗口还没结束（measured = 0）时，表格全是「— / 0.0」，白占一屏；
           收成一行提示，把下面的近 90 天结论（方向偏差＝备多了/备少了）提到首屏。 -->
      <div v-if="current.summary.measured > 0" class="pr-compare">
        <table class="pr-table pr-table-compact pr-compare-table pr-compare-day">
          <thead>
            <tr>
              <th>口径</th>
              <th class="n">合计</th>
              <th class="n">÷ 实卖</th>
              <th class="n">够卖品数</th>
              <th class="n">缺货</th>
              <th class="n">多出</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in baselineRows(current.summary)" :key="row.key">
              <td class="pr-baseline">{{ row.label }}</td>
              <td class="n" data-label="合计">{{ num(row.data?.qty_total) }}</td>
              <td class="n" data-label="÷ 实卖">{{ num(row.data?.ratio, 2) }}</td>
              <td class="n" data-label="够卖品数" :class="row.data?.coverage_items >= 0.7 ? 'is-ok' : 'is-warn'">
                {{ pct(row.data?.coverage_items) }}
              </td>
              <td class="n is-short" data-label="缺货">
                {{ pct(row.data?.shortfall_ratio) }}
                <span class="pr-dim">· {{ num(row.data?.shortfall_qty) }}</span>
              </td>
              <td class="n is-over" data-label="多出">
                {{ pct(row.data?.overfill_ratio) }}
                <span class="pr-dim">· {{ num(row.data?.overfill_qty) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="pr-metric-h">
          参考值是机器按历史算的；下达量是店长当天最终执行的量（没手改时就是参考值）。
        </p>
      </div>
      <p v-else class="pr-line pr-dim">
        这个钟点还没有可判定的品 —— 先看下面的近 90 天偏差。
      </p>
    </template>

    <p v-if="summaryError" class="pr-error" role="status">{{ summaryError }}</p>

    <template v-if="summary">
      <p class="pr-scope">{{ summaryPeriodHint }}</p>

      <div class="pr-compare">
        <table class="pr-table pr-table-compact pr-compare-table pr-compare-review">
          <thead>
            <tr>
              <th>指标</th>
              <th class="n">参考值</th>
              <th class="n">下达量</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{{ PREP_REVIEW_METRIC_ABS }}</td>
              <td class="n">{{ pct(summary.metrics?.reference?.absolute_deviation) }}</td>
              <td class="n">{{ pct(summary.metrics?.planned?.absolute_deviation) }}</td>
            </tr>
            <tr>
              <td>{{ PREP_REVIEW_METRIC_DIR }}</td>
              <td
                class="n"
                :class="directionClass(summary.metrics?.reference?.direction_deviation)"
              >
                {{ signedPct(summary.metrics?.reference?.direction_deviation) }}
                <span class="pr-metric-word">
                  {{ directionWord(summary.metrics?.reference?.direction_deviation) }}
                </span>
              </td>
              <td
                class="n"
                :class="directionClass(summary.metrics?.planned?.direction_deviation)"
              >
                {{ signedPct(summary.metrics?.planned?.direction_deviation) }}
                <span class="pr-metric-word">
                  {{ directionWord(summary.metrics?.planned?.direction_deviation) }}
                </span>
              </td>
            </tr>
            <tr>
              <td>{{ PREP_REVIEW_METRIC_COVERAGE }}</td>
              <td class="n">{{ pct(summary.metrics?.reference?.coverage_qty) }}</td>
              <td class="n">{{ pct(summary.metrics?.planned?.coverage_qty) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="pr-metric-h">
          参考合计 {{ num(summary.metrics?.reference?.qty_total) }}，下达合计
          {{ num(summary.metrics?.planned?.qty_total) }}，实卖
          {{ num(summary.metrics?.sold_total) }}；两条基线各算各的。
          {{ PREP_REVIEW_METRIC_ABS_HINT }}
        </p>
      </div>

      <section class="pr-block pr-why">
        <h2 class="pr-section-title">{{ PREP_REVIEW_WHY_TITLE }}</h2>
        <p class="pr-section-hint">{{ PREP_REVIEW_WHY_HINT }}</p>
        <div class="pr-why-grid">
          <div v-for="dimension in PREP_REVIEW_WHY_DIMENSIONS" :key="dimension.key" class="pr-why-card">
            <p class="pr-why-title">{{ dimension.label }}</p>
            <ul class="pr-why-rows">
              <li
                v-for="row in summary.groups?.[dimension.key] || []"
                :key="row.key"
                class="pr-why-row"
                :class="{ 'is-insufficient': row.insufficient }"
              >
                <div class="pr-why-head">
                  <span class="pr-why-label">{{ row.label }}</span>
                  <span class="pr-why-n">{{ row.n }} 天</span>
                </div>
                <p v-if="row.insufficient" class="pr-why-short">{{ PREP_REVIEW_SAMPLE_SHORT }}</p>
                <div v-else class="pr-why-lines">
                  <div
                    v-for="baseline in baselineRows(row.baselines)"
                    :key="baseline.key"
                    class="pr-why-line"
                  >
                    <span class="pr-why-tag">{{ baseline.label }}</span>
                    <span
                      class="pr-why-dir"
                      :class="directionClass(baseline.data?.direction_deviation)"
                    >
                      {{ directionWord(baseline.data?.direction_deviation) }}
                      {{ signedPct(baseline.data?.direction_deviation) }}
                    </span>
                    <span class="pr-why-abs">{{ pct(baseline.data?.absolute_deviation) }}</span>
                  </div>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <section v-if="summary.cost" class="pr-block pr-cost">
        <h2 class="pr-section-title">{{ PREP_REVIEW_COST_TITLE }}</h2>
        <p class="pr-section-hint">{{ PREP_REVIEW_COST_HINT }}</p>
        <p class="pr-cost-basis">
          按下达量算（店长当天最终执行的量）；参考值那一套只在上面的偏差表里。
        </p>
        <div class="pr-cost-total">
          <span class="pr-cost-k">{{ PREP_REVIEW_COST_TOTAL }}</span>
          <span class="pr-cost-v">{{ num(summary.cost.overfill_qty) }}</span>
        </div>
        <ul class="pr-cost-rows">
          <li v-for="row in PREP_REVIEW_COST_ROWS" :key="row.key" class="pr-cost-row">
            <span class="pr-cost-k">
              {{ row.label }}
              <router-link
                v-if="row.key === 'wastage'"
                class="pr-link"
                to="/workbench/kitchen/loss"
              >{{ PREP_REVIEW_COST_LOSS_LINK }}</router-link>
            </span>
            <span class="pr-cost-v">{{ num(summary.cost[row.key]) }}</span>
            <span class="pr-cost-pct">{{ sharePct(summary.cost[row.key]) }}</span>
          </li>
        </ul>
        <p class="pr-cost-money">
          {{ PREP_REVIEW_COST_AMOUNT }} <b>{{ money(summary.cost.estimated_amount) }}</b>
          <span class="pr-est">{{ PREP_REVIEW_COST_ESTIMATED }}</span>
        </p>
        <p class="pr-section-hint">{{ PREP_REVIEW_COST_ESTIMATED_HINT }}</p>
        <p v-if="summary.cost.pending_trajectory > 0" class="pr-warn-line">
          {{ prepReviewPendingHint(summary.cost.pending_trajectory) }}
        </p>
        <p v-if="summary.cost.wastage_recorded_qty > summary.cost.wastage" class="pr-section-hint">
          {{ PREP_REVIEW_COST_RECORDED_HINT(num(summary.cost.wastage_recorded_qty), num(summary.cost.wastage)) }}
        </p>
      </section>
    </template>

    <template v-if="current">
      <p class="pr-section-hint">{{ PREP_REVIEW_DETAIL_HINT }}</p>
      <div class="pr-scroll">
        <table class="pr-table pr-table-detail">
          <thead>
            <tr>
              <th>备货品</th>
              <th>档口</th>
              <th>档位</th>
              <th class="n">参考值</th>
              <th class="n">下达量</th>
              <th class="n">实际卖出</th>
              <th class="n">对参考</th>
              <th class="n">对下达</th>
              <th class="n" :title="PREP_REVIEW_SOLD_OUT_HINT">{{ PREP_REVIEW_TH_SOLD_OUT }}</th>
              <th class="n">{{ PREP_REVIEW_TH_AFTER_1H }}</th>
              <th class="n">{{ PREP_REVIEW_TH_AFTER_2H }}</th>
              <th class="n">{{ PREP_REVIEW_TH_NEXT_DAY }}</th>
              <th class="n">
                {{ PREP_REVIEW_TH_REFUND }}
                <span class="pr-refund-approx">{{ PREP_REVIEW_REFUND_APPROX }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in currentItems" :key="item.item_name + item.unit">
              <td>
                {{ item.item_name }}<span class="pr-unit">{{ item.unit }}</span>
                <span v-if="item.status === 'broken_rule'" class="pr-warn">配方没接上</span>
              </td>
              <td class="pr-dim">{{ stationName(item.station) }}</td>
              <td class="pr-dim">{{ tier(item) }}</td>
              <td class="n">{{ num(item.reference_qty) }}</td>
              <td class="n">
                {{ num(item.planned_qty) }}
                <span class="pr-unit">{{ plannedTag(item) }}</span>
              </td>
              <td class="n">{{ item.sold_qty === null ? '—' : num(item.sold_qty) }}</td>
              <td class="n" :class="gapClass(item, 'reference_qty')">
                {{ gapVs(item, 'reference_qty') }}
              </td>
              <td class="n" :class="gapClass(item, 'planned_qty')">
                {{ gapVs(item, 'planned_qty') }}
              </td>
              <td class="n">
                <template v-if="item.sold_out_at">
                  {{ soldOutLabel(item) }}
                  <span
                    class="pr-soldout-tag"
                    :class="item.sold_out_reliable ? 'is-trusted' : 'is-suspect'"
                    :title="PREP_REVIEW_SOLD_OUT_HINT"
                  >{{ item.sold_out_reliable ? PREP_REVIEW_SOLD_OUT_TRUSTED : PREP_REVIEW_SOLD_OUT_SUSPECT }}</span>
                </template>
                <span v-else class="pr-dim">—</span>
              </td>
              <td class="n">{{ afterWindowLabel(item, 'sold_1h') }}</td>
              <td class="n">{{ afterWindowLabel(item, 'sold_2h') }}</td>
              <td class="n">{{ afterWindowLabel(item, 'sold_next_day') }}</td>
              <td class="n">{{ item.refund_qty === null || item.refund_qty === undefined ? '—' : num(item.refund_qty) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details class="pr-others">
        <summary>其他钟点（{{ otherClocks.length }} 个）——用来看「越晚越不准」</summary>
        <div class="pr-scroll">
          <table class="pr-table pr-table-compact">
            <thead>
              <tr>
                <th>快照钟点</th>
                <th>口径</th>
                <th class="n">够卖品数</th>
                <th class="n">缺货</th>
                <th class="n">多出</th>
                <th class="n">合计 ÷ 实际</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="group in otherClocks" :key="group.clock">
                <tr v-for="(baseline, index) in baselineRows(group.summary)" :key="baseline.key">
                  <td v-if="index === 0" :rowspan="2">
                    <button type="button" class="pr-link" @click="clock = group.clock">{{ group.clock }}</button>
                  </td>
                  <td>{{ baseline.label }}</td>
                  <td class="n">{{ pct(baseline.data?.coverage_items) }}</td>
                  <td class="n is-short">{{ pct(baseline.data?.shortfall_ratio) }}</td>
                  <td class="n is-over">{{ pct(baseline.data?.overfill_ratio) }}</td>
                  <td class="n">{{ num(baseline.data?.ratio, 2) }}</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </details>
    </template>

    <div v-if="weatherOpen" class="pr-weather-mask" @click.self="closeWeatherSettings">
      <form
        class="pr-weather-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pr-weather-title"
        @submit.prevent="saveWeatherSettings"
      >
        <div class="pr-weather-head">
          <h2 id="pr-weather-title" class="pr-weather-title">{{ WEATHER_SETTINGS_TITLE }}</h2>
          <button
            type="button"
            class="pr-weather-close"
            :aria-label="WEATHER_SETTINGS_CLOSE"
            @click="closeWeatherSettings"
          >
            ×
          </button>
        </div>
        <p class="pr-weather-hint">{{ WEATHER_SETTINGS_HINT }}</p>

        <label class="pr-weather-field" for="pr-weather-key">
          <span>{{ WEATHER_SETTINGS_KEY_LABEL }}</span>
          <input
            id="pr-weather-key"
            v-model="weatherKeyDraft"
            class="pr-weather-input"
            type="password"
            autocomplete="off"
            :placeholder="weatherKeyPlaceholder"
          />
        </label>

        <label class="pr-weather-field" for="pr-weather-city">
          <span>{{ WEATHER_SETTINGS_CITY_LABEL }}</span>
          <input
            id="pr-weather-city"
            v-model="weatherCityDraft"
            class="pr-weather-input"
            type="text"
            autocomplete="off"
            :placeholder="WEATHER_SETTINGS_CITY_PLACEHOLDER"
          />
        </label>

        <label class="pr-weather-field" for="pr-weather-host">
          <span>{{ WEATHER_SETTINGS_HOST_LABEL }}</span>
          <input
            id="pr-weather-host"
            v-model="weatherHostDraft"
            class="pr-weather-input"
            type="text"
            autocomplete="off"
            spellcheck="false"
            :placeholder="WEATHER_SETTINGS_HOST_PLACEHOLDER"
          />
        </label>

        <div class="pr-weather-actions">
          <button
            type="button"
            class="pr-weather-test"
            :disabled="weatherTesting || weatherSaving || weatherLoading"
            @click="testWeatherSettings"
          >
            {{ weatherTesting ? WEATHER_SETTINGS_TESTING : WEATHER_SETTINGS_TEST }}
          </button>
          <button
            type="submit"
            class="pr-weather-save"
            :disabled="weatherSaving || weatherTesting || weatherLoading"
          >
            {{ weatherSaving ? WEATHER_SETTINGS_SAVING : WEATHER_SETTINGS_SAVE }}
          </button>
        </div>

        <p
          v-if="weatherStatus"
          class="pr-weather-status"
          :class="weatherStatusBad ? 'is-bad' : 'is-ok'"
          role="status"
        >
          {{ weatherStatus }}
        </p>
      </form>
    </div>
  </div>
</template>

<style scoped>
.pr {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-4);
  width: 100%;
  min-width: 0;
}

.pr-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-3);
}

.pr-heading {
  min-width: 0;
}

.pr-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.pr-kicker {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-title {
  margin: 2px 0 0;
  font-size: var(--fs-20);
}

.pr-date {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-13);
  color: var(--ink-dim);
  white-space: nowrap;
  flex: none;
}

/* 132px 会把 2026-11-03 截成 2026-11-0：日期框 + 清空按钮一起量 150px */
.pr-date :deep(.luyun-date-picker) {
  width: 150px;
}

.pr-date :deep(.luyun-date-picker .dp--input) {
  width: 100%;
  font: inherit;
  font-size: var(--fs-13);
}

/* 备注 / 导出与钟点切换都要够得着：统一 44px 触控下限 */
.pr-note-btn,
.pr-export,
.pr-note-save,
.pr-note-cancel {
  min-height: var(--tap);
  padding: 0 12px;
  border: var(--edge-w, 2px) solid var(--edge);
  box-shadow: var(--paper-shadow-sm);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink-dim);
  font: inherit;
  font-size: var(--fs-12);
  white-space: nowrap;
  cursor: pointer;
}

.pr-export:disabled {
  opacity: 0.55;
  cursor: default;
}

.pr-note-edit {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.pr-note-input {
  flex: 1 1 240px;
  min-width: 0;
  min-height: var(--tap);
  padding: 0 10px;
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
}


.pr-context-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

/* 设置按钮就挂在天气行后面：没有天气时也要在，否则第一次配置没有入口。 */
.pr-weather-btn {
  min-height: var(--tap);
  padding: 0 12px;
  border: var(--edge-w, 2px) solid var(--edge);
  box-shadow: var(--paper-shadow-sm);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink-dim);
  font: inherit;
  font-size: var(--fs-12);
  white-space: nowrap;
  cursor: pointer;
}

.pr-weather-btn:hover {
  color: var(--ink);
  border-color: var(--line-strong);
}

.pr-weather-mask {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sp-4);
  background: var(--scrim);
}

.pr-weather-dialog {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  width: 460px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 32px);
  overflow: auto;
  padding: var(--sp-4);
  border: var(--edge-w, 2px) solid var(--edge);
  box-shadow: var(--paper-shadow-sm);
  border-radius: var(--r-2);
  background: var(--surface-1);
  color: var(--ink);
}

.pr-weather-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.pr-weather-title {
  margin: 0;
  font-size: var(--fs-16);
}

.pr-weather-close {
  min-width: var(--tap);
  min-height: var(--tap);
  border: 1px solid transparent;
  border-radius: var(--r-1);
  background: transparent;
  color: var(--ink-dim);
  font: inherit;
  font-size: var(--fs-20);
  line-height: 1;
  cursor: pointer;
}

.pr-weather-hint {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-faint);
}

.pr-weather-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-weather-input {
  width: 100%;
  min-width: 0;
  min-height: var(--tap);
  padding: 0 10px;
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
}

.pr-weather-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}

.pr-weather-test,
.pr-weather-save {
  min-height: var(--tap);
  padding: 0 14px;
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-1);
  font: inherit;
  font-size: var(--fs-13);
  cursor: pointer;
}

.pr-weather-test {
  background: var(--surface-1);
  color: var(--ink-dim);
}

.pr-weather-save {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
}

.pr-weather-test:disabled,
.pr-weather-save:disabled {
  opacity: 0.55;
  cursor: default;
}

.pr-weather-status {
  margin: 0;
  font-size: var(--fs-12);
  line-height: 1.5;
}

.pr-weather-status.is-ok {
  color: var(--ok);
}

.pr-weather-status.is-bad {
  color: var(--danger-bright);
}

.pr-context,
.pr-scope,
.pr-section-hint {
  margin: 0;
  font-size: var(--fs-11);
  color: var(--ink-faint);
}

.pr-note-view {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

/* 数据质量提示（票 09）：一条提示条，横向铺满内容区。颜色全部走语义令牌，
   左侧加粗边是「解释项」与正文的区分，不靠字号压人。 */
.pr-quality {
  margin: 6px 0 0;
  padding: 8px 12px;
  border: var(--edge-w, 2px) solid var(--warn);
  border-left-width: 3px;
  border-radius: var(--r-1, 8px);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-12, 12px);
  line-height: 1.5;
}

.pr-scope {
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-section-title {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-13);
  font-weight: 600;
  color: var(--ink);
}

.pr-block {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}

.pr-empty,
.pr-error,
.pr-line {
  margin: 0;
  font-size: var(--fs-13);
  color: var(--ink-dim);
}

.pr-error {
  color: var(--danger-bright);
}

/* 24 个钟点铺三行会把「指标卡」挤出首屏：一行横向滚，手指划一下就到 */
.pr-clocks {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-bottom: 2px;
}

.pr-clocks::-webkit-scrollbar {
  display: none;
}

.pr-clock {
  flex: none;
  display: inline-flex;
  align-items: center;
  min-height: var(--tap);
  padding: 3px 9px;
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--ink-dim);
  font: inherit;
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.pr-clock.is-on {
  border-color: var(--action);
  background: var(--action-soft);
  color: var(--ink);
}

.pr-clock.is-primary:not(.is-on) {
  border-color: var(--line-strong);
  color: var(--ink);
}

.pr-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-3);
}

.pr-metric {
  padding: var(--sp-3);
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-2);
  background: var(--surface-2);
}

.pr-metric-k {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-metric-v {
  margin: 4px 0 2px;
  font-size: var(--fs-24);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.pr-metric-review .pr-metric-v {
  font-size: var(--fs-20);
}

.pr-metric-word {
  margin-left: 6px;
  font-size: var(--fs-12);
  font-weight: 400;
}

.pr-metric-v.is-ok,
.is-ok {
  color: var(--ok);
}

.pr-metric-v.is-warn {
  color: var(--warn);
}

.pr-metric-v.is-short {
  color: var(--danger-bright);
}

.pr-metric-v.is-over {
  color: var(--warn);
}

.pr-metric-h {
  margin: 0;
  font-size: var(--fs-11);
  color: var(--ink-faint);
}

/* 「为什么不准」：两列卡，手机上收成一列；行内四格 = 标签 / 天数 / 方向 / 绝对 */
.pr-why-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3);
}

.pr-why-card {
  min-width: 0;
  padding: var(--sp-2) var(--sp-3);
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-2);
  background: var(--surface-2);
}

.pr-why-title {
  margin: 0 0 var(--sp-1);
  font-size: var(--fs-12);
  font-weight: 600;
  color: var(--ink-dim);
}

.pr-why-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.pr-why-row {
  padding: 4px 0;
  border-top: 1px solid var(--line);
  font-size: var(--fs-12);
}

.pr-why-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.pr-why-lines {
  margin-top: 2px;
}

.pr-why-line {
  display: grid;
  grid-template-columns: 3.2em minmax(0, 1fr) minmax(0, 1fr);
  gap: 6px;
  align-items: baseline;
}

.pr-why-tag {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}

.pr-why-row:first-child {
  border-top: 0;
}

.pr-why-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pr-why-n {
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.pr-why-dir,
.pr-why-abs {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.pr-why-short {
  margin: 2px 0 0;
  text-align: right;
  color: var(--ink-faint);
}

/* 两条基线并排：同一份汇总里的参考值 / 下达量。 */
.pr-compare {
  overflow-x: auto;
}

.pr-compare-table {
  min-width: 620px;
}

.pr-baseline {
  font-weight: 600;
  white-space: nowrap;
}

.pr-cost-basis {
  margin: 2px 0 var(--sp-2);
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-cost-total {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  font-size: var(--fs-13);
}

.pr-cost-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.pr-cost-row {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-13);
}

.pr-cost-k {
  color: var(--ink-dim);
}

.pr-cost-v {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.pr-cost-pct {
  margin-left: auto;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}

.pr-cost-money {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-13);
}

.pr-est {
  margin-left: 6px;
  padding: 1px 6px;
  border: var(--edge-w, 2px) solid var(--edge);
  border-radius: var(--r-full);
  font-size: var(--fs-11);
  color: var(--ink-faint);
}

.pr-warn-line {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--warn);
}

/* 明细 12 列：手机上整表横滑，页面本身不横向溢出 */
.pr-scroll {
  overflow-x: auto;
}

.pr-scroll .pr-table {
  min-width: 780px;
}

.pr-scroll .pr-table-compact {
  min-width: 520px;
}

.pr-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-13);
}

.pr-table th {
  text-align: left;
  font-weight: 600;
  font-size: var(--fs-12);
  color: var(--ink-dim);
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  white-space: nowrap;
}

.pr-table td {
  border-bottom: 1px solid var(--line);
  padding: 7px 10px;
}

.pr-table .n {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.pr-table-compact td,
.pr-table-compact th {
  padding: 5px 8px;
}

.pr-unit {
  margin-left: 3px;
  font-size: var(--fs-11);
  color: var(--ink-dim);
}

.pr-refund-approx {
  margin-left: 3px;
  font-size: var(--fs-11);
  font-weight: 400;
  color: var(--ink-faint);
}

.pr-dim {
  color: var(--ink-dim);
}

.pr-warn {
  margin-left: 6px;
  font-size: var(--fs-11);
  color: var(--warn);
}

.is-short {
  color: var(--danger-bright);
}

.is-over {
  color: var(--warn);
}

.pr-soldout-tag {
  margin-left: 4px;
  padding: 0 3px;
  border: var(--edge-w, 2px) solid currentColor;
  border-radius: 4px;
  font-size: var(--fs-11);
  line-height: 13px;
  vertical-align: 1px;
}

.pr-soldout-tag.is-trusted {
  color: var(--ok);
}

.pr-soldout-tag.is-suspect {
  color: var(--ink-faint);
}

.pr-others summary {
  cursor: pointer;
  font-size: var(--fs-13);
  color: var(--ink-dim);
  padding: 6px 0;
}

.pr-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--info);
  font: inherit;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

@media (max-width: 720px) {
  .pr {
    padding: var(--sp-3);
  }

  /* 手机上指标不缩成一行三列：24px 的数字挤在 1/3 屏宽里会换行 */
  .pr-metrics {
    grid-template-columns: minmax(0, 1fr);
  }

  .pr-why-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .pr-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .pr-actions {
    width: 100%;
  }

  .pr-table {
    font-size: var(--fs-12);
  }

  /* 当天两套口径在手机上排成两组键值行：六列横滑会把缺货/多出藏在屏外。 */
  .pr-compare-day {
    min-width: 0;
  }

  .pr-compare-day thead {
    display: none;
  }

  .pr-compare-day tbody,
  .pr-compare-day tr {
    display: block;
  }

  .pr-compare-day tr {
    padding: 6px 0 8px;
    border-bottom: 1px solid var(--line);
  }

  .pr-compare-day td {
    display: flex;
    justify-content: space-between;
    gap: var(--sp-3);
    padding: 2px 0;
    border: 0;
    text-align: right;
  }

  .pr-compare-day td::before {
    content: attr(data-label);
    color: var(--ink-dim);
    text-align: left;
  }

  .pr-compare-day td.pr-baseline {
    display: block;
    padding-bottom: 4px;
    text-align: left;
  }

  .pr-compare-day td.pr-baseline::before {
    content: none;
  }

  /* 90 天只有三列，不必为了对齐去横滑。 */
  .pr-compare-review {
    min-width: 0;
  }
}
</style>
