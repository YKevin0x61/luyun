<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import {
  HYGIENE_FIX_TYPES,
  canAcceptFixTicket,
  hasLiveCamera,
} from '../../utils/hygieneCopy'
import { chinaNowIso, createCircleMark, fixOriginalUrl, fixReshootUrl } from '../../utils/hygieneMarkup'
import { deadlineUrgency, fixQueueKey, formatStamp, nextAfterRemove } from '../../utils/hygieneWorkFlow'

const CAMERA_MISSING = '此设备没有可用相机。整改开单必须现场拍摄，不能从相册选择。'

const items = ref([])
const zones = ref([])
const loading = ref(true)
const errorText = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
const deleteTarget = ref(null)
const liveOk = ref(hasLiveCamera())
const zoneId = ref('')
const ticketType = ref('卫生')
const bodyText = ref('')
const durationHours = ref(2)
const opening = ref(false)
const previewUrl = ref('')
const capturedBlob = ref(null)
const markup = ref([])
const localWatermark = ref(null)
const imageUploads = useImageUploadQueueStore()

const pending = computed(() => items.value.filter((row) => row.status === '待验收'))
const waiting = computed(() => items.value.filter((row) => row.status !== '待验收'))
const canSubmitOpen = computed(() => {
  return Boolean(
    liveOk.value
      && zoneId.value
      && ticketType.value
      && bodyText.value.trim()
      && Number(durationHours.value) > 0
      && capturedBlob.value
      && !busy.value,
  )
})

onMounted(refreshPage)
onBeforeUnmount(clearPreview)

useHygieneRealtime({
  id: 'hygiene-admin-fix',
  resources: ['fix', 'zones'],
  pull: refreshPage,
})

function clearPreview() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  capturedBlob.value = null
  localWatermark.value = null
}

async function refreshPage() {
  loading.value = true
  errorText.value = ''
  liveOk.value = hasLiveCamera()
  try {
    await Promise.all([loadZones(), loadTickets()])
  } catch (err) {
    errorText.value = err.message || '无法加载整改单'
  } finally {
    loading.value = false
  }
}

async function loadZones() {
  const data = await api.get('/api/hygiene/admin/zones')
  zones.value = data.zones || []
  if (!zoneId.value && zones.value.length) {
    zoneId.value = String(zones.value[0].id)
  }
}

async function loadTickets() {
  const data = await api.get('/api/hygiene/admin/fix')
  items.value = data.items || []
  if (selected.value) {
    const still = items.value.find((row) => row.id === selected.value.id)
    if (still) {
      selected.value = still
      review.value = still
    } else {
      selected.value = null
      review.value = null
    }
  }
}

function onCaptured(blob) {
  clearPreview()
  capturedBlob.value = blob
  previewUrl.value = URL.createObjectURL(blob)
  const zone = zones.value.find((row) => String(row.id) === String(zoneId.value))
  localWatermark.value = {
    time: chinaNowIso(),
    zone: zone ? zone.name : '',
    photographer: '超级管理员',
  }
}

function addCircle(point) {
  markup.value = [...markup.value, createCircleMark(point.x, point.y)]
}

function removeMark(index) {
  markup.value = markup.value.filter((_, i) => i !== index)
}

function submitOpen() {
  if (!canSubmitOpen.value) return
  errorText.value = ''
  const zone = zones.value.find((row) => String(row.id) === String(zoneId.value))
  const form = new FormData()
  form.append('file', capturedBlob.value, 'capture.jpg')
  form.append('live', 'true')
  form.append('zone_id', String(zoneId.value))
  form.append('ticket_type', ticketType.value)
  form.append('body_text', bodyText.value.trim())
  form.append('duration_hours', String(durationHours.value))
  form.append('markup', JSON.stringify(markup.value))
  try {
    imageUploads.enqueue({
      path: '/api/hygiene/admin/fix',
      formData: form,
      label: `整改原图 · ${zone ? zone.name : '卫生工作区'}`,
      detail: ticketType.value,
      onSuccess: loadTickets,
      // 开单是无条件 INSERT、没有幂等键：自动重试会在"已提交但响应丢了"时
      // 再开一张单，所以这里只允许人工重试。
      autoRetry: false,
    })
    bodyText.value = ''
    durationHours.value = 2
    markup.value = []
    opening.value = false
    clearPreview()
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function selectRow(row) {
  errorText.value = ''
  selected.value = row
  review.value = row
}

function canDecide(ticket) {
  return canAcceptFixTicket({ kind: 'super' }, ticket)
}

// 驳回不可撤销：整改单回到"待回拍"，员工得重新回拍。按钮紧挨着"通过"，
// 手机上容易误触，所以走一次确认。
const rejectOpen = ref(false)

function askReject() {
  if (!selected.value || busy.value) return
  rejectOpen.value = true
}


async function confirmReject(reason) {
  rejectOpen.value = false
  await decide('reject', reason)
}

async function decide(action, reason = '') {
  if (!selected.value || busy.value) return
  const current = selected.value
  const previous = pending.value
  busy.value = true
  errorText.value = ''
  try {
    await api.post(
      `/api/hygiene/admin/fix/${selected.value.id}/${action}`,
      action === 'reject' && reason ? { reason } : undefined,
    )
    selected.value = null
    review.value = null
    await loadTickets()
    const next = nextAfterRemove(previous, current, fixQueueKey)
    if (next) selectRow(next)
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}

function deadlineLabel(iso) {
  return formatStamp(iso)
}

async function confirmDelete() {
  const row = deleteTarget.value
  deleteTarget.value = null
  if (!row) return
  busy.value = true
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/fix/${row.id}`)
    if (selected.value && selected.value.id === row.id) {
      selected.value = null
      review.value = null
    }
    await loadTickets()
  } catch (err) {
    errorText.value = err.message || '无法删除整改单'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="fix-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">FIX · 整改闭环</p>
        <h1 class="wbx-title">整改单</h1>
      </div>
      <div class="wbx-pills">
        <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
      </div>
      <p class="wbx-sub">现场拍照开单，员工回拍后按时限验收。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>开单必须现场拍照，不能从相册选择。时限未到只有开单人能验收；时限到达后其他管理员或超级管理员可验收。驳回后按原时限重新计时。超级管理员可以删除整张整改单。</p>
      </details>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>

    <div class="wbx-readline">
      <span class="wbx-read"><span>等验收</span><b>{{ pending.length }}</b></span>
      <span class="wbx-read"><span>待回拍</span><b>{{ waiting.length }}</b></span>
      <span class="wbx-read"><span>已超时</span><b class="is-bad">{{ [...pending, ...waiting].filter((row) => deadlineUrgency(row.deadline) === 'overdue').length }}</b></span>
    </div>

    <div class="wbx-cols">
      <section class="wbx-panel is-open">
        <header class="wbx-ph">
          <i class="led sig"></i>
          <h2>开整改单</h2>
          <span class="wbx-hint">现场拍 · 不能选相册</span>
        </header>
        <p v-if="!liveOk" class="camera-missing">{{ CAMERA_MISSING }}</p>
        <template v-else>
          <p class="wbx-lead">必选类型、必填哪里脏怎么改、自己填时限、挂一个卫生工作区。画圈可选，画在拍好的静图上，镜头不叠图。</p>
          <div class="open-grid">
            <label class="editor-field">
              卫生工作区
              <select v-model="zoneId" class="input" aria-label="卫生工作区">
                <option v-for="zone in zones" :key="zone.id" :value="String(zone.id)">{{ zone.name }}</option>
              </select>
            </label>
            <label class="editor-field">
              类型
              <select v-model="ticketType" class="input" aria-label="整改类型">
                <option v-for="kind in HYGIENE_FIX_TYPES" :key="kind" :value="kind">{{ kind }}</option>
              </select>
            </label>
            <label class="editor-field">
              时限（小时）
              <input
                v-model.number="durationHours"
                class="input"
                type="number"
                min="1"
                step="1"
                aria-label="整改时限小时"
              >
            </label>
          </div>
          <label class="editor-field is-body">
            哪里脏、怎么改
            <textarea
              v-model="bodyText"
              class="input"
              rows="3"
              maxlength="400"
              placeholder="写明哪里脏、怎么改"
            ></textarea>
          </label>
          <button
            v-if="!opening"
            type="button"
            class="btn btn-primary"
            @click="opening = true"
          >打开相机</button>
          <template v-else>
            <p class="wbx-lead">必须现场拍摄，不能选择相册。</p>
            <HygieneLiveCamera v-if="!previewUrl" @captured="onCaptured" />
            <template v-else>
              <div class="capture-preview">
                <HygieneStandardOverlay
                  :src="previewUrl"
                  :markup="markup"
                  editable
                  alt="刚拍的整改原图"
                  @point="addCircle"
                />
                <HygieneWatermarkOverlay :watermark="localWatermark" />
              </div>
              <p class="wbx-lead">点图画面圈，可选。镜头上没有原图浮层。</p>
              <ul v-if="markup.length" class="mark-list">
                <li v-for="(mark, index) in markup" :key="index">
                  <span>圆圈</span>
                  <button type="button" class="btn btn-sm" @click="removeMark(index)">删除标注</button>
                </li>
              </ul>
              <div class="review-actions">
                <button type="button" class="btn" :disabled="busy" @click="clearPreview">重拍</button>
                <button type="button" class="btn btn-primary" :disabled="!canSubmitOpen" @click="submitOpen">
                  开整改单
                </button>
              </div>
            </template>
          </template>
        </template>
      </section>

      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led warn"></i>
          <h2>等验收</h2>
          <span class="wbx-hint">{{ pending.length }} 张 · 待回拍 {{ waiting.length }}</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <div v-else-if="!pending.length && !waiting.length" class="wbx-empty-bar">现在没有等验收的整改回拍。</div>
        <template v-else>
          <ul v-if="pending.length" class="queue-list">
            <li v-for="row in pending" :key="row.id">
              <button
                type="button"
                class="queue-btn"
                :class="{
                  active: selected && selected.id === row.id,
                  overdue: deadlineUrgency(row.deadline) === 'overdue',
                }"
                @click="selectRow(row)"
              >
                <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
                <span class="wbx-state" :class="deadlineUrgency(row.deadline) === 'overdue' ? 'is-bad' : 'is-warn'">
                  <i class="led" :class="deadlineUrgency(row.deadline) === 'overdue' ? 'bad' : 'warn'"></i>
                  {{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}{{ deadlineUrgency(row.deadline) === 'overdue' ? ' · 已超时' : '' }}
                </span>
              </button>
            </li>
          </ul>
          <div v-if="waiting.length" class="wbx-seg">
            <h3 class="wbx-seg-head"><i class="led sig"></i>待回拍 <span class="wbx-hint">{{ waiting.length }}</span></h3>
            <ul class="queue-list">
              <li v-for="row in waiting" :key="`wait-${row.id}`">
                <button
                  type="button"
                  class="queue-btn"
                  :class="{
                    active: selected && selected.id === row.id,
                    overdue: deadlineUrgency(row.deadline) === 'overdue',
                  }"
                  @click="selectRow(row)"
                >
                  <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
                  <span class="wbx-state" :class="deadlineUrgency(row.deadline) === 'overdue' ? 'is-bad' : ''">
                    <i class="led" :class="deadlineUrgency(row.deadline) === 'overdue' ? 'bad' : ''"></i>
                    {{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}{{ deadlineUrgency(row.deadline) === 'overdue' ? ' · 已超时' : '' }}
                  </span>
                </button>
              </li>
            </ul>
          </div>
        </template>
      </section>
    </div>

    <section class="wbx-panel">
      <header class="wbx-ph">
        <i class="led ok"></i>
        <h2>{{ selected ? `${selected.zone_name} · ${selected.ticket_type}` : '对照验收' }}</h2>
        <span class="wbx-hint">左开单原图 · 右回拍</span>
      </header>
      <div v-if="!selected" class="wbx-empty-bar">点一张单。时限未到只能由开单人验；到了超级管理员才能验别人开的单。</div>
      <div v-else class="review-body">
        <p class="review-meta">{{ selected.body_text }} · 时限 {{ deadlineLabel(selected.deadline) }}</p>
        <HygieneReviewPair
          left-label="开单原图"
          right-label="回拍"
          :standard-src="fixOriginalUrl('admin', selected, 'preview')"
          :original-standard-src="fixOriginalUrl('admin', selected)"
          :standard-markup="selected.markup || []"
          :standard-alt="selected.ticket_type"
          :capture-src="selected.reshoot_capture_id ? fixReshootUrl('admin', selected, 'preview') : ''"
          :original-capture-src="selected.reshoot_capture_id ? fixReshootUrl('admin', selected) : ''"
          :capture-alt="'回拍'"
          :left-watermark="selected.open_watermark"
          :watermark="selected.watermark"
        />
        <p v-if="selected.status === '待验收' && !canDecide(selected)" class="wbx-lead">时限还没到，只有开单人能验。</p>
        <div class="review-actions">
          <template v-if="canDecide(selected)">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-danger" :disabled="busy" @click="askReject">驳回</button>
          </template>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="busy"
            @click="deleteTarget = selected"
          >删除整改单</button>
        </div>
      </div>
    </section>

    <ConfirmDialog
      v-if="rejectOpen"
      title="驳回这次回拍"
      :message="`驳回「${selected ? selected.zone_name : ''} · ${selected ? selected.ticket_type : ''}」后要重新回拍；本周红黑榜会记一次驳回。`"
      confirm-label="驳回"
      danger
      :prompt="{ label: '哪里不合格（必填，员工能看到）', placeholder: '写一句让他知道改什么，例如：回拍角度不对，看不出整改结果', hint: '必填 · 员工照这句重拍', required: true, maxlength: 120 }"
      @confirm="confirmReject"
      @cancel="rejectOpen = false"
    />

    <ConfirmDialog
      v-if="deleteTarget"
      title="删除整改单"
      :message="`删除「${deleteTarget.zone_name} · ${deleteTarget.ticket_type}」会连同开单原图、回拍和逾期记录一起删除。`"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v2 + v3：页头右手只留刷新；一条读数行；开单（1.4fr）与队列（1fr）并排；
   对照验收整行；空态一律横条；行高 56。两步拍照流程原样。 */
.fix-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-head .rule-help { grid-column: 1 / -1; grid-row: 3; margin-top: 6px; }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }

.wbx-readline {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 26px;
  padding: 10px 14px;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08);
}
.wbx-read { display: inline-flex; align-items: baseline; gap: 6px; }
.wbx-read span { font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint); }
.wbx-read b { font: 500 20px/1 var(--font-mono); color: var(--ink); }
.wbx-read b.is-bad { color: var(--danger-bright); }

.wbx-cols { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 14px; align-items: stretch; }
.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-lead { margin: 0; padding: 8px 14px 0; font-size: 10px; line-height: 1.6; color: var(--ink-faint); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
}
.wbx-seg { border-top: 1px solid var(--wb-ink-a08); }
.wbx-seg-head { display: flex; align-items: center; gap: 8px; margin: 0; padding: 10px 14px 6px; font: 600 11px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-seg-head .wbx-hint { margin-left: auto; }

.open-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding: 10px 14px 0; }
.editor-field { display: flex; flex-direction: column; gap: 5px; font-size: 10px; color: var(--ink-dim); }
.editor-field.is-body { padding: 10px 14px 0; }
.is-open > .btn { margin: 10px 14px 14px; align-self: flex-start; }
.capture-preview { padding: 0 14px; }
.mark-list { list-style: none; margin: 0; padding: 8px 14px; display: flex; flex-direction: column; gap: 6px; }
.mark-list li { display: flex; justify-content: space-between; align-items: center; font-size: 10px; color: var(--ink-dim); }
.camera-missing { margin: 0; padding: 10px 14px; font-size: 11px; color: var(--danger-bright); }

.queue-list { list-style: none; margin: 0; padding: 0; }
.queue-btn {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
  width: 100%; min-height: 56px; padding: 8px 14px; text-align: left; cursor: pointer;
  background: transparent; border: 0; border-bottom: 1px solid var(--wb-ink-a08); color: var(--ink);
}
.queue-btn:hover { background: var(--surface-2); }
.queue-btn strong { font-size: 11.5px; font-weight: 600; }
.queue-btn.active { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--action); }
.queue-btn.overdue strong { color: var(--danger-bright); }
.wbx-state { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; letter-spacing: .06em; color: var(--ink-faint); }
.wbx-state.is-warn { color: var(--warn); }
.wbx-state.is-bad { color: var(--danger-bright); }
.review-body { padding: 10px 14px 14px; display: flex; flex-direction: column; gap: 10px; }
.review-meta { margin: 0; font: 500 10px/1.5 var(--font-mono); color: var(--ink-faint); }
.review-actions { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.fix-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
  .open-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
}
</style>
