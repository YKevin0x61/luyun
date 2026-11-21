<script setup>
/**
 * 厨房采购 · 下单（对齐 worktool2 小程序的交互，走服务端）。
 *
 * 口径：
 *  - 「全部」只用于浏览和填数；提交前必须选一个采购岗位，一张单只属于一个岗位。
 *  - 提交时会把该岗位下所有「需盘点」商品的库存写成当天盘点，没填按 0，
 *    同一天重提会覆盖 —— 页面必须把这件事说清楚。
 *  - 参考值 / 建议量由服务端算，页面只负责展示与填数。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client.js'
import { purchaseOrderPath } from '../../utils/purchasePaths.js'
import { formatQty, todayText } from './purchaseUi.js'
import PurchaseNav from './PurchaseNav.vue'

const router = useRouter()

const POSITION_STORAGE_KEY = 'luyun:purchase:position'
const DRAFT_PREFIX = 'luyun:purchase:draft:'

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

const weekday = computed(() => {
  const value = new Date(today + 'T00:00:00')
  if (Number.isNaN(value.getTime())) return ''
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][value.getDay()] || ''
})

const positions = computed(() =>
  (meta.value.positions || []).filter(
    (position) => position.active && position.show_in_order !== false,
  ),
)

const selectedPosition = computed(
  () => positions.value.find((position) => position.id === selectedPositionId.value) || null,
)

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

function rowCode(row) {
  return row.product_code || ''
}

function referenceText(row) {
  if (row.reference === null || row.reference === undefined) return '参考 —'
  if (row.reference === '!') return '参考 !'
  return '参考 ' + formatQty(row.reference) + ' ' + (row.unit || '')
}

function yesterdayText(row) {
  if (row.yesterday_quantity === null || row.yesterday_quantity === undefined) return '昨日 —'
  return '昨日 ' + formatQty(row.yesterday_quantity) + ' ' + (row.unit || '')
}

function suggestedText(row) {
  if (row.suggested_quantity === null || row.suggested_quantity === undefined) return ''
  return '建议 ' + formatQty(row.suggested_quantity) + ' ' + (row.unit || '')
}

function stockText(row) {
  if (row.current_stock === null || row.current_stock === undefined) return '今天未盘'
  return '已盘 ' + formatQty(row.current_stock) + ' ' + (row.unit || '')
}

function stepOf(row) {
  const step = Number(row.order_step)
  return Number.isFinite(step) && step > 0 ? step : 1
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

function adjustStock(row, direction) {
  adjustNumber('stock', row, direction)
}

function adjustQuantity(row, direction) {
  adjustNumber('quantity', row, direction)
}

function normalizeNumber(key, row) {
  const store = key === 'stock' ? stocks : quantities
  const value = Number(store[row.id])
  if (!Number.isFinite(value) || value <= 0) {
    store[row.id] = ''
    return
  }
  store[row.id] = roundValue(value, stepOf(row))
}

function useSuggested(row) {
  const value = Number(row.suggested_quantity)
  if (!Number.isFinite(value) || value <= 0) return
  quantities[row.id] = value
}

function fillSuggested() {
  let count = 0
  for (const row of filteredRows.value) {
    const value = Number(row.suggested_quantity)
    if (row.need_stock_check && Number.isFinite(value) && value > 0) {
      quantities[row.id] = value
      count += 1
    }
  }
  notice.value = count
    ? '已按建议填 ' + count + ' 项需盘商品，可以再手改。'
    : '当前筛选里没有可填的需盘建议量。'
}

function clearQuantities() {
  for (const key of Object.keys(quantities)) quantities[key] = ''
  notice.value = '已清空下单数量（盘点库存保留）。'
}

function resetInputs(list, useDraft = true) {
  for (const key of Object.keys(quantities)) delete quantities[key]
  for (const key of Object.keys(stocks)) delete stocks[key]
  for (const row of list) {
    quantities[row.id] = ''
    stocks[row.id] =
      row.current_stock === null || row.current_stock === undefined ? '' : row.current_stock
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
    const params = { day: today }
    if (selectedPositionId.value) params.position_id = selectedPositionId.value
    const data = await api.get('/api/purchase/reference', params)
    rows.value = data.products || []
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

onMounted(loadMeta)
</script>

<template>
  <div class="po-page">
    <PurchaseNav active="order" />

    <header class="po-head">
      <div>
        <h1>厨房采购</h1>
        <p class="po-sub">
          今天 {{ today }} {{ weekday }} · 「全部」只用于浏览和填数，提交前要选一个岗位
        </p>
      </div>
      <span class="po-pill">{{ selectedPosition ? selectedPosition.name : '全部' }}</span>
    </header>

    <p v-if="errorText" class="po-banner" role="alert">{{ errorText }}</p>
    <p v-else-if="notice" class="po-banner po-banner-soft">{{ notice }}</p>

    <section class="po-card">
      <div class="po-card-head">
        <h2>采购岗位</h2>
        <span class="po-hint">
          {{ rows.length }} 项商品 · 需盘点 {{ needStockRows.length }} 项
        </span>
      </div>
      <div v-if="loadingMeta" class="po-empty">正在加载岗位…</div>
      <div v-else-if="!positions.length" class="po-empty">
        还没有可下单的采购岗位：请店长在「商品与供应商」里建岗位并给商品挂上。
      </div>
      <div v-else class="po-chips">
        <button
          type="button"
          class="po-chip"
          :class="{ 'is-active': selectedPositionId === 0 }"
          @click="choosePosition(0)"
        >
          全部
        </button>
        <button
          v-for="position in positions"
          :key="position.id"
          type="button"
          class="po-chip"
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
        <button type="button" class="po-btn" @click="fillSuggested">按建议填需盘</button>
        <button type="button" class="po-btn po-btn-ghost" @click="clearQuantities">清空数量</button>
      </div>
      <p class="po-note">
        <template v-if="selectedPosition">
          提交时会把「{{ selectedPosition.name }}」下<strong>所有需盘点商品</strong>的库存写成今天的盘点，
          没填的按 0；同一天再提交一次会覆盖上一次的盘点。
        </template>
        <template v-else>
          现在看的是全部商品。提交前会让你选一个岗位，只提交那个岗位的商品和盘点；
          其他岗位填的数量会留在本页草稿里。
        </template>
      </p>
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
        <div class="po-product-main">
          <div class="po-product-title">
            <strong>{{ row.name }}</strong>
            <span v-if="row.specification" class="po-spec">{{ row.specification }}</span>
            <span v-if="row.is_long_cycle" class="po-badge po-badge-soft">长周期</span>
          </div>
          <p class="po-product-meta">
            <span class="po-code">{{ rowCode(row) }}</span>
            <span>{{ row.category_name || '未分分类' }}</span>
            <span v-if="row.need_stock_check" class="po-badge">需盘点</span>
            <span v-if="row.positions && row.positions.length" class="po-positions">
              {{ row.positions.map((item) => item.name).join('、') }}
            </span>
          </p>
          <p class="po-product-ref">
            <span :class="{ 'po-ref-warn': row.reference === '!' }">{{ referenceText(row) }}</span>
            <span> · {{ yesterdayText(row) }}</span>
            <template v-if="row.target_tag"> · 标签 {{ row.target_tag }}</template>
            <template v-if="row.current_stock !== null && row.current_stock !== undefined">
              · 今日库存 {{ formatQty(row.current_stock) }}
            </template>
          </p>
        </div>

        <div class="po-controls" :class="{ 'is-single': !row.need_stock_check }">
          <div v-if="row.need_stock_check" class="po-control">
            <div class="po-control-head">
              <span>盘点库存</span>
              <em>{{ stockText(row) }}</em>
            </div>
            <div class="po-stepper">
              <button type="button" class="po-step" @click="adjustStock(row, -10)">-10</button>
              <button type="button" class="po-step" @click="adjustStock(row, -1)">-1</button>
              <input
                v-model="stocks[row.id]"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                placeholder="未填按 0"
                :aria-label="row.name + ' 盘点库存'"
                @blur="normalizeNumber('stock', row)"
              />
              <button type="button" class="po-step" @click="adjustStock(row, 1)">+1</button>
              <button type="button" class="po-step" @click="adjustStock(row, 10)">+10</button>
            </div>
          </div>

          <div class="po-control">
            <div class="po-control-head">
              <span>下单数量</span>
              <em v-if="suggestedText(row)">{{ suggestedText(row) }}</em>
            </div>
            <div class="po-stepper">
              <button type="button" class="po-step" @click="adjustQuantity(row, -10)">-10</button>
              <button type="button" class="po-step" @click="adjustQuantity(row, -1)">-1</button>
              <input
                v-model="quantities[row.id]"
                type="number"
                inputmode="decimal"
                min="0"
                step="any"
                placeholder="0"
                :aria-label="row.name + ' 下单数量'"
                @blur="normalizeNumber('quantity', row)"
              />
              <button type="button" class="po-step" @click="adjustQuantity(row, 1)">+1</button>
              <button type="button" class="po-step" @click="adjustQuantity(row, 10)">+10</button>
            </div>
            <button
              v-if="row.suggested_quantity !== null && row.suggested_quantity !== undefined"
              type="button"
              class="po-suggest"
              @click="useSuggested(row)"
            >
              用建议 {{ formatQty(row.suggested_quantity) }}
            </button>
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
.po-card,
.po-supplier {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
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
.po-note {
  margin: 10px 0 0;
  padding: 8px 10px;
  border-left: 3px solid var(--action);
  border-radius: 0 8px 8px 0;
  background: var(--wb-tint-08);
  font-size: var(--fs-12);
  opacity: 0.78;
}
.po-supplier-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
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
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 380px);
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px dashed var(--line);
}
.po-product:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.po-product-title {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
}
.po-product-title strong {
  font-size: var(--fs-14);
}
.po-spec {
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.6;
}
.po-product-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 4px 0 0;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.68;
}
.po-code {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.4px;
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
.po-positions {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.po-product-ref {
  margin: 5px 0 0;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.62;
}
.po-ref-warn {
  color: var(--danger-bright);
  font-weight: 700;
  opacity: 1;
}
.po-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}
.po-controls.is-single {
  grid-template-columns: minmax(0, 1fr);
}
.po-control {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.po-control-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.65;
}
.po-control-head em {
  font-style: normal;
  white-space: nowrap;
}
.po-stepper {
  display: grid;
  grid-template-columns: 44px 44px minmax(0, 1fr) 44px 44px;
  gap: 4px;
}
.po-stepper input {
  min-width: 0;
  min-height: 44px;
  padding: 7px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.po-step {
  min-height: 44px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-12);
  cursor: pointer;
}
.po-suggest {
  align-self: flex-start;
  min-height: 44px;
  padding: 4px 8px;
  border: 1px dashed var(--line-strong);
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-11);
  white-space: nowrap;
  cursor: pointer;
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
@media (max-width: 900px) {
  .po-product {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 560px) {
  .po-page {
    padding-bottom: 132px;
  }
  .po-head h1 {
    font-size: var(--fs-20);
  }
  .po-controls {
    grid-template-columns: minmax(0, 1fr);
  }
  .po-stepper {
    grid-template-columns: 44px 44px minmax(0, 1fr) 44px 44px;
  }
  .po-sticky-text {
    font-size: var(--fs-12);
  }
  .po-submit {
    padding: 9px 14px;
  }
}
</style>
