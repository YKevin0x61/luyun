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
const summaryPositionId = ref(0)
const summary = ref(null)
const loadingSummary = ref(false)
const exporting = ref(false)

const errorText = ref('')
const noticeText = ref('')

// ── 小票打印（商鹏云云打印） ──────────────────────────────────
// 和上面的 printSummary() 是两条路：这条把同一份汇总排成 58mm 热敏文本，
// 经云打印平台推给店里的小票机；浏览器打印留给 A4 / PDF。
// 每次点「打小票」都会真出纸（平台接口没有幂等键），所以按钮要防连点。
const printerOpen = ref(false)
const printerSaving = ref(false)
const printingTicket = ref(false)
const testingTicket = ref(false)
const checkingPrinter = ref(false)
const printerError = ref('')
const printerNotice = ref('')
const previewText = ref('')
const previewScale = ref('tall')
const printerInfo = ref(null)
const printer = ref({
  enabled: false,
  configured: false,
  appid: '',
  sn: '',
  paper_width: 58,
  times: 1,
  font_scale: 'tall',
  has_appsecret: false,
  has_pkey: false,
})
const printerForm = ref({
  appid: '',
  appsecret: '',
  sn: '',
  pkey: '',
  paper_width: 58,
  times: 1,
  font_scale: 'tall',
  enabled: false,
})
// 预览按真实字号显示：放大一倍的字在 58mm 上换行更多，预览字号不跟着变
// 就会让人以为「字变大了、行数没变」，纸上完全不是这样。四档都走字号令牌
// （`data-scale` 选择器在 <style scoped> 里），不在这里写死 px。

const printerHint = computed(() => {
  if (!printer.value.configured) return '未配置商鹏云打印机'
  if (!printer.value.enabled) return '已配置，未开启'
  const scaleText = { normal: '正常字', tall: '变高', big: '放大一倍', huge: '放大两倍' }
  return '商鹏云 · ' + printer.value.paper_width + 'mm · ' +
    (scaleText[printer.value.font_scale] || '大')
})

// 型号：平台直接给了机型名（SP-T5-WA）就用它，只有数字时翻成纸宽说法，
// 免得界面上出现「型号 0」。
const PRINTER_MODELS = {
  0: '58mm 热敏',
  2: '出餐宝',
  3: '58mm 票据+扫码',
  4: '80mm 热敏',
  5: '桌面标签',
}
const printerModelText = computed(() => {
  const info = printerInfo.value
  if (!info) return ''
  if (info.model_name) return info.model_name
  return PRINTER_MODELS[info.model_type] || ''
})

async function loadPrinter() {
  try {
    printer.value = await api.get('/api/purchase/printer')
  } catch (_) {
    // 读不到就按「未配置」显示：按钮仍可点，点开弹窗再报具体原因。
  }
}

function openPrinter() {
  printerError.value = ''
  printerNotice.value = ''
  previewText.value = ''
  printerInfo.value = null
  printerForm.value = {
    appid: printer.value.appid || '',
    appsecret: '',
    sn: printer.value.sn || '',
    pkey: '',
    paper_width: printer.value.paper_width || 58,
    times: printer.value.times || 1,
    font_scale: printer.value.font_scale || 'tall',
    enabled: !!printer.value.enabled,
  }
  printerOpen.value = true
}

function closePrinter() {
  printerOpen.value = false
}

async function savePrinter() {
  if (printerSaving.value) return
  printerSaving.value = true
  printerError.value = ''
  printerNotice.value = ''
  try {
    const payload = {
      appid: printerForm.value.appid,
      sn: printerForm.value.sn,
      paper_width: Number(printerForm.value.paper_width),
      times: Number(printerForm.value.times),
      font_scale: printerForm.value.font_scale,
      enabled: !!printerForm.value.enabled,
    }
    // 密钥不回显：留空就是不修改，只有真填了才发上去。
    if (printerForm.value.appsecret) payload.appsecret = printerForm.value.appsecret
    if (printerForm.value.pkey) payload.pkey = printerForm.value.pkey
    printer.value = await api.put('/api/purchase/printer', payload)
    printerForm.value.appsecret = ''
    printerForm.value.pkey = ''
    printerNotice.value = printer.value.configured
      ? '已保存。点「打测试票」确认机器能出纸。'
      : '已保存，但还缺 appid / appsecret / SN。'
  } catch (err) {
    printerError.value = err.message || '保存失败'
  } finally {
    printerSaving.value = false
  }
}

async function testPrinter() {
  if (testingTicket.value) return
  testingTicket.value = true
  printerError.value = ''
  printerNotice.value = ''
  try {
    const data = await api.post('/api/purchase/printer/test', undefined, undefined, { timeoutMs: 30000 })
    printerNotice.value = '测试票已推送' +
      (data.order_id ? '（单号 ' + data.order_id + '）' : '') + '，看一眼机器出没出纸。'
  } catch (err) {
    printerError.value = err.message || '测试票没发出去'
  } finally {
    testingTicket.value = false
  }
}

async function checkPrinter() {
  if (checkingPrinter.value) return
  checkingPrinter.value = true
  printerError.value = ''
  printerNotice.value = ''
  try {
    printerInfo.value = await api.get('/api/purchase/printer/info', undefined, undefined, undefined, { timeoutMs: 20000 })
  } catch (err) {
    printerInfo.value = null
    printerError.value = err.message || '查不到设备状态'
  } finally {
    checkingPrinter.value = false
  }
}

async function previewPrinter() {
  printerError.value = ''
  printerNotice.value = ''
  try {
    const data = await api.get('/api/purchase/summary/ticket/preview', {
      day: summaryDay.value,
      position_ids: summaryPositionQuery.value,
    })
    // 平台标签是给打印机看的：<BR> 换行、<CUT> 切纸。预览里换成真换行，
    // 否则整张票挤成一段，看不出排版对不对（宽度、缩进、有没有串行）。
    previewText.value = (data.text || '')
      .replace(/<BR>/gi, '\n')
      .replace(/<CUT>/gi, '')
      .replace(/<\/?(?:L1|L2|H|W|B|C)>/gi, '')
      .replace(/\n+$/, '')
    previewScale.value = data.font_scale || printerForm.value.font_scale || 'tall'
    const paper = data.paper_width || printer.value.paper_width || 58
    const size = data.bytes ? data.bytes + ' / 5000 字节' : ''
    if (data.truncated) {
      printerNotice.value = '放不下：这张票只打了 ' + data.printed_products +
        ' 项（共 ' + data.total_products + ' 项），完整清单看 Excel 导出。' +
        (size ? ' 已用 ' + size + '。' : '')
    } else {
      printerNotice.value = paper + 'mm 排版，共 ' + (data.lines || 0) + ' 行' +
        (size ? '、' + size : '') + '。'
    }
  } catch (err) {
    previewText.value = ''
    printerError.value = err.message || '预览没出来'
  }
}

async function printTicket() {
  if (printingTicket.value) return
  printingTicket.value = true
  errorText.value = ''
  noticeText.value = ''
  try {
    const data = await api.post('/api/purchase/summary/ticket', undefined, {
      day: summaryDay.value,
      position_ids: summaryPositionQuery.value,
    }, { timeoutMs: 30000 })
    let text = '小票已推送（' + (data.printed_products || 0) + ' 项 / ' +
      (data.printed_suppliers || 0) + ' 家供应商），稍等出纸。'
    if (data.truncated) {
      text += ' 内容超过一张，完整清单请看 Excel 导出。'
    }
    noticeText.value = text
  } catch (err) {
    // 409 是状态不是故障（当天没单、开关没开），用提示条说清楚，不弹红条吓人。
    if (err.status === 409) {
      noticeText.value = err.message || '这一天没有要打的内容'
    } else {
      errorText.value = err.message || '小票没打出去'
    }
  } finally {
    printingTicket.value = false
  }
}

const positions = computed(() => (meta.value.positions || []).filter((item) => item.active))
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / (pageSize.value || 10))))
const summaryPositionNames = computed(() => {
  if (!summaryPositionId.value) return '全部岗位'
  return positions.value.find((item) => item.id === summaryPositionId.value)?.name || '全部岗位'
})
const summaryPositionQuery = computed(() =>
  summaryPositionId.value ? String(summaryPositionId.value) : undefined,
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
 *
 * 内容照小程序「今日汇总」的打印预览排：日期标题、下单人员、按供应商 /
 * 分类 / 商品折起来、每个供应商的采购种类、整体的供应商与种类总数、
 * 打印时间。热敏纸 58mm 与 A4 都用同一份文本，不再另做一套表格。
 */
function printSummary() {
  if (!summary.value) return
  const data = summary.value
  const blocks = []
  const submitters = []
  const seenSubmitters = new Set()
  let totalSuppliers = 0
  let totalTypes = 0

  for (const supplier of data.suppliers || []) {
    const groups = []
    let supplierTypeCount = 0
    for (const category of supplier.categories || []) {
      const products = (category.products || []).filter((product) => Number(product.quantity) > 0)
      if (!products.length) continue
      groups.push({ name: category.category_name || '未分类', products })
      supplierTypeCount += products.length
      for (const product of products) {
        for (const submitter of product.submitters || []) {
          const name = String(submitter || '').trim()
          if (name && !seenSubmitters.has(name)) {
            seenSubmitters.add(name)
            submitters.push(name)
          }
        }
      }
    }
    if (!groups.length) continue
    totalSuppliers += 1
    totalTypes += supplierTypeCount

    let block = '【' + (supplier.supplier_name || '') + '】\n'
    block += '------------------------\n'
    for (const group of groups) {
      block += '[' + group.name + ']\n'
      for (const product of group.products) {
        const stock = product.stock_qty === null || product.stock_qty === undefined
          ? ''
          : '【库' + formatQty(product.stock_qty) + '】'
        block += '　　' + (product.name || '') + '： ' +
          formatQty(product.quantity) + (product.unit || '') + stock + '\n'
      }
    }
    block += '----\n'
    block += '采购种类：' + supplierTypeCount + '种\n'
    block += '------------------------\n'
    blocks.push(block)
  }

  let text = data.date + ' 采购汇总\n'
  text += '========================\n'
  if (submitters.length) text += '下单人员：' + submitters.join('、') + '\n'
  text += '========================\n\n'
  text += blocks.join('\n')
  text += '========================\n'
  text += '供应商总数：' + totalSuppliers + '家\n'
  text += '采购种类总数：' + totalTypes + '种\n'
  text += '========================\n'
  text += '打印时间：' + printTimeText() + '\n'

  const win = window.open('', '_blank', 'width=900,height=700')
  if (!win) {
    errorText.value = '浏览器拦住了打印窗口：请允许本站弹出窗口再试。'
    return
  }
  win.document.write(
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">' +
    '<title>' + escapeHtml(data.date) + ' 采购汇总</title>' +
    '<style>body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif;' +
    'color:black;margin:28px;font-size:10pt}pre{margin:0;font-family:"SFMono-Regular",Menlo,Consolas,' +
    '"PingFang SC","Hiragino Sans GB",monospace;font-size:10pt;line-height:1.65;' +
    'white-space:pre-wrap;word-break:break-word}@media print{body{margin:10mm}}</style>' +
    '</head><body><pre>' + escapeHtml(text) + '</pre></body></html>',
  )
  win.document.close()
  win.focus()
  win.setTimeout(() => {
    win.print()
  }, 250)
}

function printTimeText(now = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  return now.getFullYear() + '/' + (now.getMonth() + 1) + '/' + now.getDate() +
    ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds())
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
  await loadPrinter()
  if (activeTab.value === 'summary') await loadSummary()
  else await loadOrders()
})
</script>

<template>
  <div class="pl-page">
    <PurchaseNav :active="activeTab" />

    <header class="pl-head">
      <div class="pl-head-text">
        <h1>{{ activeTab === 'summary' ? '今日采购汇总' : '采购单' }}</h1>
        <p class="pl-sub">
          {{ activeTab === 'summary'
            ? '按供应商 / 分类 / 商品折起来，导出 Excel 或打印给供应商'
            : '按提交日期查；点一行看明细、改单或完成核对' }}
        </p>
      </div>
      <div class="pl-head-count">
        <strong>{{ activeTab === 'summary' ? (summary?.order_count || 0) : total }}</strong>
        <small>张单</small>
      </div>
    </header>

    <p v-if="errorText" class="pl-banner" role="alert">{{ errorText }}</p>
    <p v-if="noticeText" class="pl-notice" role="status">{{ noticeText }}</p>

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
          <header class="pl-order-head">
            <div class="pl-order-person">
              <span class="pl-person-dot" aria-hidden="true"></span>
              <strong>{{ order.submitter_name || '未知用户' }}</strong>
              <span class="pl-order-pos">{{ order.position_name }}</span>
            </div>
            <span class="pl-status" :class="statusClass(order.status)">{{ statusText(order.status) }}</span>
          </header>
          <div class="pl-order-items">
            <div
              v-for="item in (order.items || []).slice(0, 6)"
              :key="item.product_code + ':' + item.name"
              class="pl-order-item"
            >
              <span class="pl-item-name">{{ item.name }}</span>
              <span
                v-if="item.stock_qty !== null && item.stock_qty !== undefined"
                class="pl-item-stock"
              >库:{{ formatQty(item.stock_qty) }}</span>
              <span class="pl-item-qty">×{{ formatQty(item.quantity) }} <small>{{ item.unit }}</small></span>
            </div>
            <p v-if="(order.items || []).length > 6" class="pl-order-more">
              还有 {{ order.items.length - 6 }} 项，点开看全部
            </p>
          </div>
          <footer class="pl-order-foot">
            <span class="pl-order-no">{{ order.order_no }}</span>
            <span>{{ order.item_count || 0 }} 项 · 合计 {{ formatQty(order.total_quantity) }}</span>
            <span class="pl-order-at">{{ formatDateTime(order.submitted_at) }}</span>
          </footer>
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

      <section class="pl-ticket-bar">
        <button
          type="button"
          class="pl-btn pl-btn-primary"
          :disabled="printingTicket || !summary"
          @click="printTicket"
        >
          {{ printingTicket ? '推送中…' : '打小票' }}
        </button>
        <button type="button" class="pl-btn" @click="openPrinter">打印机</button>
        <span class="pl-ticket-hint">{{ printerHint }}</span>
      </section>

      <label class="pl-summary-filter">
        <span class="pl-positions-label">岗位</span>
        <select v-model.number="summaryPositionId" class="pl-select pl-summary-select" @change="loadSummary">
          <option :value="0">全部岗位</option>
          <option v-for="position in positions" :key="position.id" :value="position.id">
            {{ position.name }}
          </option>
        </select>
      </label>

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
        <section v-else class="pl-paper" aria-label="今日采购汇总明细">
          <header class="pl-paper-head">
            <span>{{ summary.day || summaryDay }} 采购汇总</span>
            <strong>{{ summary.order_count || 0 }} 张单</strong>
          </header>
          <div v-for="supplier in summary.suppliers" :key="supplier.supplier_name" class="pl-paper-supplier">
            <header class="pl-supplier-head">
              <h2>{{ supplier.supplier_name }}</h2>
              <span>{{ formatQty(supplier.quantity) }}</span>
            </header>
            <div v-for="category in supplier.categories" :key="category.category_name" class="pl-category">
              <h3>{{ category.category_name }}</h3>
              <div v-for="product in category.products" :key="product.product_id" class="pl-row">
                <div class="pl-row-main">
                  <strong>{{ product.name }}</strong>
                  <span class="pl-row-code">{{ product.product_code }}</span>
                  <span v-if="product.specification" class="pl-row-spec">{{ product.specification }}</span>
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
          </div>
          <footer class="pl-paper-foot">
            <span>合计</span>
            <strong>{{ formatQty(summary.total_quantity) }}</strong>
          </footer>
        </section>
      </template>
    </template>
    <div
      v-if="printerOpen"
      class="pl-modal"
      role="dialog"
      aria-modal="true"
      aria-label="小票打印机设置"
      @click.self="closePrinter"
    >
      <section class="pl-modal-card">
        <header class="pl-modal-head">
          <h2>小票打印机</h2>
          <button type="button" class="pl-modal-close" aria-label="关闭" @click="closePrinter">×</button>
        </header>
        <div class="pl-modal-body">
          <p class="pl-modal-tip">
            商鹏云云打印（spyun.net）：打印机 4G / WiFi 直连云端，这里填开发者账号与机身标签上的 SN / PKEY 就能出票。
          </p>

          <label class="pl-field">
            <span>appid</span>
            <input v-model.trim="printerForm.appid" class="pl-input" autocomplete="off" />
          </label>
          <label class="pl-field">
            <span>appsecret</span>
            <input
              v-model.trim="printerForm.appsecret"
              class="pl-input"
              type="password"
              autocomplete="new-password"
              :placeholder="printer.has_appsecret ? '已保存，留空不修改' : '开发者信息里的一串密钥'"
            />
          </label>
          <label class="pl-field">
            <span>打印机 SN</span>
            <input v-model.trim="printerForm.sn" class="pl-input" autocomplete="off" />
          </label>
          <label class="pl-field">
            <span>PKEY</span>
            <input
              v-model.trim="printerForm.pkey"
              class="pl-input"
              type="password"
              autocomplete="new-password"
              :placeholder="printer.has_pkey ? '已保存，留空不修改' : '机身标签上的 PKEY'"
            />
          </label>

          <div class='pl-modal-row'>
            <label class='pl-field'>
              <span>纸宽</span>
              <select v-model.number='printerForm.paper_width' class='pl-select'>
                <option :value='58'>58mm（一行 16 个汉字）</option>
                <option :value='80'>80mm</option>
              </select>
            </label>
            <label class='pl-field'>
              <span>字号</span>
              <select v-model='printerForm.font_scale' class='pl-select'>
                <option value='normal'>正常</option>
                <option value='tall'>只变高（行数不变）</option>
                <option value='big'>整体放大一倍</option>
                <option value='huge' :disabled='printerForm.paper_width == 58'>
                  放大两倍{{ printerForm.paper_width == 58 ? '（仅 80mm）' : '' }}
                </option>
              </select>
            </label>
          </div>

          <div class='pl-modal-row'>
            <label class='pl-field'>
              <span>份数</span>
              <input v-model.number='printerForm.times' class='pl-input' type='number' min='1' max='5' />
            </label>
            <label class='pl-check'>
              <input v-model='printerForm.enabled' type='checkbox' />
              <span>开启小票打印</span>
            </label>
          </div>

          <p v-if="printerError" class="pl-banner" role="alert">{{ printerError }}</p>
          <p v-if="printerNotice" class="pl-notice">{{ printerNotice }}</p>
          <p v-if="printerInfo" class="pl-modal-status">
            设备 {{ printerInfo.online ? '在线' : '离线' }} · 待打 {{ printerInfo.queued || 0 }} 张<template v-if="printerModelText"> · {{ printerModelText }}</template>
          </p>

          <pre v-if="previewText" class="pl-ticket-preview" :data-scale="previewScale">{{ previewText }}</pre>
        </div>
        <footer class="pl-modal-foot">
          <button type="button" class="pl-btn" @click="previewPrinter">预览小票</button>
          <button type="button" class="pl-btn" :disabled="checkingPrinter" @click="checkPrinter">
            {{ checkingPrinter ? '查询中…' : '检查设备' }}
          </button>
          <button type="button" class="pl-btn" :disabled="testingTicket" @click="testPrinter">
            {{ testingTicket ? '发送中…' : '打测试票' }}
          </button>
          <button type="button" class="pl-btn pl-btn-primary" :disabled="printerSaving" @click="savePrinter">
            {{ printerSaving ? '保存中…' : '保存' }}
          </button>
        </footer>
      </section>
    </div>
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
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: linear-gradient(135deg, var(--surface-1), var(--wb-tint-14));
  box-shadow: var(--paper-shadow-sm);
}
.pl-head-text {
  min-width: 0;
}
.pl-head-count {
  display: flex;
  flex: 0 0 auto;
  align-items: baseline;
  gap: 4px;
}
.pl-head-count strong {
  font-size: var(--fs-24);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.pl-head-count small {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.6;
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
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 14px;
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
.pl-order-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--wb-tint-08);
}
.pl-order-person {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.pl-order-person strong {
  font-size: var(--fs-14);
}
.pl-person-dot {
  width: 16px;
  height: 16px;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  background: var(--surface-2);
  flex: 0 0 auto;
}
.pl-order-pos {
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--surface-1);
  font-size: var(--fs-11);
  opacity: 0.72;
}
.pl-order-items {
  padding: 2px 14px;
}
.pl-order-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--line);
}
.pl-order-item:last-child {
  border-bottom: 0;
}
.pl-item-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-13);
  font-weight: 600;
}
.pl-item-stock {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 6px;
  background: var(--wb-warn-a10);
  color: var(--ink);
  font-size: var(--fs-11);
  font-variant-numeric: tabular-nums;
}
.pl-item-qty {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 7px;
  background: var(--wb-tint-08);
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
}
.pl-item-qty small {
  font-size: var(--fs-11);
  opacity: 0.7;
}
.pl-order-more {
  margin: 0;
  padding: 7px 0;
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.58;
}
.pl-order-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  padding: 8px 14px;
  border-top: 1px solid var(--line);
  background: var(--surface-2);
  font-size: var(--fs-11);
  opacity: 0.72;
}
.pl-order-at {
  margin-left: auto;
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
  gap: 8px;
  margin: -4px 0 12px;
}
.pl-summary-select {
  min-width: 180px;
  max-width: 100%;
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
.pl-paper {
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pl-paper-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--wb-tint-08);
  font-size: var(--fs-13);
}
.pl-paper-head strong {
  font-size: var(--fs-12);
  font-weight: 500;
  opacity: 0.62;
}
.pl-paper .pl-supplier {
  margin: 0;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}
.pl-paper .pl-supplier + .pl-supplier {
  border-top: 1px solid var(--line);
}
.pl-paper .pl-supplier-head {
  padding: 11px 14px 7px;
  border-bottom: 0;
  background: transparent;
}
.pl-paper .pl-category {
  padding-top: 2px;
}
.pl-paper-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid var(--line-strong);
  background: var(--surface-2);
}
.pl-paper-foot span {
  font-size: var(--fs-12);
  opacity: 0.65;
}
.pl-paper-foot strong {
  font-size: var(--fs-16);
  font-variant-numeric: tabular-nums;
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
.pl-notice {
  margin: 0 0 10px;
  padding: 9px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--wb-tint-08);
  font-size: var(--fs-13);
}
.pl-input {
  min-height: 44px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
  width: 100%;
  box-sizing: border-box;
}
.pl-ticket-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.pl-ticket-hint {
  color: var(--ink);
  font-size: var(--fs-11);
  opacity: 0.6;
}
.pl-modal {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--scrim);
}
.pl-modal-card {
  display: flex;
  flex-direction: column;
  width: min(560px, 100%);
  max-height: 88vh;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
  overflow: hidden;
}
.pl-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.pl-modal-head h2 {
  margin: 0;
  font-size: var(--fs-16);
}
.pl-modal-close {
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--ink);
  font-size: var(--fs-20);
  line-height: 1;
  cursor: pointer;
}
.pl-modal-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  overflow: auto;
}
.pl-modal-tip {
  margin: 0;
  color: var(--ink);
  font-size: var(--fs-12);
  line-height: 1.55;
  opacity: 0.66;
}
.pl-modal-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
}
.pl-check {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  font-size: var(--fs-13);
}
.pl-modal-status {
  margin: 0;
  font-size: var(--fs-12);
  opacity: 0.72;
}
.pl-ticket-preview {
  margin: 0;
  padding: 12px;
  max-height: 320px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-2);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--fs-12);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  overflow: auto;
}
/* 四档预览字号：与平台 `fonts` 参数一一对应（19px 那一档归到 20 令牌）。 */
.pl-ticket-preview[data-scale='normal'] { font-size: var(--fs-12); }
.pl-ticket-preview[data-scale='tall'] { font-size: var(--fs-13); }
.pl-ticket-preview[data-scale='big'] { font-size: var(--fs-16); }
.pl-ticket-preview[data-scale='huge'] { font-size: var(--fs-20); }
.pl-modal-foot {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
}
@media (max-width: 560px) {
  .pl-summary-tools {
    flex-wrap: nowrap;
    gap: 5px;
    padding: 8px;
  }
  .pl-summary-tools .pl-field {
    flex: 1 1 auto;
    min-width: 0;
  }
  .pl-summary-tools .luyun-date-picker {
    width: 100%;
    min-width: 0;
  }
  .pl-summary-tools .pl-btn {
    flex: 0 0 auto;
    padding: 8px;
    font-size: var(--fs-12);
    white-space: nowrap;
  }
  .pl-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .pl-stat strong {
    font-size: var(--fs-20);
  }
  /* 手机上四个按钮挤一行会把「保存」顶成孤零零的第二行，改成两列均分 */
  .pl-modal-foot {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .pl-modal-foot .pl-btn {
    width: 100%;
    padding: 8px 10px;
    font-size: var(--fs-13);
  }
  .pl-modal {
    padding: 10px;
  }
  .pl-modal-card {
    max-height: 92vh;
  }
}
</style>
