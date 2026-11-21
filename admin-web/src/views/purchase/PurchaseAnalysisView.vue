<script setup>
/**
 * 厨房采购 · 采购分析（管理端）。
 *
 * 对齐小程序采购分析的三段式：概览 / 异常预警 / 商品钻取。口径沿用后端：
 * 取消单不计、下单人按员工 ID、无单天数只算非长周期商品。
 *
 * 接口（api/purchase.py，字段以 services/purchase/service.py 为准）：
 *  - GET  /api/purchase/analysis/overview                当日概览 + 近 7 日趋势
 *  - GET  /api/purchase/analysis/anomalies               四类异常
 *  - GET  /api/purchase/analysis/products/{id}/history   单品时间线
 *  - GET  /api/purchase/products?include_inactive=false  商品选择
 *  - PUT  /api/purchase/stock                            记一次盘点
 *  - POST /api/purchase/analysis/export + 轮询 + /download
 *
 * 为什么 tab 写进 route.query：刷新与分享链接还能停在同一个分段；
 * 页面本体由 WorkbenchLayout 包着，这里不再套外壳。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/client.js'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { formatMonthDay, formatQty, ratioText, severityText, todayText } from './purchaseUi.js'
import PurchaseNav from './PurchaseNav.vue'

const route = useRoute()
const router = useRouter()
const today = todayText()

// ── 分段 ────────────────────────────────────────────────────────────────────
const TAB_KEYS = ['overview', 'anomalies', 'drill']
const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'anomalies', label: '异常预警' },
  { key: 'drill', label: '商品钻取' },
]
function normalizeTab(raw) {
  const value = String((Array.isArray(raw) ? raw[0] : raw) || '')
  return TAB_KEYS.includes(value) ? value : 'overview'
}
const tab = ref(normalizeTab(route.query.tab))

function switchTab(next) {
  if (!TAB_KEYS.includes(next) || next === tab.value) return
  tab.value = next
  const query = { ...route.query }
  if (next === 'overview') delete query.tab
  else query.tab = next
  router.replace({ query }).catch(() => {})
}

watch(() => route.query.tab, (value) => {
  const next = normalizeTab(value)
  if (next !== tab.value) tab.value = next
})

// ── 日期 ────────────────────────────────────────────────────────────────────
const day = ref(today)

watch(day, (value) => {
  if (!value) {
    day.value = today
    return
  }
  if (value > today) {
    day.value = today
    return
  }
  loadOverview()
  loadAnomalies()
  if (tab.value === 'drill' && selectedProductId.value) loadHistory()
})

function goToday() {
  if (day.value === today) refresh()
  else day.value = today
}

function pickTrendDay(value) {
  if (!value || value === day.value || value > today) return
  day.value = value
}

// ── 异常参数（localStorage 持久化）──────────────────────────────────────────
const PARAMS_KEY = 'luyun.purchase.analysis.params.v1'
const DEFAULT_PARAMS = Object.freeze({
  no_order_days: 3,
  over_ratio: 1.5,
  under_ratio: 0.5,
  history_days: 21,
})
const params = reactive({ ...DEFAULT_PARAMS })
const draft = reactive({ ...DEFAULT_PARAMS })
const paramsOpen = ref(false)

function clampNumber(value, min, max, fallback) {
  const number = Number(value)
  if (!Number.isFinite(number)) return fallback
  return Math.min(max, Math.max(min, number))
}
function round2(value) {
  return Math.round(value * 100) / 100
}
function fillParams(source) {
  draft.no_order_days = Math.round(clampNumber(source && source.no_order_days, 1, 90, DEFAULT_PARAMS.no_order_days))
  draft.over_ratio = round2(clampNumber(source && source.over_ratio, 0.1, 20, DEFAULT_PARAMS.over_ratio))
  draft.under_ratio = round2(clampNumber(source && source.under_ratio, 0.05, 9.9, DEFAULT_PARAMS.under_ratio))
  draft.history_days = Math.round(clampNumber(source && source.history_days, 7, 120, DEFAULT_PARAMS.history_days))
}
function readParams() {
  let saved = null
  try {
    const raw = window.localStorage.getItem(PARAMS_KEY)
    if (raw) saved = JSON.parse(raw)
  } catch (_err) {
    saved = null
  }
  fillParams(saved || DEFAULT_PARAMS)
  Object.assign(params, draft)
}
function saveParams() {
  try {
    window.localStorage.setItem(PARAMS_KEY, JSON.stringify({ ...params }))
  } catch (_err) {
    // 隐私模式下写不进去就算了，页面照常按当前值查
  }
}
function applyParams() {
  fillParams(draft)
  Object.assign(params, draft)
  saveParams()
  paramsOpen.value = false
  loadAnomalies()
}
function resetParams() {
  fillParams(DEFAULT_PARAMS)
  Object.assign(params, DEFAULT_PARAMS)
  saveParams()
  loadAnomalies()
}

const paramsSummary = computed(() => '连续 ' + params.no_order_days + ' 天无单 · 超量 ' + params.over_ratio + ' 倍 · 低量 ' + params.under_ratio + ' 倍 · 回看 ' + params.history_days + ' 天')

// ── 概览 ────────────────────────────────────────────────────────────────────
const overview = ref(null)
const overviewLoading = ref(false)
const overviewError = ref('')
let overviewSeq = 0

async function loadOverview() {
  const seq = ++overviewSeq
  overviewLoading.value = true
  overviewError.value = ''
  try {
    const data = await api.get('/api/purchase/analysis/overview', { day: day.value || today })
    if (seq !== overviewSeq) return
    overview.value = data
  } catch (err) {
    if (seq !== overviewSeq) return
    overview.value = null
    overviewError.value = err.message || '概览没加载出来'
  } finally {
    if (seq === overviewSeq) overviewLoading.value = false
  }
}

const yesterdayLabel = computed(() => (overview.value && overview.value.compare_mode === 'same_time' ? '较昨日同时段' : '较昨日整日'))
const orderDelta = computed(() => {
  if (!overview.value) return 0
  return (Number(overview.value.order_count) || 0) - (Number(overview.value.yesterday_order_count) || 0)
})
const deltaText = computed(() => (orderDelta.value > 0 ? '+' : '') + formatQty(orderDelta.value))
const deltaClass = computed(() => {
  if (orderDelta.value > 0) return 'is-up'
  if (orderDelta.value < 0) return 'is-down'
  return 'is-flat'
})
const deltaNote = computed(() => {
  const data = overview.value
  if (!data) return ''
  const yesterday = Number(data.yesterday_order_count) || 0
  const total = Number(data.yesterday_total_order_count) || 0
  if (data.compare_mode === 'same_time') return '昨日同时段 ' + yesterday + ' 单（全天 ' + total + ' 单）'
  return '昨日 ' + yesterday + ' 单'
})
const windowText = computed(() => {
  const win = overview.value && overview.value.window
  return win ? formatMonthDay(win.start) + ' ~ ' + formatMonthDay(win.end) : ''
})

// ── 近 7 日趋势（按订单数）──────────────────────────────────────────────────
const trendRows = computed(() => ((overview.value && overview.value.trend) || []).slice(-7))
const trendMax = computed(() => Math.max(1, ...trendRows.value.map((row) => Number(row.order_count) || 0)))
const trendTotal = computed(() => trendRows.value.reduce((sum, row) => sum + (Number(row.order_count) || 0), 0))
const trendPeak = computed(() => trendRows.value.reduce((max, row) => Math.max(max, Number(row.order_count) || 0), 0))
const trendAvg = computed(() => (trendRows.value.length ? trendTotal.value / trendRows.value.length : 0))
const trendAvgText = computed(() => formatQty(trendAvg.value))
const trendAllZero = computed(() => trendRows.value.length > 0 && trendRows.value.every((row) => !(Number(row.order_count) > 0)))

function barHeight(row) {
  const value = Number(row.order_count) || 0
  if (!(value > 0)) return 0
  return Math.max(3, (value / trendMax.value) * 100)
}
function barTop(row) {
  return 100 - barHeight(row)
}

// ── 异常 ────────────────────────────────────────────────────────────────────
const ANOMALY_TABS = [
  { key: 'no_order', label: '连续没下单' },
  { key: 'over', label: '采购超量' },
  { key: 'under', label: '采购偏低' },
  { key: 'zero_stock', label: '零库存' },
]
const GROUP_META = {
  no_order: {
    title: '连续没下单',
    hint: '超过设定天数没下单',
    empty: '没有长时间没下单的商品。',
  },
  over: {
    title: '采购超量',
    hint: '当天采购量偏高',
    empty: '当天没有明显超量的商品。',
  },
  under: {
    title: '采购偏低',
    hint: '当天采购量偏低',
    empty: '当天没有明显偏低的商品。',
  },
  zero_stock: {
    title: '零库存',
    hint: '最近一次盘点为 0',
    empty: '没有零库存的需盘商品。',
  },
}
const anomalies = ref(null)
const anomaliesLoading = ref(false)
const anomaliesError = ref('')
const activeGroup = ref('no_order')
const keyword = ref('')
const sortBy = ref('severity')
const stockNotice = ref('')
let anomaliesSeq = 0
let stockNoticeTimer = 0

async function loadAnomalies() {
  const seq = ++anomaliesSeq
  anomaliesLoading.value = true
  anomaliesError.value = ''
  try {
    const data = await api.get('/api/purchase/analysis/anomalies', {
      day: day.value || today,
      no_order_days: params.no_order_days,
      over_ratio: params.over_ratio,
      under_ratio: params.under_ratio,
      history_days: params.history_days,
    })
    if (seq !== anomaliesSeq) return
    anomalies.value = data
  } catch (err) {
    if (seq !== anomaliesSeq) return
    anomalies.value = null
    anomaliesError.value = err.message || '异常清单没加载出来'
  } finally {
    if (seq === anomaliesSeq) anomaliesLoading.value = false
  }
}

const groupCounts = computed(() => {
  const data = anomalies.value || {}
  return {
    no_order: (data.no_order || []).length,
    over: (data.over || []).length,
    under: (data.under || []).length,
    zero_stock: (data.zero_stock || []).length,
  }
})
const activeMeta = computed(() => GROUP_META[activeGroup.value] || GROUP_META.no_order)
const groupRows = computed(() => {
  const data = anomalies.value
  return data && Array.isArray(data[activeGroup.value]) ? data[activeGroup.value] : []
})
const keywordText = computed(() => keyword.value.trim().toLowerCase())

const SEVERITY_RANK = { high: 3, normal: 2, low: 1 }

function compareQuantity(a, b) {
  if (activeGroup.value === 'no_order') return (Number(b.days) || 0) - (Number(a.days) || 0)
  if (activeGroup.value === 'zero_stock') return (Number(a.current_stock) || 0) - (Number(b.current_stock) || 0)
  if (activeGroup.value === 'under') return (Number(a.quantity) || 0) - (Number(b.quantity) || 0)
  return (Number(b.quantity) || 0) - (Number(a.quantity) || 0)
}

const filteredRows = computed(() => {
  const query = keywordText.value
  const rows = groupRows.value
  const matched = query
    ? rows.filter((row) => [row.name, row.product_code, row.supplier_name]
      .some((value) => String(value || '').toLowerCase().includes(query)))
    : rows.slice()
  const indexed = matched.map((row, index) => ({ row, index }))
  indexed.sort((left, right) => {
    const a = left.row
    const b = right.row
    let result = 0
    if (sortBy.value === 'quantity') {
      result = compareQuantity(a, b)
    } else {
      result = (SEVERITY_RANK[b.severity] || 0) - (SEVERITY_RANK[a.severity] || 0)
    }
    return result || left.index - right.index
  })
  return indexed.map((item) => item.row)
})

function severityClass(value) {
  if (value === 'high') return 'is-high'
  if (value === 'normal') return 'is-normal'
  return 'is-low'
}
function comparePercent(row, key) {
  const max = Math.max(Number(row.quantity) || 0, Number(row.reference) || 0, 1)
  const value = Number(row[key]) || 0
  return value > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 0
}
function selectGroup(key) {
  if (!GROUP_META[key]) return
  activeGroup.value = key
  switchTab('anomalies')
}

// ── 零库存弹窗：改库存 ──────────────────────────────────────────────────────
const stockOpen = ref(false)
const stockRow = ref(null)
const stockValue = ref('')
const stockSaving = ref(false)
const stockError = ref('')
const stockInput = ref(null)

function openStockDialog(row) {
  stockRow.value = row
  stockValue.value = row && row.current_stock !== null && row.current_stock !== undefined ? String(row.current_stock) : '0'
  stockError.value = ''
  stockOpen.value = true
  nextTick(() => {
    if (stockInput.value && stockInput.value.focus) stockInput.value.focus()
  })
}
function closeStockDialog() {
  if (stockSaving.value) return
  stockOpen.value = false
  stockRow.value = null
}
function nudgeStock(delta) {
  const current = Number(stockValue.value)
  const base = Number.isFinite(current) ? current : 0
  stockValue.value = String(Math.max(0, round2(base + delta)))
}
async function saveStock() {
  const row = stockRow.value
  if (!row || stockSaving.value) return
  const value = Number(stockValue.value)
  if (stockValue.value === '' || !Number.isFinite(value) || value < 0) {
    stockError.value = '当前库存填一个不小于 0 的数'
    return
  }
  stockSaving.value = true
  stockError.value = ''
  try {
    await api.put('/api/purchase/stock', {
      product_id: row.product_id,
      count_date: today,
      current_stock: value,
    })
    stockOpen.value = false
    stockRow.value = null
    stockNotice.value = row.name + ' 的库存已更新为 ' + formatQty(value) + (row.unit || '')
    window.clearTimeout(stockNoticeTimer)
    stockNoticeTimer = window.setTimeout(() => {
      stockNotice.value = ''
    }, 4000)
    loadAnomalies()
    if (selectedProductId.value === row.product_id) loadHistory()
  } catch (err) {
    stockError.value = err.message || '库存没保存成功'
  } finally {
    stockSaving.value = false
  }
}

// ── 商品选择 ────────────────────────────────────────────────────────────────
const PRODUCTS_LIMIT = 50
const RECENT_KEY = 'luyun.purchase.analysis.recent.v1'
const products = ref([])
const productsLoading = ref(false)
const productsError = ref('')
const productsLoaded = ref(false)
const productQuery = ref('')
const supplierFilter = ref('')
const recentIds = ref([])
const selectedProductId = ref(0)
let productsSeq = 0

function readRecent() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.map((value) => Number(value)).filter((value) => Number.isInteger(value) && value > 0).slice(0, 6)
  } catch (_err) {
    return []
  }
}
function rememberProduct(id) {
  recentIds.value = [id, ...recentIds.value.filter((value) => value !== id)].slice(0, 6)
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(recentIds.value))
  } catch (_err) {
    // 写不进去只影响「最近」这一排，不影响钻取
  }
}

async function loadProducts(force = false) {
  if (productsLoaded.value && !force) return
  const seq = ++productsSeq
  productsLoading.value = true
  productsError.value = ''
  try {
    const data = await api.get('/api/purchase/products', { include_inactive: 'false' })
    if (seq !== productsSeq) return
    products.value = data && Array.isArray(data.products) ? data.products : []
    productsLoaded.value = true
    if (selectedProductId.value && !products.value.some((item) => Number(item.id) === selectedProductId.value)) {
      selectedProductId.value = 0
      history.value = null
    }
  } catch (err) {
    if (seq !== productsSeq) return
    products.value = []
    productsError.value = err.message || '商品列表没加载出来'
  } finally {
    if (seq === productsSeq) productsLoading.value = false
  }
}

const suppliers = computed(() => {
  const names = new Set()
  for (const item of products.value) {
    const name = String(item.supplier_name || '').trim()
    if (name) names.add(name)
  }
  return [...names].sort((a, b) => a.localeCompare(b, 'zh-Hans'))
})

const filteredProducts = computed(() => {
  const query = productQuery.value.trim().toLowerCase()
  return products.value.filter((item) => {
    if (supplierFilter.value && item.supplier_name !== supplierFilter.value) return false
    if (!query) return true
    return [item.name, item.product_code, item.supplier_name]
      .some((value) => String(value || '').toLowerCase().includes(query))
  })
})
const visibleProducts = computed(() => filteredProducts.value.slice(0, PRODUCTS_LIMIT))
const selectedProduct = computed(() => products.value.find((item) => Number(item.id) === selectedProductId.value) || null)
const product = computed(() => (history.value && history.value.product) || selectedProduct.value || null)

const recentProducts = computed(() => {
  const byId = new Map(products.value.map((item) => [Number(item.id), item]))
  const picked = []
  for (const id of recentIds.value) {
    const item = byId.get(id)
    if (item && !picked.includes(item)) picked.push(item)
  }
  for (const item of filteredProducts.value) {
    if (picked.length >= 6) break
    if (!picked.includes(item)) picked.push(item)
  }
  return picked.slice(0, 6)
})

function selectProduct(item) {
  const id = Number(item && item.id)
  if (!id || id === selectedProductId.value) return
  selectedProductId.value = id
  rememberProduct(id)
  mgmtDate.value = today
  mgmtStock.value = ''
  mgmtError.value = ''
  mgmtNotice.value = ''
  showAllPurchases.value = false
  history.value = null
  loadHistory()
}

function ensureSelection() {
  if (selectedProductId.value) {
    if (!history.value) loadHistory()
    return
  }
  const first = recentProducts.value[0]
  if (!first) return
  selectedProductId.value = Number(first.id)
  rememberProduct(selectedProductId.value)
  loadHistory()
}

async function openDrill() {
  await loadProducts()
  if (!productsError.value) ensureSelection()
}

watch(tab, (value) => {
  if (value === 'drill') openDrill()
})

// ── 单品历史 ────────────────────────────────────────────────────────────────
const PERIODS = [7, 14, 30, 60, 90, 180]
const period = ref(30)
const history = ref(null)
const historyLoading = ref(false)
const historyError = ref('')
let historySeq = 0

async function loadHistory() {
  const id = selectedProductId.value
  if (!id) return
  const seq = ++historySeq
  historyLoading.value = true
  historyError.value = ''
  try {
    const data = await api.get('/api/purchase/analysis/products/' + id + '/history', {
      days: period.value,
      day: day.value || today,
    })
    if (seq !== historySeq) return
    history.value = data
    syncMgmtStock()
  } catch (err) {
    if (seq !== historySeq) return
    history.value = null
    historyError.value = err.message || '商品历史没加载出来'
  } finally {
    if (seq === historySeq) historyLoading.value = false
  }
}

function changePeriod(value) {
  if (period.value === value) return
  period.value = value
  showAllPurchases.value = false
  loadHistory()
}

const timeline = computed(() => {
  const rows = history.value && history.value.timeline
  return Array.isArray(rows) ? rows : []
})
const latestStock = computed(() => {
  const rows = timeline.value
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const value = rows[index].stock
    if (value !== null && value !== undefined) return Number(value)
  }
  return null
})
const latestStockText = computed(() => (latestStock.value === null ? '未盘' : formatQty(latestStock.value)))

function syncMgmtStock() {
  if (mgmtStock.value !== '') return
  mgmtStock.value = latestStock.value === null ? '' : String(latestStock.value)
}

const orderStep = computed(() => {
  const raw = (history.value && history.value.product && history.value.product.order_step) ?? (selectedProduct.value && selectedProduct.value.order_step)
  const step = Number(raw)
  return Number.isFinite(step) && step > 0 ? step : 1
})

// ── 趋势图：柱 = 采购量，线 = 库存 / 每日用量（各自纵轴）─────────────────────
const CHART = { width: 720, height: 240, top: 16, right: 14, bottom: 26, left: 14 }
const chartInnerWidth = CHART.width - CHART.left - CHART.right
const chartInnerHeight = CHART.height - CHART.top - CHART.bottom

const chartMaxPurchase = computed(() => Math.max(1, ...timeline.value.map((row) => Number(row.purchase) || 0)))
const chartMaxLine = computed(() => {
  let max = 1
  for (const row of timeline.value) {
    for (const key of ['stock', 'usage']) {
      const raw = row[key]
      if (raw === null || raw === undefined) continue
      const value = Number(raw)
      if (Number.isFinite(value)) max = Math.max(max, value)
    }
  }
  return max
})
const chartEmpty = computed(() => timeline.value.length > 0 && timeline.value.every((row) => {
  const purchase = Number(row.purchase) || 0
  return !(purchase > 0) && (row.stock === null || row.stock === undefined) && (row.usage === null || row.usage === undefined)
}))

function buildLinePath(rows, key, xFor, yFor) {
  let path = ''
  let open = false
  rows.forEach((row, index) => {
    const raw = row[key]
    if (raw === null || raw === undefined) {
      open = false
      return
    }
    const value = Number(raw)
    if (!Number.isFinite(value)) {
      open = false
      return
    }
    path += (open ? ' L ' : ' M ') + xFor(index).toFixed(1) + ' ' + yFor(value).toFixed(1)
    open = true
  })
  return path
}

const chartGeometry = computed(() => {
  const rows = timeline.value
  const count = rows.length
  if (!count) return { bars: [], stockPath: '', usagePath: '', grid: [] }
  const step = chartInnerWidth / count
  const barWidth = Math.max(2, Math.min(40, step * 0.46))
  const maxPurchase = chartMaxPurchase.value
  const maxLine = chartMaxLine.value
  const xFor = (index) => CHART.left + step * index + step / 2
  const yPurchase = (value) => CHART.top + chartInnerHeight - (value / maxPurchase) * chartInnerHeight
  const yLine = (value) => CHART.top + chartInnerHeight - (value / maxLine) * chartInnerHeight
  const bars = rows.map((row, index) => {
    const value = Number(row.purchase) || 0
    const height = value > 0 ? Math.max(2, (value / maxPurchase) * chartInnerHeight) : 0
    return {
      date: row.date,
      value,
      x: CHART.left + step * index + (step - barWidth) / 2,
      y: CHART.top + chartInnerHeight - height,
      width: barWidth,
      height,
    }
  })
  return {
    bars,
    stockPath: buildLinePath(rows, 'stock', xFor, yLine),
    usagePath: buildLinePath(rows, 'usage', xFor, yLine),
    grid: [CHART.top, CHART.top + chartInnerHeight / 2, CHART.top + chartInnerHeight],
  }
})

const chartAxis = computed(() => {
  const rows = timeline.value
  if (!rows.length) return []
  if (rows.length <= 3) return rows.map((row) => row.date)
  return [rows[0].date, rows[Math.floor((rows.length - 1) / 2)].date, rows[rows.length - 1].date]
})

// ── 库存管理（钻取页）──────────────────────────────────────────────────────
const mgmtDate = ref(today)
const mgmtStock = ref('')
const mgmtSaving = ref(false)
const mgmtError = ref('')
const mgmtNotice = ref('')
let mgmtNoticeTimer = 0

function nudgeMgmt(direction) {
  const current = Number(mgmtStock.value)
  const base = Number.isFinite(current) ? current : 0
  mgmtStock.value = String(Math.max(0, round2(base + orderStep.value * direction)))
}

async function saveMgmtStock() {
  if (!product.value) {
    mgmtError.value = '先选一个商品'
    return
  }
  if (!mgmtDate.value) {
    mgmtError.value = '选一个盘点日期'
    return
  }
  const value = Number(mgmtStock.value)
  if (mgmtStock.value === '' || !Number.isFinite(value) || value < 0) {
    mgmtError.value = '当前库存填一个不小于 0 的数'
    return
  }
  mgmtSaving.value = true
  mgmtError.value = ''
  mgmtNotice.value = ''
  try {
    await api.put('/api/purchase/stock', {
      product_id: Number(product.value.id),
      count_date: mgmtDate.value,
      current_stock: value,
    })
    await Promise.all([loadHistory(), loadAnomalies()])
    mgmtNotice.value = '已记下 ' + formatMonthDay(mgmtDate.value) + ' 的库存 ' + formatQty(value)
    window.clearTimeout(mgmtNoticeTimer)
    mgmtNoticeTimer = window.setTimeout(() => {
      mgmtNotice.value = ''
    }, 4000)
  } catch (err) {
    mgmtError.value = err.message || '库存没保存成功'
  } finally {
    mgmtSaving.value = false
  }
}

const stockPills = computed(() => timeline.value
  .filter((row) => row.stock !== null && row.stock !== undefined)
  .slice(-7)
  .reverse())

const purchaseRows = computed(() => timeline.value.filter((row) => Number(row.purchase) > 0).slice().reverse())
const showAllPurchases = ref(false)
const visiblePurchaseRows = computed(() => (showAllPurchases.value ? purchaseRows.value : purchaseRows.value.slice(0, 20)))

// ── 导出（job + 轮询 + 下载）───────────────────────────────────────────────
const exporting = ref(false)
const exportHint = ref('')
const exportFailed = ref(false)
let exportHintTimer = 0

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function exportAnalysis() {
  if (exporting.value) return
  exporting.value = true
  exportFailed.value = false
  exportHint.value = '正在生成 Excel…'
  try {
    const started = await api.post('/api/purchase/analysis/export', {}, {
      day: day.value || today,
      no_order_days: params.no_order_days,
      over_ratio: params.over_ratio,
      under_ratio: params.under_ratio,
      history_days: params.history_days,
    })
    const jobId = started && started.job_id
    if (!jobId) throw new Error('导出任务没拿到编号')
    let state = null
    for (let attempt = 0; attempt < 120; attempt += 1) {
      state = await api.get('/api/purchase/analysis/export/' + jobId)
      if (state && state.state === 'done') break
      if (state && state.state === 'failed') throw new Error(state.error || '导出失败')
      await sleep(500)
    }
    if (!state || state.state !== 'done') throw new Error('导出超时，请稍后再试')
    await api.download(
      '/api/purchase/analysis/export/' + jobId + '/download',
      'purchase-analysis-' + (day.value || today) + '.xlsx',
    )
    exportHint.value = '已导出'
    window.clearTimeout(exportHintTimer)
    exportHintTimer = window.setTimeout(() => {
      if (exportHint.value === '已导出') exportHint.value = ''
    }, 3000)
  } catch (err) {
    exportFailed.value = true
    exportHint.value = err.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

// ── 刷新 ────────────────────────────────────────────────────────────────────
const refreshing = ref(false)

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const jobs = [loadOverview(), loadAnomalies()]
    if (tab.value === 'drill' && selectedProductId.value) jobs.push(loadHistory())
    await Promise.all(jobs)
  } finally {
    refreshing.value = false
  }
}

// ── 弹窗键盘 / 生命周期 ────────────────────────────────────────────────────
function onKeydown(event) {
  if (event.key !== 'Escape') return
  if (stockOpen.value) closeStockDialog()
}
function clearTimers() {
  window.clearTimeout(stockNoticeTimer)
  window.clearTimeout(mgmtNoticeTimer)
  window.clearTimeout(exportHintTimer)
}

onMounted(() => {
  readParams()
  recentIds.value = readRecent()
  window.addEventListener('keydown', onKeydown)
  loadOverview()
  loadAnomalies()
  if (tab.value === 'drill') openDrill()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  clearTimers()
})
</script>

<template>
  <div class="pa-page">
    <PurchaseNav active="analysis" />

    <header class="pa-head">
      <h1 class="pa-title">采购分析</h1>
      <p class="pa-sub">看当天买了多少、哪些品没买 / 买多 / 买少，以及单个商品最近的采购与盘点。</p>
    </header>

    <div class="pa-toolbar">
      <div class="pa-toolbar-group">
        <LuyunDatePicker v-model="day" :max-date="today" aria-label="分析哪一天" />
      </div>
      <div class="pa-toolbar-group">
        <button type="button" class="pa-btn pa-btn-ghost" @click="goToday">今天</button>
        <button type="button" class="pa-btn pa-btn-ghost" :disabled="refreshing" @click="refresh">
          {{ refreshing ? '刷新中…' : '刷新' }}
        </button>
        <button type="button" class="pa-btn pa-btn-primary" :disabled="exporting || refreshing" @click="exportAnalysis">
          {{ exporting ? '生成中…' : '导出 Excel' }}
        </button>
      </div>
    </div>

    <p v-if="exportHint" class="pa-export-hint" :class="{ 'is-error': exportFailed }" role="status">{{ exportHint }}</p>

    <div class="pa-tabs" role="tablist" aria-label="采购分析分段">
      <button
        v-for="item in TABS"
        :key="item.key"
        type="button"
        role="tab"
        class="pa-tab"
        :class="{ 'is-active': tab === item.key }"
        :aria-selected="tab === item.key"
        @click="switchTab(item.key)"
      >
        {{ item.label }}
      </button>
    </div>

    <!-- 概览 -->
    <section v-if="tab === 'overview'" class="pa-panel" role="tabpanel" aria-label="概览">
      <p v-if="overviewError" class="pa-banner" role="alert">
        {{ overviewError }}
        <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadOverview">重试</button>
      </p>
      <div v-else-if="overviewLoading && !overview" class="pa-skeleton">
        <div class="pa-skeleton-card"></div>
        <div class="pa-skeleton-card"></div>
        <div class="pa-skeleton-card"></div>
        <div class="pa-skeleton-card"></div>
      </div>
      <template v-else-if="overview">
        <section class="pa-stats" aria-label="当日概览">
          <div class="pa-stat">
            <span class="pa-stat-label">采购单</span>
            <strong class="pa-stat-value">{{ overview.order_count }}<span class="pa-stat-unit">张</span></strong>
            <span class="pa-delta" :class="deltaClass">{{ deltaText }} 单 · {{ yesterdayLabel }}</span>
            <span class="pa-stat-note">{{ deltaNote }}</span>
          </div>
          <div class="pa-stat">
            <span class="pa-stat-label">采购量</span>
            <strong class="pa-stat-value">{{ formatQty(overview.item_count) }}</strong>
            <span class="pa-stat-note">当日数量合计</span>
          </div>
          <div class="pa-stat">
            <span class="pa-stat-label">商品数</span>
            <strong class="pa-stat-value">{{ overview.sku_count }}<span class="pa-stat-unit">个 SKU</span></strong>
            <span class="pa-stat-note">当天出现过的商品</span>
          </div>
          <div class="pa-stat">
            <span class="pa-stat-label">下单人</span>
            <strong class="pa-stat-value">{{ overview.submitter_count }}<span class="pa-stat-unit">人</span></strong>
            <span class="pa-stat-note">按员工 ID 去重</span>
          </div>
        </section>

        <section class="pa-card">
          <header class="pa-card-head">
            <div>
              <h2>近 7 日订单趋势</h2>
              <p class="pa-hint">按订单数（不是采购量）；点一根柱子可以切到那天。</p>
            </div>
            <span v-if="windowText" class="pa-window">{{ windowText }}</span>
          </header>

          <p class="pa-trend-summary">
            <span>峰值 <strong>{{ trendPeak }}</strong> 单</span>
            <span>合计 <strong>{{ trendTotal }}</strong> 单</span>
            <span>日均 <strong>{{ trendAvgText }}</strong> 单</span>
            <span>{{ yesterdayLabel }} <strong>{{ overview.yesterday_order_count }}</strong> 单</span>
          </p>

          <div v-if="trendAllZero" class="pa-empty">
            <p>近 7 日还没有采购单。</p>
          </div>
          <div v-else class="pa-chart" role="img" :aria-label="'近 7 日订单趋势，峰值 ' + trendPeak + ' 单，合计 ' + trendTotal + ' 单'">
            <button
              v-for="row in trendRows"
              :key="row.date"
              type="button"
              class="pa-bar-item"
              :class="{ 'is-today': row.date === day }"
              :aria-label="row.date + ' ' + row.order_count + ' 单'"
              @click="pickTrendDay(row.date)"
            >
              <span class="pa-bar-value">{{ row.order_count }}</span>
              <span class="pa-bar-track">
                <svg class="pa-bar" viewBox="0 0 10 100" preserveAspectRatio="none" aria-hidden="true">
                  <rect class="pa-bar-fill" x="0" :y="barTop(row)" width="10" :height="barHeight(row)" />
                </svg>
              </span>
              <span class="pa-bar-date">{{ formatMonthDay(row.date) }}</span>
              <span class="pa-bar-orders">{{ row.order_count }} 单</span>
            </button>
          </div>
        </section>

        <section class="pa-card">
          <header class="pa-card-head">
            <div>
              <h2>异常商品</h2>
              <p class="pa-hint">{{ paramsSummary }}</p>
            </div>
            <button type="button" class="pa-btn pa-btn-ghost" @click="switchTab('anomalies')">看全部</button>
          </header>

          <p v-if="overview.order_count === 0" class="pa-inline-empty">今天还没有采购单。</p>
          <p v-if="anomaliesError" class="pa-hint">异常数量没取到：{{ anomaliesError }}</p>

          <div class="pa-quad">
            <button
              v-for="item in ANOMALY_TABS"
              :key="item.key"
              type="button"
              class="pa-quad-cell"
              @click="selectGroup(item.key)"
            >
              <span class="pa-quad-head">
                <span class="pa-quad-title">{{ item.label }}</span>
                <span class="pa-quad-count">{{ anomalies ? groupCounts[item.key] : '—' }}</span>
              </span>
              <span class="pa-quad-hint">{{ GROUP_META[item.key].hint }}</span>
            </button>
          </div>
        </section>
      </template>
      <div v-else-if="!overviewLoading" class="pa-empty">
        <p>这天还没有采购数据。</p>
      </div>
    </section>

    <!-- 异常预警 -->
    <section v-else-if="tab === 'anomalies'" class="pa-panel" role="tabpanel" aria-label="异常预警">
      <section class="pa-card">
        <header class="pa-card-head">
          <div>
            <h2>{{ activeMeta.title }}</h2>
            <p class="pa-hint">{{ paramsSummary }}</p>
          </div>
          <div class="pa-card-actions">
            <span v-if="anomaliesLoading" class="pa-window">更新中…</span>
            <button type="button" class="pa-btn pa-btn-ghost" @click="paramsOpen = !paramsOpen">
              {{ paramsOpen ? '收起参数' : '调参数' }}
            </button>
          </div>
        </header>

        <div v-if="paramsOpen" class="pa-params">
          <label class="pa-field">
            <span>连续无单天数</span>
            <input v-model.number="draft.no_order_days" class="pa-input" type="number" min="1" max="90" step="1" />
          </label>
          <label class="pa-field">
            <span>超量倍数</span>
            <input v-model.number="draft.over_ratio" class="pa-input" type="number" min="0.1" max="20" step="0.1" />
          </label>
          <label class="pa-field">
            <span>低量倍数</span>
            <input v-model.number="draft.under_ratio" class="pa-input" type="number" min="0.05" max="9.9" step="0.05" />
          </label>
          <label class="pa-field">
            <span>历史窗口（天）</span>
            <input v-model.number="draft.history_days" class="pa-input" type="number" min="7" max="120" step="1" />
          </label>
          <div class="pa-params-actions">
            <button type="button" class="pa-btn pa-btn-ghost" @click="resetParams">恢复默认</button>
            <button type="button" class="pa-btn pa-btn-primary" @click="applyParams">应用</button>
          </div>
        </div>

        <div class="pa-anomaly-bar">
          <div class="pa-chips" role="group" aria-label="异常分类">
            <button
              v-for="item in ANOMALY_TABS"
              :key="item.key"
              type="button"
              class="pa-chip"
              :class="{ 'is-active': activeGroup === item.key }"
              :aria-pressed="activeGroup === item.key"
              @click="activeGroup = item.key"
            >
              {{ item.label }}
              <span class="pa-chip-count">{{ anomalies ? groupCounts[item.key] : '—' }}</span>
            </button>
          </div>
          <input
            v-model="keyword"
            type="search"
            class="pa-input pa-search"
            placeholder="搜商品名 / 编码 / 供应商"
            aria-label="搜索异常商品"
          />
          <select v-model="sortBy" class="pa-select" aria-label="排序方式">
            <option value="severity">按严重度</option>
            <option value="quantity">按数量</option>
          </select>
        </div>

        <p v-if="stockNotice" class="pa-notice" role="status">{{ stockNotice }}</p>

        <div v-if="anomaliesError" class="pa-banner" role="alert">
          {{ anomaliesError }}
          <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadAnomalies">重试</button>
        </div>
        <div v-else-if="anomaliesLoading && !anomalies" class="pa-skeleton">
          <div class="pa-skeleton-card"></div>
          <div class="pa-skeleton-card"></div>
          <div class="pa-skeleton-card"></div>
          <div class="pa-skeleton-card"></div>
        </div>
        <div v-else-if="!filteredRows.length" class="pa-empty">
          <p v-if="keyword">没有匹配「{{ keyword }}」的商品。</p>
          <p v-else>{{ activeMeta.empty }}</p>
          <p v-if="overview && overview.order_count === 0">今天还没有采购单。</p>
          <button v-if="keyword" type="button" class="pa-btn pa-btn-ghost" @click="keyword = ''">清空关键词</button>
        </div>
        <div v-else class="pa-anoms">
          <article v-for="row in filteredRows" :key="activeGroup + '-' + row.product_id" class="pa-anom">
            <header class="pa-anom-head">
              <div class="pa-anom-title">
                <strong>{{ row.name }}</strong>
                <span class="pa-anom-code">{{ row.product_code }}</span>
              </div>
              <span class="pa-sev" :class="severityClass(row.severity)">{{ severityText(row.severity) }}</span>
            </header>

            <p class="pa-anom-meta">
              <span v-if="row.specification">规格 {{ row.specification }}</span>
              <span v-if="row.supplier_name">供应商 {{ row.supplier_name }}</span>
              <span v-if="row.category_name">分类 {{ row.category_name }}</span>
            </p>

            <template v-if="activeGroup === 'over' || activeGroup === 'under'">
              <div class="pa-meter" :class="activeGroup === 'over' ? 'is-over' : 'is-under'">
                <div class="pa-meter-row">
                  <span class="pa-meter-label">参考</span>
                  <svg class="pa-meter-bar" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    <rect class="pa-meter-ref" x="0" y="0" :width="comparePercent(row, 'reference')" height="100" />
                  </svg>
                  <span class="pa-meter-num">{{ formatQty(row.reference) }}</span>
                </div>
                <div class="pa-meter-row">
                  <span class="pa-meter-label">今日</span>
                  <svg class="pa-meter-bar" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    <rect class="pa-meter-qty" x="0" y="0" :width="comparePercent(row, 'quantity')" height="100" />
                  </svg>
                  <span class="pa-meter-num">{{ formatQty(row.quantity) }}</span>
                </div>
              </div>
              <p class="pa-anom-detail">
                为参考的 {{ ratioText(row.ratio) }}（{{ formatQty(row.quantity) }} / {{ formatQty(row.reference) }} {{ row.unit || '' }}）
                <span v-if="row.submitters && row.submitters.length"> · 下单人 {{ row.submitters.join('、') }}</span>
              </p>
            </template>

            <p v-else-if="activeGroup === 'no_order'" class="pa-anom-detail">
              最后采购 {{ row.last_ordered ? formatMonthDay(row.last_ordered) : '从未' }} · 已 {{ row.days }} 天没下单
              <span v-if="row.last_quantity !== null && row.last_quantity !== undefined"> · 上次 {{ formatQty(row.last_quantity) }} {{ row.unit || '' }}</span>
            </p>

            <template v-else>
              <p class="pa-anom-detail">
                最近盘点 {{ formatQty(row.current_stock) }} {{ row.unit || '' }}
                · {{ row.count_date || '未记日期' }}
                <span v-if="row.counted_by"> · {{ row.counted_by }}</span>
              </p>
              <div class="pa-anom-actions">
                <button type="button" class="pa-btn pa-btn-primary" @click="openStockDialog(row)">改库存</button>
              </div>
            </template>
          </article>
        </div>
      </section>
    </section>

    <!-- 商品钻取 -->
    <section v-else class="pa-panel" role="tabpanel" aria-label="商品钻取">
      <div class="pa-drill">
        <aside class="pa-products-card" aria-label="选商品">
          <div class="pa-product-tools">
            <input v-model="productQuery" type="search" class="pa-input" placeholder="搜商品名 / 编码 / 供应商" aria-label="搜索商品" />
            <select v-model="supplierFilter" class="pa-select" aria-label="按供应商筛选">
              <option value="">全部供应商</option>
              <option v-for="name in suppliers" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>

          <div v-if="recentProducts.length" class="pa-recent">
            <span class="pa-recent-label">最近</span>
            <button
              v-for="item in recentProducts"
              :key="'recent-' + item.id"
              type="button"
              class="pa-recent-chip"
              :class="{ 'is-selected': Number(item.id) === selectedProductId }"
              @click="selectProduct(item)"
            >
              {{ item.name }}
            </button>
          </div>

          <div v-if="productsError" class="pa-banner" role="alert">
            {{ productsError }}
            <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadProducts(true)">重试</button>
          </div>
          <div v-else-if="productsLoading && !productsLoaded" class="pa-empty">
            <p>正在加载商品…</p>
          </div>
          <ul v-else class="pa-product-list">
            <li v-for="item in visibleProducts" :key="item.id">
              <button
                type="button"
                class="pa-product"
                :class="{ 'is-selected': Number(item.id) === selectedProductId }"
                :aria-pressed="Number(item.id) === selectedProductId"
                @click="selectProduct(item)"
              >
                <span class="pa-product-name">{{ item.name }}</span>
                <span class="pa-product-meta">{{ item.product_code }} · {{ item.supplier_name || '无供应商' }}</span>
              </button>
            </li>
            <li v-if="!visibleProducts.length" class="pa-empty">
              <p>没有匹配的商品。</p>
            </li>
          </ul>
          <p v-if="filteredProducts.length > PRODUCTS_LIMIT" class="pa-hint">
            共 {{ filteredProducts.length }} 个，先显示前 {{ PRODUCTS_LIMIT }} 个，搜关键词缩小范围。
          </p>
        </aside>

        <div class="pa-detail">
          <div v-if="!selectedProduct" class="pa-empty">
            <p>先在左边选一个商品。</p>
          </div>
          <template v-else>
            <div v-if="historyLoading && !history" class="pa-empty">
              <p>正在加载商品历史…</p>
            </div>
            <p v-else-if="historyError" class="pa-banner" role="alert">
              {{ historyError }}
              <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadHistory">重试</button>
            </p>
            <template v-else-if="history">
              <div class="pa-detail-toolbar">
                <div class="pa-periods" role="group" aria-label="统计周期">
                  <button
                    v-for="value in PERIODS"
                    :key="value"
                    type="button"
                    class="pa-period"
                    :class="{ 'is-active': period === value }"
                    :aria-pressed="period === value"
                    @click="changePeriod(value)"
                  >
                    {{ value }} 天
                  </button>
                </div>
                <span class="pa-window">{{ history.start }} ~ {{ history.end }}</span>
              </div>

              <section class="pa-card pa-product-head">
                <div class="pa-product-main">
                  <h2>{{ product.name }}</h2>
                  <p class="pa-hint">
                    {{ product.product_code }} · {{ product.supplier_name || '无供应商' }} · {{ product.category_name || '未分类' }}
                  </p>
                  <p v-if="product.specification" class="pa-hint">规格 {{ product.specification }} · 单位 {{ product.unit || '—' }}</p>
                </div>
                <div class="pa-product-side">
                  <span class="pa-pill">最近盘点 {{ latestStockText }}</span>
                  <span class="pa-pill">步长 {{ formatQty(orderStep) }}</span>
                  <span v-if="history.stats && history.stats.last_order_date" class="pa-pill">上次采购 {{ formatMonthDay(history.stats.last_order_date) }}</span>
                </div>
              </section>

              <section v-if="history.stats" class="pa-stats" aria-label="商品统计">
                <div class="pa-stat">
                  <span class="pa-stat-label">订单数</span>
                  <strong class="pa-stat-value">{{ history.stats.total_orders }}<span class="pa-stat-unit">单</span></strong>
                </div>
                <div class="pa-stat">
                  <span class="pa-stat-label">采购量</span>
                  <strong class="pa-stat-value">{{ formatQty(history.stats.total_quantity) }}</strong>
                </div>
                <div class="pa-stat">
                  <span class="pa-stat-label">日均量</span>
                  <strong class="pa-stat-value">{{ formatQty(history.stats.average_quantity) }}</strong>
                </div>
                <div class="pa-stat">
                  <span class="pa-stat-label">日均单</span>
                  <strong class="pa-stat-value">{{ formatQty(history.stats.order_frequency) }}<span class="pa-stat-unit">单/天</span></strong>
                </div>
              </section>
              <p v-if="history.stats" class="pa-hint pa-stats-note">
                有采购的天数 {{ history.stats.days_with_purchase }} 天
                <span v-if="history.stats.last_order_date"> · 上次采购 {{ history.stats.last_order_date }}</span>
              </p>

              <section class="pa-card">
                <header class="pa-card-head">
                  <div>
                    <h2>采购 / 库存 / 用量</h2>
                    <p class="pa-hint">柱是采购量；线是盘点库存与每日用量。库存为空的日期没盘点。</p>
                  </div>
                </header>
                <div v-if="!timeline.length" class="pa-empty">
                  <p>这个周期里没有记录。</p>
                </div>
                <div v-else-if="chartEmpty" class="pa-empty">
                  <p>这个周期里没有采购或盘点记录。</p>
                </div>
                <template v-else>
                  <p class="pa-hint">纵轴：柱 0–{{ formatQty(chartMaxPurchase) }}，线 0–{{ formatQty(chartMaxLine) }}</p>
                  <svg class="pa-line-chart" viewBox="0 0 720 240" preserveAspectRatio="none" role="img" aria-label="采购量、库存与每日用量趋势">
                    <line
                      v-for="(value, index) in chartGeometry.grid"
                      :key="'grid-' + index"
                      class="pa-grid-line"
                      x1="0"
                      :y1="value"
                      x2="720"
                      :y2="value"
                    />
                    <rect
                      v-for="bar in chartGeometry.bars"
                      :key="bar.date"
                      class="pa-chart-bar"
                      :x="bar.x"
                      :y="bar.y"
                      :width="bar.width"
                      :height="bar.height"
                    >
                      <title>{{ bar.date }} 采购 {{ formatQty(bar.value) }}</title>
                    </rect>
                    <path v-if="chartGeometry.stockPath" class="pa-chart-stock" :d="chartGeometry.stockPath" />
                    <path v-if="chartGeometry.usagePath" class="pa-chart-usage" :d="chartGeometry.usagePath" />
                  </svg>
                  <div class="pa-axis">
                    <span v-for="value in chartAxis" :key="'axis-' + value">{{ formatMonthDay(value) }}</span>
                  </div>
                  <p class="pa-legend">
                    <span class="pa-legend-item"><span class="pa-legend-dot"></span>采购量</span>
                    <span class="pa-legend-item"><span class="pa-legend-dot is-stock"></span>盘点库存</span>
                    <span class="pa-legend-item"><span class="pa-legend-dot is-usage"></span>每日用量（虚线）</span>
                  </p>
                </template>
              </section>

              <section class="pa-card">
                <header class="pa-card-head">
                  <div>
                    <h2>库存盘点</h2>
                    <p class="pa-hint">盘一次，之后就能算每日用量：昨天库存 + 昨天采购 − 今天库存。</p>
                  </div>
                </header>
                <div class="pa-stock-form">
                  <label class="pa-field">
                    <span>盘点日期</span>
                    <LuyunDatePicker v-model="mgmtDate" :max-date="today" aria-label="盘点日期" />
                  </label>
                  <label class="pa-field">
                    <span>当前库存（{{ product.unit || '单位' }}）</span>
                    <div class="pa-stock-input-row">
                      <button type="button" class="pa-step" aria-label="减少一个步长" @click="nudgeMgmt(-1)">−</button>
                      <input v-model="mgmtStock" class="pa-input" type="number" min="0" step="any" inputmode="decimal" placeholder="0" />
                      <button type="button" class="pa-step" aria-label="增加一个步长" @click="nudgeMgmt(1)">+</button>
                    </div>
                  </label>
                  <button type="button" class="pa-btn pa-btn-primary" :disabled="mgmtSaving" @click="saveMgmtStock">
                    {{ mgmtSaving ? '保存中…' : '保存库存' }}
                  </button>
                </div>
                <p class="pa-hint">加减按钮按商品步长 {{ formatQty(orderStep) }} 走；日期不能晚于今天。</p>
                <p v-if="mgmtError" class="pa-banner" role="alert">{{ mgmtError }}</p>
                <p v-if="mgmtNotice" class="pa-notice" role="status">{{ mgmtNotice }}</p>
                <div v-if="stockPills.length" class="pa-pills">
                  <span v-for="row in stockPills" :key="'stock-' + row.date" class="pa-pill">
                    {{ formatMonthDay(row.date) }} · {{ formatQty(row.stock) }}
                  </span>
                </div>
                <p v-else class="pa-hint">这个周期里还没有盘点记录。</p>
              </section>

              <section class="pa-card">
                <header class="pa-card-head">
                  <div>
                    <h2>采购明细</h2>
                    <p class="pa-hint">只列有采购的日期，按时间倒序；默认最近 20 天。</p>
                  </div>
                  <button
                    v-if="purchaseRows.length > 20"
                    type="button"
                    class="pa-btn pa-btn-ghost"
                    @click="showAllPurchases = !showAllPurchases"
                  >
                    {{ showAllPurchases ? '只看最近 20 天' : '展开全部 ' + purchaseRows.length + ' 天' }}
                  </button>
                </header>
                <div v-if="!purchaseRows.length" class="pa-empty">
                  <p>这个周期里没有采购记录。</p>
                </div>
                <div v-else class="pa-table-wrap">
                  <table class="pa-table">
                    <thead>
                      <tr>
                        <th scope="col">日期</th>
                        <th scope="col">采购量</th>
                        <th scope="col">库存</th>
                        <th scope="col">每日用量</th>
                        <th scope="col">盘点</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="row in visiblePurchaseRows" :key="'buy-' + row.date">
                        <td>{{ formatMonthDay(row.date) }}</td>
                        <td class="is-num">{{ formatQty(row.purchase) }}</td>
                        <td class="is-num">{{ row.stock === null || row.stock === undefined ? '未盘' : formatQty(row.stock) }}</td>
                        <td class="is-num">{{ row.usage === null || row.usage === undefined ? '—' : formatQty(row.usage) }}</td>
                        <td>{{ row.counted ? '已盘' : '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            </template>
          </template>
        </div>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="stockOpen" class="pa-scrim" @click.self="closeStockDialog">
        <div class="pa-dialog" role="dialog" aria-modal="true" aria-labelledby="pa-stock-title">
          <header class="pa-dialog-head">
            <div>
              <h2 id="pa-stock-title">改库存</h2>
              <p class="pa-dialog-sub">{{ stockRow && stockRow.name }} · {{ stockRow && stockRow.product_code }}</p>
            </div>
            <button type="button" class="pa-close" aria-label="关闭" @click="closeStockDialog">×</button>
          </header>
          <div class="pa-dialog-body">
            <p class="pa-hint">按今天的盘点填；保存后异常清单会刷新。</p>
            <label class="pa-field">
              <span>当前库存（{{ (stockRow && stockRow.unit) || '单位' }}）</span>
              <div class="pa-stock-input-row">
                <button type="button" class="pa-step" aria-label="减一" @click="nudgeStock(-1)">−</button>
                <input ref="stockInput" v-model="stockValue" class="pa-input" type="number" min="0" step="any" inputmode="decimal" />
                <button type="button" class="pa-step" aria-label="加一" @click="nudgeStock(1)">+</button>
              </div>
            </label>
            <p v-if="stockError" class="pa-banner" role="alert">{{ stockError }}</p>
            <div class="pa-dialog-actions">
              <button type="button" class="pa-btn pa-btn-ghost" @click="closeStockDialog">取消</button>
              <button type="button" class="pa-btn pa-btn-primary" :disabled="stockSaving" @click="saveStock">
                {{ stockSaving ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pa-page {
  width: 100%;
  min-width: 0;
  max-width: 1080px;
  margin: 0 auto;
  padding: 10px 12px 44px;
  color: var(--ink);
  font-size: var(--fs-14);
  line-height: 1.45;
}
.pa-head {
  padding: 2px 0 10px;
}
.pa-title {
  margin: 0;
  font-size: var(--fs-24);
  line-height: 1.25;
}
.pa-sub {
  max-width: 620px;
  margin: 4px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.5;
}
.pa-toolbar {
  position: sticky;
  top: 0;
  z-index: var(--z-shell);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 -12px 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-0);
}
.pa-toolbar-group {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: nowrap;
  align-items: center;
  min-width: 0;
  gap: 6px;
}
.pa-export-hint {
  margin: 0 0 10px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-export-hint.is-error {
  border-color: var(--danger-bright);
  color: var(--danger-bright);
}
.pa-tabs {
  display: flex;
  gap: 4px;
  margin: 0 0 10px;
  padding: 4px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  scrollbar-width: none;
}
.pa-tabs::-webkit-scrollbar {
  display: none;
}
.pa-tab {
  flex: 0 0 auto;
  min-height: 44px;
  padding: 7px 14px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  white-space: nowrap;
  cursor: pointer;
}
.pa-tab.is-active {
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pa-btn {
  min-height: 44px;
  padding: 7px 13px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
  cursor: pointer;
}
.pa-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.pa-btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pa-btn-ghost {
  background: transparent;
}
.pa-banner-btn {
  margin-left: 8px;
  padding: 2px 10px;
}
.pa-tab:focus-visible,
.pa-btn:focus-visible,
.pa-chip:focus-visible,
.pa-quad-cell:focus-visible,
.pa-bar-item:focus-visible,
.pa-product:focus-visible,
.pa-recent-chip:focus-visible,
.pa-period:focus-visible,
.pa-step:focus-visible,
.pa-close:focus-visible,
.pa-input:focus-visible,
.pa-select:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.pa-banner {
  margin: 0 0 10px;
  padding: 9px 12px;
  border: 1px solid var(--danger-bright);
  border-radius: 10px;
  background: var(--wb-danger-a10);
  font-size: var(--fs-13);
}
.pa-notice {
  margin: 8px 0 0;
  padding: 8px 12px;
  border: 1px solid var(--ok);
  border-radius: 10px;
  background: var(--wb-ok-a10);
  font-size: var(--fs-13);
}
.pa-card {
  margin-bottom: 10px;
  padding: 13px 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
}
.pa-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.pa-card-head h2 {
  margin: 0;
  font-size: var(--fs-16);
  line-height: 1.3;
}
.pa-card-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}
.pa-hint {
  margin: 3px 0 0;
  color: var(--ink-faint);
  font-size: var(--fs-12);
  line-height: 1.5;
}
.pa-window {
  flex: 0 0 auto;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}
.pa-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 13px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-stat-label {
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-stat-value {
  font-size: var(--fs-24);
  font-weight: 600;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.pa-stat-unit {
  margin-left: 3px;
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 400;
}
.pa-stat-note {
  color: var(--ink-faint);
  font-size: var(--fs-11);
  line-height: 1.4;
}
.pa-delta {
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
}
.pa-delta.is-up {
  color: var(--danger-bright);
}
.pa-delta.is-down {
  color: var(--ok);
}
.pa-delta.is-flat {
  color: var(--ink-dim);
}
.pa-trend-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 0 0 8px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-trend-summary strong {
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.pa-chart {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  align-items: end;
  gap: 8px;
  min-height: 180px;
  padding: 6px 0 0;
}
.pa-bar-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  min-width: 0;
  min-height: 44px;
  padding: 4px 2px 0;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  cursor: pointer;
}
.pa-bar-item:hover {
  background: var(--wb-tint-04);
}
.pa-bar-item.is-today {
  background: var(--wb-tint-08);
}
.pa-bar-value {
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
}
.pa-bar-track {
  display: flex;
  align-items: flex-end;
  width: 100%;
  height: 120px;
  border-bottom: 1px solid var(--line-strong);
}
.pa-bar {
  display: block;
  width: 100%;
  max-width: 40px;
  height: 100%;
  margin: 0 auto;
  overflow: visible;
}
.pa-bar-fill {
  fill: var(--wb-tint-45);
}
.pa-bar-item.is-today .pa-bar-fill {
  fill: var(--action);
}
.pa-bar-date {
  font-size: var(--fs-11);
  white-space: nowrap;
}
.pa-bar-orders {
  color: var(--ink-dim);
  font-size: var(--fs-11);
  white-space: nowrap;
}
.pa-inline-empty {
  margin: 0 0 8px;
  padding: 8px 10px;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-quad {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.pa-quad-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-height: 68px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.pa-quad-cell:hover {
  border-color: var(--line-strong);
}
.pa-quad-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}
.pa-quad-title {
  font-size: var(--fs-13);
  font-weight: 600;
}
.pa-quad-count {
  font-size: var(--fs-20);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.pa-quad-hint {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-params {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
}
.pa-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pa-field > span {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-field .pa-input {
  width: 104px;
}
.pa-params-actions {
  display: flex;
  gap: 6px;
  margin-left: auto;
}
.pa-input {
  min-height: 44px;
  padding: 7px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
}
.pa-select {
  min-height: 44px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
}
.pa-anomaly-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.pa-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pa-chip {
  min-height: 44px;
  padding: 6px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
  cursor: pointer;
}
.pa-chip.is-active {
  border-color: var(--action);
  background: var(--action-soft);
  font-weight: 600;
}
.pa-chip-count {
  margin-left: 5px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--wb-tint-14);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-search {
  flex: 1 1 200px;
  min-width: 0;
}
.pa-anoms {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.pa-anom {
  padding: 11px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
}
.pa-anom-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.pa-anom-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.pa-anom-title strong {
  font-size: var(--fs-14);
}
.pa-anom-code {
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-sev {
  flex: 0 0 auto;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-11);
  line-height: 1.7;
}
.pa-sev.is-high {
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}
.pa-sev.is-normal {
  background: var(--wb-warn-a10);
  color: var(--ink);
}
.pa-sev.is-low {
  background: var(--wb-tint-08);
  color: var(--ink-dim);
}
.pa-anom-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 12px;
  margin: 4px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-11);
  overflow-wrap: anywhere;
}
.pa-anom-detail {
  margin: 6px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.5;
}
.pa-anom-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}
.pa-meter {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 8px;
}
.pa-meter-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}
.pa-meter-label {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-meter-bar {
  display: block;
  width: 100%;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--wb-ink-a04);
}
.pa-meter-ref {
  fill: var(--wb-ink-a22);
}
.pa-meter-qty {
  fill: var(--action);
}
.pa-meter.is-over .pa-meter-qty {
  fill: var(--danger);
}
.pa-meter.is-under .pa-meter-qty {
  fill: var(--info);
}
.pa-meter-num {
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
}
.pa-empty {
  margin: 8px 0 0;
  padding: 20px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  color: var(--ink-dim);
  font-size: var(--fs-13);
  text-align: center;
}
.pa-empty p {
  margin: 0;
}
.pa-empty .pa-btn {
  margin-top: 10px;
}
.pa-skeleton {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.pa-skeleton-card {
  height: 86px;
  border-radius: 13px;
  background: var(--surface-1);
  animation: pa-pulse 1.2s ease-in-out infinite;
}
@keyframes pa-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 0.8;
  }
}
.pa-drill {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}
.pa-products-card {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
}
.pa-product-tools {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pa-recent {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}
.pa-recent-label {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-recent-chip {
  min-height: 44px;
  padding: 5px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-12);
  cursor: pointer;
}
.pa-recent-chip.is-selected {
  border-color: var(--action);
  background: var(--action-soft);
  font-weight: 600;
}
.pa-product-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 440px;
  margin: 10px 0 0;
  padding: 0;
  overflow: auto;
  list-style: none;
}
.pa-product {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.pa-product:hover {
  background: var(--wb-tint-04);
}
.pa-product.is-selected {
  border-color: var(--action);
  background: var(--action-soft);
}
.pa-product-name {
  overflow: hidden;
  font-size: var(--fs-13);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-product-meta {
  overflow: hidden;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-detail {
  min-width: 0;
}
.pa-detail-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.pa-periods {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.pa-period {
  min-height: 44px;
  padding: 6px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-12);
  cursor: pointer;
}
.pa-period.is-active {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pa-product-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.pa-product-main {
  min-width: 0;
}
.pa-product-main h2 {
  margin: 0;
  font-size: var(--fs-20);
  line-height: 1.3;
}
.pa-product-side {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pa-pill {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-2);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.pa-stats-note {
  margin: -2px 0 10px;
}
.pa-line-chart {
  display: block;
  width: 100%;
  height: 200px;
}
.pa-grid-line {
  stroke: var(--line);
  stroke-width: 1;
  stroke-dasharray: 4 4;
}
.pa-chart-bar {
  fill: var(--wb-tint-45);
}
.pa-chart-stock {
  fill: none;
  stroke: var(--info);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pa-chart-usage {
  fill: none;
  stroke: var(--warn);
  stroke-width: 2.5;
  stroke-dasharray: 6 5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pa-axis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 2px;
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin: 8px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.pa-legend-dot {
  width: 12px;
  height: 3px;
  border-radius: 2px;
  background: var(--action);
}
.pa-legend-dot.is-stock {
  background: var(--info);
}
.pa-legend-dot.is-usage {
  background: var(--warn);
}
.pa-stock-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
}
.pa-stock-input-row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.pa-stock-input-row .pa-input {
  width: 96px;
  text-align: center;
}
.pa-step {
  width: 44px;
  min-height: 44px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-16);
  cursor: pointer;
}
.pa-table-wrap {
  overflow-x: auto;
}
.pa-table {
  width: 100%;
  min-width: 420px;
  border-collapse: collapse;
  font-size: var(--fs-12);
}
.pa-table th,
.pa-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  text-align: right;
  white-space: nowrap;
}
.pa-table th:first-child,
.pa-table td:first-child {
  text-align: left;
}
.pa-table th {
  color: var(--ink-dim);
  font-size: var(--fs-11);
  font-weight: 600;
}
.pa-table .is-num {
  font-variant-numeric: tabular-nums;
}
.pa-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--wb-scrim-strong);
}
.pa-dialog {
  display: flex;
  flex-direction: column;
  width: min(420px, 100%);
  max-height: 86vh;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface-1);
  color: var(--ink);
  box-shadow: var(--paper-shadow);
}
.pa-dialog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 16px 18px 8px;
}
.pa-dialog-head h2 {
  margin: 0;
  font-size: var(--fs-16);
}
.pa-dialog-sub {
  margin: 3px 0 0;
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-close {
  min-width: 44px;
  min-height: 44px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--ink);
  font-size: var(--fs-24);
  line-height: 1;
  cursor: pointer;
}
.pa-dialog-body {
  overflow: auto;
  padding: 4px 18px 18px;
}
.pa-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
@media (max-width: 900px) {
  .pa-drill {
    grid-template-columns: 1fr;
  }
  .pa-product-list {
    max-height: 260px;
  }
}
@media (max-width: 720px) {
  .pa-stats,
  .pa-skeleton {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pa-anoms {
    grid-template-columns: 1fr;
  }
  .pa-chart {
    gap: 4px;
  }
  .pa-params-actions {
    margin-left: 0;
  }
}
@media (max-width: 560px) {
  .pa-quad {
    grid-template-columns: 1fr;
  }
  .pa-toolbar-group {
    width: 100%;
  }
  .pa-toolbar-group:first-child :deep(.luyun-date-picker) {
    flex: 1 1 0;
    width: auto;
    min-width: 0;
  }
  .pa-toolbar-group:last-child {
    justify-content: flex-end;
  }
}
@media (max-width: 430px) {
  .pa-chart {
    min-height: 150px;
  }
  .pa-bar-track {
    height: 92px;
  }
  .pa-line-chart {
    height: 160px;
  }
}
</style>
