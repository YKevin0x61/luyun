<script setup>
/**
 * 厨房采购 · 单据详情。
 *
 * 能做哪些动作由服务端在 order 上给的 can_* 标志决定（页面不重复判权限）：
 * 管理端可改单 / 完成核对 / 取消 / 软删；提交者本人只能取消自己的待处理单。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/client.js'
import { PURCHASE_ORDERS_PATH } from '../../utils/purchasePaths.js'
import {
  formatDateTime,
  formatQty,
  parseOrderId,
  statusText,
} from './purchaseUi.js'
import PurchaseNav from './PurchaseNav.vue'

const route = useRoute()
const router = useRouter()

const orderId = parseOrderId(route.query.id)
const order = ref(null)
const items = ref([])
const events = ref([])
const loading = ref(false)
const errorText = ref('')
const notice = ref('')
const editMode = ref(false)
const editQuantities = reactive({})
const saving = ref(false)
const confirmKind = ref('')
const confirmBusy = ref(false)
const cancelReason = ref('')

const EVENT_TEXT = {
  submitted: '提交',
  items_updated: '改单',
  completed: '完成核对',
  cancelled: '取消',
  deleted: '删除',
}

const canEdit = computed(() => Boolean(order.value && order.value.can_edit))
const canComplete = computed(() => Boolean(order.value && order.value.can_complete))
const canCancel = computed(() => Boolean(order.value && order.value.can_cancel))
const canDelete = computed(() => Boolean(order.value && order.value.can_delete))
const positionName = computed(() => (order.value ? order.value.position_name : ''))

const confirmTitle = computed(() => {
  if (confirmKind.value === 'complete') return '确认这张单已完成核对？'
  if (confirmKind.value === 'cancel') return '取消这张采购单？'
  if (confirmKind.value === 'delete') return '删除这张采购单？'
  return ''
})

const confirmText = computed(() => {
  if (confirmKind.value === 'complete') {
    return '完成后这张单不能再改、也不能取消；门店汇总与采购分析仍会统计它。'
  }
  if (confirmKind.value === 'cancel') {
    return '取消后这张单不计入汇总与采购分析；只有待处理单能取消，取消后不能恢复。'
  }
  if (confirmKind.value === 'delete') {
    return '删除是软删：列表与汇总里不再显示，历史数据仍在库里；只有管理端能删。'
  }
  return ''
})

async function loadOrder() {
  if (!orderId) {
    errorText.value = '链接里没有采购单 id。'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/purchase/orders/' + orderId)
    order.value = data.order || null
    items.value = data.items || []
    events.value = data.events || []
    editMode.value = false
  } catch (err) {
    errorText.value = err.message || '采购单没加载出来'
  } finally {
    loading.value = false
  }
}

function startEdit() {
  if (!order.value) return
  for (const key of Object.keys(editQuantities)) delete editQuantities[key]
  for (const item of items.value) editQuantities[item.product_id] = item.quantity
  editMode.value = true
  notice.value = ''
}

function cancelEdit() {
  editMode.value = false
  notice.value = ''
}

function removeItem(productId) {
  delete editQuantities[productId]
}

function saveEdit() {
  const nextItems = []
  for (const item of items.value) {
    if (!(item.product_id in editQuantities)) continue
    const quantity = Number(editQuantities[item.product_id])
    if (!Number.isFinite(quantity) || quantity <= 0) {
      errorText.value = '「' + item.name + '」的数量要大于 0；不想要它就点删除。'
      return
    }
    nextItems.push({ product_id: item.product_id, quantity })
  }
  if (!nextItems.length) {
    errorText.value = '一张单至少要保留一项商品；整张不要请直接取消这张单。'
    return
  }
  errorText.value = ''
  saving.value = true
  api
    .patch('/api/purchase/orders/' + orderId + '/items', { items: nextItems })
    .then((data) => {
      order.value = data.order || order.value
      items.value = data.items || []
      events.value = data.events || []
      editMode.value = false
      notice.value = '改单已保存。'
    })
    .catch((err) => {
      errorText.value = err.message || '改单没保存成功'
    })
    .finally(() => {
      saving.value = false
    })
}

function openConfirm(kind) {
  confirmKind.value = kind
  cancelReason.value = ''
  errorText.value = ''
}

async function runConfirm() {
  if (!confirmKind.value) return
  confirmBusy.value = true
  errorText.value = ''
  const kind = confirmKind.value
  try {
    if (kind === 'complete') {
      await api.post('/api/purchase/orders/' + orderId + '/complete')
      notice.value = '已标记完成核对。'
    } else if (kind === 'cancel') {
      await api.post('/api/purchase/orders/' + orderId + '/cancel', {
        reason: cancelReason.value.trim() || undefined,
      })
      notice.value = '采购单已取消。'
    } else if (kind === 'delete') {
      await api.delete('/api/purchase/orders/' + orderId)
      confirmKind.value = ''
      router.replace(PURCHASE_ORDERS_PATH)
      return
    }
    confirmKind.value = ''
    await loadOrder()
  } catch (err) {
    errorText.value = err.message || '操作没成功'
    confirmKind.value = ''
  } finally {
    confirmBusy.value = false
  }
}

function eventText(kind) {
  return EVENT_TEXT[kind] || kind || ''
}

function eventDetail(event) {
  const detail = event && event.detail_json ? event.detail_json : null
  if (!detail || typeof detail !== 'object') return ''
  if (detail.reason) return '原因：' + detail.reason
  if (detail.items) return detail.items + ' 项'
  return ''
}

onMounted(loadOrder)
</script>

<template>
  <div class="pd-page">
    <PurchaseNav active="orders" />

    <button type="button" class="pd-back" @click="router.push(PURCHASE_ORDERS_PATH)">
      ‹ 采购单
    </button>

    <p v-if="errorText" class="pd-banner" role="alert">{{ errorText }}</p>
    <p v-else-if="notice" class="pd-banner pd-banner-soft">{{ notice }}</p>

    <div v-if="loading" class="pd-empty">正在加载采购单…</div>

    <template v-else-if="order">
      <header class="pd-head">
        <div>
          <p class="pd-no">{{ order.order_no }}</p>
          <h1>{{ positionName }} 采购单</h1>
          <p class="pd-sub">
            {{ order.submitter_name }} · 提交 {{ formatDateTime(order.submitted_at) }}
            <template v-if="order.completed_at"> · 完成 {{ formatDateTime(order.completed_at) }}</template>
            <template v-else-if="order.cancelled_at"> · 取消 {{ formatDateTime(order.cancelled_at) }}</template>
          </p>
        </div>
        <span class="pd-status" :class="'is-' + order.status">{{ statusText(order.status) }}</span>
      </header>

      <div class="pd-actions">
        <button v-if="canEdit && !editMode" type="button" class="pd-btn" @click="startEdit">改单</button>
        <button v-if="canEdit && editMode" type="button" class="pd-btn pd-btn-primary" :disabled="saving" @click="saveEdit">
          {{ saving ? '保存中…' : '保存改动' }}
        </button>
        <button v-if="canEdit && editMode" type="button" class="pd-btn pd-btn-ghost" :disabled="saving" @click="cancelEdit">
          取消改单
        </button>
        <button v-if="canComplete" type="button" class="pd-btn pd-btn-primary" @click="openConfirm('complete')">
          完成核对
        </button>
        <button v-if="canCancel" type="button" class="pd-btn pd-btn-ghost" @click="openConfirm('cancel')">
          取消这张单
        </button>
        <button v-if="canDelete" type="button" class="pd-btn pd-btn-danger" @click="openConfirm('delete')">
          删除
        </button>
      </div>

      <p v-if="editMode" class="pd-edit-note">
        改单会按提交日重新算新加商品的参考值；已在单里的商品保留当时的参考值 / 库存快照。
      </p>

      <section class="pd-section">
        <h2 class="pd-section-title">基本信息</h2>
        <div class="pd-info">
          <div class="pd-info-row">
            <span>下单人</span>
            <strong>{{ order.submitter_name || '未知用户' }}</strong>
          </div>
          <div class="pd-info-row">
            <span>下单时间</span>
            <strong>{{ formatDateTime(order.submitted_at) }}</strong>
          </div>
          <div class="pd-info-row">
            <span>订单状态</span>
            <strong class="pd-info-status" :class="'is-' + order.status">{{ statusText(order.status) }}</strong>
          </div>
        </div>
      </section>

      <section class="pd-card">
        <header class="pd-card-head">
          <h2>商品列表</h2>
          <span v-if="!editMode">合计 {{ formatQty(items.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0)) }}</span>
        </header>

        <div v-if="!items.length" class="pd-empty">这张单没有商品行。</div>
        <div v-else class="pd-items">
          <div v-for="item in items" :key="item.product_id" class="pd-item">
            <div class="pd-item-main">
              <div class="pd-item-title">
                <strong>{{ item.name }}</strong>
                <span v-if="item.specification" class="pd-spec">{{ item.specification }}</span>
              </div>
              <p class="pd-item-meta">
                <span>{{ item.product_code }}</span>
                <span>{{ item.supplier_name }}</span>
                <span>{{ item.category_name }}</span>
              </p>
              <p class="pd-item-ref">
                参考 {{ formatQty(item.reference_qty) }}
                <template v-if="item.stock_qty !== null && item.stock_qty !== undefined">
                  · 当时库存 {{ formatQty(item.stock_qty) }}
                </template>
              </p>
            </div>

            <div v-if="editMode" class="pd-item-edit">
              <label class="pd-field">
                <span>数量</span>
                <input
                  v-model.number="editQuantities[item.product_id]"
                  type="number"
                  inputmode="decimal"
                  min="0"
                  step="any"
                  :aria-label="item.name + ' 数量'"
                />
              </label>
              <button type="button" class="pd-remove" @click="removeItem(item.product_id)">删除这项</button>
            </div>
            <div v-else class="pd-item-qty">
              {{ formatQty(item.quantity) }} <small>{{ item.unit }}</small>
            </div>
          </div>
        </div>
      </section>


      <section class="pd-section">
        <h2 class="pd-section-title">订单汇总</h2>
        <div class="pd-info">
          <div class="pd-info-row">
            <span>商品种类</span>
            <strong>{{ items.length }} 种</strong>
          </div>
          <div class="pd-info-row">
            <span>总数量</span>
            <strong>{{ formatQty(items.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0)) }}</strong>
          </div>
        </div>
      </section>
      <section class="pd-card">
        <h2 class="pd-card-title">操作记录</h2>
        <div v-if="!events.length" class="pd-empty">还没有操作记录。</div>
        <ol v-else class="pd-events">
          <li v-for="(event, index) in events" :key="index">
            <span class="pd-event-kind">{{ eventText(event.kind) }}</span>
            <span class="pd-event-actor">{{ event.actor_name || (event.actor_kind === 'admin' ? '管理端' : '') }}</span>
            <span class="pd-event-time">{{ formatDateTime(event.created_at) }}</span>
            <span v-if="eventDetail(event)" class="pd-event-detail">{{ eventDetail(event) }}</span>
          </li>
        </ol>
      </section>
    </template>

    <div v-else-if="!loading && !errorText" class="pd-empty">采购单不存在，或已被删除。</div>

    <Teleport to="body">
      <div v-if="confirmKind" class="pd-scrim" @click.self="confirmKind = ''">
        <div class="pd-dialog" role="dialog" aria-modal="true" :aria-label="confirmTitle">
          <h2>{{ confirmTitle }}</h2>
          <p>{{ confirmText }}</p>
          <label v-if="confirmKind === 'cancel'" class="pd-field pd-dialog-field">
            <span>取消原因（可不填）</span>
            <input v-model="cancelReason" maxlength="200" placeholder="例如：岗位报错了" />
          </label>
          <div class="pd-dialog-actions">
            <button type="button" class="pd-btn pd-btn-ghost" :disabled="confirmBusy" @click="confirmKind = ''">
              再想想
            </button>
            <button
              type="button"
              class="pd-btn"
              :class="confirmKind === 'delete' ? 'pd-btn-danger' : 'pd-btn-primary'"
              :disabled="confirmBusy"
              @click="runConfirm"
            >
              {{ confirmBusy ? '处理中…' : '确定' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pd-page {
  width: 100%;
  min-width: 0;
  max-width: 920px;
  margin: 0 auto;
  padding: 10px 12px 40px;
  color: var(--ink);
  font-size: var(--fs-14);
  line-height: 1.45;
}
.pd-back {
  min-height: 44px;
  margin: 0 0 8px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-13);
  opacity: 0.7;
  cursor: pointer;
}
.pd-banner {
  margin: 0 0 10px;
  padding: 9px 12px;
  border: 1px solid var(--danger-bright);
  border-radius: 10px;
  background: var(--wb-danger-a10);
  font-size: var(--fs-13);
}
.pd-banner-soft {
  border-color: var(--line-strong);
  background: var(--surface-1);
}
.pd-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 14px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: linear-gradient(135deg, var(--surface-1), var(--wb-tint-08));
  box-shadow: var(--paper-shadow-sm);
}
.pd-no {
  margin: 0;
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.4px;
  opacity: 0.55;
}
.pd-head h1 {
  margin: 2px 0 0;
  font-size: var(--fs-24);
  line-height: 1.25;
}
.pd-sub {
  margin: 4px 0 0;
  color: var(--ink);
  font-size: var(--fs-12);
  opacity: 0.62;
}
.pd-status {
  flex: 0 0 auto;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: var(--fs-12);
  font-weight: 600;
}
.pd-status.is-pending {
  background: var(--wb-warn-a10);
}
.pd-status.is-completed {
  background: var(--wb-ok-a10);
}
.pd-status.is-cancelled {
  background: var(--wb-danger-a10);
  opacity: 0.75;
}
.pd-section {
  margin: 0 0 12px;
}
.pd-section-title {
  margin: 0 0 7px;
  font-size: var(--fs-14);
}
.pd-info {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pd-info-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px dashed var(--line);
  font-size: var(--fs-13);
}
.pd-info-row:last-child {
  border-bottom: 0;
}
.pd-info-row span {
  opacity: 0.6;
}
.pd-info-row strong {
  font-weight: 600;
  text-align: right;
}
.pd-info-status.is-pending {
  color: var(--action);
}
.pd-info-status.is-completed {
  color: var(--ink);
}
.pd-info-status.is-cancelled {
  opacity: 0.55;
}
.pd-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 12px;
}
.pd-edit-note {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-left: 3px solid var(--action);
  border-radius: 0 8px 8px 0;
  background: var(--wb-tint-08);
  font-size: var(--fs-12);
}
.pd-btn {
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
.pd-btn-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.pd-btn-ghost {
  background: transparent;
}
.pd-btn-danger {
  border-color: var(--danger-bright);
  background: transparent;
  color: var(--danger-bright);
}
.pd-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.pd-card {
  margin: 0 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.pd-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.pd-card h2 {
  margin: 0;
  font-size: var(--fs-14);
  font-weight: 700;
}
.pd-card-head span {
  font-size: var(--fs-12);
  opacity: 0.6;
}
.pd-card-title {
  margin-bottom: 6px !important;
}
.pd-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--line);
}
.pd-item:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.pd-item-title {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
}
.pd-item-title strong {
  font-size: var(--fs-14);
}
.pd-spec {
  font-size: var(--fs-12);
  opacity: 0.6;
}
.pd-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 3px 0 0;
  font-size: var(--fs-11);
  opacity: 0.58;
}
.pd-item-ref {
  margin: 4px 0 0;
  font-size: var(--fs-11);
  opacity: 0.58;
}
.pd-item-qty {
  font-size: var(--fs-16);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.pd-item-qty small {
  font-size: var(--fs-11);
  font-weight: 400;
  opacity: 0.6;
}
.pd-item-edit {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.pd-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pd-field span {
  font-size: var(--fs-11);
  opacity: 0.6;
}
.pd-field input {
  width: 92px;
  min-height: 44px;
  padding: 7px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  text-align: right;
}
.pd-remove {
  min-height: 44px;
  padding: 7px 9px;
  border: 1px dashed var(--danger-bright);
  border-radius: 9px;
  background: transparent;
  color: var(--danger-bright);
  font: inherit;
  font-size: var(--fs-11);
  cursor: pointer;
}
.pd-events {
  margin: 0;
  padding: 0;
  list-style: none;
}
.pd-events li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line);
  font-size: var(--fs-12);
}
.pd-events li:last-child {
  border-bottom: 0;
}
.pd-event-kind {
  font-weight: 600;
}
.pd-event-actor,
.pd-event-time,
.pd-event-detail {
  opacity: 0.6;
}
.pd-empty {
  padding: 22px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  color: var(--ink);
  font-size: var(--fs-13);
  text-align: center;
  opacity: 0.68;
}
.pd-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 16px;
  background: var(--wb-scrim-strong);
}
.pd-dialog {
  width: min(460px, 100%);
  padding: 18px;
  border-radius: 16px;
  background: var(--surface-1);
  color: var(--ink);
  box-shadow: var(--paper-shadow);
}
.pd-dialog h2 {
  margin: 0 0 8px;
  font-size: var(--fs-16);
}
.pd-dialog p {
  margin: 0 0 8px;
  font-size: var(--fs-13);
  opacity: 0.75;
}
.pd-dialog-field {
  margin-top: 10px;
}
.pd-dialog-field input {
  width: 100%;
  text-align: left;
}
.pd-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
@media (max-width: 560px) {
  .pd-item {
    grid-template-columns: 1fr;
  }
  .pd-item-qty {
    justify-self: end;
  }
}
</style>
