<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import { api } from '../../api/client'
import {
  HYGIENE_FIX_TYPES,
  canAcceptFixTicket,
  hasLiveCamera,
} from '../../utils/hygieneCopy'
import { createCircleMark, fixOriginalUrl, fixReshootUrl } from '../../utils/hygieneMarkup'

const CAMERA_MISSING = '这台电脑没有相机，不能开整改单。必须现场拍，没有相册。'

const items = ref([])
const zones = ref([])
const loading = ref(true)
const errorText = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
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
    time: new Date().toISOString(),
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

async function submitOpen() {
  if (!canSubmitOpen.value) return
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', capturedBlob.value, 'capture.jpg')
    form.append('live', 'true')
    form.append('zone_id', String(zoneId.value))
    form.append('ticket_type', ticketType.value)
    form.append('body_text', bodyText.value.trim())
    form.append('duration_hours', String(durationHours.value))
    form.append('markup', JSON.stringify(markup.value))
    await api.upload('/api/hygiene/admin/fix', form)
    bodyText.value = ''
    durationHours.value = 2
    markup.value = []
    opening.value = false
    clearPreview()
    await loadTickets()
  } catch (err) {
    errorText.value = err.message || '开单失败'
  } finally {
    busy.value = false
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
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/fix/${selected.value.id}/${action}`)
    selected.value = null
    review.value = null
    await loadTickets()
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}

function deadlineLabel(iso) {
  const raw = String(iso || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  return matched ? `${matched[1]} ${matched[2]}` : raw
}
</script>

<template>
  <div class="fix-page">
    <div class="card roster-head">
      <div>
        <h2>整改单</h2>
        <p>超级管理员在这里开单也必须现场拍。没相机就开不了，不能从相册选。时限到了才能验别人开的单；自己开的随时能验。驳回后用原来的时限重新倒计时。</p>
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
          <p class="editor-lead">现场拍，没有相册。</p>
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
                <button type="button" class="btn btn-sm" @click="removeMark(index)">去掉</button>
              </li>
            </ul>
            <div class="review-actions">
              <button type="button" class="btn" :disabled="busy" @click="clearPreview">重拍</button>
              <button type="button" class="btn btn-primary" :disabled="!canSubmitOpen" @click="submitOpen">
                {{ busy ? '正在开单…' : '开整改单' }}
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
              :class="{ active: selected && selected.id === row.id }"
              @click="selectRow(row)"
            >
              <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
              <span>{{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}</span>
            </button>
          </li>
        </ul>
        <div v-if="waiting.length" class="waiting-block">
          <h4>待回拍</h4>
          <ul class="queue-list">
            <li v-for="row in waiting" :key="`wait-${row.id}`">
              <button type="button" class="queue-btn" :class="{ active: selected && selected.id === row.id }" @click="selectRow(row)">
                <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
                <span>{{ row.status }} · 时限 {{ deadlineLabel(row.deadline) }}</span>
              </button>
            </li>
          </ul>
        </div>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? `${selected.zone_name} · ${selected.ticket_type}` : '对照验收' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">从左边点一张单。时限未到只能由开单人验；到了超级管理员才能验别人开的单。</div>
        <div v-else class="review-body">
          <p class="review-meta">{{ selected.body_text }} · 时限 {{ deadlineLabel(selected.deadline) }}</p>
          <HygieneReviewPair
            left-label="开单原图"
            right-label="回拍"
            :standard-src="fixOriginalUrl('admin', selected)"
            :standard-markup="selected.markup || []"
            :standard-alt="selected.ticket_type"
            :capture-src="selected.reshoot_capture_id ? fixReshootUrl('admin', selected) : ''"
            :capture-alt="'回拍'"
            :watermark="selected.watermark"
          />
          <p v-if="selected.status === '待验收' && !canDecide(selected)" class="editor-lead">时限还没到，只有开单人能验。</p>
          <div v-if="canDecide(selected)" class="review-actions">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-danger" :disabled="busy" @click="decide('reject')">驳回</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fix-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1180px;
}
.roster-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.roster-head h2 { margin: 0 0 6px; font-size: 16px; }
.roster-head p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  max-width: 56em;
}
.open-card {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.open-card h3 { margin: 0; font-size: 14px; }
.open-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 800px) {
  .open-grid { grid-template-columns: 1fr; }
}
.editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.editor-lead {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
}
.camera-missing {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
.roster-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
.roster-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
.daily-grid {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .daily-grid { grid-template-columns: 1fr; }
}
.queue-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.queue-btn {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  background: var(--card2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  font-family: inherit;
}
.queue-btn span { color: var(--text-dim); font-size: 12px; }
.queue-btn.active { border-color: var(--accent); }
.waiting-block h4 {
  margin: 8px 12px 0;
  font-size: 12px;
  color: var(--text-dim);
}
.review-body { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.review-meta { margin: 0; color: var(--text-dim); font-size: 13px; }
.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
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
  font-size: 12px;
}
</style>
