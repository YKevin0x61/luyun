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
import { formatMonthDay, formatMonthDayChinese, formatQty, pad2, ratioText, severityText, todayText, weekdayText } from './purchaseUi.js'
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
const updatedClock = ref('')
const isToday = computed(() => day.value === today)
const dateMain = computed(() => formatMonthDayChinese(day.value))
const dateWeek = computed(() => weekdayOf(day.value))
const dateSub = computed(() => (isToday.value ? '今日' : '历史日期') + ' · 更新于 ' + (updatedClock.value || '--:--'))

function weekdayOf(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return ''
  return weekdayText(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))).getUTCDay())
}

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
}
function closeParams() {
  paramsOpen.value = false
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
    const now = new Date()
    updatedClock.value = pad2(now.getHours()) + ':' + pad2(now.getMinutes())
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
const showDelta = computed(() => isToday.value && !!(overview.value && overview.value.compare_mode))

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
function trendLabel(row) {
  if (!row || !row.date) return ''
  if (row.date === today) return '今日'
  const parts = String(row.date).split('-')
  return parts.length === 3 ? Number(parts[1]) + '/' + Number(parts[2]) : formatMonthDay(row.date)
}

// ── 异常 ────────────────────────────────────────────────────────────────────
const ANOMALY_TABS = [
  { key: 'no_order', label: '连续无订单' },
  { key: 'over', label: '超量订单' },
  { key: 'under', label: '低量订单' },
  { key: 'zero_stock', label: '库存为零' },
]
const ANOMALY_FILTERS = [{ key: 'all', label: '全部' }, ...ANOMALY_TABS]
const GROUP_META = {
  all: {
    title: '异常预警',
    hint: '四类异常合在一起看，按严重度排。',
    empty: '没有异常商品。',
  },
  no_order: {
    title: '连续无订单',
    hint: '超过设定天数没下单',
    empty: '没有长时间没下单的商品。',
  },
  over: {
    title: '超量订单',
    hint: '当天采购量超过参考值',
    empty: '当天没有明显超量的商品。',
  },
  under: {
    title: '低量订单',
    hint: '当天采购量不到参考值',
    empty: '当天没有明显偏低的商品。',
  },
  zero_stock: {
    title: '库存为零',
    hint: '最近一次盘点为 0',
    empty: '没有零库存的需盘商品。',
  },
}
const GROUP_TONE = {
  no_order: 'is-danger',
  over: 'is-accent',
  under: 'is-warning',
  zero_stock: 'is-muted',
}
const anomalies = ref(null)
const anomaliesLoading = ref(false)
const anomaliesError = ref('')
const activeGroup = ref('all')
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

const ANOMALY_KEYS = ['no_order', 'over', 'under', 'zero_stock']
const groupCounts = computed(() => {
  const data = anomalies.value || {}
  const counts = {
    no_order: (data.no_order || []).length,
    over: (data.over || []).length,
    under: (data.under || []).length,
    zero_stock: (data.zero_stock || []).length,
  }
  counts.all = ANOMALY_KEYS.reduce((sum, key) => sum + counts[key], 0)
  return counts
})
const activeMeta = computed(() => GROUP_META[activeGroup.value] || GROUP_META.all)
function tagRows(rows, key) {
  return (Array.isArray(rows) ? rows : []).map((row) => ({ ...row, _group: key }))
}
const allAnomalyRows = computed(() => ANOMALY_KEYS.flatMap((key) => tagRows((anomalies.value || {})[key], key)))
const groupRows = computed(() => {
  if (activeGroup.value === 'all') return allAnomalyRows.value
  return tagRows((anomalies.value || {})[activeGroup.value], activeGroup.value)
})
const keywordText = computed(() => keyword.value.trim().toLowerCase())

const SEVERITY_RANK = { high: 3, normal: 2, low: 1 }

function compareQuantity(a, b) {
  const group = a._group || activeGroup.value
  if (group === 'no_order') return (Number(b.days) || 0) - (Number(a.days) || 0)
  if (group === 'zero_stock') return (Number(a.current_stock) || 0) - (Number(b.current_stock) || 0)
  if (group === 'under') return (Number(a.quantity) || 0) - (Number(b.quantity) || 0)
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
function toneClass(value) {
  return GROUP_TONE[value] || 'is-muted'
}
function groupLabel(value) {
  const found = ANOMALY_TABS.find((item) => item.key === value)
  return found ? found.label : '异常'
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
function maxRow(rows, key) {
  return rows.reduce((best, row) => (!best || Number(row[key]) > Number(best[key]) ? row : best), null)
}
const alertTiles = computed(() => {
  const data = anomalies.value || {}
  const noOrder = data.no_order || []
  const over = data.over || []
  const under = data.under || []
  const zero = data.zero_stock || []
  const topNoOrder = maxRow(noOrder, 'days')
  const topOver = maxRow(over, 'ratio')
  const lowUnder = under.reduce((best, row) => (!best || Number(row.ratio) < Number(best.ratio) ? row : best), null)
  return [
    {
      key: 'no_order',
      title: '连续无订单',
      count: noOrder.length,
      hint: topNoOrder ? '最久 ' + topNoOrder.days + ' 天 · ' + topNoOrder.name : '超过设定天数没下单',
    },
    {
      key: 'over',
      title: '超量订单',
      count: over.length,
      hint: topOver ? '最高 ' + formatQty(topOver.ratio) + ' 倍 · ' + topOver.name : '当天采购量超过参考值',
    },
    {
      key: 'under',
      title: '低量订单',
      count: under.length,
      hint: lowUnder ? '最低 ' + formatQty(lowUnder.ratio) + ' 倍 · ' + lowUnder.name : '当天采购量不到参考值',
    },
    {
      key: 'zero_stock',
      title: '库存为零',
      count: zero.length,
      hint: zero.length ? '最近盘点为 0，需要补货' : '最近一次盘点为 0',
    },
  ]
})
const pendingTotal = computed(() => groupCounts.value.all)
const pendingText = computed(() => (pendingTotal.value
  ? pendingTotal.value + ' 项待处理 · ' + groupCounts.value.no_order + ' 项连续无订单'
  : '暂无异常，订单节奏正常'))
function showGroupFromTile(key) {
  selectGroup(key)
}
async function viewAnalysis(row) {
  const id = Number(row && row.product_id)
  if (!id) return
  selectedProductId.value = id
  rememberProduct(id)
  if (tab.value !== 'drill') {
    switchTab('drill')
    return
  }
  await loadProducts()
  ensureSelection()
}

// ── 零库存弹窗：改库存 ──────────────────────────────────────────────────────
const stockOpen = ref(false)
const stockRow = ref(null)
const stockDate = ref(today)
const stockValue = ref('')
const stockSaving = ref(false)
const stockError = ref('')
const stockInput = ref(null)

function openStockDialog(row) {
  stockRow.value = row
  stockDate.value = today
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
const stockStep = computed(() => {
  const raw = stockRow.value && stockRow.value.order_step
  const step = Number(raw)
  return Number.isFinite(step) && step > 0 ? step : 1
})
function nudgeStock(delta) {
  const current = Number(stockValue.value)
  const base = Number.isFinite(current) ? current : 0
  stockValue.value = String(Math.max(0, round2(base + delta * stockStep.value)))
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
      count_date: stockDate.value || today,
      current_stock: value,
    })
    stockOpen.value = false
    stockRow.value = null
    stockNotice.value = row.name + ' ' + formatMonthDay(stockDate.value || today) + ' 的库存已更新为 ' + formatQty(value) + (row.unit || '')
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
const searchOpen = ref(false)
const productSearchInput = ref(null)
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
const searchResults = computed(() => visibleProducts.value.slice(0, 8))
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
  if (!id) return
  searchOpen.value = false
  productQuery.value = ''
  if (id === selectedProductId.value) return
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
function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (searchOpen.value) nextTick(() => productSearchInput.value && productSearchInput.value.focus())
}
function clearProductSearch() {
  productQuery.value = ''
  searchOpen.value = true
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
  searchOpen.value = false
  await loadProducts()
  if (!productsError.value) ensureSelection()
}

watch(tab, (value) => {
  if (value === 'drill') openDrill()
})

// ── 单品历史 ────────────────────────────────────────────────────────────────
const PERIODS = [7, 14, 30, 60, 90, 180]
const period = ref(14)
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
const usageDays = computed(() => timeline.value.filter((row) => row.usage !== null && row.usage !== undefined).length)
const usageTotal = computed(() => timeline.value.reduce((sum, row) => {
  const value = Number(row.usage)
  return Number.isFinite(value) ? sum + value : sum
}, 0))

function syncMgmtStock() {
  if (mgmtStock.value !== '') return
  mgmtStock.value = latestStock.value === null ? '' : String(latestStock.value)
}

const orderStep = computed(() => {
  const raw = (history.value && history.value.product && history.value.product.order_step) ?? (selectedProduct.value && selectedProduct.value.order_step)
  const step = Number(raw)
  return Number.isFinite(step) && step > 0 ? step : 1
})

// ── 趋势图：柱 = 采购量，线 = 库存 / 每日用量（共用一根纵轴）─────────────────
const CHART = { width: 720, height: 240, top: 16, right: 46, bottom: 26, left: 48 }
const chartInnerWidth = CHART.width - CHART.left - CHART.right
const chartInnerHeight = CHART.height - CHART.top - CHART.bottom

const chartMax = computed(() => {
  let max = 1
  for (const row of timeline.value) {
    for (const raw of [Number(row.purchase) || 0, row.stock, row.usage]) {
      if (raw === null || raw === undefined) continue
      const value = Number(raw)
      if (Number.isFinite(value)) max = Math.max(max, value)
    }
  }
  return max
})
const chartHasLine = computed(() => timeline.value.some((row) => {
  for (const key of ['stock', 'usage']) {
    const raw = row[key]
    if (raw !== null && raw !== undefined && Number.isFinite(Number(raw))) return true
  }
  return false
}))
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
  const plotLeft = CHART.left
  const plotRight = CHART.width - CHART.right
  if (!count) return { bars: [], stockPath: '', usagePath: '', ticks: [], left: plotLeft, right: plotRight }
  const step = chartInnerWidth / count
  const barWidth = Math.max(2, Math.min(40, step * 0.46))
  const maxValue = chartMax.value
  const xFor = (index) => CHART.left + step * index + step / 2
  const yValue = (value) => CHART.top + chartInnerHeight - (value / maxValue) * chartInnerHeight
  const bars = rows.map((row, index) => {
    const value = Number(row.purchase) || 0
    const height = value > 0 ? Math.max(2, (value / maxValue) * chartInnerHeight) : 0
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
    stockPath: buildLinePath(rows, 'stock', xFor, yValue),
    usagePath: buildLinePath(rows, 'usage', xFor, yValue),
    left: plotLeft,
    right: plotRight,
    ticks: [1, 0.5, 0].map((ratio) => ({
      y: CHART.top + chartInnerHeight * (1 - ratio),
      value: formatQty(maxValue * ratio),
    })),
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
  else if (paramsOpen.value) closeParams()
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
      <div class="pa-head-copy">
        <h1 class="pa-title">采购分析</h1>
        <p class="pa-sub">看当天买了多少、哪些品没买 / 买多 / 买少，以及单个商品最近的采购与盘点。</p>
      </div>
      <button type="button" class="pa-export-link" :disabled="exporting || refreshing" @click="exportAnalysis">
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 2v10m0 0 4-4m-4 4-4-4M3 15h14" />
        </svg>
        <span>{{ exporting ? '生成中…' : '导出 Excel' }}</span>
      </button>
    </header>
    <p v-if="exportHint" class="pa-export-hint" :class="{ 'is-error': exportFailed }" role="status">{{ exportHint }}</p>

    <section class="pa-topbar" aria-label="分析日期与操作">
      <div class="pa-date-block">
        <div class="pa-date-copy">
          <p class="pa-date-line">
            <strong>{{ dateMain }}</strong>
            <span>{{ dateWeek }}</span>
          </p>
          <p class="pa-date-sub">{{ dateSub }}</p>
        </div>
        <LuyunDatePicker v-model="day" class="pa-date-picker" :max-date="today" aria-label="分析哪一天" />
        <button v-if="!isToday" type="button" class="pa-btn pa-btn-ghost pa-today-inline" @click="goToday">回到今日</button>
      </div>
      <div class="pa-topbar-actions">
        <button type="button" class="pa-icon-btn pa-export-icon" :disabled="exporting || refreshing" aria-label="导出 Excel" title="导出 Excel" @click="exportAnalysis">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M10 2v10m0 0 4-4m-4 4-4-4M3 15h14" />
          </svg>
        </button>
        <button type="button" class="pa-icon-btn" :class="{ 'is-spinning': refreshing }" :disabled="refreshing" aria-label="刷新" title="刷新" @click="refresh">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M16 8a6 6 0 1 0-1.4 6.1M16 3v5h-5" />
          </svg>
        </button>
        <button type="button" class="pa-icon-btn" aria-label="分析设置" title="分析设置" @click="paramsOpen = true">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3 5h2M9 5h8M3 10h8M15 10h2M3 15h4M11 15h6" />
            <circle cx="7" cy="5" r="2" />
            <circle cx="13" cy="10" r="2" />
            <circle cx="9" cy="15" r="2" />
          </svg>
        </button>
      </div>
    </section>

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
        <span>{{ item.label }}</span>
        <span v-if="item.key === 'anomalies' && pendingTotal > 0" class="pa-tab-badge">{{ pendingTotal }}</span>
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
      </div>
      <template v-else-if="overview">
        <section class="pa-hero" aria-label="当日概览">
          <div class="pa-hero-top">
            <div>
              <span class="pa-hero-eyebrow">{{ isToday ? '今日订单' : '当日订单' }}</span>
              <strong class="pa-hero-value">{{ overview.order_count }}<small>单</small></strong>
            </div>
            <div v-if="showDelta" class="pa-hero-delta">
              <span class="pa-delta" :class="deltaClass">{{ deltaText }} 单</span>
              <span>{{ yesterdayLabel }}</span>
            </div>
          </div>
          <div class="pa-hero-strip">
            <div>
              <strong>{{ formatQty(overview.item_count) }}</strong>
              <span>商品件数</span>
            </div>
            <div>
              <strong>{{ overview.sku_count }}</strong>
              <span>涉及商品</span>
            </div>
            <div>
              <strong>{{ overview.submitter_count }}</strong>
              <span>下单人</span>
            </div>
          </div>
        </section>

        <section class="pa-card">
          <header class="pa-card-head">
            <div class="pa-card-title">
              <h2>近 7 日订单趋势</h2>
              <p class="pa-hint">按订单数（不是采购量）；点一根柱子可以切到那天。</p>
            </div>
            <span class="pa-card-meta">合计 {{ trendTotal }} 单</span>
          </header>
          <div v-if="trendRows.length" class="pa-trend" role="img" :aria-label="'近 7 日订单趋势，合计 ' + trendTotal + ' 单'">
            <button
              v-for="row in trendRows"
              :key="row.date"
              type="button"
              class="pa-trend-col"
              :class="{ 'is-today': row.date === today, 'is-selected': row.date === day }"
              :aria-label="row.date + '，' + row.order_count + ' 单'"
              @click="pickTrendDay(row.date)"
            >
              <span class="pa-trend-value">{{ row.order_count }}</span>
              <span class="pa-bar-track">
                <svg class="pa-bar" viewBox="0 0 10 100" preserveAspectRatio="none" aria-hidden="true">
                  <rect class="pa-bar-fill" x="0" :y="barTop(row)" width="10" :height="barHeight(row)" rx="2" />
                </svg>
              </span>
              <span class="pa-trend-date">{{ trendLabel(row) }}</span>
            </button>
          </div>
          <p v-if="trendAllZero" class="pa-empty-line">近 7 日还没有采购单。</p>
          <footer class="pa-trend-foot">
            <span>日均 {{ trendAvgText }} 单</span>
            <span v-if="showDelta">{{ yesterdayLabel }} {{ overview.yesterday_order_count }} 单</span>
          </footer>
        </section>

        <section class="pa-card">
          <header class="pa-card-head">
            <div class="pa-card-title">
              <h2>异常预警总览</h2>
              <p class="pa-hint">四类异常，点一块直接看清单。</p>
            </div>
            <button type="button" class="pa-link" @click="switchTab('anomalies')">全部处理</button>
          </header>
          <p v-if="anomaliesError" class="pa-banner" role="alert">
            {{ anomaliesError }}
            <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadAnomalies">重试</button>
          </p>
          <div v-else-if="anomaliesLoading && !anomalies" class="pa-skeleton pa-skeleton-tight">
            <div class="pa-skeleton-card"></div>
          </div>
          <template v-else>
            <div class="pa-alert-grid">
              <button
                v-for="tile in alertTiles"
                :key="tile.key"
                type="button"
                class="pa-alert-tile"
                :class="toneClass(tile.key)"
                @click="selectGroup(tile.key)"
              >
                <span class="pa-alert-name"><span class="pa-dot"></span>{{ tile.title }}</span>
                <span class="pa-alert-count">{{ tile.count }}<small>项</small></span>
                <span class="pa-alert-hint">{{ tile.hint }}</span>
              </button>
            </div>
            <div class="pa-alert-banner" :class="{ 'is-calm': !pendingTotal }">
              <span>{{ pendingText }}</span>
              <button v-if="pendingTotal" type="button" class="pa-link" @click="switchTab('anomalies')">去处理</button>
            </div>
          </template>
        </section>
      </template>
    </section>

    <!-- 异常预警 -->
    <section v-else-if="tab === 'anomalies'" class="pa-panel" role="tabpanel" aria-label="异常预警">
      <p v-if="anomaliesError" class="pa-banner" role="alert">
        {{ anomaliesError }}
        <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadAnomalies">重试</button>
      </p>
      <div v-else-if="anomaliesLoading && !anomalies" class="pa-skeleton">
        <div class="pa-skeleton-card"></div>
        <div class="pa-skeleton-card"></div>
      </div>
      <template v-else>
        <section class="pa-filter-card">
          <header class="pa-filter-head">
            <div>
              <h2>{{ activeMeta.title }}</h2>
              <p class="pa-hint">{{ paramsSummary }}</p>
            </div>
            <button type="button" class="pa-btn pa-btn-ghost" @click="paramsOpen = true">调参数</button>
          </header>
          <div class="pa-chips">
            <button
              v-for="item in ANOMALY_FILTERS"
              :key="item.key"
              type="button"
              class="pa-chip"
              :class="{ 'is-active': activeGroup === item.key }"
              @click="selectGroup(item.key)"
            >
              {{ item.label }}<span>{{ groupCounts[item.key] }}</span>
            </button>
          </div>
          <div class="pa-filter-row">
            <input v-model="keyword" type="search" class="pa-input" placeholder="筛选商品名称 / 编码 / 供应商" aria-label="筛选商品" />
            <select v-model="sortBy" class="pa-select" aria-label="排序方式">
              <option value="severity">按严重度</option>
              <option value="quantity">按数量</option>
            </select>
          </div>
        </section>

        <p v-if="stockNotice" class="pa-notice" role="status">{{ stockNotice }}</p>
        <p v-if="activeMeta.hint" class="pa-filter-hint">{{ activeMeta.hint }}</p>

        <div v-if="filteredRows.length" class="pa-anoms">
          <article
            v-for="row in filteredRows"
            :key="row._group + '-' + row.product_id"
            class="pa-anom"
            :class="toneClass(row._group)"
          >
            <header class="pa-anom-head">
              <div class="pa-anom-title">
                <strong>{{ row.name }}</strong>
                <span>{{ row.product_code }}</span>
              </div>
              <div class="pa-anom-badges">
                <span class="pa-type" :class="toneClass(row._group)">{{ groupLabel(row._group) }}</span>
                <span class="pa-sev" :class="severityClass(row.severity)">{{ severityText(row.severity) }}</span>
              </div>
            </header>
            <p class="pa-anom-meta">
              <span v-if="row.supplier_name">供应商 {{ row.supplier_name }}</span>
              <span v-if="row.category_name">分类 {{ row.category_name }}</span>
              <span v-if="row.specification">{{ row.specification }}</span>
            </p>

            <template v-if="row._group === 'over' || row._group === 'under'">
              <div class="pa-meter">
                <span class="pa-meter-label">参考</span>
                <span class="pa-meter-bar">
                  <svg class="pa-meter-svg" viewBox="0 0 100 7" preserveAspectRatio="none" aria-hidden="true">
                    <rect class="pa-meter-fill is-reference" x="0" y="0" :width="comparePercent(row, 'reference')" height="7" rx="3.5" />
                  </svg>
                </span>
                <span class="pa-meter-value">{{ formatQty(row.reference) }}</span>
              </div>
              <div class="pa-meter">
                <span class="pa-meter-label">今日</span>
                <span class="pa-meter-bar">
                  <svg class="pa-meter-svg" viewBox="0 0 100 7" preserveAspectRatio="none" aria-hidden="true">
                    <rect class="pa-meter-fill" :class="row._group === 'over' ? 'is-over' : 'is-under'" x="0" y="0" :width="comparePercent(row, 'quantity')" height="7" rx="3.5" />
                  </svg>
                </span>
                <span class="pa-meter-value">{{ formatQty(row.quantity) }}</span>
              </div>
              <p class="pa-anom-detail">
                {{ row._group === 'over' ? '超量' : '低量' }} {{ formatQty(row.ratio) }} 倍
                <span v-if="row._group === 'over'">· 超出 {{ formatQty(Number(row.quantity) - Number(row.reference)) }}</span>
              </p>
            </template>

            <template v-else-if="row._group === 'no_order'">
              <div class="pa-anom-info">
                <div><span>最后采购</span><strong>{{ formatMonthDay(row.last_ordered) }}</strong></div>
                <div><span>已空档</span><strong>{{ row.days }} 天</strong></div>
                <div><span>上次数量</span><strong>{{ formatQty(row.last_quantity) }}{{ row.unit || '' }}</strong></div>
              </div>
              <p v-if="row.last_submitters && row.last_submitters.length" class="pa-anom-detail">最后下单人 {{ row.last_submitters.join('、') }}</p>
            </template>

            <template v-else>
              <div class="pa-anom-info">
                <div><span>库存</span><strong>{{ formatQty(row.current_stock) }}{{ row.unit || '' }}</strong></div>
                <div><span>盘点日期</span><strong>{{ formatMonthDay(row.count_date) }}</strong></div>
                <div><span>盘点人</span><strong>{{ row.counted_by || '—' }}</strong></div>
              </div>
            </template>

            <footer class="pa-anom-foot">
              <button
                v-if="row._group !== 'zero_stock'"
                type="button"
                class="pa-btn pa-btn-ghost"
                @click="viewAnalysis(row)"
              >查看分析</button>
              <button
                v-else
                type="button"
                class="pa-btn pa-btn-ghost"
                @click="openStockDialog(row)"
              >修正库存</button>
            </footer>
          </article>
        </div>
        <div v-else class="pa-empty">
          <p>{{ keyword ? '没有匹配的商品。' : activeMeta.empty }}</p>
        </div>
      </template>
    </section>

    <!-- 商品钻取 -->
    <section v-else class="pa-panel" role="tabpanel" aria-label="商品钻取">
      <section class="pa-search-card">
        <div class="pa-search-head">
          <h2>商品钻取</h2>
          <span v-if="products.length" class="pa-card-meta">共 {{ products.length }} 个商品</span>
        </div>
        <div class="pa-search-box">
          <input
            ref="productSearchInput"
            v-model="productQuery"
            type="search"
            class="pa-input"
            placeholder="搜商品名称 / 编码 / 供应商"
            aria-label="搜索商品"
            @focus="searchOpen = true"
            @keydown.esc="searchOpen = false"
          />
          <button v-if="productQuery" type="button" class="pa-search-clear" aria-label="清空搜索" @click="clearProductSearch">×</button>
          <div v-if="searchOpen" class="pa-search-results">
            <button
              v-for="item in searchResults"
              :key="item.id"
              type="button"
              class="pa-search-result"
              @click="selectProduct(item)"
            >
              <span>{{ item.name }}</span>
              <small>{{ item.product_code }} · {{ item.supplier_name || '未设供应商' }}</small>
            </button>
            <p v-if="!searchResults.length" class="pa-search-empty">没有匹配的商品。</p>
            <p v-else-if="filteredProducts.length > 8" class="pa-search-empty">共 {{ filteredProducts.length }} 个，继续输入缩小范围。</p>
          </div>
        </div>
        <div class="pa-filter-grid">
          <label class="pa-field">
            <span>供应商</span>
            <select v-model="supplierFilter" class="pa-select">
              <option value="">全部供应商</option>
              <option v-for="name in suppliers" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>
          <label class="pa-field">
            <span>周期</span>
            <select class="pa-select" :value="period" @change="changePeriod(Number($event.target.value))">
              <option v-for="value in PERIODS" :key="value" :value="value">{{ value }} 天</option>
            </select>
          </label>
        </div>
        <div v-if="recentProducts.length" class="pa-recent">
          <span class="pa-recent-label">最近</span>
          <button
            v-for="item in recentProducts"
            :key="item.id"
            type="button"
            class="pa-recent-chip"
            :class="{ 'is-active': Number(item.id) === selectedProductId }"
            @click="selectProduct(item)"
          >{{ item.name }}</button>
        </div>
      </section>

      <p v-if="productsError" class="pa-banner" role="alert">
        {{ productsError }}
        <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadProducts(true)">重试</button>
      </p>

      <template v-if="product">
        <section class="pa-product-card">
          <header class="pa-product-head">
            <div>
              <h2>{{ product.name }}</h2>
              <p class="pa-hint">{{ product.product_code }} · {{ product.supplier_name || '未设供应商' }} · {{ product.category_name || '未设分类' }}</p>
              <p v-if="product.specification" class="pa-hint">规格 {{ product.specification }}</p>
            </div>
            <button type="button" class="pa-btn pa-btn-ghost" @click="toggleSearch">切换商品</button>
          </header>
          <div class="pa-badges">
            <span class="pa-pill">盘库存 {{ latestStockText }}</span>
            <span class="pa-pill">步长 {{ formatQty(orderStep) }}</span>
            <span v-if="history && history.stats && history.stats.last_order_date" class="pa-pill">上次采购 {{ formatMonthDay(history.stats.last_order_date) }}</span>
          </div>
        </section>

        <p v-if="historyError" class="pa-banner" role="alert">
          {{ historyError }}
          <button type="button" class="pa-btn pa-btn-ghost pa-banner-btn" @click="loadHistory">重试</button>
        </p>
        <div v-else-if="historyLoading && !history" class="pa-skeleton">
          <div class="pa-skeleton-card"></div>
          <div class="pa-skeleton-card"></div>
        </div>

        <template v-if="history">
          <section class="pa-stats" aria-label="采购统计">
            <div>
              <strong>{{ history.stats.total_orders }}</strong>
              <span>订单数</span>
            </div>
            <div>
              <strong>{{ formatQty(history.stats.total_quantity) }}</strong>
              <span>采购量</span>
            </div>
            <div>
              <strong>{{ formatQty(history.stats.average_quantity) }}</strong>
              <span>日均量</span>
            </div>
            <div>
              <strong>{{ formatQty(history.stats.order_frequency) }}</strong>
              <span>日均单</span>
            </div>
          </section>
          <p class="pa-stats-note">周期内 {{ history.stats.days_with_purchase }} 天有采购 · 最近采购 {{ history.stats.last_order_date ? formatMonthDay(history.stats.last_order_date) : '—' }}</p>

          <section class="pa-card">
            <header class="pa-card-head pa-chart-head">
              <h2>数据趋势</h2>
              <p class="pa-hint pa-chart-range">最近采购 {{ history.start }} ~ {{ history.end }}</p>
            </header>
            <div class="pa-legend">
              <span><i class="pa-legend-bar"></i>采购量</span>
              <span><i class="pa-legend-line is-stock"></i>库存量</span>
              <span><i class="pa-legend-line is-usage"></i>日使用量</span>
            </div>
            <div v-if="chartEmpty" class="pa-empty-line">这个周期还没有采购和盘点。</div>
            <div class="pa-chart-plot">
              <div class="pa-chart-y is-left" aria-hidden="true">
                <span v-for="tick in chartGeometry.ticks" :key="'yl-' + tick.y">{{ tick.value }}</span>
              </div>
              <div v-if="chartHasLine" class="pa-chart-y is-right" aria-hidden="true">
                <span v-for="tick in chartGeometry.ticks" :key="'yr-' + tick.y">{{ tick.value }}</span>
              </div>
              <svg class="pa-drill-chart" viewBox="0 0 720 240" role="img" aria-label="采购量（左轴）、库存量与日使用量（右轴）趋势">
                <line v-for="tick in chartGeometry.ticks" :key="'grid-' + tick.y" :x1="chartGeometry.left" :y1="tick.y" :x2="chartGeometry.right" :y2="tick.y" class="pa-chart-grid" />
                <rect
                  v-for="item in chartGeometry.bars"
                  :key="'bar-' + item.date"
                  :x="item.x"
                  :y="item.y"
                  :width="item.width"
                  :height="item.height"
                  rx="3"
                  class="pa-drill-bar"
                />
                <path v-if="chartGeometry.stockPath" :d="chartGeometry.stockPath" class="pa-chart-line is-stock" />
                <path v-if="chartGeometry.usagePath" :d="chartGeometry.usagePath" class="pa-chart-line is-usage" />
              </svg>
            </div>
            <div class="pa-chart-axis">
              <span v-for="(label, index) in chartAxis" :key="'axis-' + index">{{ formatMonthDay(label) }}</span>
            </div>
            <footer class="pa-chart-foot">
              <div><span>最新库存</span><strong>{{ latestStockText }}</strong></div>
              <div><span>用量记录</span><strong>{{ usageDays }} 天</strong></div>
              <div><span>累计用量</span><strong>{{ formatQty(usageTotal) }}</strong></div>
            </footer>
          </section>

          <section class="pa-card">
            <header class="pa-card-head">
              <div class="pa-card-title">
                <h2>库存管理</h2>
                <p class="pa-hint">改库存会写一条盘点记录；同一商品同一天只保留最后一次。</p>
              </div>
            </header>
            <p v-if="mgmtNotice" class="pa-notice" role="status">{{ mgmtNotice }}</p>
            <p v-if="mgmtError" class="pa-error" role="alert">{{ mgmtError }}</p>
            <div class="pa-stock-form">
              <label class="pa-field">
                <span>盘点日期</span>
                <LuyunDatePicker v-model="mgmtDate" :max-date="today" aria-label="盘点日期" />
              </label>
              <label class="pa-field">
                <span>库存{{ product.unit ? '（' + product.unit + '）' : '' }}</span>
                <div class="pa-stepper">
                  <button type="button" class="pa-stepper-btn" aria-label="减少一个步长" @click="nudgeMgmt(-1)">−{{ formatQty(orderStep) }}</button>
                  <input v-model="mgmtStock" type="number" min="0" step="any" class="pa-stepper-input" aria-label="库存数量" />
                  <button type="button" class="pa-stepper-btn" aria-label="增加一个步长" @click="nudgeMgmt(1)">+{{ formatQty(orderStep) }}</button>
                </div>
              </label>
              <button type="button" class="pa-btn pa-btn-primary pa-stock-save" :disabled="mgmtSaving" @click="saveMgmtStock">{{ mgmtSaving ? '保存中…' : '保存' }}</button>
            </div>
            <div v-if="stockPills.length" class="pa-stock-pills">
              <span class="pa-stock-pill" v-for="row in stockPills" :key="'pill-' + row.date">
                <small>{{ formatMonthDay(row.date) }}</small>
                <strong>{{ formatQty(row.stock) }}</strong>
              </span>
            </div>
          </section>

          <section class="pa-card">
            <header class="pa-card-head">
              <div class="pa-card-title">
                <h2>采购明细</h2>
                <p class="pa-hint">周期内有采购的日子，最近的在上面。</p>
              </div>
              <button v-if="purchaseRows.length > 20" type="button" class="pa-link" @click="showAllPurchases = !showAllPurchases">
                {{ showAllPurchases ? '收起' : '展开全部' }}
              </button>
            </header>
            <div v-if="visiblePurchaseRows.length" class="pa-purchase-list">
              <div v-for="row in visiblePurchaseRows" :key="'buy-' + row.date" class="pa-purchase-row">
                <div class="pa-purchase-date">
                  <strong>{{ formatMonthDay(row.date) }}</strong>
                  <span>{{ weekdayOf(row.date) }}</span>
                </div>
                <div class="pa-purchase-main">
                  <strong>{{ formatQty(row.purchase) }}{{ product.unit || '' }}</strong>
                  <span>库存 {{ formatQty(row.stock) }} · 用量 {{ formatQty(row.usage) }} · {{ row.counted ? '已盘' : '未盘' }}</span>
                </div>
              </div>
            </div>
            <p v-else class="pa-empty-line">这个周期还没有采购记录。</p>
          </section>
        </template>
      </template>
      <div v-else-if="!productsLoading" class="pa-empty">
        <p>先搜一个商品，或者从「最近」里点一个。</p>
      </div>
    </section>

    <!-- 分析设置 -->
    <Teleport to="body">
      <div v-if="paramsOpen" class="pa-scrim" @click.self="closeParams">
        <div class="pa-sheet" role="dialog" aria-modal="true" aria-labelledby="pa-params-title">
          <header class="pa-sheet-head">
            <div>
              <h2 id="pa-params-title">分析设置</h2>
              <p class="pa-hint">调整异常判定阈值；只影响这台电脑上的采购分析。</p>
            </div>
            <button type="button" class="pa-icon-btn" aria-label="关闭设置" @click="closeParams">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5 5 15" /></svg>
            </button>
          </header>
          <div class="pa-params-grid">
            <label class="pa-field">
              <span>连续无单天数</span>
              <input v-model.number="draft.no_order_days" type="number" min="1" max="90" class="pa-input" />
            </label>
            <label class="pa-field">
              <span>超量倍数</span>
              <input v-model.number="draft.over_ratio" type="number" min="0.1" max="20" step="0.1" class="pa-input" />
            </label>
            <label class="pa-field">
              <span>低量倍数</span>
              <input v-model.number="draft.under_ratio" type="number" min="0.05" max="9.9" step="0.05" class="pa-input" />
            </label>
            <label class="pa-field">
              <span>历史窗口（天）</span>
              <input v-model.number="draft.history_days" type="number" min="7" max="120" class="pa-input" />
            </label>
          </div>
          <div class="pa-sheet-actions">
            <button type="button" class="pa-btn pa-btn-ghost" @click="resetParams">恢复默认</button>
            <button type="button" class="pa-btn pa-btn-primary" @click="applyParams">应用并刷新</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 零库存修正 -->
    <Teleport to="body">
      <div v-if="stockOpen && stockRow" class="pa-scrim pa-scrim-center" @click.self="closeStockDialog">
        <div class="pa-dialog" role="dialog" aria-modal="true" aria-labelledby="pa-stock-title">
          <header class="pa-dialog-head">
            <div>
              <h2 id="pa-stock-title">{{ stockRow.name }} 的库存</h2>
              <p class="pa-hint">{{ stockRow.product_code }} · {{ stockRow.supplier_name || '未设供应商' }}</p>
            </div>
            <button type="button" class="pa-icon-btn" aria-label="关闭库存弹窗" @click="closeStockDialog">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5 5 15" /></svg>
            </button>
          </header>
          <label class="pa-field">
            <span>盘点日期</span>
            <LuyunDatePicker v-model="stockDate" :max-date="today" aria-label="盘点日期" />
          </label>
          <label class="pa-field">
            <span>当前库存{{ stockRow.unit ? '（' + stockRow.unit + '）' : '' }}</span>
            <div class="pa-stepper">
              <button type="button" class="pa-stepper-btn" aria-label="减少一个步长" @click="nudgeStock(-1)">−{{ formatQty(stockStep) }}</button>
              <input ref="stockInput" v-model="stockValue" type="number" min="0" step="any" class="pa-stepper-input" aria-label="库存数量" />
              <button type="button" class="pa-stepper-btn" aria-label="增加一个步长" @click="nudgeStock(1)">+{{ formatQty(stockStep) }}</button>
            </div>
          </label>
          <p class="pa-hint">会按所选日期写一条盘点，同一天再写一次会覆盖上一次。</p>
          <p v-if="stockError" class="pa-error" role="alert">{{ stockError }}</p>
          <footer class="pa-dialog-actions">
            <button type="button" class="pa-btn pa-btn-ghost" @click="closeStockDialog">取消</button>
            <button type="button" class="pa-btn pa-btn-primary" :disabled="stockSaving" @click="saveStock">{{ stockSaving ? '保存中…' : '保存库存' }}</button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pa-page {
  width: 100%;
  min-width: 0;
  max-width: 920px;
  margin: 0 auto;
  padding: 10px 12px calc(28px + var(--safe-b));
  box-sizing: border-box;
  color: var(--ink);
  font-size: var(--fs-14);
}
.pa-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 2px 10px;
}
.pa-head-copy {
  min-width: 0;
}
.pa-title {
  margin: 0;
  font-size: var(--fs-20);
  line-height: 1.2;
  letter-spacing: .01em;
}
.pa-sub {
  margin: 5px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.5;
}
.pa-export-link {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 13px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--surface-1);
  color: var(--action);
  font-size: var(--fs-13);
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.pa-export-link svg {
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pa-export-link:disabled {
  opacity: .55;
  cursor: default;
}
.pa-export-icon {
  display: none;
}
.pa-today-inline {
  flex: 0 0 auto;
}
.pa-export-hint {
  margin: 0 2px 8px;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--wb-tint-08);
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-export-hint.is-error {
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}
.pa-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px;
  margin-bottom: 10px;
  border: 1px solid var(--line-strong);
  border-radius: 18px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-date-block {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1 1 auto;
}
.pa-date-copy {
  min-width: 0;
}
.pa-date-line {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin: 0;
  white-space: nowrap;
}
.pa-date-line strong {
  font-size: var(--fs-24);
  line-height: 1.1;
  letter-spacing: .01em;
}
.pa-date-line span {
  color: var(--ink-dim);
  font-size: var(--fs-13);
}
.pa-date-sub {
  margin: 4px 0 0;
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-date-picker {
  flex: 0 0 168px;
  width: 168px;
  min-width: 0;
}
.pa-date-picker :deep(.luyun-date-picker) {
  width: 100%;
}
.pa-topbar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.pa-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--surface-1);
  color: var(--ink);
  cursor: pointer;
}
.pa-icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pa-icon-btn:disabled {
  opacity: .5;
  cursor: default;
}
.pa-icon-btn.is-spinning svg {
  animation: pa-spin .8s linear infinite;
}
@keyframes pa-spin {
  to { transform: rotate(360deg); }
}
.pa-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 11px;
  border: 1px solid transparent;
  font-size: var(--fs-13);
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.pa-btn-ghost {
  border-color: var(--line-strong);
  background: var(--surface-1);
  color: var(--ink);
}
.pa-btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
}
.pa-btn:disabled {
  opacity: .55;
  cursor: default;
}
.pa-tabs {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 3px;
  padding: 3px;
  margin-bottom: 10px;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 44px;
  padding: 0 8px;
  border: 0;
  border-radius: 11px;
  background: transparent;
  color: var(--ink-dim);
  font-size: var(--fs-13);
  font-weight: 700;
  cursor: pointer;
}
.pa-tab.is-active {
  background: var(--action);
  color: var(--action-ink);
}
.pa-tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--wb-tint-14);
  color: var(--action);
  font-size: var(--fs-11);
  font-weight: 800;
}
.pa-tab.is-active .pa-tab-badge {
  background: var(--surface-1);
  color: var(--action);
}
.pa-panel {
  min-width: 0;
}
.pa-hero {
  padding: 16px;
  margin-bottom: 10px;
  border: 1px solid var(--line-strong);
  border-radius: 18px;
  background: linear-gradient(135deg, var(--action), var(--action-soft));
  color: var(--action-ink);
  box-shadow: var(--paper-shadow-sm);
}
.pa-hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.pa-hero-eyebrow {
  display: block;
  font-size: var(--fs-12);
  font-weight: 700;
  opacity: .8;
}
.pa-hero-value {
  display: block;
  margin-top: 4px;
  font-size: calc(var(--fs-24) * 1.45);
  line-height: 1;
  letter-spacing: -.02em;
}
.pa-hero-value small {
  margin-left: 5px;
  font-size: var(--fs-14);
  font-weight: 600;
  letter-spacing: 0;
}
.pa-hero-delta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  font-size: var(--fs-11);
  white-space: nowrap;
}
.pa-hero-delta .pa-delta {
  font-size: var(--fs-14);
  font-weight: 800;
}
.pa-delta.is-up { color: var(--action-ink); }
.pa-delta.is-down { color: var(--danger-bright); }
.pa-delta.is-flat { color: var(--ink-dim); }
.pa-hero-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 14px;
  border-radius: 12px;
  background: var(--surface-1);
  overflow: hidden;
}
.pa-hero-strip div {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 8px 4px;
  border-right: 1px solid var(--line);
}
.pa-hero-strip div:last-child {
  border-right: 0;
}
.pa-hero-strip strong {
  color: var(--ink);
  font-size: var(--fs-20);
  line-height: 1.1;
}
.pa-hero-strip span {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-card {
  padding: 14px;
  margin-bottom: 10px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.pa-chart-head {
  align-items: center;
}
.pa-chart-range {
  flex: 0 0 auto;
  margin: 0;
  text-align: right;
  white-space: nowrap;
}
.pa-card-title {
  min-width: 0;
}
.pa-card-head h2,
.pa-filter-head h2,
.pa-search-head h2,
.pa-product-head h2 {
  margin: 0;
  font-size: var(--fs-16);
  line-height: 1.3;
}
.pa-hint {
  margin: 3px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  line-height: 1.45;
}
.pa-card-meta {
  flex: 0 0 auto;
  color: var(--ink-dim);
  font-size: var(--fs-12);
  white-space: nowrap;
}
.pa-trend {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
  align-items: end;
  margin: 12px 0 8px;
}
.pa-trend-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: 3px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink-dim);
  font: inherit;
  cursor: pointer;
}
.pa-trend-value {
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-bar-track {
  display: flex;
  align-items: flex-end;
  width: 100%;
  height: 104px;
  border-bottom: 1px solid var(--line-strong);
}
.pa-bar {
  display: block;
  width: 100%;
  max-width: 36px;
  height: 100%;
  margin: 0 auto;
  overflow: visible;
}
.pa-bar-fill {
  fill: var(--wb-tint-22);
  opacity: .8;
  transition: fill .16s ease, opacity .16s ease;
}
.pa-trend-col.is-today .pa-bar-fill,
.pa-trend-col.is-selected .pa-bar-fill {
  fill: var(--action);
  opacity: 1;
}
.pa-trend-col:hover .pa-bar-fill {
  opacity: 1;
}
.pa-trend-date {
  font-size: var(--fs-11);
  white-space: nowrap;
}
.pa-trend-col.is-today .pa-trend-date,
.pa-trend-col.is-selected .pa-trend-date {
  color: var(--action);
  font-weight: 800;
}
.pa-trend-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 2px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--action);
  font-size: var(--fs-12);
  font-weight: 700;
  cursor: pointer;
}
.pa-alert-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin-top: 10px;
}
.pa-alert-tile {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  text-align: left;
  cursor: pointer;
}
.pa-alert-tile.is-danger { background: var(--wb-danger-a10); }
.pa-alert-tile.is-accent { background: var(--wb-tint-14); }
.pa-alert-tile.is-warning { background: var(--wb-warn-a10); }
.pa-alert-tile.is-muted { background: var(--surface-2); }
.pa-alert-name {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--action);
}
.pa-alert-tile.is-danger .pa-dot { background: var(--danger-bright); }
.pa-alert-tile.is-warning .pa-dot { background: var(--warn); }
.pa-alert-tile.is-muted .pa-dot { background: var(--ink-faint); }
.pa-alert-count {
  font-size: var(--fs-24);
  line-height: 1.1;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
.pa-alert-count small {
  margin-left: 2px;
  font-size: var(--fs-11);
  font-weight: 600;
}
.pa-alert-hint {
  width: 100%;
  overflow: hidden;
  color: var(--ink-dim);
  font-size: var(--fs-11);
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-alert-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 9px;
  padding: 9px 10px;
  border-left: 3px solid var(--action);
  border-radius: 4px 10px 10px 4px;
  background: var(--wb-tint-08);
  color: var(--ink);
  font-size: var(--fs-12);
}
.pa-alert-banner.is-calm {
  border-left-color: var(--ok);
  background: var(--wb-ok-a10);
  color: var(--ink-dim);
}
.pa-filter-card {
  padding: 13px;
  margin-bottom: 9px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-filter-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.pa-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 10px;
}
.pa-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 44px;
  padding: 0 11px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12);
  cursor: pointer;
}
.pa-chip span {
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pa-chip.is-active {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.pa-chip.is-active span {
  color: var(--action-ink);
}
.pa-filter-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px;
  gap: 7px;
  margin-top: 8px;
}
.pa-input,
.pa-select {
  width: 100%;
  min-width: 0;
  min-height: 44px;
  padding: 0 11px;
  border: 1px solid var(--line-strong);
  border-radius: 11px;
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-13);
  box-sizing: border-box;
}
.pa-select {
  appearance: none;
}
.pa-filter-hint {
  margin: 0 2px 7px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-anoms {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pa-anom {
  padding: 12px;
  border: 1px solid var(--line-strong);
  border-left: 4px solid var(--line-strong);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-anom.is-danger { border-left-color: var(--danger-bright); }
.pa-anom.is-accent { border-left-color: var(--action); }
.pa-anom.is-warning { border-left-color: var(--warn); }
.pa-anom.is-muted { border-left-color: var(--ink-faint); }
.pa-anom-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.pa-anom-title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 5px;
  min-width: 0;
}
.pa-anom-title strong {
  font-size: var(--fs-14);
}
.pa-anom-title span {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-anom-badges {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}
.pa-type,
.pa-sev {
  display: inline-flex;
  align-items: center;
  padding: 3px 7px;
  border-radius: 999px;
  font-size: var(--fs-11);
  font-weight: 700;
  line-height: 1.4;
  white-space: nowrap;
}
.pa-type.is-danger { background: var(--wb-danger-a10); color: var(--danger-bright); }
.pa-type.is-accent { background: var(--wb-tint-14); color: var(--action); }
.pa-type.is-warning { background: var(--wb-warn-a10); color: var(--ink); }
.pa-type.is-muted { background: var(--surface-2); color: var(--ink-dim); }
.pa-sev.is-high { background: var(--wb-danger-a10); color: var(--danger-bright); }
.pa-sev.is-normal { background: var(--wb-warn-a10); color: var(--ink); }
.pa-sev.is-low { background: var(--surface-2); color: var(--ink-dim); }
.pa-anom-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 9px;
  margin: 5px 0 7px;
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-meter {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 48px;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
}
.pa-meter-label {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-meter-bar {
  display: block;
  height: 7px;
  border-radius: 99px;
  background: var(--wb-tint-08);
  overflow: hidden;
}
.pa-meter-svg {
  display: block;
  width: 100%;
  height: 100%;
}
.pa-meter-fill {
  fill: var(--action);
  opacity: .75;
}
.pa-meter-fill.is-reference {
  fill: var(--ink-faint);
}
.pa-meter-fill.is-over {
  fill: var(--action);
  opacity: 1;
}
.pa-meter-fill.is-under {
  fill: var(--warn);
  opacity: 1;
}
.pa-meter-value {
  color: var(--ink);
  font-size: var(--fs-11);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.pa-anom-detail {
  margin: 5px 0 0;
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-anom-info {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: 7px;
  padding: 8px;
  border-radius: 10px;
  background: var(--surface-2);
}
.pa-anom-info div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.pa-anom-info span {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-anom-info strong {
  overflow: hidden;
  font-size: var(--fs-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-anom-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}
.pa-anom-foot .pa-btn {
  padding: 0 11px;
  font-size: var(--fs-12);
}
.pa-search-card {
  padding: 13px;
  margin-bottom: 9px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-search-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.pa-search-box {
  position: relative;
  margin-top: 8px;
}
.pa-search-clear {
  position: absolute;
  top: 50%;
  right: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink-dim);
  font-size: var(--fs-20);
  cursor: pointer;
  transform: translateY(-50%);
}
.pa-search-results {
  position: absolute;
  top: calc(100% + 5px);
  left: 0;
  right: 0;
  z-index: var(--z-shell);
  max-height: 300px;
  overflow: auto;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}
.pa-search-result {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  width: 100%;
  min-height: 44px;
  padding: 6px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}
.pa-search-result:hover {
  background: var(--wb-tint-08);
}
.pa-search-result span {
  font-size: var(--fs-13);
}
.pa-search-result small {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-search-empty {
  margin: 0;
  padding: 10px 8px;
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-filter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin-top: 8px;
}
.pa-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.pa-field > span {
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-recent {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 9px;
}
.pa-recent-label {
  flex: 0 0 auto;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  white-space: nowrap;
}
.pa-recent-chip {
  flex: 0 0 auto;
  min-height: 44px;
  padding: 0 10px;
  white-space: nowrap;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12);
  cursor: pointer;
}
.pa-recent-chip.is-active {
  border-color: var(--action);
  background: var(--wb-tint-14);
  color: var(--ink);
  font-weight: 700;
}
.pa-product-card {
  padding: 14px;
  margin-bottom: 9px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-product-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.pa-product-head > div {
  min-width: 0;
}
.pa-product-head h2 {
  font-size: var(--fs-20);
}
.pa-product-head .pa-btn {
  flex: 0 0 auto;
  padding: 0 10px;
  font-size: var(--fs-12);
}
.pa-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}
.pa-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-11);
  line-height: 1.4;
}
.pa-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 4px;
}
.pa-stats div {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 9px 4px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pa-stats strong {
  font-size: var(--fs-20);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.pa-stats span {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-stats-note {
  margin: 0 2px 8px;
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 12px;
  margin: 9px 0 2px;
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.pa-legend-bar {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: var(--action);
  opacity: .8;
}
.pa-legend-line {
  width: 15px;
  height: 2px;
  border-radius: 2px;
  background: var(--action);
}
.pa-legend-line.is-usage {
  background: var(--ink-dim);
}
.pa-chart-plot {
  position: relative;
  margin-top: 5px;
}
.pa-drill-chart {
  display: block;
  width: 100%;
  height: auto;
}
.pa-chart-y {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 6.67%;
  color: var(--ink-faint);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}
.pa-chart-y.is-left {
  left: 0;
}
.pa-chart-y.is-right {
  right: 0;
  width: 6.39%;
}
.pa-chart-y span {
  position: absolute;
  right: 4px;
  transform: translateY(-50%);
  line-height: 1;
  white-space: nowrap;
}
.pa-chart-y.is-right span {
  right: auto;
  left: 4px;
}
.pa-chart-y span:nth-child(1) {
  top: 6.67%;
}
.pa-chart-y span:nth-child(2) {
  top: 47.92%;
}
.pa-chart-y span:nth-child(3) {
  top: 89.17%;
}
.pa-chart-grid {
  stroke: var(--line);
  stroke-width: 1;
}
.pa-drill-bar {
  fill: var(--action);
  opacity: .72;
}
.pa-chart-line {
  fill: none;
  stroke: var(--action);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.pa-chart-line.is-usage {
  stroke: var(--ink-dim);
  stroke-dasharray: 4 4;
}
.pa-chart-axis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: -4px;
  padding: 0 6.39% 0 6.67%;
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-chart-foot {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: 9px;
  padding-top: 9px;
  border-top: 1px dashed var(--line);
}
.pa-chart-foot div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.pa-chart-foot span {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-chart-foot strong {
  overflow: hidden;
  font-size: var(--fs-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-stock-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr) auto;
  align-items: end;
  gap: 7px;
  margin-top: 9px;
}
.pa-stock-save {
  padding: 0 16px;
}
.pa-stepper {
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
}
.pa-stepper-btn {
  flex: 0 0 auto;
  min-width: 50px;
  min-height: 44px;
  padding: 0 6px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-12);
  font-weight: 700;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.pa-stepper-input {
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  min-height: 44px;
  padding: 0 6px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--surface-1);
  color: var(--ink);
  font-size: var(--fs-14);
  text-align: center;
  box-sizing: border-box;
}
.pa-stock-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 9px;
}
.pa-stock-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 4px 8px;
  border-radius: 8px;
  background: var(--surface-2);
}
.pa-stock-pill small {
  color: var(--ink-dim);
  font-size: var(--fs-11);
}
.pa-stock-pill strong {
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
}
.pa-purchase-list {
  margin-top: 8px;
}
.pa-purchase-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-top: 1px solid var(--line);
}
.pa-purchase-row:first-child {
  border-top: 0;
}
.pa-purchase-date {
  display: flex;
  flex-direction: column;
  flex: 0 0 54px;
  gap: 1px;
}
.pa-purchase-date strong {
  font-size: var(--fs-12);
}
.pa-purchase-date span {
  color: var(--ink-faint);
  font-size: var(--fs-11);
}
.pa-purchase-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  gap: 1px;
  min-width: 0;
}
.pa-purchase-main strong {
  font-size: var(--fs-13);
}
.pa-purchase-main span {
  overflow: hidden;
  color: var(--ink-dim);
  font-size: var(--fs-11);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pa-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: var(--wb-scrim-strong);
}
.pa-scrim-center {
  align-items: center;
  padding: 16px;
  box-sizing: border-box;
}
.pa-sheet {
  width: 100%;
  max-width: 560px;
  max-height: 90vh;
  overflow: auto;
  padding: 16px 16px calc(16px + var(--safe-b));
  border-radius: 20px 20px 0 0;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}
.pa-sheet-head,
.pa-dialog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 11px;
}
.pa-sheet-head h2,
.pa-dialog-head h2 {
  margin: 0;
  font-size: var(--fs-20);
}
.pa-params-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}
.pa-sheet-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.pa-dialog {
  width: min(420px, calc(100vw - 32px));
  max-height: 90vh;
  overflow: auto;
  padding: 16px;
  border: 1px solid var(--line-strong);
  border-radius: 18px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}
.pa-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.pa-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 9px;
  padding: 9px 10px;
  border-radius: 10px;
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
  font-size: var(--fs-12);
}
.pa-banner-btn {
  padding: 0 10px;
  font-size: var(--fs-12);
}
.pa-notice,
.pa-error {
  margin: 7px 0;
  padding: 8px 10px;
  border-radius: 9px;
  background: var(--wb-tint-08);
  color: var(--ink-dim);
  font-size: var(--fs-12);
}
.pa-error {
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}
.pa-empty {
  padding: 32px 12px;
  border: 1px dashed var(--line-strong);
  border-radius: 14px;
  color: var(--ink-dim);
  text-align: center;
  font-size: var(--fs-13);
}
.pa-empty p {
  margin: 0;
}
.pa-empty-line {
  margin: 8px 0;
  color: var(--ink-faint);
  font-size: var(--fs-12);
}
.pa-skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pa-skeleton-tight {
  margin-top: 10px;
}
.pa-skeleton-card {
  height: 122px;
  border-radius: 16px;
  background: var(--surface-2);
  animation: pa-pulse 1.2s ease-in-out infinite;
}
@keyframes pa-pulse {
  0%, 100% { opacity: .55; }
  50% { opacity: 1; }
}
@media (max-width: 900px) {
  .pa-page {
    padding-left: 10px;
    padding-right: 10px;
  }
}
@media (max-width: 560px) {
  .pa-head {
    display: none;
  }
  .pa-export-hint {
    margin-top: 2px;
  }
  .pa-export-icon {
    display: inline-flex;
  }
  .pa-topbar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "date actions"
      "picker picker";
    align-items: start;
    gap: 8px;
    padding: 10px;
  }
  .pa-date-block {
    display: contents;
  }
  .pa-date-copy {
    grid-area: date;
    min-width: 0;
  }
  .pa-date-line {
    gap: 5px;
  }
  .pa-date-line strong {
    font-size: var(--fs-20);
  }
  .pa-date-line span {
    font-size: var(--fs-12);
  }
  .pa-date-sub {
    margin-top: 2px;
  }
  .pa-date-picker {
    grid-area: picker;
    width: 100%;
    flex: none;
  }
  .pa-today-inline {
    grid-column: 1 / -1;
    grid-row: 3;
    justify-self: end;
  }
  .pa-topbar-actions {
    grid-area: actions;
    gap: 4px;
  }
  .pa-hero {
    padding: 13px;
  }
  .pa-hero-value {
    font-size: calc(var(--fs-24) * 1.25);
  }
  .pa-hero-strip {
    margin-top: 11px;
  }
  .pa-card {
    padding: 12px;
  }
  .pa-alert-grid {
    gap: 6px;
  }
  .pa-alert-tile {
    padding: 9px;
  }
  .pa-filter-row {
    grid-template-columns: minmax(0, 1fr) 104px;
  }
  .pa-filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pa-recent {
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .pa-recent::-webkit-scrollbar {
    display: none;
  }
  .pa-stock-form {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  }
  .pa-stock-save {
    grid-column: 1 / -1;
    width: 100%;
  }
  .pa-params-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pa-anom-head {
    flex-wrap: wrap;
  }
}
@media (max-width: 430px) {
  .pa-anom-info {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pa-anom-info div:nth-child(3) {
    grid-column: 1 / -1;
  }
  .pa-stock-form {
    grid-template-columns: minmax(0, 1fr);
  }
  .pa-stock-save {
    width: 100%;
  }
  .pa-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pa-sheet {
    padding-left: 12px;
    padding-right: 12px;
  }
}
</style>
