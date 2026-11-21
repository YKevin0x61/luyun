<script setup>
/**
 * 厨房采购 · 采购单与今日汇总。
 *
 * 一页两个视角（?tab=summary 切换）：单据列表按提交日筛；汇总按「一天的采购量」
 * 按供应商 / 分类 / 商品折起来，导出 Excel 与打印都从这里走。
 * 打印标题只写提交日期（第三轮 q6），不写「次日用料」。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/client.js'
import { purchaseOrderPath } from '../../utils/purchasePaths.js'
import {
  formatDateTime,
  formatMonthDay,
  formatQty,
  statusText,
  todayText,
} from './purchaseUi.js'
import PurchaseNav from './PurchaseNav.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'

const route = useRoute()

const today = todayText()
const activeTab = computed(() => (route.query.tab === 'summary' ? 'summary' : 'orders'))

const meta = ref({ positions: [] })
const dateFrom = ref(today)
const dateTo = ref(today)
const statusFilter = ref('')
const positionFilter = ref(0)
const page = ref(1)
const orders = ref([])
const total = ref(0)
const pageSize = ref(10)
const loadingOrders = ref(false)

const summaryDay = ref(today)
const summaryPositionIds = ref([])
const summary = ref(null)
const loadingSummary = ref(false)
const exporting = ref(false)

const errorText = ref('')

const positions = computed(() => (meta.value.positions || []).filter((item) => item.active))
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / (pageSize.value || 10))))
const summaryPositionNames = computed(() => {
  if (!summaryPositionIds.value.length) return '全部岗位'
  return positions.value
    .filter((item) => summaryPositionIds.value.includes(item.id))
    .map((item) => item.name)
    .join('、')
})
const summaryPositionQuery = computed(() =>
  summaryPositionIds.value.length ? summaryPositionIds.value.join(',') : undefined,
)

function statusClass(status) {
  return 'is-' + (status || 'pending')
}

async function loadMeta() {
  try {
    meta.value = (await api.get('/api/purchase/meta')) || { positions: [] }
  } catch (_) {
    // 岗位清单只是筛选项：拿不到也让单据列表先出来。
  }
}

async function loadOrders() {
  if (activeTab.value !== 'orders') return
  loadingOrders.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/purchase/orders', {
      day_from: dateFrom.value,
      day_to: dateTo.value,
      status: statusFilter.value || undefined,
      position_id: positionFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    orders.value = data.orders || []
    total.value = Number(data.total || 0)
    pageSize.value = Number(data.page_size || 10)
  } catch (err) {
    errorText.value = err.message || '采购单没加载出来'
  } finally {
    loadingOrders.value = false
  }
}

async function loadSummary() {
  loadingSummary.value = true
  errorText.value = ''
  try {
    summary.value = await api.get('/api/purchase/summary', {
      day: summaryDay.value,
      position_ids: summaryPositionQuery.value,
    })
  } catch (err) {
    summary.value = null
    errorText.value = err.message || '今日汇总没加载出来'
  } finally {
    loadingSummary.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadOrders()
}

function toggleSummaryPosition(positionId) {
  if (!positionId) {
    summaryPositionIds.value = []
  } else if (summaryPositionIds.value.includes(positionId)) {
    summaryPositionIds.value = summaryPositionIds.value.filter((id) => id !== positionId)
  } else {
    summaryPositionIds.value = [...summaryPositionIds.value, positionId].sort((a, b) => a - b)
  }
  loadSummary()
}

function shiftPage(delta) {
  const next = page.value + delta
  if (next < 1 || next > totalPages.value) return
  page.value = next
  loadOrders()
}

async function exportSummary() {
  exporting.value = true
  errorText.value = ''
  try {
    const query = new URLSearchParams({ day: summaryDay.value })
    if (summaryPositionQuery.value) {
      query.set('position_ids', summaryPositionQuery.value)
    }
    await api.download(
      '/api/purchase/summary/export?' + query.toString(),
      'purchase-summary-' + summaryDay.value + '.xlsx',
    )
  } catch (err) {
    errorText.value = err.message || 'Excel 没导出来'
  } finally {
    exporting.value = false
  }
}

function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * 打印视图放在一个空白窗口里：工作台外壳（顶栏 / 底栏 / 导航）不参与打印，
 * 也不必为了打印往页面里塞一套 @media print 的全局样式。
 */
function printSummary() {
  if (!summary.value) return
  const data = summary.value
  const lines = []
  lines.push('<h1>厨房采购汇总</h1>')
  lines.push('<p class="meta">' + escapeHtml(data.date) + ' · ' + escapeHtml(summaryPositionNames.value) + '</p>')
  for (const supplier of data.suppliers || []) {
    lines.push('<h2>' + escapeHtml(supplier.supplier_name) + '</h2>')
    lines.push('<table><thead><tr><th>商品编码</th><th>商品</th><th>规格</th><th>单位</th><th>数量</th><th>库存快照</th><th>下单人</th><th>岗位</th></tr></thead><tbody>')
    for (const category of supplier.categories || []) {
      for (const product of category.products || []) {
        const positionText = (product.positions || [])
          .map((item) => item.position_name + ' ' + formatQty(item.quantity))
          .join(' / ')
        const stockText = product.stock_qty === null || product.stock_qty === undefined
          ? '—'
          : formatQty(product.stock_qty)
        const submitterText = (product.submitters || []).join('、') || '—'
        lines.push(
          '<tr><td>' + escapeHtml(product.product_code) + '</td><td>' +
          escapeHtml(product.name) + '</td><td>' + escapeHtml(product.specification) +
          '</td><td>' + escapeHtml(product.unit) + '</td><td class="num">' +
          escapeHtml(formatQty(product.quantity)) + '</td><td class="num">' +
          escapeHtml(stockText) + '</td><td>' + escapeHtml(submitterText) +
          '</td><td>' + escapeHtml(positionText) + '</td></tr>',
        )
      }
    }
    lines.push('</tbody></table>')
  }
  lines.push('<p class="total">合计 ' + escapeHtml(formatQty(data.total_quantity)) + ' · ' +
    escapeHtml(data.order_count || 0) + ' 张单</p>')

  const win = window.open('', '_blank', 'width=900,height=700')
  if (!win) {
    errorText.value = '浏览器拦住了打印窗口：请允许本站弹出窗口再试。'
    return
  }
  win.document.write(
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">' +
    '<title>厨房采购汇总 ' + escapeHtml(data.date) + '</title>' +
    '<style>body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif;' +
    'color:black;margin:28px;font-size:10pt}h1{font-size:18pt;margin:0 0 4px}' +
    '.meta{color:dimgray;margin:0 0 18px}h2{font-size:12pt;margin:16px 0 6px;' +
    'border-bottom:1px solid darkgray;padding-bottom:4px}table{width:100%;border-collapse:collapse}' +
    'th,td{text-align:left;padding:4px 6px;border-bottom:1px solid gainsboro;vertical-align:top}' +
    'th{font-weight:600;background:whitesmoke}.num{text-align:right;font-variant-numeric:tabular-nums}' +
    '.total{margin-top:14px;font-size:10.5pt;font-weight:600}@media print{body{margin:10mm}}' +
    '</style></head><body>' + lines.join('') + '</body></html>',
  )
  win.document.close()
  win.focus()
  win.setTimeout(() => {
    win.print()
  }, 250)
}

watch(activeTab, (value) => {
  if (value === 'summary' && !summary.value) loadSummary()
  if (value === 'orders') loadOrders()
})

watch(summaryDay, () => {
  if (activeTab.value === 'summary') loadSummary()
})

onMounted(async () => {
  await loadMeta()
  if (activeTab.value === 'summary') await loadSummary()
  else await loadOrders()
})
</script>

<template>
  <div class="pl-page">
    <PurchaseNav :active="activeTab" />

    <header class="pl-head">
      <div>
        <h1>{{ activeTab === 'summary' ? '今日采购汇总' : '采购单' }}</h1>
        <p class="pl-sub">
          {{ activeTab === 'summary'
            ? '按供应商 / 分类 / 商品折起来，导出 Excel 或打印给供应商'
            : '按提交日期查；点一行看明细、改单或完成核对' }}
        </p>
      </div>
    </header>

    <p v-if="errorText" class="pl-banner" role="alert">{{ errorText }}</p>

    <template v-if="activeTab === 'orders'">
      <section class="pl-filters">
        <label class="pl-field">
          <span>从</span>
          <LuyunDatePicker v-model="dateFrom" aria-label="开始日期" />
        </label>
        <label class="pl-field">
          <span>到</span>
          <LuyunDatePicker v-model="dateTo" aria-label="结束日期" />
        </label>
        <label class="pl-field">
          <span>状态</span>
          <select v-model="statusFilter" class="pl-select">
            <option value="">全部</option>
            <option value="pending">待处理</option>
            <option value="completed">已完成</option>
            <option value="cancelled">已取消</option>
          </select>
        </label>
        <label class="pl-field">
          <span>岗位</span>
          <select v-model.number="positionFilter" class="pl-select">
            <option :value="0">全部岗位</option>
            <option v-for="position in positions" :key="position.id" :value="position.id">
              {{ position.name }}
            </option>
          </select>
        </label>
        <button type="button" class="pl-btn pl-btn-primary" @click="applyFilters">查询</button>
        <button type="button" class="pl-btn" :disabled="loadingOrders" @click="loadOrders">
          {{ loadingOrders ? '刷新中…' : '刷新' }}
        </button>
      </section>

      <div v-if="loadingOrders" class="pl-empty">正在加载采购单…</div>
      <div v-else-if="!orders.length" class="pl-empty">这个范围里还没有采购单。</div>
      <div v-else class="pl-list">
        <article
          v-for="order in orders"
          :key="order.id"
          class="pl-order"
          role="button"
          tabindex="0"
          @click="$router.push(purchaseOrderPath(order.id))"
          @keydown.enter="$router.push(purchaseOrderPath(order.id))"
          @keydown.space.prevent="$router.push(purchaseOrderPath(order.id))"
        >
          <div class="pl-order-top">
            <span class="pl-order-no">{{ order.order_no }}</span>
            <span class="pl-status" :class="statusClass(order.status)">{{ statusText(order.status) }}</span>
          </div>
          <div class="pl-order-main">
            <strong>{{ order.position_name }}</strong>
            <span>{{ order.submitter_name }}</span>
            <span>{{ formatDateTime(order.submitted_at) }}</span>
          </div>
          <div class="pl-order-meta">
            <span>{{ order.item_count || 0 }} 项</span>
            <span>合计 {{ formatQty(order.total_quantity) }}</span>
            <template v-if="order.completed_at">· 完成 {{ formatMonthDay(order.completed_at) }}</template>
            <template v-else-if="order.cancelled_at">· 取消 {{ formatMonthDay(order.cancelled_at) }}</template>
          </div>
        </article>
      </div>

      <div v-if="total > pageSize" class="pl-pager">
        <button type="button" class="pl-btn" :disabled="page <= 1" @click="shiftPage(-1)">上一页</button>
        <span>{{ page }} / {{ totalPages }}（共 {{ total }} 张）</span>
        <button type="button" class="pl-btn" :disabled="page >= totalPages" @click="shiftPage(1)">下一页</button>
      </div>
    </template>

    <template v-else>
      <section class="pl-filters pl-summary-tools">
        <label class="pl-field">
          <span>日期</span>
          <LuyunDatePicker v-model="summaryDay" aria-label="汇总日期" />
        </label>
        <button type="button" class="pl-btn" :disabled="exporting" @click="exportSummary">
          {{ exporting ? '导出中…' : '导出 Excel' }}
        </button>
        <button type="button" class="pl-btn pl-btn-primary" :disabled="!summary" @click="printSummary">
          打印
        </button>
        <button type="button" class="pl-btn" :disabled="loadingSummary" @click="loadSummary">
          {{ loadingSummary ? '刷新中…' : '刷新' }}
        </button>
      </section>

      <div class="pl-summary-filter">
        <span class="pl-positions-label">岗位</span>
        <button
          type="button"
          class="pl-chip"
          :class="{ 'is-on': !summaryPositionIds.length }"
          @click="toggleSummaryPosition(0)"
        >全部</button>
        <button
          v-for="position in positions"
          :key="position.id"
          type="button"
          class="pl-chip"
          :class="{ 'is-on': summaryPositionIds.includes(position.id) }"
          @click="toggleSummaryPosition(position.id)"
        >{{ position.name }}</button>
      </div>

      <div v-if="loadingSummary" class="pl-empty">正在汇总…</div>
      <div v-else-if="!summary" class="pl-empty">这一天还没有汇总。</div>
      <template v-else>
        <div class="pl-stats">
          <div class="pl-stat"><span>采购单</span><strong>{{ summary.order_count || 0 }}</strong></div>
          <div class="pl-stat"><span>商品项次</span><strong>{{ summary.item_count || 0 }}</strong></div>
          <div class="pl-stat"><span>合计数量</span><strong>{{ formatQty(summary.total_quantity) }}</strong></div>
        </div>

        <div v-if="(summary.positions || []).length" class="pl-positions">
          <span class="pl-positions-label">岗位合计</span>
          <span v-for="position in summary.positions" :key="position.position_name" class="pl-position">
            {{ position.position_name }} {{ formatQty(position.quantity) }}
          </span>
        </div>

        <div v-if="!(summary.suppliers || []).length" class="pl-empty">这一天还没有有效采购。</div>
        <section v-for="supplier in summary.suppliers" :key="supplier.supplier_name" class="pl-supplier">
          <header class="pl-supplier-head">
            <h2>{{ supplier.supplier_name }}</h2>
            <span>{{ formatQty(supplier.quantity) }}</span>
          </header>
          <div v-for="category in supplier.categories" :key="category.category_name" class="pl-category">
            <h3>{{ category.category_name }}</h3>
            <div v-for="product in category.products" :key="product.product_id" class="pl-row">
              <div class="pl-row-main">
                <strong>{{ product.name }}</strong>
                <span v-if="product.specification" class="pl-row-spec">{{ product.specification }}</span>
                <span class="pl-row-code">{{ product.product_code }}</span>
              </div>
              <div class="pl-row-qty">{{ formatQty(product.quantity) }} <small>{{ product.unit }}</small></div>
              <div class="pl-row-positions">
                <span v-for="item in product.positions" :key="item.position_name">
                  {{ item.position_name }} {{ formatQty(item.quantity) }}
                </span>
              </div>
              <div class="pl-row-extra">
                <span>库存快照 {{ formatQty(product.stock_qty) }}</span>
                <span>下单人 {{ (product.submitters || []).join('、') || '—' }}</span>
              </div>
            </div>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.pl-page {
  width: 100%;
  min-width: 0;
  max-width: 980px;
  margin: 0 auto;
  padding: 10px 12px 40px;
  color: var(--ink);
  font-size: var(--fs-14);
  line-height: 1.45;
}
.pl-head {
  padding: 2px 0 12px;
}
.pl-head h1 {
  margin: 0;
  font-size: var(--fs-24);
  line-height: 1.25;
}
.pl-sub {
  margin: 3px 0 0;
  color: var(--ink);
  font-size: var(--fs-13);
  opacity: 0.62;
}
.pl-banner {
  margin: 0 0 10px;
  padding: 9px 12px;
  border: 1px solid var(--danger-bright);
  border-radius: 10px;
  background: var(--wb-danger-a10);
  font-size: var(--fs-13);
}
.pl-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
}
.pl-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pl-field > span {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.62;
}
.pl-select {
  min-height: 44px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
}
.pl-btn {
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
.pl-btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pl-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.pl-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pl-order {
  display: block;
  width: 100%;
  min-height: 44px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  box-shadow: var(--paper-shadow-sm);
}
.pl-order:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.pl-order-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pl-order-no {
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
  opacity: 0.6;
}
.pl-status {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: var(--fs-11);
  font-weight: 600;
}
.pl-status.is-pending {
  background: var(--wb-warn-a10);
}
.pl-status.is-completed {
  background: var(--wb-ok-a10);
}
.pl-status.is-cancelled {
  background: var(--wb-danger-a10);
  opacity: 0.75;
}
.pl-order-main {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin-top: 5px;
  font-size: var(--fs-13);
}
.pl-order-main strong {
  font-size: var(--fs-16);
}
.pl-order-main span {
  opacity: 0.62;
  font-size: var(--fs-12);
}
.pl-order-meta {
  margin-top: 4px;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.6;
}
.pl-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
  font-size: var(--fs-12);
}
.pl-summary-tools {
  align-items: flex-end;
}
.pl-summary-filter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: -4px 0 12px;
}
.pl-chip {
  min-height: 44px;
  padding: 5px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-12);
  cursor: pointer;
}
.pl-chip.is-on {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pl-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.pl-stat {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--surface-1);
}
.pl-stat span {
  display: block;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.6;
}
.pl-stat strong {
  display: block;
  margin-top: 3px;
  font-size: var(--fs-24);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.pl-positions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}
.pl-positions-label {
  font-size: var(--fs-12);
  opacity: 0.6;
}
.pl-position {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--wb-tint-14);
  font-size: var(--fs-12);
}
.pl-supplier {
  margin-bottom: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  overflow: hidden;
  box-shadow: var(--paper-shadow-sm);
}
.pl-supplier-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-2);
}
.pl-supplier-head h2 {
  margin: 0;
  font-size: var(--fs-14);
}
.pl-supplier-head span {
  font-size: var(--fs-13);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.pl-category {
  padding: 8px 14px 10px;
}
.pl-category h3 {
  margin: 0 0 4px;
  color: var(--ink);
  font-size: var(--fs-12);
  font-weight: 600;
  opacity: 0.6;
}
.pl-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line);
}
.pl-row:last-child {
  border-bottom: 0;
}
.pl-row-main {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
  font-size: var(--fs-14);
}
.pl-row-spec,
.pl-row-code {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.55;
}
.pl-row-code {
  font-variant-numeric: tabular-nums;
}
.pl-row-qty {
  font-size: var(--fs-14);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.pl-row-qty small {
  font-size: var(--fs-11);
  font-weight: 400;
  opacity: 0.6;
}
.pl-row-positions {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.58;
}
.pl-row-positions span {
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--wb-tint-08);
}
.pl-row-extra {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.6;
}
.pl-empty {
  padding: 24px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  color: var(--ink);
  font-size: var(--fs-13);
  text-align: center;
  opacity: 0.68;
}
@media (max-width: 560px) {
  .pl-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .pl-stat strong {
    font-size: var(--fs-20);
  }
}
</style>
