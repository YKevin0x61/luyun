<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SvgIcon from '../../components/SvgIcon.vue'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import StandardPhotoCachePanel from '../../components/hygiene/StandardPhotoCachePanel.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import {
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  HYGIENE_FIX_TYPES,
  HYGIENE_SHIFTS,
  HYGIENE_STAFF_TABS,
  canAcceptFixTicket,
  hasLiveCamera,
  hygieneDocumentTitle,
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
import { standardVersionChanged } from '../../utils/standardPhotoCache'
import { staffRequest, staffUpload } from '../../utils/hygieneStaff'
import {
  buildWorkQueue,
  dailyPrimaryAction,
  dailyProgress,
  deadlineUrgency,
  deepPrimaryAction,
  fixPrimaryAction,
  formatStamp,
  groupByZone,
  nextDeepShootRow,
  nextFixWorkRow,
  nextShootRow,
  openRows,
  passedRows,
  queueGroups,
  shiftClock,
  statusTone,
  tabWorkCount,
} from '../../utils/hygieneWorkFlow'

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
const standardPhotoCache = useStandardPhotoCacheStore()
const tab = ref('inbox')
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
const beforePreviewUrl = ref('')
const localWatermark = ref(null)
const localBeforeWatermark = ref(null)
const dailyClocks = ref(null)
const deepClock = ref(null)
const flashText = ref('')
const nowTick = ref(Date.now())
const closeButton = ref(null)
let clockTimer = null

const needsShiftPick = computed(() => {
  return Boolean(employee.value) && !employee.value.shift
})

const dailyStats = computed(() => dailyProgress(inbox.value))
const groupedPassed = computed(() => groupByZone(passedRows(inbox.value)))
const openDeep = computed(() => openRows(deepInbox.value))
const passedDeep = computed(() => passedRows(deepInbox.value))
const deepStats = computed(() => dailyProgress(deepInbox.value))
const shiftDue = computed(() => shiftClock(
  employee.value && employee.value.shift,
  dailyClocks.value,
))
const deepDue = computed(() => (deepClock.value && deepClock.value.hhmm) || '')

const isManager = computed(() => employee.value && employee.value.permission === '管理员')
const liveOk = computed(() => hasLiveCamera())
const workQueue = computed(() => buildWorkQueue({
  inbox: inbox.value,
  deepInbox: deepInbox.value,
  fixInbox: fixInbox.value,
  shiftDue: shiftDue.value,
  deepDue: deepDue.value,
  now: nowTick.value,
  isManager: isManager.value,
}))
const nextWork = computed(() => workQueue.value[0] || null)
const restWorkGroups = computed(() => {
  const nextKey = nextWork.value && nextWork.value.key
  return queueGroups(workQueue.value.filter((task) => task.key !== nextKey))
})
const queueSummary = computed(() => ({
  overdue: workQueue.value.filter((task) => task.bucket === 'overdue').length,
  soon: workQueue.value.filter((task) => task.bucket === 'soon').length,
  waiting: workQueue.value.filter((task) => task.bucket === 'waiting').length,
}))
const currentStaffTab = computed(() => (
  HYGIENE_STAFF_TABS.find((item) => item.id === tab.value) || HYGIENE_STAFF_TABS[0]
))

watch(currentStaffTab, (item) => {
  document.title = hygieneDocumentTitle(item.title)
}, { immediate: true })

watch(sheet, async (value, previous) => {
  standardPhotoCache.setTaskSheetOpen(Boolean(value))
  if (!value || previous) return
  await nextTick()
  if (closeButton.value) closeButton.value.focus()
})

function tabCount(id) {
  return tabWorkCount(id, {
    inbox: inbox.value,
    deepInbox: deepInbox.value,
    fixInbox: fixInbox.value,
  })
}
const sheetTitle = computed(() => {
  if (!sheet.value) return ''
  if (sheet.value.kind === 'fix' && sheet.value.mode === 'form') return '开整改单'
  if (sheet.value.kind === 'teaching') return sheet.value.row && sheet.value.row.title
  if (sheet.value.kind === 'fix' && sheet.value.row) {
    return `${sheet.value.row.zone_name} · ${sheet.value.row.ticket_type}`
  }
  return sheet.value.row && sheet.value.row.item_name
})

function currentStandardId(itemId, fallback = null) {
  return standardPhotoCache.currentStandardId(itemId) || fallback || null
}

function standardMissingOffline(itemId, fallback = null) {
  const standardId = currentStandardId(itemId, fallback)
  return Boolean(standardId && !standardPhotoCache.online && standardPhotoCache.isMissing(standardId))
}

function tickClock() {
  nowTick.value = Date.now()
}

function onKeydown(event) {
  if (event.key === 'Escape' && sheet.value) closeSheet()
}

onMounted(() => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 30_000)
  window.addEventListener('keydown', onKeydown)
  loadMe()
})

onBeforeUnmount(() => {
  if (clockTimer) window.clearInterval(clockTimer)
  window.removeEventListener('keydown', onKeydown)
  standardPhotoCache.setTaskSheetOpen(false)
  clearPreview()
})

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
    dailyClocks.value = data.daily_clocks || null
    deepClock.value = data.deep_clock || null
  } catch (err) {
    errorText.value = err.message || '无法读取登录状态'
    router.replace('/hygiene/login')
    return
  }
  const jobs = [loadDeepClean, loadFixTickets, loadZones, loadBoards, loadTeaching]
  if (employee.value && employee.value.shift) jobs.push(loadInbox)
  else inbox.value = []
  const results = await Promise.allSettled(jobs.map((fn) => fn()))
  const failed = results.find((result) => result.status === 'rejected')
  if (failed) {
    errorText.value = (failed.reason && failed.reason.message) || '无法加载卫生待办'
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
  return formatStamp(iso)
}

function personLabel(row) {
  return row.name || row.phone || `员工 ${row.employee_id}`
}

function openTeaching(row) {
  errorText.value = ''
  flashText.value = ''
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
  return formatStamp(iso)
}

function fixUrgency(row) {
  if (!row || row.status === '待验收') return 'none'
  return deadlineUrgency(row.deadline, nowTick.value)
}

function fixDueLabel(row) {
  if (row && row.status === '待验收') return '已交，等验收'
  return deadlineLabel(row && row.deadline)
}

function runDailyPrimary(row) {
  const action = dailyPrimaryAction(row)
  if (action === 'review') return openReview(row)
  if (action === 'shoot') return openStandard(row)
  return undefined
}

function runDeepPrimary(row) {
  const action = deepPrimaryAction(row)
  if (action === 'review') return openDeepReview(row)
  if (action === 'shoot') return openDeepCapture(row)
  return undefined
}

function runFixPrimary(row) {
  const action = fixPrimaryAction(row, { isManager: isManager.value })
  if (action === 'review') return openFixReview(row)
  return openFixOriginal(row)
}

function runQueueTask(task) {
  if (!task) return
  if (task.kind === 'daily') return runDailyPrimary(task.row)
  if (task.kind === 'deep') return runDeepPrimary(task.row)
  return runFixPrimary(task.row)
}

function openFixForm() {
  errorText.value = ''
  flashText.value = ''
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
  flashText.value = ''
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
  flashText.value = ''
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
  flashText.value = ''
  sheet.value = { mode: 'standard', row }
}

async function openReview(row) {
  errorText.value = ''
  flashText.value = ''
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
  if (standardMissingOffline(sheet.value.row.item_id, sheet.value.row.current_standard_id)) {
    errorText.value = '这张标准图尚未缓存，联网后才能打开相机。'
    return
  }
  clearPreview()
  const row = sheet.value.row
  sheet.value = {
    mode: 'camera',
    row,
    seenStandardId: currentStandardId(row.item_id, row.current_standard_id),
  }
}

function openDeepCapture(row) {
  errorText.value = ''
  flashText.value = ''
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
  flashText.value = ''
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
  const current = sheet.value
  const row = current && current.row
  if (current && current.kind === 'fix') {
    clearPreview()
    previewUrl.value = URL.createObjectURL(blob)
    const zoneName = (row && row.zone_name)
      || (zones.value.find((zone) => zone.id === current.zoneId) || {}).name
    localWatermark.value = {
      time: chinaNowIso(),
      zone: zoneName,
      photographer: employee.value && (employee.value.name || employee.value.phone),
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
    const capturedAt = chinaNowIso()
    const watermark = {
      time: capturedAt,
      item_name: row && row.item_name,
      photographer: employee.value && (employee.value.name || employee.value.phone),
    }
    if (current.mode === 'before-camera') {
      clearBeforePreview()
      beforePreviewUrl.value = URL.createObjectURL(blob)
      localBeforeWatermark.value = watermark
      sheet.value = { ...current, mode: 'before-preview', beforeBlob: blob }
      return
    }
    if (current.mode === 'after-camera') {
      clearAfterPreview()
      previewUrl.value = URL.createObjectURL(blob)
      localWatermark.value = watermark
      sheet.value = { ...current, mode: 'after-preview', afterBlob: blob }
      return
    }
  }
  clearPreview()
  previewUrl.value = URL.createObjectURL(blob)
  localWatermark.value = {
    time: chinaNowIso(),
    zone: row && row.zone_name,
    photographer: employee.value && (employee.value.name || employee.value.phone),
  }
  sheet.value = {
    mode: 'preview',
    row,
    blob,
    seenStandardId: current && current.seenStandardId,
  }
}

function openDeepAfterCamera() {
  if (!sheet.value || sheet.value.kind !== 'deep') return
  clearAfterPreview()
  sheet.value = { ...sheet.value, mode: 'after-camera' }
}

function openDeepBeforeCamera() {
  if (!sheet.value || sheet.value.kind !== 'deep') return
  clearPreview()
  sheet.value = { ...sheet.value, mode: 'before-camera', beforeBlob: null }
}

function clearAfterPreview() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  localWatermark.value = null
}

function clearBeforePreview() {
  if (beforePreviewUrl.value) {
    URL.revokeObjectURL(beforePreviewUrl.value)
    beforePreviewUrl.value = ''
  }
  localBeforeWatermark.value = null
}

function clearPreview() {
  clearAfterPreview()
  clearBeforePreview()
}

function closeSheet() {
  clearPreview()
  sheet.value = null
  flashText.value = ''
}

function continueDaily(current) {
  const next = nextShootRow(inbox.value, current)
  if (next) {
    flashText.value = `已交，下一项：${next.zone_name} · ${next.item_name}`
    sheet.value = { mode: 'standard', row: next }
    return
  }
  closeSheet()
}

function continueDeep(current) {
  const next = nextDeepShootRow(deepInbox.value, current)
  if (next) {
    openDeepCapture(next)
    flashText.value = `已交，下一项：${next.item_name}`
    return
  }
  closeSheet()
}

function continueFix(current) {
  const next = nextFixWorkRow(fixInbox.value, current, { isManager: isManager.value })
  if (!next) {
    closeSheet()
    return
  }
  if (next.status === '待验收' && isManager.value) {
    openFixReview(next)
  } else {
    openFixOriginal(next)
  }
  flashText.value = next.status === '待验收'
    ? `已交，下一张对照：${next.zone_name}`
    : `已交，下一张回拍：${next.zone_name}`
}

async function submitCapture() {
  if (!sheet.value || !sheet.value.blob || busy.value) return
  if (sheet.value.kind === 'fix') {
    await submitFixOpen()
    return
  }
  const row = sheet.value.row
  const latestStandardId = currentStandardId(row.item_id, row.current_standard_id)
  if (standardVersionChanged(sheet.value.seenStandardId, latestStandardId)) {
    errorText.value = '标准图已更新，请先查看新版再拍摄。'
    sheet.value = {
      mode: 'standard',
      row: { ...row, current_standard_id: latestStandardId },
    }
    return
  }
  busy.value = true
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    form.append('shift', row.shift)
    await staffUpload(`/api/hygiene/staff/daily/${row.item_id}/submit`, form)
    clearPreview()
    await loadInbox()
    continueDaily(row)
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
    const current = sheet.value.row
    clearPreview()
    await loadFixTickets()
    continueFix(current)
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
    clearPreview()
    await loadDeepClean()
    continueDeep(row)
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
      const next = openDeep.value.find((item) => item.status === '待验收' && item.item_id !== row.item_id)
      if (next && isManager.value) await openDeepReview(next)
    } else if (sheet.value.kind === 'fix') {
      await staffRequest(`/api/hygiene/staff/fix/${row.id}/${action}`, {
        method: 'POST',
      })
      closeSheet()
      await loadFixTickets()
      const next = nextFixWorkRow(fixInbox.value, row, { isManager: isManager.value })
      if (next) {
        if (next.status === '待验收' && isManager.value) await openFixReview(next)
        else openFixOriginal(next)
        flashText.value = next.status === '待验收'
          ? `下一张对照：${next.zone_name}`
          : `下一张回拍：${next.zone_name}`
      }
    } else {
      await staffRequest(`/api/hygiene/staff/daily/${row.item_id}/${action}`, {
        method: 'POST',
        body: { shift: row.shift },
      })
      closeSheet()
      await loadInbox()
      const next = inbox.value.find((item) => (
        item.status === '待验收' && !(item.item_id === row.item_id && item.shift === row.shift)
      ))
      if (next && isManager.value) await openReview(next)
    }
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="hygiene-staff hygiene-work">
    <StandardPhotoCachePanel />
    <a class="hy-skip" href="#hygiene-work-main">跳到内容</a>
    <header class="hy-work-header">
      <div class="hy-work-header-inner">
        <div class="hy-brand">
          <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
          <span class="hy-brand-text">
            <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
            <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
          </span>
        </div>
        <span v-if="employee" class="hy-work-shift">
          {{ employee.name || employee.phone }} · {{ hygieneShiftLabel(employee.shift) }}
        </span>
      </div>
    </header>

    <main id="hygiene-work-main" class="hy-work-main">
      <p v-if="errorText && !sheet" class="hy-staff-alert" role="alert">{{ errorText }}</p>
      <p
        v-if="needsShiftPick && tab !== 'inbox'"
        class="hy-staff-alert"
        role="status"
      >
        交日常前先选今天班次。
        <button type="button" class="btn btn-primary" @click="tab = 'inbox'">去选班</button>
      </p>

      <section v-if="tab === 'inbox'">
        <template v-if="needsShiftPick">
          <h1>今天上哪一班？</h1>
          <p class="hy-staff-lead">选一次就锁在这个营业日。白班和夜班的日常分开交，选错了要找超级管理员改。</p>
          <div class="hy-shift-choices">
            <button
              v-for="shift in HYGIENE_SHIFTS"
              :key="shift"
              type="button"
              class="btn"
              :class="{ 'btn-primary': shift === '白班' }"
              :disabled="Boolean(picking)"
              @click="pickShift(shift)"
            >
              {{ picking === shift ? '正在锁定…' : shift }}
            </button>
          </div>
        </template>
        <template v-else-if="employee">
          <h1>今天还差什么</h1>
          <div class="hy-work-facts">
            <span>日常 {{ dailyStats.passed }}/{{ dailyStats.total }}</span>
            <span v-if="deepStats.total">专项 {{ deepStats.passed }}/{{ deepStats.total }}</span>
            <span v-if="fixInbox.length">整改 {{ fixInbox.length }}</span>
            <span v-if="queueSummary.overdue" class="is-overdue">超时 {{ queueSummary.overdue }}</span>
            <span v-else-if="queueSummary.soon" class="is-soon">快到时 {{ queueSummary.soon }}</span>
            <span v-if="queueSummary.waiting">等验收 {{ queueSummary.waiting }}</span>
          </div>
          <p class="hy-staff-lead">
            <template v-if="shiftDue">本班 {{ shiftDue }} 前交。专项和整改不跟班次。</template>
            <template v-else>按超时、快到截止、待拍的顺序排好，照下一个做就行。</template>
          </p>

          <article
            v-if="nextWork"
            class="hy-next"
            :class="[`is-${nextWork.bucket}`, `is-${statusTone(nextWork.status)}`]"
          >
            <div class="hy-next-head">
              <span class="hy-task-kind">{{ nextWork.typeLabel }}</span>
              <span class="hy-status" :class="`is-${statusTone(nextWork.status)}`">{{ nextWork.status }}</span>
            </div>
            <h2>{{ nextWork.title }}</h2>
            <p class="hy-next-context">{{ nextWork.context }}</p>
            <p v-if="nextWork.dueText" class="hy-next-due">{{ nextWork.dueText }}</p>
            <button
              type="button"
              class="btn btn-primary btn-block hy-next-action"
              :disabled="busy"
              @click="runQueueTask(nextWork)"
            >{{ nextWork.primaryLabel }}</button>
          </article>

          <p v-else class="hy-staff-done">
            <template v-if="dailyStats.total">今天的卫生待办都交了。等验收不算逾期。</template>
            <template v-else>还没有带标准图的日常检查项。</template>
          </p>

          <section v-for="group in restWorkGroups" :key="group.id" class="hy-queue-group">
            <h2>{{ group.label }} <span>{{ group.rows.length }}</span></h2>
            <button
              v-for="task in group.rows"
              :key="task.key"
              type="button"
              class="hy-work-row"
              :class="[`is-${task.bucket}`, `is-${statusTone(task.status)}`]"
              :disabled="busy"
              @click="runQueueTask(task)"
            >
              <span class="hy-task-kind">{{ task.typeLabel }}</span>
              <span class="hy-work-copy">
                <strong>{{ task.title }}</strong>
                <span>{{ task.context }}</span>
              </span>
              <span class="hy-work-due">{{ task.dueText || task.status }}</span>
              <SvgIcon name="chevron-right" :size="18" />
            </button>
          </section>

          <details v-if="groupedPassed.length" class="hy-done">
            <summary>已通过 {{ dailyStats.passed }}</summary>
            <section v-for="zone in groupedPassed" :key="`done-${zone.id}`" class="hy-section">
              <h2>{{ zone.name }}</h2>
              <article v-for="row in zone.rows" :key="`done-${row.item_id}-${row.shift}`" class="hy-task is-done">
                <div>
                  <strong>{{ row.item_name }}</strong>
                  <p>{{ row.shift }} · 已通过</p>
                </div>
              </article>
            </section>
          </details>
        </template>
        <p v-else class="hy-staff-lead">正在确认登录…</p>
      </section>

      <section v-if="tab === 'deep'" class="hy-section">
        <h1>专项卫生{{ deepStatus ? ` · ${deepStatus}` : '' }}</h1>
        <p v-if="deepStats.total" class="hy-progress">
          {{ deepStats.passed }}/{{ deepStats.total }}
          <span v-if="deepDue"> · {{ deepDue }} 前做完</span>
        </p>
        <p class="hy-staff-lead">不跟班次。每项拍清理前和清理后，不要标准图。</p>
        <p v-if="!deepInbox.length" class="hy-staff-lead">这一轮没有专项卫生。</p>
        <article
          v-for="row in openDeep"
          :key="`deep-${row.item_id}`"
          class="hy-task"
          :class="`is-${statusTone(row.status)}`"
        >
          <button type="button" class="hy-task-main" :disabled="busy" @click="runDeepPrimary(row)">
            <strong>{{ row.item_name }}</strong>
            <p><span class="hy-status" :class="`is-${statusTone(row.status)}`">{{ row.status }}</span></p>
          </button>
          <div class="hy-task-actions">
            <button
              v-if="deepPrimaryAction(row) === 'review'"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openDeepReview(row)"
            >对照</button>
            <button
              v-else-if="canShootDeep(row)"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openDeepCapture(row)"
            >拍前后</button>
            <button
              v-if="canShootDeep(row) && deepPrimaryAction(row) === 'review'"
              type="button"
              class="btn"
              :disabled="busy"
              @click="openDeepCapture(row)"
            >重拍</button>
          </div>
        </article>
        <p v-if="deepInbox.length && !openDeep.length" class="hy-staff-done">今天专项都交了。</p>
        <details v-if="passedDeep.length" class="hy-done">
          <summary>已通过 {{ passedDeep.length }}</summary>
          <article v-for="row in passedDeep" :key="`deep-done-${row.item_id}`" class="hy-task is-done">
            <div>
              <strong>{{ row.item_name }}</strong>
              <p>已通过</p>
            </div>
          </article>
        </details>
      </section>

      <section v-if="tab === 'fix'" class="hy-section">
        <h1>整改单</h1>
        <p class="hy-staff-lead">不跟班次。先看开单原图再拍，镜头不叠图。</p>
        <button
          v-if="isManager"
          type="button"
          class="btn btn-primary hy-staff-submit"
          :disabled="busy"
          @click="openFixForm"
        >开整改单</button>
        <p v-if="!fixInbox.length" class="hy-staff-lead">现在没有整改单。</p>
        <article
          v-for="row in fixInbox"
          :key="`fix-${row.id}`"
          class="hy-task"
          :class="[`is-${statusTone(row.status)}`, `is-${fixUrgency(row)}`]"
        >
          <button type="button" class="hy-task-main" :disabled="busy" @click="runFixPrimary(row)">
            <strong>{{ row.zone_name }} · {{ row.ticket_type }}</strong>
            <p>
              <span class="hy-status" :class="`is-${statusTone(row.status)}`">{{ row.status }}</span>
              · {{ fixDueLabel(row) }}
            </p>
            <p v-if="row.body_text" class="hy-task-body">{{ row.body_text }}</p>
          </button>
          <div class="hy-task-actions">
            <button
              v-if="fixPrimaryAction(row, { isManager }) === 'review'"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openFixReview(row)"
            >对照</button>
            <button
              v-else
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openFixOriginal(row)"
            >回拍</button>
            <button
              v-if="fixPrimaryAction(row, { isManager }) === 'review'"
              type="button"
              class="btn"
              :disabled="busy"
              @click="openFixOriginal(row)"
            >重拍</button>
          </div>
        </article>
      </section>

      <section v-if="tab === 'boards'">
        <h1>红黑榜</h1>
        <p class="hy-staff-lead">本周 {{ weekLabel(boards.week_start) }} 起。只记次数。</p>
        <section class="hy-section">
          <h2>人的红黑榜</h2>
          <p v-if="!(boards.people || []).length" class="hy-staff-lead">这一周还没有人的次数。</p>
          <article v-for="row in boards.people" :key="`person-${row.employee_id}`" class="hy-task">
            <div>
              <strong>{{ personLabel(row) }}</strong>
              <p>实拍 {{ row['实拍'] }} · 驳回 {{ row['驳回'] }} · 一次通过 {{ row['一次通过'] }} · 逾期 {{ row['逾期'] }}</p>
            </div>
          </article>
        </section>
        <section class="hy-section">
          <h2>卫生责任区红黑榜</h2>
          <p v-if="!(boards.zones || []).length" class="hy-staff-lead">这一周还没有卫生责任区的次数。</p>
          <article v-for="row in boards.zones" :key="`zone-${row.zone_id}`" class="hy-task">
            <div>
              <strong>{{ row.zone_name }}</strong>
              <p>逾期 {{ row['逾期'] }}</p>
            </div>
          </article>
        </section>
        <section class="hy-section">
          <h2>卫生教材</h2>
          <p class="hy-staff-lead">超级管理员手点的合格对照。合格图不会自动进来。</p>
          <p v-if="!teaching.length" class="hy-staff-lead">还没有卫生教材。</p>
          <article v-for="row in teaching" :key="`teach-${row.id}`" class="hy-task">
            <div>
              <strong>{{ row.title }}</strong>
              <p>{{ row.left_label }} / {{ row.right_label }}</p>
            </div>
            <button type="button" class="btn" @click="openTeaching(row)">打开</button>
          </article>
        </section>
      </section>

      <section v-if="tab === 'me'">
        <h1>我</h1>
        <template v-if="employee">
          <dl class="hy-meta">
            <div>
              <dt>姓名</dt>
              <dd>{{ employee.name || '未设置' }}</dd>
            </div>
            <div>
              <dt>手机号</dt>
              <dd>{{ employee.phone }}</dd>
            </div>
            <div>
              <dt>当天班次</dt>
              <dd>{{ hygieneShiftLabel(employee.shift) }}</dd>
            </div>
            <div v-if="shiftDue">
              <dt>本班日常截止</dt>
              <dd>{{ shiftDue }} 前交</dd>
            </div>
            <div v-if="deepDue">
              <dt>专项截止</dt>
              <dd>{{ deepDue }} 前做完</dd>
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
          <p class="hy-staff-lead">专项和整改不跟班次。都不能从相册选。</p>
          <button type="button" class="btn btn-block hy-staff-submit" :disabled="loggingOut" @click="logout">
            {{ loggingOut ? '正在退出…' : '退出登录' }}
          </button>
        </template>
        <p v-else class="hy-staff-lead">正在确认登录…</p>
      </section>
    </main>

    <nav v-if="employee" class="hy-tabbar" aria-label="卫生入口">
      <button
        v-for="item in HYGIENE_STAFF_TABS"
        :key="item.id"
        type="button"
        class="hy-tab"
        :class="{ 'is-active': tab === item.id }"
        :aria-current="tab === item.id ? 'page' : undefined"
        @click="tab = item.id"
      >
        <SvgIcon :name="item.icon" :size="20" />
        <span>{{ item.title }}</span>
        <span v-if="tabCount(item.id)" class="hy-tab-badge">{{ tabCount(item.id) }}</span>
      </button>
    </nav>

    <div
      v-if="sheet"
      class="staff-preview"
      role="dialog"
      aria-modal="true"
      aria-labelledby="hygiene-sheet-title"
      @click.self="closeSheet"
    >
      <div class="staff-preview-card">
        <div class="staff-preview-head">
          <h2 id="hygiene-sheet-title">{{ sheetTitle }}</h2>
          <button ref="closeButton" type="button" class="staff-preview-close" @click="closeSheet">关闭</button>
        </div>
        <p class="staff-lead">
          <template v-if="sheet.kind === 'fix'">先看开单原图再拍。镜头不叠图。</template>
          <template v-else-if="sheet.kind === 'teaching'">{{ sheet.row.left_label }} / {{ sheet.row.right_label }}</template>
          <template v-else-if="sheet.kind === 'deep'">清理前 / 清理后</template>
          <template v-else>{{ sheet.row.zone_name }} · {{ sheet.row.shift }}</template>
        </p>
        <p v-if="flashText" class="staff-flash" role="status">{{ flashText }}</p>
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
          <p class="staff-lead">对照标准图，看清角度再拍。</p>
          <HygieneStandardOverlay
            :src="dailyItemStandardUrl('staff', sheet.row)"
            :standard-id="currentStandardId(sheet.row.item_id, sheet.row.current_standard_id)"
            :markup="sheet.row.markup || []"
            :alt="sheet.row.item_name"
          />
          <button
            type="button"
            class="btn btn-primary btn-block staff-submit"
            :disabled="standardMissingOffline(sheet.row.item_id, sheet.row.current_standard_id)"
            @click="openCamera"
          >打开相机</button>
        </template>

        <template v-else-if="sheet.mode === 'before-camera' || sheet.mode === 'after-camera'">
          <p class="staff-lead">{{ sheet.mode === 'before-camera' ? '先拍清理前。' : '再拍清理后。' }}</p>
          <HygieneLiveCamera @captured="onCaptured" />
        </template>

        <HygieneLiveCamera
          v-else-if="sheet.mode === 'camera' || sheet.mode === 'fix-camera' || sheet.mode === 'reshoot-camera'"
          @captured="onCaptured"
        />

        <template v-else-if="sheet.mode === 'original'">
          <p class="staff-lead">对照开单原图，看清圈点再拍。镜头不叠图。</p>
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
            <img v-if="beforePreviewUrl" :src="beforePreviewUrl" alt="清理前">
            <HygieneWatermarkOverlay :watermark="localBeforeWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" @click="openDeepAfterCamera">拍清理后</button>
          <button type="button" class="btn btn-block staff-submit" @click="openDeepBeforeCamera">重拍清理前</button>
        </template>

        <template v-else-if="sheet.mode === 'after-preview'">
          <HygieneReviewPair
            left-label="清理前"
            right-label="清理后"
            :standard-src="beforePreviewUrl"
            :standard-alt="'清理前'"
            :left-watermark="localBeforeWatermark"
            :capture-src="previewUrl"
            :capture-alt="'清理后'"
            :watermark="localWatermark"
          />
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
      </div>
    </div>
  </div>
</template>
