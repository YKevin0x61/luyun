<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import {
  HYGIENE_FIX_TYPES,
  HYGIENE_SHIFTS,
  canAcceptFixTicket,
  hasLiveCamera,
  hygienePermissionLabel,
  hygieneShiftLabel,
} from '../../utils/hygieneCopy'
import {
  chinaNowIso,
  createCircleMark,
  dailyCaptureUrl,
  dailyItemStandardUrl,
  deepCleanShotUrl,
  fixOriginalUrl,
  fixReshootUrl,
  frozenStandardUrl,
  teachingShotUrl,
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
const fixInbox = ref([])
const zones = ref([])
const boards = ref({ week_start: '', people: [], zones: [] })
const teaching = ref([])
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
const liveOk = computed(() => hasLiveCamera())
const sheetTitle = computed(() => {
  if (!sheet.value) return ''
  if (sheet.value.kind === 'fix' && sheet.value.mode === 'form') return '开整改单'
  if (sheet.value.kind === 'teaching') return sheet.value.row && sheet.value.row.title
  if (sheet.value.kind === 'fix' && sheet.value.row) {
    return `${sheet.value.row.zone_name} · ${sheet.value.row.ticket_type}`
  }
  return sheet.value.row && sheet.value.row.item_name
})

onMounted(loadMe)
onBeforeUnmount(clearPreview)

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
    try {
      await Promise.all([loadDeepClean(), loadFixTickets(), loadZones(), loadBoards(), loadTeaching()])
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

async function loadFixTickets() {
  const data = await staffRequest('/api/hygiene/staff/fix')
  fixInbox.value = data.items || []
}

async function loadZones() {
  const data = await staffRequest('/api/hygiene/staff/daily-catalog')
  zones.value = data.zones || []
}

async function loadBoards() {
  boards.value = await staffRequest('/api/hygiene/staff/boards')
}

async function loadTeaching() {
  const data = await staffRequest('/api/hygiene/staff/teaching')
  teaching.value = data.items || []
}

function weekLabel(iso) {
  const raw = String(iso || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  return matched ? `${matched[1]} ${matched[2]}` : raw
}

function personLabel(row) {
  return row.phone || `员工 ${row.employee_id}`
}

function openTeaching(row) {
  errorText.value = ''
  sheet.value = { kind: 'teaching', mode: 'review', row }
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

function canDecideFix(ticket) {
  if (!ticket || !employee.value) return false
  return canAcceptFixTicket(employee.value, ticket)
}

function deadlineLabel(iso) {
  const raw = String(iso || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  return matched ? `${matched[1]} ${matched[2]}` : raw
}

function openFixForm() {
  errorText.value = ''
  if (!isManager.value) return
  if (!liveOk.value) {
    errorText.value = '打不开相机。必须现场拍，没有相册入口。'
    return
  }
  clearPreview()
  const firstZone = zones.value[0]
  sheet.value = {
    kind: 'fix',
    mode: 'form',
    row: { item_name: '开整改单', zone_name: firstZone ? firstZone.name : '' },
    zoneId: firstZone ? firstZone.id : '',
    ticketType: '卫生',
    bodyText: '',
    durationHours: 2,
    markup: [],
    blob: null,
  }
}

function openFixOriginal(row) {
  errorText.value = ''
  clearPreview()
  sheet.value = { kind: 'fix', mode: 'original', row }
}

function openFixCameraFromForm() {
  if (!sheet.value || sheet.value.kind !== 'fix') return
  if (!sheet.value.zoneId || !sheet.value.bodyText.trim() || !sheet.value.durationHours) {
    errorText.value = '请填类型、说明、时限，并选卫生责任区。'
    return
  }
  errorText.value = ''
  clearPreview()
  sheet.value = { ...sheet.value, mode: 'fix-camera' }
}

function openFixReshootCamera() {
  if (!sheet.value || sheet.value.kind !== 'fix') return
  clearPreview()
  sheet.value = { ...sheet.value, mode: 'reshoot-camera' }
}

async function openFixReview(row) {
  errorText.value = ''
  busy.value = true
  try {
    const review = await staffRequest(`/api/hygiene/staff/fix/${row.id}`)
    sheet.value = { kind: 'fix', mode: 'review', row: review, review }
  } catch (err) {
    errorText.value = err.message || '无法打开整改对照'
  } finally {
    busy.value = false
  }
}

function addFixCircle(point) {
  if (!sheet.value || sheet.value.kind !== 'fix') return
  sheet.value = {
    ...sheet.value,
    markup: [...(sheet.value.markup || []), createCircleMark(point.x, point.y)],
  }
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
  if (current && current.kind === 'fix') {
    const zoneName = (row && row.zone_name)
      || (zones.value.find((zone) => zone.id === current.zoneId) || {}).name
    localWatermark.value = {
      time: chinaNowIso(),
      zone: zoneName,
      photographer: employee.value && employee.value.phone,
    }
    if (current.mode === 'fix-camera') {
      sheet.value = { ...current, mode: 'fix-preview', blob }
      return
    }
    if (current.mode === 'reshoot-camera') {
      sheet.value = { ...current, mode: 'reshoot-preview', blob }
      return
    }
  }
  if (current && current.kind === 'deep') {
    localWatermark.value = {
      time: chinaNowIso(),
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
    time: chinaNowIso(),
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
  if (sheet.value.kind === 'fix') {
    await submitFixOpen()
    return
  }
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

async function submitFixOpen() {
  if (!sheet.value || !sheet.value.blob || busy.value) return
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    form.append('zone_id', String(sheet.value.zoneId))
    form.append('ticket_type', sheet.value.ticketType)
    form.append('body_text', sheet.value.bodyText.trim())
    form.append('duration_hours', String(sheet.value.durationHours))
    form.append('markup', JSON.stringify(sheet.value.markup || []))
    await staffUpload('/api/hygiene/staff/fix', form)
    closeSheet()
    await loadFixTickets()
  } catch (err) {
    errorText.value = err.message || '开单失败'
  } finally {
    busy.value = false
  }
}

async function submitFixReshoot() {
  if (!sheet.value || !sheet.value.blob || !sheet.value.row || busy.value) return
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    await staffUpload(`/api/hygiene/staff/fix/${sheet.value.row.id}/reshoot`, form)
    closeSheet()
    await loadFixTickets()
  } catch (err) {
    errorText.value = err.message || '回拍失败'
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
  if (!sheet.value || busy.value) return
  if (sheet.value.kind !== 'fix' && !sheet.value.review) return
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
    } else if (sheet.value.kind === 'fix') {
      await staffRequest(`/api/hygiene/staff/fix/${row.id}/${action}`, {
        method: 'POST',
      })
      closeSheet()
      await loadFixTickets()
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
        <section v-if="isManager || fixInbox.length" class="staff-zone staff-fix">
          <h2>整改单</h2>
          <p class="staff-lead">不跟班次。先看开单原图再拍，镜头不叠图。</p>
          <button
            v-if="isManager"
            type="button"
            class="btn btn-sm btn-primary"
            :disabled="busy"
            @click="openFixForm"
          >开整改单</button>
          <article v-for="row in fixInbox" :key="`shift-fix-${row.id}`" class="staff-row">
            <div>
              <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
              <p>{{ row.status }} · {{ deadlineLabel(row.deadline) }}</p>
            </div>
            <div class="staff-row-actions">
              <button type="button" class="btn btn-sm btn-primary" :disabled="busy" @click="openFixOriginal(row)">回拍</button>
              <button
                v-if="isManager && row.status === '待验收'"
                type="button"
                class="btn btn-sm"
                :disabled="busy"
                @click="openFixReview(row)"
              >对照</button>
            </div>
          </article>
        </section>
        <section class="staff-zone">
          <h2>人的红黑榜</h2>
          <p class="staff-lead">本周 {{ weekLabel(boards.week_start) }} 起。只记次数。</p>
          <p v-if="!(boards.people || []).length" class="staff-lead">这一周还没有人的次数。</p>
          <article v-for="row in boards.people" :key="`person-${row.employee_id}`" class="staff-row">
            <div>
              <strong>{{ personLabel(row) }}</strong>
              <p>实拍 {{ row['实拍'] }} · 驳回 {{ row['驳回'] }} · 一次通过 {{ row['一次通过'] }} · 逾期 {{ row['逾期'] }}</p>
            </div>
          </article>
        </section>
        <section class="staff-zone">
          <h2>卫生责任区红黑榜</h2>
          <p v-if="!(boards.zones || []).length" class="staff-lead">这一周还没有卫生责任区的次数。</p>
          <article v-for="row in boards.zones" :key="`zone-${row.zone_id}`" class="staff-row">
            <div>
              <strong>{{ row.zone_name }}</strong>
              <p>逾期 {{ row['逾期'] }}</p>
            </div>
          </article>
        </section>
        <section class="staff-zone">
          <h2>卫生教材</h2>
          <p class="staff-lead">超级管理员手点的合格对照。合格图不会自动进来。</p>
          <p v-if="!teaching.length" class="staff-lead">还没有卫生教材。</p>
          <article v-for="row in teaching" :key="`teach-${row.id}`" class="staff-row">
            <div>
              <strong>{{ row.title }}</strong>
              <p>{{ row.left_label }} / {{ row.right_label }}</p>
            </div>
            <button type="button" class="btn btn-sm" @click="openTeaching(row)">打开</button>
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
          <p class="staff-lead">日常只能交自己班次；专项和整改单白班夜班都能交。先看标准图再拍日常。整改先看开单原图再拍，镜头不叠图。都不能从相册选。</p>
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
          <section v-if="isManager || fixInbox.length" class="staff-zone staff-fix">
            <h2>整改单</h2>
            <p class="staff-lead">先看开单原图再拍，镜头不叠图。后交覆盖先交。</p>
            <button
              v-if="isManager"
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="busy"
              @click="openFixForm"
            >开整改单</button>
            <article v-for="row in fixInbox" :key="`fix-${row.id}`" class="staff-row">
              <div>
                <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
                <p>{{ row.status }} · {{ deadlineLabel(row.deadline) }}</p>
              </div>
              <div class="staff-row-actions">
                <button type="button" class="btn btn-sm btn-primary" :disabled="busy" @click="openFixOriginal(row)">回拍</button>
                <button
                  v-if="isManager && row.status === '待验收'"
                  type="button"
                  class="btn btn-sm"
                  :disabled="busy"
                  @click="openFixReview(row)"
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
          <section class="staff-zone">
            <h2>人的红黑榜</h2>
            <p class="staff-lead">本周 {{ weekLabel(boards.week_start) }} 起。只记次数。</p>
            <p v-if="!(boards.people || []).length" class="staff-lead">这一周还没有人的次数。</p>
            <article v-for="row in boards.people" :key="`home-person-${row.employee_id}`" class="staff-row">
              <div>
                <strong>{{ personLabel(row) }}</strong>
                <p>实拍 {{ row['实拍'] }} · 驳回 {{ row['驳回'] }} · 一次通过 {{ row['一次通过'] }} · 逾期 {{ row['逾期'] }}</p>
              </div>
            </article>
          </section>
          <section class="staff-zone">
            <h2>卫生责任区红黑榜</h2>
            <p v-if="!(boards.zones || []).length" class="staff-lead">这一周还没有卫生责任区的次数。</p>
            <article v-for="row in boards.zones" :key="`home-zone-${row.zone_id}`" class="staff-row">
              <div>
                <strong>{{ row.zone_name }}</strong>
                <p>逾期 {{ row['逾期'] }}</p>
              </div>
            </article>
          </section>
          <section class="staff-zone">
            <h2>卫生教材</h2>
            <p class="staff-lead">超级管理员手点的合格对照。合格图不会自动进来。</p>
            <p v-if="!teaching.length" class="staff-lead">还没有卫生教材。</p>
            <article v-for="row in teaching" :key="`home-teach-${row.id}`" class="staff-row">
              <div>
                <strong>{{ row.title }}</strong>
                <p>{{ row.left_label }} / {{ row.right_label }}</p>
              </div>
              <button type="button" class="btn btn-sm" @click="openTeaching(row)">打开</button>
            </article>
          </section>
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
      :aria-label="sheetTitle"
      @click.self="closeSheet"
    >
      <div class="staff-preview-card">
        <h2>{{ sheetTitle }}</h2>
        <p class="staff-lead">
          <template v-if="sheet.kind === 'fix'">整改单 · 现场拍，没有相册。先看开单原图再拍，镜头不叠图。</template>
          <template v-else-if="sheet.kind === 'teaching'">卫生教材 · {{ sheet.row.left_label }} / {{ sheet.row.right_label }}</template>
          <template v-else-if="sheet.kind === 'deep'">专项卫生 · 清理前 / 清理后</template>
          <template v-else>{{ sheet.row.zone_name }} · {{ sheet.row.shift }}</template>
        </p>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>

        <template v-if="sheet.mode === 'form'">
          <label class="staff-field">
            卫生责任区
            <select v-model="sheet.zoneId" class="staff-input">
              <option v-for="zone in zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
            </select>
          </label>
          <label class="staff-field">
            类型
            <select v-model="sheet.ticketType" class="staff-input">
              <option v-for="kind in HYGIENE_FIX_TYPES" :key="kind" :value="kind">{{ kind }}</option>
            </select>
          </label>
          <label class="staff-field">
            时限（小时）
            <input v-model.number="sheet.durationHours" class="staff-input" type="number" min="1" step="1">
          </label>
          <label class="staff-field">
            哪里脏、怎么改
            <textarea v-model="sheet.bodyText" class="staff-input" rows="3" maxlength="400"></textarea>
          </label>
          <button type="button" class="btn btn-primary btn-block staff-submit" @click="openFixCameraFromForm">打开相机</button>
        </template>

        <template v-else-if="sheet.mode === 'standard'">
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

        <HygieneLiveCamera
          v-else-if="sheet.mode === 'camera' || sheet.mode === 'fix-camera' || sheet.mode === 'reshoot-camera'"
          @captured="onCaptured"
        />

        <template v-else-if="sheet.mode === 'original'">
          <p class="staff-lead">先看开单原图，看清圈点再打开相机。镜头不叠图。</p>
          <HygieneStandardOverlay
            :src="fixOriginalUrl('staff', sheet.row)"
            :markup="sheet.row.markup || []"
            :alt="sheet.row.ticket_type"
          />
          <button type="button" class="btn btn-primary btn-block staff-submit" @click="openFixReshootCamera">打开相机</button>
        </template>

        <template v-else-if="sheet.mode === 'fix-preview'">
          <div class="capture-preview">
            <HygieneStandardOverlay
              v-if="previewUrl"
              :src="previewUrl"
              :markup="sheet.markup || []"
              editable
              alt="刚拍的整改原图"
              @point="addFixCircle"
            />
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <p class="staff-lead">点图画面圈，可选。</p>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitFixOpen">
            {{ busy ? '正在开单…' : '开整改单' }}
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openFixCameraFromForm">重拍</button>
        </template>

        <template v-else-if="sheet.mode === 'reshoot-preview'">
          <div class="capture-preview">
            <img v-if="previewUrl" :src="previewUrl" alt="刚拍的回拍">
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitFixReshoot">
            {{ busy ? '正在提交…' : '提交回拍' }}
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openFixReshootCamera">重拍</button>
        </template>

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

        <template v-else-if="sheet.kind === 'teaching' && sheet.row">
          <HygieneReviewPair
            :left-label="sheet.row.left_label"
            :right-label="sheet.row.right_label"
            :standard-src="teachingShotUrl('staff', sheet.row, 'left')"
            :standard-markup="sheet.row.left_markup || []"
            :standard-alt="sheet.row.left_label"
            :capture-src="teachingShotUrl('staff', sheet.row, 'right')"
            :capture-alt="sheet.row.right_label"
          />
        </template>

        <template v-else-if="sheet.kind === 'fix' && sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            left-label="开单原图"
            right-label="回拍"
            :standard-src="fixOriginalUrl('staff', sheet.row)"
            :standard-markup="sheet.review.markup || []"
            :standard-alt="sheet.row.ticket_type"
            :capture-src="sheet.row.reshoot_capture_id ? fixReshootUrl('staff', sheet.row) : ''"
            :capture-alt="'回拍'"
            :left-watermark="sheet.review.open_watermark"
            :watermark="sheet.review.watermark"
          />
          <p v-if="isManager && !canDecideFix(sheet.row)" class="staff-lead">时限还没到，只有开单人能验。</p>
          <div v-if="canDecideFix(sheet.row)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="decide('reject')">驳回</button>
          </div>
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
.staff-fix { margin: 16px 0 8px; }
.staff-fix > .btn { margin-bottom: 10px; }
.staff-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0 0 12px;
  color: var(--text-dim);
  font-size: 13px;
}
.staff-input {
  min-height: 44px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card2);
  color: var(--text);
  padding: 8px 10px;
  font: inherit;
}
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
