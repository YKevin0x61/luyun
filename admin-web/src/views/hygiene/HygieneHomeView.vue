<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import {
  HYGIENE_SHIFTS,
  hygienePermissionLabel,
  hygieneShiftLabel,
} from '../../utils/hygieneCopy'
import {
  dailyCaptureUrl,
  dailyItemStandardUrl,
  deepCleanShotUrl,
  frozenStandardUrl,
} from '../../utils/hygieneMarkup'
import { staffRequest, staffUpload } from '../../utils/hygieneStaff'

const router = useRouter()
const employee = ref(null)
const errorText = ref('')
const loggingOut = ref(false)
const picking = ref('')
const inbox = ref([])
const deepInbox = ref([])
const deepStatus = ref('')
const busy = ref(false)
const sheet = ref(null)
const previewUrl = ref('')
const localWatermark = ref(null)

const needsShiftPick = computed(() => {
  return Boolean(employee.value) && !employee.value.shift
})

const groupedInbox = computed(() => {
  const zones = []
  const byId = new Map()
  for (const row of inbox.value) {
    if (!byId.has(row.zone_id)) {
      const zone = { id: row.zone_id, name: row.zone_name, rows: [] }
      byId.set(row.zone_id, zone)
      zones.push(zone)
    }
    byId.get(row.zone_id).rows.push(row)
  }
  return zones
})

const isManager = computed(() => employee.value && employee.value.permission === '管理员')

onMounted(loadMe)
onBeforeUnmount(clearPreview)

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
    try {
      await loadDeepClean()
    } catch (err) {
      errorText.value = err.message || '无法加载专项卫生'
    }
    if (data.employee && data.employee.shift) {
      try {
        await loadInbox()
      } catch (err) {
        errorText.value = err.message || '无法加载日常待办'
      }
    } else {
      inbox.value = []
    }
  } catch (err) {
    errorText.value = err.message || '无法读取登录状态'
    router.replace('/hygiene/login')
  }
}

async function loadInbox() {
  const data = await staffRequest('/api/hygiene/staff/daily-work')
  inbox.value = data.items || []
}

async function loadDeepClean() {
  const data = await staffRequest('/api/hygiene/staff/deep-clean')
  deepInbox.value = data.items || []
  deepStatus.value = data.status || ''
}

async function pickShift(shift) {
  if (picking.value) return
  errorText.value = ''
  picking.value = shift
  try {
    await staffRequest('/api/hygiene/staff/shift', {
      method: 'POST',
      body: { shift },
    })
    await loadMe()
  } catch (err) {
    errorText.value = err.message || '选班失败'
  } finally {
    picking.value = ''
  }
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await staffRequest('/api/hygiene/staff/logout', { method: 'POST' })
  } catch {
    // Session may already be gone; still leave the phone entry.
  }
  router.replace('/hygiene/login')
}

function canShoot(row) {
  return Boolean(employee.value && employee.value.shift === row.shift && row.status !== '已通过')
}

function canReview(row) {
  return row.status === '待验收'
}

function canShootDeep(row) {
  return Boolean(employee.value && row.status !== '已通过')
}

function isDeepSheet() {
  return Boolean(sheet.value && sheet.value.kind === 'deep')
}

function canDecide(review) {
  if (!isManager.value || !review || !employee.value) return false
  return employee.value.id !== review.submitter_id
}

function openStandard(row) {
  errorText.value = ''
  sheet.value = { mode: 'standard', row }
}

async function openReview(row) {
  errorText.value = ''
  busy.value = true
  try {
    const review = await staffRequest(
      `/api/hygiene/staff/daily/${row.item_id}/review?shift=${encodeURIComponent(row.shift)}`,
    )
    sheet.value = { mode: 'review', row, review }
  } catch (err) {
    errorText.value = err.message || '无法打开验收'
  } finally {
    busy.value = false
  }
}

function openCamera() {
  if (!sheet.value) return
  clearPreview()
  sheet.value = { mode: 'camera', row: sheet.value.row }
}

function openDeepCapture(row) {
  errorText.value = ''
  clearPreview()
  sheet.value = {
    kind: 'deep',
    mode: 'before-camera',
    row,
    beforeBlob: null,
    afterBlob: null,
  }
}

async function openDeepReview(row) {
  errorText.value = ''
  busy.value = true
  try {
    const review = await staffRequest(`/api/hygiene/staff/deep-clean/${row.item_id}/review`)
    sheet.value = { kind: 'deep', mode: 'review', row, review }
  } catch (err) {
    errorText.value = err.message || '无法打开前后对照'
  } finally {
    busy.value = false
  }
}

function onCaptured(blob) {
  clearPreview()
  previewUrl.value = URL.createObjectURL(blob)
  const current = sheet.value
  const row = current && current.row
  if (current && current.kind === 'deep') {
    localWatermark.value = {
      time: new Date().toISOString(),
      item_name: row && row.item_name,
      photographer: employee.value && employee.value.phone,
    }
    if (current.mode === 'before-camera') {
      sheet.value = { ...current, mode: 'before-preview', beforeBlob: blob }
      return
    }
    if (current.mode === 'after-camera') {
      sheet.value = { ...current, mode: 'after-preview', afterBlob: blob }
      return
    }
  }
  localWatermark.value = {
    time: new Date().toISOString(),
    zone: row && row.zone_name,
    photographer: employee.value && employee.value.phone,
  }
  sheet.value = { mode: 'preview', row, blob }
}

function openDeepAfterCamera() {
  if (!sheet.value || sheet.value.kind !== 'deep') return
  clearPreview()
  sheet.value = { ...sheet.value, mode: 'after-camera' }
}

function openDeepBeforeCamera() {
  if (!sheet.value || sheet.value.kind !== 'deep') return
  clearPreview()
  sheet.value = { ...sheet.value, mode: 'before-camera', beforeBlob: null }
}

function clearPreview() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  localWatermark.value = null
}

function closeSheet() {
  clearPreview()
  sheet.value = null
}

async function submitCapture() {
  if (!sheet.value || !sheet.value.blob || busy.value) return
  const row = sheet.value.row
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    form.append('shift', row.shift)
    await staffUpload(`/api/hygiene/staff/daily/${row.item_id}/submit`, form)
    closeSheet()
    await loadInbox()
  } catch (err) {
    errorText.value = err.message || '提交失败'
  } finally {
    busy.value = false
  }
}

async function submitDeepPair() {
  if (!sheet.value || !sheet.value.beforeBlob || !sheet.value.afterBlob || busy.value) return
  const row = sheet.value.row
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('before', sheet.value.beforeBlob, 'before.jpg')
    form.append('after', sheet.value.afterBlob, 'after.jpg')
    form.append('live', 'true')
    await staffUpload(`/api/hygiene/staff/deep-clean/${row.item_id}/submit`, form)
    closeSheet()
    await loadDeepClean()
  } catch (err) {
    errorText.value = err.message || '提交失败'
  } finally {
    busy.value = false
  }
}

async function decide(action) {
  if (!sheet.value || !sheet.value.review || busy.value) return
  const row = sheet.value.row
  const deep = isDeepSheet()
  busy.value = true
  errorText.value = ''
  try {
    if (deep) {
      await staffRequest(`/api/hygiene/staff/deep-clean/${row.item_id}/${action}`, {
        method: 'POST',
      })
      closeSheet()
      await loadDeepClean()
    } else {
      await staffRequest(`/api/hygiene/staff/daily/${row.item_id}/${action}`, {
        method: 'POST',
        body: { shift: row.shift },
      })
      closeSheet()
      await loadInbox()
    }
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="staff-phone">
    <div class="staff-card">
      <p class="staff-brand">LuckIn<span>卫生</span></p>
      <template v-if="needsShiftPick">
        <h1 class="staff-title">今天上哪一班？</h1>
        <p class="staff-lead">选一次就锁在这个营业日。白班和夜班的日常检查分开交，选错了要找超级管理员改。</p>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>
        <div class="staff-shift-choices">
          <button
            v-for="shift in HYGIENE_SHIFTS"
            :key="shift"
            type="button"
            class="btn staff-shift-btn"
            :class="{ 'btn-primary': shift === '白班', 'staff-shift-night': shift === '夜班' }"
            :disabled="Boolean(picking)"
            @click="pickShift(shift)"
          >
            {{ picking === shift ? '正在锁定…' : shift }}
          </button>
        </div>
        <section v-if="deepInbox.length" class="staff-zone staff-deep">
          <h2>专项卫生{{ deepStatus ? ` · ${deepStatus}` : '' }}</h2>
          <p class="staff-lead">不跟班次。每项都要拍清理前和清理后，不要标准图。</p>
          <article v-for="row in deepInbox" :key="`deep-${row.item_id}`" class="staff-row">
            <div>
              <strong>{{ row.item_name }}</strong>
              <p>{{ row.status }}</p>
            </div>
            <div class="staff-row-actions">
              <button
                v-if="canShootDeep(row)"
                type="button"
                class="btn btn-sm btn-primary"
                :disabled="busy"
                @click="openDeepCapture(row)"
              >拍前后</button>
              <button
                v-if="canReview(row)"
                type="button"
                class="btn btn-sm"
                :disabled="busy"
                @click="openDeepReview(row)"
              >对照</button>
            </div>
          </article>
        </section>
        <button type="button" class="btn btn-block staff-submit staff-chooser-logout" :disabled="loggingOut" @click="logout">
          退出登录
        </button>
      </template>
      <template v-else>
        <h1 class="staff-title">卫生待办</h1>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>
        <template v-else-if="employee">
          <p class="staff-hello">{{ employee.phone }}</p>
          <dl class="staff-meta">
            <div>
              <dt>当天班次</dt>
              <dd>{{ hygieneShiftLabel(employee.shift) }}</dd>
            </div>
            <div>
              <dt>职位</dt>
              <dd>{{ employee.job_title || '未设置' }}</dd>
            </div>
            <div>
              <dt>卫生权限</dt>
              <dd>{{ hygienePermissionLabel(employee.permission) }}</dd>
            </div>
          </dl>
          <p class="staff-lead">日常只能交自己班次；专项卫生白班夜班都能交。先看标准图再拍日常。专项拍清理前、再拍清理后。都不能从相册选。</p>
          <section v-if="deepInbox.length" class="staff-zone staff-deep">
            <h2>专项卫生 · {{ deepStatus }}</h2>
            <article v-for="row in deepInbox" :key="`deep-${row.item_id}`" class="staff-row">
              <div>
                <strong>{{ row.item_name }}</strong>
                <p>{{ row.status }}</p>
              </div>
              <div class="staff-row-actions">
                <button
                  v-if="canShootDeep(row)"
                  type="button"
                  class="btn btn-sm btn-primary"
                  :disabled="busy"
                  @click="openDeepCapture(row)"
                >拍前后</button>
                <button
                  v-if="canReview(row)"
                  type="button"
                  class="btn btn-sm"
                  :disabled="busy"
                  @click="openDeepReview(row)"
                >对照</button>
              </div>
            </article>
          </section>
          <div v-if="groupedInbox.length" class="staff-catalog">
            <section v-for="zone in groupedInbox" :key="zone.id" class="staff-zone">
              <h2>{{ zone.name }}</h2>
              <article v-for="row in zone.rows" :key="`${row.item_id}-${row.shift}`" class="staff-row">
                <div>
                  <strong>{{ row.item_name }}</strong>
                  <p>{{ row.shift }} · {{ row.status }}</p>
                </div>
                <div class="staff-row-actions">
                  <button
                    v-if="canShoot(row)"
                    type="button"
                    class="btn btn-sm btn-primary"
                    :disabled="busy"
                    @click="openStandard(row)"
                  >拍摄</button>
                  <button
                    v-if="canReview(row)"
                    type="button"
                    class="btn btn-sm"
                    :disabled="busy"
                    @click="openReview(row)"
                  >对照</button>
                </div>
              </article>
            </section>
          </div>
          <p v-else class="staff-lead">还没有带标准图的日常检查项。</p>
          <button type="button" class="btn btn-block staff-submit" :disabled="loggingOut" @click="logout">
            退出登录
          </button>
        </template>
        <p v-else class="staff-lead">正在确认登录…</p>
      </template>
    </div>

    <div
      v-if="sheet"
      class="staff-preview"
      role="dialog"
      aria-modal="true"
      :aria-label="sheet.row.item_name"
      @click.self="closeSheet"
    >
      <div class="staff-preview-card">
        <h2>{{ sheet.row.item_name }}</h2>
        <p class="staff-lead">
          {{ sheet.kind === 'deep' ? '专项卫生 · 清理前 / 清理后' : `${sheet.row.zone_name} · ${sheet.row.shift}` }}
        </p>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>

        <template v-if="sheet.mode === 'standard'">
          <p class="staff-lead">先看标准图，看清角度再打开相机。</p>
          <HygieneStandardOverlay
            :src="dailyItemStandardUrl('staff', sheet.row)"
            :markup="sheet.row.markup || []"
            :alt="sheet.row.item_name"
          />
          <button type="button" class="btn btn-primary btn-block staff-submit" @click="openCamera">打开相机</button>
        </template>

        <template v-else-if="sheet.mode === 'before-camera' || sheet.mode === 'after-camera'">
          <p class="staff-lead">{{ sheet.mode === 'before-camera' ? '先拍清理前。现场拍，没有相册。' : '再拍清理后。现场拍，没有相册。' }}</p>
          <HygieneLiveCamera @captured="onCaptured" />
        </template>

        <HygieneLiveCamera v-else-if="sheet.mode === 'camera'" @captured="onCaptured" />

        <template v-else-if="sheet.mode === 'before-preview'">
          <div class="capture-preview">
            <img v-if="previewUrl" :src="previewUrl" alt="清理前">
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" @click="openDeepAfterCamera">拍清理后</button>
          <button type="button" class="btn btn-block staff-submit" @click="openDeepBeforeCamera">重拍清理前</button>
        </template>

        <template v-else-if="sheet.mode === 'after-preview'">
          <div class="capture-preview">
            <img v-if="previewUrl" :src="previewUrl" alt="清理后">
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitDeepPair">
            {{ busy ? '正在提交…' : '提交这一组待验收' }}
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openDeepAfterCamera">重拍清理后</button>
        </template>

        <template v-else-if="sheet.mode === 'preview'">
          <div class="capture-preview">
            <img v-if="previewUrl" :src="previewUrl" alt="刚拍的实拍">
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitCapture">
            {{ busy ? '正在提交…' : '提交待验收' }}
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openCamera">重拍</button>
        </template>

        <template v-else-if="sheet.kind === 'deep' && sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            left-label="清理前"
            right-label="清理后"
            :standard-src="deepCleanShotUrl('staff', sheet.row, 'before')"
            :standard-alt="'清理前'"
            :left-watermark="sheet.review.before_watermark"
            :capture-src="deepCleanShotUrl('staff', sheet.row, 'after')"
            :capture-alt="'清理后'"
            :watermark="sheet.review.after_watermark || sheet.review.watermark"
          />
          <p v-if="isManager && !canDecide(sheet.review)" class="staff-lead">交这一组的人不能自己验收。</p>
          <div v-if="canDecide(sheet.review)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="decide('reject')">驳回</button>
          </div>
        </template>

        <template v-else-if="sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            :standard-src="frozenStandardUrl('staff', sheet.row)"
            :standard-markup="sheet.review.frozen_markup || []"
            :standard-alt="sheet.row.item_name"
            :capture-src="dailyCaptureUrl('staff', sheet.row)"
            :capture-alt="'实拍'"
            :watermark="sheet.review.watermark"
          />
          <p v-if="isManager && !canDecide(sheet.review)" class="staff-lead">交这张的人不能自己验收。</p>
          <div v-if="canDecide(sheet.review)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="decide('reject')">驳回</button>
          </div>
        </template>

        <button type="button" class="btn btn-block staff-submit" @click="closeSheet">关掉</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.staff-phone {
  min-height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: max(20px, env(safe-area-inset-top)) 16px max(24px, env(safe-area-inset-bottom));
}
.staff-card {
  width: 100%;
  max-width: 420px;
  margin: auto 0;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px 22px;
}
.staff-brand {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  margin: 0 0 10px;
}
.staff-brand span { color: var(--accent); margin-left: 6px; }
.staff-title { font-size: 22px; margin: 0 0 8px; }
.staff-hello {
  font-size: 20px;
  font-variant-numeric: tabular-nums;
  margin: 0 0 16px;
}
.staff-meta {
  display: grid;
  gap: 10px;
  margin: 0 0 16px;
  padding: 12px 14px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.staff-meta div { display: flex; justify-content: space-between; gap: 12px; }
.staff-meta dt { color: var(--text-dim); font-size: 13px; }
.staff-meta dd { margin: 0; font-size: 14px; }
.staff-lead {
  color: var(--text-dim);
  font-size: 14px;
  line-height: 1.55;
  margin: 0 0 20px;
}
.staff-alert {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin: 0 0 16px;
}
.staff-submit { min-height: 48px; font-size: 16px; }
.staff-shift-choices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.staff-shift-btn {
  min-height: 56px;
  font-size: 18px;
  font-weight: 600;
}
.staff-shift-night {
  background: #1e293b;
  border-color: #334155;
  color: #e2e8f0;
}
.staff-shift-night:hover:not(:disabled) {
  border-color: var(--cyan);
  color: #fff;
}
.staff-chooser-logout { margin-top: 16px; }
.staff-deep { margin: 16px 0 8px; }
.staff-catalog {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0 0 20px;
}
.staff-zone h2 {
  margin: 0 0 8px;
  font-size: 15px;
}
.staff-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin: 0 0 6px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--card2);
}
.staff-row strong { display: block; font-size: 15px; }
.staff-row p {
  margin: 4px 0 0;
  color: var(--text-dim);
  font-size: 12px;
}
.staff-row-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.staff-preview {
  position: fixed;
  inset: 0;
  background: rgba(2, 6, 23, 0.72);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 16px;
  z-index: 20;
  overflow: auto;
}
.staff-preview-card {
  width: 100%;
  max-width: 420px;
  background: rgba(17, 24, 39, 0.96);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 16px 20px;
}
.staff-preview-card h2 {
  margin: 0 0 6px;
  font-size: 18px;
}
.staff-preview-card .staff-submit { margin-top: 14px; }
.staff-decide { display: grid; gap: 8px; }
.capture-preview {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  background: #020617;
}
.capture-preview img {
  display: block;
  width: 100%;
  height: auto;
}
</style>
