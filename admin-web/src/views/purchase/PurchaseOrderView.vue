<script setup>
/**
 * 厨房采购 · 下单（对齐 worktool2 小程序的交互，走服务端）。
 *
 * 口径：
 *  - 「全部」只用于浏览和填数；提交前必须选一个采购岗位，一张单只属于一个岗位。
 *  - 提交时会把该岗位下所有「需盘点」商品的库存写成当天盘点，没填按 0，
 *    同一天重提会覆盖 —— 页面必须把这件事说清楚。
 *  - 需要盘点的商品不再单列建议量：参考值就是按库存算出的下单量，
 *    公式为参考值 × 安全系数 − 库存，按步长向上取整；库存一改就实时重算。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client.js'
import { purchaseOrderPath } from '../../utils/purchasePaths.js'
import {
  formatClock,
  formatMonthDay,
  formatMonthDayChinese,
  formatQty,
  lunarMonthDay,
  suggestedQuantity,
  todayText,
} from './purchaseUi.js'
import PurchaseNav from './PurchaseNav.vue'

const router = useRouter()

const POSITION_STORAGE_KEY = 'luyun:purchase:position'
const DRAFT_PREFIX = 'luyun:purchase:draft:'
const REFERENCE_DAYS_KEY = 'luyun:purchase:reference-days'
const REFERENCE_DAYS_MIN = 3
const REFERENCE_DAYS_MAX = 90
const REFERENCE_DAYS_DEFAULT = 7
const WEATHER_REFRESH_MS = 30 * 60 * 1000
const WEATHER_LABELS = ['今天', '明天', '后天']

function readReferenceDays() {
  try {
    const value = Number(window.localStorage.getItem(REFERENCE_DAYS_KEY))
    if (Number.isInteger(value) && value >= REFERENCE_DAYS_MIN && value <= REFERENCE_DAYS_MAX) {
      return value
    }
  } catch (err) {
    // localStorage 不可用时按默认 7 天。
  }
  return REFERENCE_DAYS_DEFAULT
}

const meta = ref({ positions: [] })
const selectedPositionId = ref(0)
const submitPositionId = ref(0)
const rows = ref([])
const quantities = reactive({})
const stocks = reactive({})
const search = ref('')
const loadingMeta = ref(false)
const loadingRows = ref(false)
const submitting = ref(false)
const errorText = ref('')
const notice = ref('')
const dialogError = ref('')
const confirmOpen = ref(false)

const today = todayText()
const draftKey = DRAFT_PREFIX + today
const referenceDays = ref(readReferenceDays())
const referenceDayOptions = Array.from(
  { length: REFERENCE_DAYS_MAX - REFERENCE_DAYS_MIN + 1 },
  (_, index) => REFERENCE_DAYS_MIN + index,
)
const weather = ref({ configured: false, available: false, days: [], fetched_at: '' })
const weatherLoading = ref(true)
let weatherTimer = 0

const weekday = computed(() => {
  const value = new Date(today + 'T00:00:00')
  if (Number.isNaN(value.getTime())) return ''
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][value.getDay()] || ''
})

const dateLabel = computed(() => formatMonthDayChinese(today))
const lunarLabel = computed(() => lunarMonthDay(today))

const nextDayLabel = computed(() => {
  const value = new Date(today + 'T00:00:00')
  if (Number.isNaN(value.getTime())) return ''
  value.setDate(value.getDate() + 1)
  const iso = [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, '0'),
    String(value.getDate()).padStart(2, '0'),
  ].join('-')
  return formatMonthDay(iso) + ' ' + ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][value.getDay()]
})

const targetTagValue = ref('')
const targetTag = computed(() => {
  if (targetTagValue.value) return targetTagValue.value
  const row = rows.value.find((item) => item.target_tag)
  return row ? row.target_tag : ''
})

const positions = computed(() =>
  (meta.value.positions || []).filter(
    (position) => position.active && position.show_in_order !== false,
  ),
)

const selectedPosition = computed(
  () => positions.value.find((position) => position.id === selectedPositionId.value) || null,
)

function offsetDateText(offset) {
  const value = new Date(today + 'T12:00:00')
  if (Number.isNaN(value.getTime())) return ''
  value.setDate(value.getDate() + offset)
  return todayText(value)
}

function weatherTemp(day) {
  if (!day) return '—'
  const min = day.temp_min
  const max = day.temp_max
  if ((min === null || min === undefined) && (max === null || max === undefined)) return '—'
  if (min === null || min === undefined) return max + '℃'
  if (max === null || max === undefined) return min + '℃'
  return min + '~' + max + '℃'
}

function weatherMeta(day) {
  if (!day) return ''
  const parts = []
  if (day.humidity !== null && day.humidity !== undefined && day.humidity !== '') {
    parts.push('湿度 ' + day.humidity + '%')
  }
  const wind = [day.wind_dir, day.wind_scale].filter(Boolean).join(' ')
  if (wind) parts.push(wind.endsWith('级') ? wind : wind + '级')
  return parts.join(' · ')
}

const weatherCards = computed(() =>
  WEATHER_LABELS.map((label, index) => {
    const day = (weather.value.days || [])[index] || null
    const dateText = day && day.date ? day.date : offsetDateText(index)
    return {
      label,
      dateLabel: formatMonthDayChinese(dateText),
      temp: weatherTemp(day),
      text: day && day.text ? day.text : weatherLoading.value ? '正在读天气…' : '天气暂不可用',
      meta: weatherMeta(day),
      placeholder: !day || !day.text,
    }
  }),
)

const weatherTimeText = computed(() => {
  if (weather.value.available && weather.value.fetched_at) {
    return '更新于 ' + formatClock(weather.value.fetched_at)
  }
  if (weatherLoading.value) return '正在读天气…'
  if (!weather.value.configured) return '天气未配置'
  return '天气暂不可用'
})

const confirmPosition = computed(
  () => positions.value.find((position) => position.id === submitPositionId.value) || null,
)

const filteredRows = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return rows.value
  return rows.value.filter((row) => {
    const haystack = [
      row.name,
      row.product_code,
      row.supplier_name,
      row.category_name,
      row.specification,
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
})

const groups = computed(() => {
  const buckets = new Map()
  for (const row of filteredRows.value) {
    const key = row.supplier_name || '未分供应商'
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(row)
  }
  return Array.from(buckets.entries()).map(([supplierName, items]) => ({
    supplierName,
    items,
    quantity: items.reduce((sum, row) => sum + (Number(quantities[row.id]) || 0), 0),
  }))
})

function positionIdsOf(row) {
  const list = Array.isArray(row.positions) ? row.positions : []
  return list
    .map((item) => (typeof item === 'object' ? Number(item.id) : Number(item)))
    .filter((id) => Number.isFinite(id) && id > 0)
}

function belongsTo(row, positionId) {
  if (!positionId) return false
  const ids = positionIdsOf(row)
  if (!ids.length) return false
  return ids.includes(Number(positionId))
}

const scopedRows = computed(() => {
  const id = submitPositionId.value
  if (!id) return []
  return rows.value.filter((row) => belongsTo(row, id))
})

const confirmStockRows = computed(() =>
  scopedRows.value.filter((row) => row.need_stock_check),
)

const confirmItems = computed(() =>
  scopedRows.value.filter((row) => {
    const value = Number(quantities[row.id])
    return Number.isFinite(value) && value > 0
  }),
)

const confirmQuantity = computed(() =>
  confirmItems.value.reduce((sum, row) => sum + (Number(quantities[row.id]) || 0), 0),
)

const confirmMissingStock = computed(
  () =>
    confirmStockRows.value.filter(
      (row) => stocks[row.id] === '' || stocks[row.id] === null || stocks[row.id] === undefined,
    ).length,
)

function previewSort(a, b) {
  return String(a.product_code || '').localeCompare(String(b.product_code || ''))
}

function groupPreviewRows(list) {
  const buckets = new Map()
  for (const row of list) {
    const key = row.supplier_name || '未分供应商'
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(row)
  }
  return Array.from(buckets.entries()).map(([supplierName, items]) => ({
    supplierName,
    items: items.slice().sort(previewSort),
  }))
}

// 小程序 previewOrder 的结构：已填数量的按供应商分组，未填的单独列在「未下单」。
const previewOrderedGroups = computed(() => groupPreviewRows(confirmItems.value))
const previewNotOrderedGroups = computed(() =>
  groupPreviewRows(
    scopedRows.value.filter((row) => {
      const value = Number(quantities[row.id])
      return !(Number.isFinite(value) && value > 0)
    }),
  ),
)

function previewQuantity(row) {
  const value = Number(quantities[row.id])
  return Number.isFinite(value) && value > 0 ? formatQty(value) : '-'
}

function previewStock(row) {
  if (!row.need_stock_check) return ''
  const raw = stocks[row.id]
  if (raw === '' || raw === null || raw === undefined) return '库存 按 0'
  return '库存 ' + formatQty(raw)
}

const needStockRows = computed(() => rows.value.filter((row) => row.need_stock_check))

const filledRows = computed(() =>
  rows.value.filter((row) => {
    const value = Number(quantities[row.id])
    return Number.isFinite(value) && value > 0
  }),
)

const filledQuantity = computed(() =>
  filledRows.value.reduce((sum, row) => sum + (Number(quantities[row.id]) || 0), 0),
)

const filledStockCount = computed(
  () =>
    needStockRows.value.filter(
      (row) => stocks[row.id] !== '' && stocks[row.id] !== null && stocks[row.id] !== undefined,
    ).length,
)

function referenceText(row) {
  if (row.reference === null || row.reference === undefined) return '参考 —'
  if (row.reference === '!') return '参考！'
  // 需要盘点的商品不再单列建议量：参考值就是按库存算出的下单量。
  if (row.need_stock_check) return '参考 ' + suggestedValue(row)
  return '参考 ' + formatQty(row.reference)
}

function yesterdayText(row) {
  if (row.yesterday_quantity === null || row.yesterday_quantity === undefined) return '昨日 —'
  return '昨日 ' + formatQty(row.yesterday_quantity)
}

function suggestedValue(row) {
  const rawStock = stocks[row.id]
  if (rawStock === '' || rawStock === null || rawStock === undefined) {
    return row.suggested_quantity === null || row.suggested_quantity === undefined
      ? '—'
      : formatQty(row.suggested_quantity)
  }
  const value = suggestedQuantity(row.reference, {
    safetyFactor: row.safety_factor,
    currentStock: Number(rawStock),
    orderStep: row.order_step,
  })
  return value === null ? '—' : formatQty(value)
}

function positionText(row) {
  if (!row.positions || !row.positions.length) return '未挂岗位'
  return row.positions.map((item) => item.name).join('、')
}

function isStockMissing(row) {
  if (!row.need_stock_check) return false
  const value = stocks[row.id]
  return value === '' || value === null || value === undefined
}

function stepOf(row) {
  const step = Number(row.order_step)
  return Number.isFinite(step) && step > 0 ? step : 1
}

function stepLabel(row, factor) {
  const value = stepOf(row) * factor
  return (factor > 0 ? '+' : '') + formatQty(value)
}

function roundValue(value, step) {
  if (step < 1) return Math.round(value * 10) / 10
  return Math.round(value * 100) / 100
}

function adjustNumber(key, row, direction) {
  const store = key === 'stock' ? stocks : quantities
  const step = stepOf(row)
  const current = Number(store[row.id])
  const base = Number.isFinite(current) ? current : 0
  const next = Math.max(0, roundValue(base + direction * step, step))
  store[row.id] = next > 0 ? next : ''
}

function roundStock(value) {
  return Math.round(value * 10) / 10
}

function adjustStock(row, direction) {
  const raw = stocks[row.id]
  const current = raw === '' || raw === null || raw === undefined ? 0 : Number(raw)
  const base = Number.isFinite(current) ? current : 0
  // 库存的减号减到 0 就停在 0，不能变成空值（空值代表还没盘）。
  stocks[row.id] = Math.max(0, roundStock(base + direction * stepOf(row)))
}

function limitStockInput(row) {
  const raw = String(stocks[row.id] ?? '')
  if (!raw) return
  let cleaned = raw.replace(/[^\d.]/g, '')
  const dot = cleaned.indexOf('.')
  if (dot >= 0) {
    cleaned =
      cleaned.slice(0, dot + 1) +
      cleaned.slice(dot + 1).replace(/\./g, '').slice(0, 1)
  }
  if (cleaned !== raw) stocks[row.id] = cleaned
}

function adjustQuantity(row, direction) {
  adjustNumber('quantity', row, direction)
}

function normalizeNumber(key, row) {
  const store = key === 'stock' ? stocks : quantities
  const raw = store[row.id]
  if (raw === '' || raw === null || raw === undefined) return
  const value = Number(raw)
  if (!Number.isFinite(value)) {
    store[row.id] = ''
    return
  }
  if (key === 'stock') {
    // 盘点库存只保留小数点后一位；0 是有效值，不能当空值。
    store[row.id] = Math.max(0, roundStock(value))
    return
  }
  if (value <= 0) {
    store[row.id] = ''
    return
  }
  store[row.id] = roundValue(value, stepOf(row))
}

function clearInputs() {
  for (const key of Object.keys(quantities)) quantities[key] = ''
  for (const key of Object.keys(stocks)) stocks[key] = ''
  notice.value = '已清空下单数量和盘点库存。'
}

function resetInputs(list, useDraft = true) {
  for (const key of Object.keys(quantities)) delete quantities[key]
  for (const key of Object.keys(stocks)) delete stocks[key]
  for (const row of list) {
    quantities[row.id] = ''
    // 盘点默认空着，必须由下单人自己填；草稿里保存过的再恢复。
    stocks[row.id] = ''
  }
  if (useDraft) applyDraft()
}

function applyDraft() {
  try {
    const raw = window.localStorage.getItem(draftKey)
    if (!raw) return
    const draft = JSON.parse(raw)
    if (!draft || typeof draft !== 'object') return
    for (const [key, value] of Object.entries(draft.quantities || {})) {
      if (key in quantities) quantities[key] = value
    }
    for (const [key, value] of Object.entries(draft.stocks || {})) {
      if (key in stocks) stocks[key] = value
    }
  } catch (err) {
    // 草稿坏了不影响下单，直接忽略。
  }
}

function readDraft() {
  try {
    const raw = window.localStorage.getItem(draftKey)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch (err) {
    return {}
  }
}

function saveDraft() {
  try {
    // 切换岗位时只重算当前岗位的行；已填的其他岗位要留在草稿里，
    // 所以先读旧草稿再合并，当前行清空才从草稿里删掉。
    const previous = readDraft()
    const next = {
      quantities: { ...(previous.quantities || {}) },
      stocks: { ...(previous.stocks || {}) },
    }
    for (const [key, value] of Object.entries(quantities)) {
      if (value === '' || value === null || value === undefined) delete next.quantities[key]
      else next.quantities[key] = value
    }
    for (const [key, value] of Object.entries(stocks)) {
      if (value === '' || value === null || value === undefined) delete next.stocks[key]
      else next.stocks[key] = value
    }
    if (!Object.keys(next.quantities).length && !Object.keys(next.stocks).length) {
      window.localStorage.removeItem(draftKey)
      return
    }
    window.localStorage.setItem(draftKey, JSON.stringify(next))
  } catch (err) {
    // localStorage 不可用时静默跳过。
  }
}

function clearDraftForPosition(positionId) {
  try {
    const draft = readDraft()
    const ids = new Set(
      rows.value
        .filter((row) => belongsTo(row, positionId))
        .map((row) => String(row.id)),
    )
    for (const key of ids) {
      delete (draft.quantities || {})[key]
      delete (draft.stocks || {})[key]
    }
    if (!Object.keys(draft.quantities || {}).length && !Object.keys(draft.stocks || {}).length) {
      window.localStorage.removeItem(draftKey)
    } else {
      window.localStorage.setItem(draftKey, JSON.stringify(draft))
    }
  } catch (err) {
    // ignore
  }
}

async function loadWeather() {
  try {
    const data = await api.get('/api/purchase/weather')
    if (data && Array.isArray(data.days)) weather.value = data
  } catch (err) {
    // 天气只是头部信息：失败保留上一次成功的预报，第一次失败就画占位卡。
    if (!weather.value.days.length) {
      // 拿不到响应时不知道门店有没有配 Key，按「暂不可用」显示，不误导成未配置。
      weather.value = { configured: true, available: false, days: [], fetched_at: '' }
    }
  } finally {
    weatherLoading.value = false
  }
}

function changeReferenceDays(event) {
  const parsed = Number(event.target.value)
  const value =
    Number.isInteger(parsed) && parsed >= REFERENCE_DAYS_MIN && parsed <= REFERENCE_DAYS_MAX
      ? parsed
      : REFERENCE_DAYS_DEFAULT
  referenceDays.value = value
  try {
    window.localStorage.setItem(REFERENCE_DAYS_KEY, String(value))
  } catch (err) {
    // localStorage 不可用时只影响下次打开，不影响本次计算。
  }
  loadReference()
}

async function loadMeta() {
  loadingMeta.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/purchase/meta')
    meta.value = data || { positions: [] }
    const saved = Number(window.localStorage.getItem(POSITION_STORAGE_KEY) || 0)
    const match = positions.value.find((position) => position.id === saved)
    selectedPositionId.value = match ? match.id : 0
    await loadReference()
  } catch (err) {
    errorText.value = err.message || '采购岗位没加载出来'
  } finally {
    loadingMeta.value = false
  }
}

async function loadReference() {
  loadingRows.value = true
  errorText.value = ''
  notice.value = ''
  try {
    const params = { day: today, days: referenceDays.value }
    if (selectedPositionId.value) params.position_id = selectedPositionId.value
    const data = await api.get('/api/purchase/reference', params)
    rows.value = data.products || []
    targetTagValue.value = data.target_tag || ''
    resetInputs(rows.value)
    window.localStorage.setItem(POSITION_STORAGE_KEY, String(selectedPositionId.value))
  } catch (err) {
    rows.value = []
    errorText.value = err.message || '采购参考值没算出来'
  } finally {
    loadingRows.value = false
  }
}

function choosePosition(positionId) {
  if (positionId === selectedPositionId.value) return
  selectedPositionId.value = positionId
  loadReference()
}

function openConfirm() {
  dialogError.value = ''
  submitPositionId.value = selectedPositionId.value || 0
  confirmOpen.value = true
}

async function submitOrder() {
  if (!submitPositionId.value) {
    dialogError.value = '提交前先选一个采购岗位。'
    return
  }
  const scoped = rows.value.filter((row) => belongsTo(row, submitPositionId.value))
  const items = []
  const stock = {}
  for (const row of scoped) {
    const quantity = Number(quantities[row.id])
    if (Number.isFinite(quantity) && quantity > 0) {
      items.push({ product_id: row.id, quantity })
    }
    if (row.need_stock_check) {
      const raw = stocks[row.id]
      const value = raw === '' || raw === null || raw === undefined ? 0 : Number(raw)
      stock[row.id] = Number.isFinite(value) && value >= 0 ? value : 0
    }
  }
  if (!items.length) {
    dialogError.value = '这个岗位还没有填下单数量。'
    return
  }
  submitting.value = true
  dialogError.value = ''
  try {
    const data = await api.post('/api/purchase/orders', {
      position_id: submitPositionId.value,
      items,
      stock,
      reference_days: referenceDays.value,
    })
    const orderId = data && data.order ? data.order.id : 0
    confirmOpen.value = false
    clearDraftForPosition(submitPositionId.value)
    if (orderId) {
      router.push(purchaseOrderPath(orderId))
    } else {
      notice.value = '采购单已提交。'
      await loadReference()
    }
  } catch (err) {
    dialogError.value = err.message || '采购单没提交成功'
  } finally {
    submitting.value = false
  }
}

watch([quantities, stocks], saveDraft, { deep: true })

onMounted(async () => {
  await loadMeta()
  loadWeather()
  weatherTimer = window.setInterval(loadWeather, WEATHER_REFRESH_MS)
})

onBeforeUnmount(() => {
  if (weatherTimer) window.clearInterval(weatherTimer)
  weatherTimer = 0
})
</script>

<template>
  <div class="po-page">
    <PurchaseNav active="order" />

    <header class="po-hero">
      <h1 class="po-sr-only">厨房采购</h1>
      <div class="po-date-row">
        <div class="po-date-block">
          <strong class="po-date">{{ dateLabel }} <em>{{ weekday }}</em></strong>
          <span v-if="lunarLabel" class="po-lunar">{{ lunarLabel }}</span>
        </div>
        <div class="po-factor">
          <span class="po-factor-pill">次日 {{ nextDayLabel }}</span>
          <span v-if="targetTag" class="po-factor-pill po-factor-tag">{{ targetTag }}</span>
          <label class="po-days">
            <span>参考</span>
            <select
              class="po-days-select"
              :value="String(referenceDays)"
              aria-label="参考天数"
              @change="changeReferenceDays"
            >
              <option v-for="days in referenceDayOptions" :key="days" :value="String(days)">
                {{ days }} 天
              </option>
            </select>
          </label>
        </div>
      </div>
      <div class="po-weather-grid">
        <article
          v-for="card in weatherCards"
          :key="card.label"
          class="po-weather-card"
          :class="{ 'is-placeholder': card.placeholder }"
        >
          <div class="po-weather-head">
            <span class="po-weather-label">{{ card.label }}</span>
            <span class="po-weather-date">{{ card.dateLabel }}</span>
          </div>
          <strong class="po-weather-temp">{{ card.temp }}</strong>
          <span class="po-weather-text">{{ card.text }}</span>
          <span v-if="card.meta" class="po-weather-meta">{{ card.meta }}</span>
        </article>
      </div>
      <p class="po-weather-time">{{ weatherTimeText }}</p>
    </header>

    <p class="po-context">
      当前 {{ selectedPosition ? selectedPosition.name : '全部岗位' }} · {{ rows.length }} 项商品
    </p>

    <p v-if="errorText" class="po-banner" role="alert">{{ errorText }}</p>
    <p v-else-if="notice" class="po-banner po-banner-soft">{{ notice }}</p>

    <section class="po-card po-position-card">
      <div class="po-card-head">
        <div>
          <h2>采购岗位</h2>
          <p class="po-hint">先选岗位看商品；提交时会再确认一次。</p>
        </div>
        <span class="po-hint">
          {{ rows.length }} 项 · 需盘点 {{ needStockRows.length }} 项
        </span>
      </div>
      <div v-if="loadingMeta" class="po-empty">正在加载岗位…</div>
      <div v-else-if="!positions.length" class="po-empty">
        还没有可下单的采购岗位：请店长在「商品与供应商」里建岗位并给商品挂上。
      </div>
      <div v-else class="po-position-grid">
        <button
          type="button"
          class="po-position-button"
          :class="{ 'is-active': selectedPositionId === 0 }"
          @click="choosePosition(0)"
        >
          全部
        </button>
        <button
          v-for="position in positions"
          :key="position.id"
          type="button"
          class="po-position-button"
          :class="{ 'is-active': position.id === selectedPositionId }"
          @click="choosePosition(position.id)"
        >
          {{ position.name }}
        </button>
      </div>
    </section>

    <section class="po-card">
      <div class="po-tools">
        <input
          v-model="search"
          class="po-search"
          type="search"
          placeholder="搜商品 / 编码 / 供应商"
          aria-label="搜商品"
        />
        <button type="button" class="po-btn po-btn-ghost" @click="clearInputs">清空数量</button>
      </div>
    </section>

    <div v-if="loadingRows" class="po-empty">正在算参考值…</div>
    <div v-else-if="!rows.length" class="po-empty">
      这个范围还没有挂商品：请店长在「商品与供应商」里给商品勾上岗位。
    </div>
    <div v-else-if="!filteredRows.length" class="po-empty">没有匹配的商品。</div>

    <section v-for="group in groups" :key="group.supplierName" class="po-supplier">
      <header class="po-supplier-head">
        <h2>{{ group.supplierName }}</h2>
        <span>{{ group.items.length }} 项 · 合计 {{ formatQty(group.quantity) }}</span>
      </header>

      <article v-for="row in group.items" :key="row.id" class="po-product">
        <header class="po-product-head">
          <div class="po-product-title">
            <strong>{{ row.name }}</strong>
            <span v-if="row.is_long_cycle" class="po-badge po-badge-soft">长周期</span>
                      </div>
          <div v-if="row.need_stock_check" class="po-flag" :title="'安全系数 ' + formatQty(row.safety_factor)">
            <span>盘</span>
            <span>{{ formatQty(row.safety_factor) }}</span>
          </div>
        </header>

        <div class="po-pills">
          <span class="po-pill"><em>单位</em><strong>{{ row.unit || '—' }}</strong></span>
          <span v-if="row.specification" class="po-pill"><em>规格</em><strong>{{ row.specification }}</strong></span>
          <span class="po-pill"><em>岗位</em><strong>{{ positionText(row) }}</strong></span>
        </div>

        <p v-if="row.remark" class="po-remark"><em>备注</em>{{ row.remark }}</p>

        <div class="po-product-body">
          <template v-if="row.need_stock_check">
            <div class="po-info po-info-stock">
              <strong>库存<i class="po-required">*</i></strong>
            </div>
            <div class="po-stepper-row">
              <button type="button" class="po-step po-step-dec10" @click="adjustStock(row, -10)">{{ stepLabel(row, -10) }}</button>
              <button type="button" class="po-step po-step-dec1" @click="adjustStock(row, -1)">{{ stepLabel(row, -1) }}</button>
              <input
                v-model="stocks[row.id]"
                class="po-step-input"
                :class="{ 'is-required-empty': isStockMissing(row) }"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                :placeholder="isStockMissing(row) ? '必填' : '0'"
                :aria-label="row.name + ' 盘点库存'"
                @input="limitStockInput(row)"
                @blur="normalizeNumber('stock', row)"
              />
              <button type="button" class="po-step po-step-inc1" @click="adjustStock(row, 1)">{{ stepLabel(row, 1) }}</button>
              <button type="button" class="po-step po-step-inc10" @click="adjustStock(row, 10)">{{ stepLabel(row, 10) }}</button>
            </div>
          </template>

          <div class="po-info">
            <span class="po-info-label">{{ yesterdayText(row) }}</span>
            <strong :class="{ 'po-ref-warn': row.reference === '!' }">{{ referenceText(row) }}</strong>
          </div>
          <div class="po-stepper-row">
            <button type="button" class="po-step po-step-dec10" @click="adjustQuantity(row, -10)">{{ stepLabel(row, -10) }}</button>
            <button type="button" class="po-step po-step-dec1" @click="adjustQuantity(row, -1)">{{ stepLabel(row, -1) }}</button>
            <input
              v-model="quantities[row.id]"
              class="po-step-input"
              type="number"
              inputmode="decimal"
              min="0"
              step="any"
              placeholder="0"
              :aria-label="row.name + ' 下单数量'"
              @blur="normalizeNumber('quantity', row)"
            />
            <button type="button" class="po-step po-step-inc1" @click="adjustQuantity(row, 1)">{{ stepLabel(row, 1) }}</button>
            <button type="button" class="po-step po-step-inc10" @click="adjustQuantity(row, 10)">{{ stepLabel(row, 10) }}</button>
          </div>
        </div>
      </article>
    </section>

    <div class="po-sticky">
      <div class="po-sticky-text">
        <strong>{{ filledRows.length }}</strong> 项 · 合计 {{ formatQty(filledQuantity) }}
        <span v-if="selectedPosition"> · 盘点已填 {{ filledStockCount }} / {{ needStockRows.length }}</span>
        <span v-else> · 全部浏览</span>
      </div>
      <button class="po-btn po-btn-primary po-submit" type="button" @click="openConfirm">
        提交采购单
      </button>
    </div>

    <Teleport to="body">
      <div v-if="confirmOpen" class="po-scrim" @click.self="confirmOpen = false">
        <div class="po-dialog" role="dialog" aria-modal="true" aria-label="确认提交采购单">
          <h2>提交这张采购单？</h2>

          <div v-if="!selectedPosition" class="po-position-picker">
            <p class="po-dialog-label">选一个岗位提交（一张单只属于一个岗位）</p>
            <div class="po-chips">
              <button
                v-for="position in positions"
                :key="position.id"
                type="button"
                class="po-chip"
                :class="{ 'is-active': position.id === submitPositionId }"
                @click="submitPositionId = position.id"
              >
                {{ position.name }}
              </button>
            </div>
          </div>

          <template v-if="confirmPosition">
            <p>
              岗位 <strong>{{ confirmPosition.name }}</strong>，共
              <strong>{{ confirmItems.length }}</strong> 项、合计
              <strong>{{ formatQty(confirmQuantity) }}</strong>。
            </p>
            <p class="po-dialog-note">
              提交后会同时写入今天的盘点：这个岗位下 {{ confirmStockRows.length }} 个需盘点商品，
              没填库存的按 0 记录；今天再提交会覆盖上一次盘点。
            </p>
            <p v-if="confirmMissingStock" class="po-dialog-warn">
              有 {{ confirmMissingStock }} 个需盘商品还没填库存，提交后按 0 记。
            </p>

            <div class="po-preview">
              <div class="po-preview-head">
                <strong>下单预览</strong>
                <span>共 {{ confirmItems.length }} 项</span>
              </div>
              <div class="po-preview-scroll">
                <div v-for="group in previewOrderedGroups" :key="'ordered-' + group.supplierName">
                  <div class="po-preview-supplier">
                    {{ group.supplierName }}<span>{{ group.items.length }} 项</span>
                  </div>
                  <div v-for="row in group.items" :key="row.id" class="po-preview-item">
                    <div class="po-preview-name">
                      {{ row.name }}
                      <small v-if="row.specification">{{ row.specification }}</small>
                    </div>
                    <div class="po-preview-meta">
                      <span v-if="previewStock(row)" class="po-preview-stock">{{ previewStock(row) }}</span>
                      <strong class="po-preview-qty">{{ previewQuantity(row) }} {{ row.unit }}</strong>
                    </div>
                  </div>
                </div>

                <template v-if="previewNotOrderedGroups.length">
                  <div class="po-preview-divider">未下单</div>
                  <div v-for="group in previewNotOrderedGroups" :key="'not-' + group.supplierName">
                    <div class="po-preview-supplier is-muted">
                      {{ group.supplierName }}<span>{{ group.items.length }} 项</span>
                    </div>
                    <div v-for="row in group.items" :key="row.id" class="po-preview-item is-muted">
                      <div class="po-preview-name">
                        {{ row.name }}
                        <small v-if="row.specification">{{ row.specification }}</small>
                      </div>
                      <div class="po-preview-meta">
                        <span v-if="previewStock(row)" class="po-preview-stock">{{ previewStock(row) }}</span>
                        <strong class="po-preview-qty is-zero">- {{ row.unit }}</strong>
                      </div>
                    </div>
                  </div>
                </template>

                <p
                  v-if="!previewOrderedGroups.length && !previewNotOrderedGroups.length"
                  class="po-preview-empty"
                >
                  这个岗位下还没有商品。
                </p>
              </div>
            </div>
          </template>
          <p v-else class="po-dialog-note">先选岗位，才会计算要提交哪些商品和盘点。</p>

          <p v-if="dialogError" class="po-dialog-error" role="alert">{{ dialogError }}</p>

          <div class="po-dialog-actions">
            <button
              type="button"
              class="po-btn po-btn-ghost"
              :disabled="submitting"
              @click="confirmOpen = false"
            >
              再看看
            </button>
            <button
              type="button"
              class="po-btn po-btn-primary"
              :disabled="submitting || !confirmPosition || !confirmItems.length"
              @click="submitOrder"
            >
              {{ submitting ? '提交中…' : '确认提交' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.po-page {
  width: 100%;
  min-width: 0;
  max-width: 980px;
  margin: 0 auto;
  padding: 10px 12px 120px;
  color: var(--ink);
  font-size: var(--fs-14);
  line-height: 1.45;
}
/* 小程序那套「日期 + 农历 + 次日标签 + 三天天气」的头图，换成工作台的纸色和橙色。 */
.po-hero {
  position: relative;
  margin: 2px 0 12px;
  padding: 16px;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 18px;
  background: linear-gradient(135deg, var(--surface-1), var(--wb-tint-08));
  box-shadow: var(--paper-shadow-sm);
}
.po-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.po-date-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.po-date-block {
  min-width: 0;
}
.po-date {
  display: block;
  font-size: var(--fs-24);
  line-height: 1.15;
}
.po-date em {
  font-style: normal;
  font-size: var(--fs-14);
  font-weight: 500;
  opacity: 0.62;
}
.po-lunar {
  display: block;
  margin-top: 4px;
  font-size: var(--fs-12);
  opacity: 0.62;
}
.po-factor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  flex: 0 1 auto;
}
.po-factor-pill {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-1);
  font-size: var(--fs-12);
  font-weight: 600;
}
.po-factor-tag {
  background: var(--wb-tint-08);
}
.po-days {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-12);
}
.po-days-select {
  min-height: 44px;
  padding: 0 8px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-1);
  color: var(--ink);
  font-size: var(--fs-13);
  font-weight: 600;
}
.po-weather-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}
.po-weather-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
}
.po-weather-card.is-placeholder {
  opacity: 0.62;
}
.po-weather-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px;
}
.po-weather-label {
  font-size: var(--fs-12);
  font-weight: 600;
}
.po-weather-date {
  font-size: var(--fs-11);
  opacity: 0.6;
  white-space: nowrap;
}
.po-weather-temp {
  font-size: var(--fs-16);
  line-height: 1.2;
  font-weight: 700;
}
.po-weather-text {
  font-size: var(--fs-13);
  font-weight: 500;
}
.po-weather-meta {
  margin-top: 2px;
  font-size: var(--fs-11);
  line-height: 1.5;
  opacity: 0.64;
}
.po-weather-time {
  margin: 8px 0 0;
  text-align: right;
  font-size: var(--fs-11);
  opacity: 0.58;
}
.po-context {
  margin: -2px 0 12px;
  padding: 0 4px;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.7;
}
.po-position-card {
  padding: 12px;
}
.po-position-card .po-card-head {
  align-items: flex-start;
}
.po-position-card .po-hint {
  margin: 2px 0 0;
  font-size: var(--fs-11);
}
.po-position-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 5px;
}
.po-position-button {
  min-height: 44px;
  padding: 5px 2px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-12);
  line-height: 1.2;
  cursor: pointer;
}
.po-position-button.is-active {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.po-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0 12px;
}
.po-head h1 {
  margin: 0;
  font-size: var(--fs-24);
  line-height: 1.25;
}
.po-sub {
  margin: 3px 0 0;
  color: var(--ink);
  font-size: var(--fs-13);
  opacity: 0.62;
}
.po-pill {
  flex: 0 0 auto;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--wb-tint-14);
  font-size: var(--fs-12);
  font-weight: 600;
}
.po-banner {
  margin: 0 0 10px;
  padding: 9px 12px;
  border: 1px solid var(--danger-bright);
  border-radius: 10px;
  background: var(--wb-danger-a10);
  font-size: var(--fs-13);
}
.po-banner-soft {
  border-color: var(--line-strong);
  background: var(--surface-1);
}
.po-card {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.po-supplier {
  margin: 0 0 14px;
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}
.po-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.po-card h2,
.po-supplier h2 {
  margin: 0;
  font-size: var(--fs-14);
  font-weight: 700;
}
.po-hint {
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.6;
}
.po-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.po-chip {
  min-height: 44px;
  padding: 7px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
  cursor: pointer;
}
.po-chip.is-active {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.po-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.po-search {
  flex: 1 1 180px;
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
}
.po-supplier-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--action);
  border-radius: 10px;
  background: var(--wb-tint-08);
}
.po-supplier-head h2 {
  font-size: var(--fs-14);
}
.po-supplier-head span {
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.55;
}
.po-product {
  margin-bottom: 8px;
  padding: 14px 14px 16px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.po-product:last-child {
  margin-bottom: 0;
}
.po-product-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.po-product-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.po-product-title strong {
  font-size: var(--fs-20);
}
.po-remark {
  margin: 8px 0 0;
  padding: 6px 8px;
  border-left: 2px solid var(--action);
  border-radius: 0 8px 8px 0;
  background: var(--wb-tint-08);
  color: var(--ink);
  font-size: var(--fs-12);
  line-height: 1.5;
}
.po-remark em {
  margin-right: 6px;
  font-style: normal;
  opacity: 0.6;
}
.po-code {
  color: var(--ink);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.4px;
  opacity: 0.45;
}
.po-badge {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--wb-warn-a10);
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 1;
}
.po-badge-soft {
  background: var(--wb-tint-14);
}
.po-flag {
  display: flex;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  font-size: var(--fs-12);
  font-weight: 700;
  line-height: 1;
}
.po-flag span {
  padding: 6px 8px;
}
.po-flag span:first-child {
  background: var(--action);
  color: var(--action-ink);
}
.po-flag span:last-child {
  border-left: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
}
.po-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}
.po-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-2);
  font-size: var(--fs-12);
}
.po-pill em {
  color: var(--ink);
  font-style: normal;
  opacity: 0.5;
}
.po-pill strong {
  font-weight: 600;
}
.po-product-body {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 8px 10px;
  margin-top: 10px;
  align-items: stretch;
}
.po-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 1px;
  min-width: 0;
  min-height: 60px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
  font-size: var(--fs-11);
  line-height: 1.25;
}
.po-info-stock {
  border-color: var(--wb-tint-45);
  background: var(--wb-tint-08);
}
.po-info-label {
  color: var(--ink);
  opacity: 0.58;
}
.po-info strong {
  overflow: hidden;
  color: var(--ink);
  font-size: var(--fs-14);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.po-required {
  margin-left: 1px;
  color: var(--danger-bright);
  font-style: normal;
}
.po-ref-warn {
  color: var(--danger-bright);
  opacity: 1;
}
.po-stepper-row {
  display: grid;
  grid-template-columns: 44px 44px minmax(0, 1fr) 44px 44px;
  gap: 6px;
  align-items: stretch;
  max-width: 480px;
}
.po-step {
  min-height: 52px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  cursor: pointer;
}
.po-step-dec10 {
  border-color: var(--wb-danger-a45);
  background: var(--wb-danger-a16);
  color: var(--danger-bright);
}
.po-step-dec1 {
  border-color: var(--wb-warn-a45);
  background: var(--wb-warn-a16);
}
.po-step-inc10 {
  border-color: var(--wb-ok-a45);
  background: var(--wb-ok-a16);
}
.po-step-input {
  min-width: 0;
  min-height: 52px;
  padding: 7px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-16);
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.po-step-input.is-required-empty {
  border-color: var(--danger-bright);
  color: var(--danger-bright);
}
.po-step-input.is-required-empty::placeholder {
  color: var(--danger-bright);
  opacity: 1;
}
.po-btn {
  min-height: 44px;
  padding: 8px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  cursor: pointer;
}
.po-btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.po-btn-ghost {
  background: transparent;
}
.po-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.po-empty {
  margin: 0 0 12px;
  padding: 22px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  color: var(--ink);
  font-size: var(--fs-13);
  text-align: center;
  opacity: 0.7;
}
.po-sticky {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: var(--z-shell);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  max-width: 980px;
  margin: 0 auto;
  padding: 10px 14px calc(10px + var(--safe-b));
  border-top: 1px solid var(--line);
  background: var(--surface-1);
  box-shadow: 0 -8px 24px var(--wb-ink-a08);
}
.po-sticky-text {
  font-size: var(--fs-13);
}
.po-sticky-text strong {
  font-size: var(--fs-16);
}
.po-submit {
  min-height: 44px;
  padding: 9px 22px;
  font-size: var(--fs-14);
}
.po-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--wb-scrim-strong);
}
.po-dialog {
  width: min(560px, 100%);
  max-height: 88vh;
  overflow: auto;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
  font-size: var(--fs-14);
}
.po-dialog h2 {
  margin: 0 0 10px;
  font-size: var(--fs-20);
}
.po-dialog p {
  margin: 0 0 8px;
}
.po-dialog-label {
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.7;
}
.po-dialog-note {
  padding: 8px 10px;
  border-left: 3px solid var(--action);
  border-radius: 0 8px 8px 0;
  background: var(--wb-tint-08);
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.8;
}
.po-dialog-warn {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--wb-warn-a10);
  font-size: var(--fs-12);
}
.po-dialog-error {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
  font-size: var(--fs-12);
}
.po-preview {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
}
.po-preview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  font-size: var(--fs-14);
}
.po-preview-head span {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.55;
}
.po-preview-scroll {
  max-height: 34vh;
  overflow: auto;
  padding-right: 2px;
}
.po-preview-supplier {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin: 8px 0 2px;
  padding-left: 7px;
  border-left: 3px solid var(--action);
  color: var(--ink);
  font-size: var(--fs-12);
}
.po-preview-supplier span {
  font-size: var(--fs-11);
  opacity: 0.5;
}
.po-preview-supplier.is-muted {
  border-left-color: var(--line);
  opacity: 0.55;
}
.po-preview-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--line);
}
.po-preview-item:last-child {
  border-bottom: 0;
}
.po-preview-item.is-muted {
  opacity: 0.55;
}
.po-preview-name {
  min-width: 0;
  font-size: var(--fs-13);
}
.po-preview-name small {
  margin-left: 4px;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.55;
}
.po-preview-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  white-space: nowrap;
}
.po-preview-stock {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.6;
}
.po-preview-qty {
  font-size: var(--fs-13);
}
.po-preview-qty.is-zero {
  opacity: 0.6;
}
.po-preview-divider {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0 2px;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.55;
}
.po-preview-divider::before,
.po-preview-divider::after {
  content: '';
  flex: 1;
  border-top: 1px solid var(--line);
}
.po-preview-empty {
  margin: 8px 0 0;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.55;
}
.po-position-picker {
  margin: 0 0 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
}
.po-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
@media (max-width: 560px) {
  .po-page {
    padding-bottom: 132px;
  }
  .po-head h1 {
    font-size: var(--fs-20);
  }
  .po-hero {
    padding: 14px 12px 12px;
  }
  .po-date-row {
    gap: 8px;
  }
  .po-factor {
    gap: 6px;
  }
  .po-factor-pill {
    padding: 7px 8px;
    font-size: var(--fs-11);
  }
  .po-days {
    gap: 4px;
  }
  .po-days-select {
    padding: 0 6px;
    font-size: var(--fs-12);
  }
  .po-weather-grid {
    gap: 6px;
  }
  .po-weather-card {
    padding: 8px;
    gap: 2px;
  }
  .po-weather-temp {
    font-size: var(--fs-14);
  }
  .po-product {
    padding: 10px 10px 11px;
  }
  .po-product-title strong {
    font-size: var(--fs-16);
  }
  .po-product-body {
    /* 信息格随屏宽收窄，把余宽留给中间的数字输入框：360px 宽时输入框约 72px，
       不会再被四颗固定按钮挤成一条线。 */
    grid-template-columns: clamp(68px, 20vw, 88px) minmax(0, 1fr);
    gap: 7px 6px;
  }
  .po-info {
    min-height: 52px;
    padding: 4px 6px;
  }
  .po-stepper-row {
    /* 两侧按钮随屏宽略收，输入框吃剩余宽度；3px 间距比 6px 多留 12px 给输入框。 */
    grid-template-columns:
      repeat(2, clamp(34px, 10.5vw, 44px))
      minmax(0, 1fr)
      repeat(2, clamp(34px, 10.5vw, 44px));
    gap: 3px;
  }
  .po-step {
    min-height: 48px;
    min-width: 0;
    font-size: var(--fs-12);
  }
  .po-step-input {
    min-height: 48px;
    font-size: var(--fs-14);
  }
  .po-sticky-text {
    font-size: var(--fs-12);
  }
  .po-submit {
    padding: 9px 14px;
  }
}
</style>
