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
      label: `整改原图 · ${zone ? zone.name : '卫生责任区'}`,
      detail: ticketType.value,
      onSuccess: loadTickets,
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

async function decide(action) {
  if (!selected.value || busy.value) return
  const current = selected.value
  const previous = pending.value
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/fix/${selected.value.id}/${action}`)
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
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Fix · 整改闭环</p>
        <h1>整改单</h1>
        <p>现场拍照开单，员工回拍后按时限验收。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>开单必须现场拍照，不能从相册选择。时限未到只有开单人能验收；时限到达后其他管理员或超级管理员可验收。驳回后按原时限重新计时。超级管理员可以删除整张整改单。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <div class="card open-card">
      <h3>开整改单</h3>
      <p v-if="!liveOk" class="camera-missing">{{ CAMERA_MISSING }}</p>
      <template v-else>
        <p class="editor-lead">必选类型、必填哪里脏怎么改、自己填时限、挂一个卫生责任区。画圈可选，画在拍好的静图上，镜头不叠图。</p>
        <div class="open-grid">
          <label class="editor-field">
            卫生责任区
            <select v-model="zoneId" class="input" aria-label="卫生责任区">
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
        <label class="editor-field">
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
          <p class="editor-lead">必须现场拍摄，不能选择相册。</p>
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
            <p class="editor-lead">点图画面圈，可选。镜头上没有原图浮层。</p>
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
    </div>

    <div class="daily-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>待验收 <span>{{ pending.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!pending.length" class="roster-empty">现在没有待验收的整改回拍。</div>
        <ul v-else class="queue-list">
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
              <span>{{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}{{ deadlineUrgency(row.deadline) === 'overdue' ? ' · 已超时' : '' }}</span>
            </button>
          </li>
        </ul>
        <div v-if="waiting.length" class="waiting-block">
          <h4>待回拍</h4>
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
                <span>{{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}{{ deadlineUrgency(row.deadline) === 'overdue' ? ' · 已超时' : '' }}</span>
              </button>
            </li>
          </ul>
        </div>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? `${selected.zone_name} · ${selected.ticket_type}` : '对照验收' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">点一张单。时限未到只能由开单人验；到了超级管理员才能验别人开的单。</div>
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
          <p v-if="selected.status === '待验收' && !canDecide(selected)" class="editor-lead">时限还没到，只有开单人能验。</p>
          <div class="review-actions">
            <template v-if="canDecide(selected)">
              <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
              <button type="button" class="btn btn-danger" :disabled="busy" @click="decide('reject')">驳回</button>
            </template>
            <button
              type="button"
              class="btn btn-danger"
              :disabled="busy"
              @click="deleteTarget = selected"
            >删除整改单</button>
          </div>
        </div>
      </div>
    </div>

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
.open-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
@media (max-width: 800px) {
  .open-grid { grid-template-columns: 1fr; }
}
.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--hy-muted);
}
.capture-preview { position: relative; }
.mark-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mark-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--hy-muted);
}
.waiting-block {
  border-top: 1px dashed var(--hy-line);
  margin-top: 4px;
}
.waiting-block h4 {
  margin: 10px 12px 0;
  font-size: 11px;
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--hy-faint);
}
</style>
