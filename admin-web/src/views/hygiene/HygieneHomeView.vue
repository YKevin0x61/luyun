<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import SvgIcon from '../../components/SvgIcon.vue'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneImageLightbox from '../../components/hygiene/HygieneImageLightbox.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from '../../components/hygiene/HygieneWatermarkOverlay.vue'
import StandardPhotoCachePanel from '../../components/hygiene/StandardPhotoCachePanel.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { FALLBACK_GRACE_MS } from '../../composables/useConnectionFallback'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
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
import { staffRequest } from '../../utils/hygieneStaff'
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
const imageUploads = useImageUploadQueueStore()
const standardPhotoCache = useStandardPhotoCacheStore()
const tab = ref('inbox')
const employee = ref(null)
const errorText = ref('')
const loggingOut = ref(false)
const picking = ref('')
const changingAssignment = ref(false)
const selectedShift = ref('')
const selectedZoneId = ref('')
const profileEditing = ref(false)
const profileName = ref('')
const profilePhone = ref('')
const profileSaving = ref(false)
const profileError = ref('')
const profileFlash = ref('')
const profilePhoneConfirmOpen = ref(false)
const passwordEditing = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordSaving = ref(false)
const passwordError = ref('')
const passwordFlash = ref('')
const lightboxOpen = ref(false)
const lightbox = ref({ src: '', alt: '', markup: [], watermark: null })
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
const confirmCloseOpen = ref(false)
const formError = ref('')
const formErrorField = ref('')
const nowTick = ref(Date.now())
const closeButton = ref(null)
const lastFocusedElement = ref(null)
const zonesLoaded = ref(false)
const boardsLoaded = ref(false)
const teachingLoaded = ref(false)
let clockTimer = null

const assignmentMismatch = computed(() => {
  const current = employee.value
  if (!current || !current.shift || !current.zone_id) return false
  const shifts = current.zone_shifts
  if (!Array.isArray(shifts) || !shifts.length) return false
  return !shifts.includes(current.shift)
})
const needsAssignment = computed(() => {
  return Boolean(employee.value) && (
    !employee.value.shift || !employee.value.zone_id || assignmentMismatch.value
  )
})
const assignableZones = computed(() => {
  const shift = selectedShift.value
  if (!shift) return zones.value
  return zones.value.filter((zone) => (zone.shifts || HYGIENE_SHIFTS).includes(shift))
})
watch(assignableZones, (allowed) => {
  if (!allowed.length) {
    selectedZoneId.value = ''
    return
  }
  if (!allowed.some((zone) => String(zone.id) === String(selectedZoneId.value))) {
    selectedZoneId.value = allowed[0].id
  }
})
const sheetDirty = computed(() => {
  const current = sheet.value
  if (!current) return false
  if (current.mode === 'form') {
    return Boolean(
      String(current.bodyText || '').trim()
        || (current.markup || []).length
    )
  }
  return Boolean(
    current.blob
      || current.beforeBlob
      || current.afterBlob
      || (current.mode && current.mode.endsWith('-preview')),
  )
})
const showAssignmentPicker = computed(() => needsAssignment.value || changingAssignment.value)

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
// 已入队、还没确认上传成功的任务（键与 buildWorkQueue 的 task.key 一致）：让待办
// 立刻把这一项当"交过了"，避免员工在慢网下重拍。
//
// 从 store 的任务列表派生，而不是本组件的 ref：刷新页面 / 回收 webview 之后草稿会被
// 恢复继续传，那时若用局部状态，这一项会重新出现在待办里，员工就会重拍一遍——恰好
// 是这套机制要防的事。任务成功或落到终态失败后不再是 activeTasks，标记自动消失。
const pendingKeys = computed(() => new Set(
  imageUploads.activeTasks
    .map((task) => task.pendingKey)
    .filter(Boolean),
))

/** 上传最终失败：点名提示，让员工知道要重试哪一项（标记由 pendingKeys 自动撤销）。 */
function notifySubmitFailed(label) {
  errorText.value = `「${label}」上传失败，可在上传列表里重试`
}

// 待办页就是「日常」页：专项、整改各自有 tab 与角标，不再混进这一屏的队列。
// 它们仍照常加载（loadDeepClean / loadFixTickets 喂各自的 tab），只是不参与这条队列。
const workQueue = computed(() => buildWorkQueue({
  inbox: inbox.value,
  shiftDue: shiftDue.value,
  now: nowTick.value,
  isManager: isManager.value,
  pendingKeys: pendingKeys.value,
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
  if (!value) {
    if (previous && lastFocusedElement.value instanceof HTMLElement) {
      lastFocusedElement.value.focus()
    }
    document.body.style.overflow = ''
    lastFocusedElement.value = null
    return
  }
  if (previous) return
  lastFocusedElement.value = document.activeElement
  document.body.style.overflow = 'hidden'
  await nextTick()
  if (closeButton.value) closeButton.value.focus()
})

async function loadBoardsAndTeaching({ force = false } = {}) {
  const results = await Promise.allSettled([
    loadBoards({ force }),
    loadTeaching({ force }),
  ])
  const failed = results.find((result) => result.status === 'rejected')
  if (failed) {
    errorText.value = (failed.reason && failed.reason.message) || '无法加载红黑榜和卫生教材'
  }
}

watch(tab, async (id) => {
  if (id === 'boards') {
    errorText.value = ''
    await loadBoardsAndTeaching({ force: true })
  } else if (id === 'fix') {
    await loadZones()
  }
})

watch(needsAssignment, (needed) => {
  if (needed && !zonesLoaded.value) loadZones()
}, { immediate: true })

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
  if (event.key === 'Escape' && sheet.value) {
    requestCloseSheet()
    return
  }
  if (event.key === 'Tab' && sheet.value) {
    const container = document.querySelector('.staff-preview-card')
    if (!container) return
    const focusable = Array.from(container.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ))
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

useHygieneRealtime({
  id: 'hygiene-staff-home',
  resources: [
    'assignment',
    'daily',
    'deep',
    'fix',
    'boards',
    'teaching',
    'zones',
    'settings',
  ],
  pull: async (event) => {
    if (!employee.value) return
    try {
      const resource = event && event.scope && event.scope.resource
      if (resource === 'assignment' || resource === 'settings') {
        await loadMe()
        return
      }
      if (resource === 'daily') await loadInbox()
      if (resource === 'deep') await loadDeepClean()
      if (resource === 'fix') await loadFixTickets()
      // 别人的拍照/开单会广播 boards：只有正开着「榜」那一屏时才需要跟着拉。
      // 切到该 tab 时本来就会强制拉一次（见 watch(tab)），所以这里不拉不会漏。
      if (resource === 'boards' && tab.value === 'boards') {
        await loadBoards({ force: true })
      }
      if (resource === 'teaching' && tab.value === 'boards') {
        await loadTeaching({ force: true })
      }
      // `zones` 这个 resource 承载两件事：责任区列表变更，以及**标准图换版 / 改标注**
      // （action=standard_updated）。已选区的员工不必跟着刷新责任区列表，但必须刷新
      // 待办——否则他手里的标准图与标注还是旧的，而服务端已经换了版本，拍摄前的
      // 「标准图已更新」守卫也就不会触发。
      if (resource === 'zones') {
        if (!employee.value.zone_id) await loadZones({ force: true })
        if (event?.scope?.action === 'standard_updated') await loadInbox()
      }
      if (!resource && employee.value.shift && employee.value.zone_id) {
        await Promise.allSettled([
          loadInbox(),
          loadDeepClean(),
          loadFixTickets(),
          loadZones(),
        ])
      }
    } catch (err) {
      errorText.value = err.message || '无法刷新卫生数据'
    }
  },
})

function openImageLightbox(src, alt, watermark = null, markup = []) {
  if (!src) return
  lightbox.value = { src, alt, watermark, markup }
  lightboxOpen.value = true
}

// 断连提示。nudge 驱动失效时页面会一直停在旧数据上——员工看不出「今天做完了」是
// 不是真的做过，只会照着过期画面判断。8s 宽限避免瞬时抖动闪一下；恢复连接立即消失。
// 兜底轮询（useConnectionFallback）仍在跑，横幅只是把状态说清楚并给一个手动入口。
const wsConnected = inject('wsConnected', null)
const connectionLost = ref(false)
let connectionTimer = null

/** 手动刷新：不挑 resource，全部重拉——断线期间任何变更都可能漏掉。 */
async function refreshAll() {
  errorText.value = ''
  await Promise.allSettled([
    loadMe(),
    loadInbox(),
    loadDeepClean(),
    loadFixTickets(),
    loadZones({ force: true }),
  ])
}

function handleConnectionChange(connected) {
  if (connectionTimer) {
    window.clearTimeout(connectionTimer)
    connectionTimer = null
  }
  if (connected) {
    connectionLost.value = false
    return
  }
  connectionTimer = window.setTimeout(() => {
    connectionTimer = null
    connectionLost.value = true
  }, FALLBACK_GRACE_MS)
}

// App.vue 通过 provide 下发连接状态；单测里可能没挂载 App，缺失时静默跳过。
if (wsConnected) {
  watch(wsConnected, handleConnectionChange, { immediate: true })
}

// 会话失效才回登录页；网络抖动只重试，不动上传队列。原来两者走同一条路，
// 店员在厨房断两秒信号就会丢掉刚拍的照片并被踢出去重登。
const ME_RETRY_DELAYS_MS = [2000, 4000, 8000]
let meRetryTimer = null
let meRetryIndex = 0

function isAuthError(err) {
  return Boolean(err && err.status === 401)
}

function leaveForStaffLogin() {
  imageUploads.clearTasksByTransport('staff')
  router.replace({
    path: '/hygiene/login',
    query: { next: router.currentRoute.value.fullPath },
  })
}

function scheduleMeRetry() {
  if (meRetryTimer || meRetryIndex >= ME_RETRY_DELAYS_MS.length) return
  const delay = ME_RETRY_DELAYS_MS[meRetryIndex]
  meRetryIndex += 1
  meRetryTimer = window.setTimeout(() => {
    meRetryTimer = null
    void loadMe()
  }, delay)
}

onMounted(() => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 30_000)
  window.addEventListener('keydown', onKeydown)
  loadMe()
})

onBeforeUnmount(() => {
  if (clockTimer) window.clearInterval(clockTimer)
  if (meRetryTimer) window.clearTimeout(meRetryTimer)
  meRetryTimer = null
  if (connectionTimer) window.clearTimeout(connectionTimer)
  connectionTimer = null
  window.removeEventListener('keydown', onKeydown)
  standardPhotoCache.setTaskSheetOpen(false)
  document.body.style.overflow = ''
  clearPreview()
})

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
    if (employee.value.shift && !selectedShift.value) {
      selectedShift.value = employee.value.shift
    }
    if (employee.value.zone_id && !selectedZoneId.value) {
      selectedZoneId.value = employee.value.zone_id
    }
    dailyClocks.value = data.daily_clocks || null
    deepClock.value = data.deep_clock || null
    meRetryIndex = 0
    // 取消还没到点的重试：否则成功之后它仍会多跑一次 /me + loadDeepClean。
    if (meRetryTimer) {
      window.clearTimeout(meRetryTimer)
      meRetryTimer = null
    }
  } catch (err) {
    if (isAuthError(err)) {
      errorText.value = err.message || '登录已过期，请重新登录'
      leaveForStaffLogin()
      return
    }
    // 网络问题：留在本页、保住待上传照片，退避重试。
    // 预算用尽后不能再谎称"正在重试"——页面没有手动刷新入口，得把话说明白。
    const exhausted = meRetryIndex >= ME_RETRY_DELAYS_MS.length
    errorText.value = exhausted
      ? '网络还是不通，请走到信号好的地方后重新打开页面'
      : (err.message || '网络不好，正在重试…')
    scheduleMeRetry()
    return
  }
  const jobs = [loadDeepClean]
  if (employee.value && employee.value.shift && employee.value.zone_id) {
    jobs.push(loadInbox, loadFixTickets)
  } else {
    inbox.value = []
    fixInbox.value = []
  }
  const results = await Promise.allSettled(jobs.map((fn) => fn()))
  const failed = results.find((result) => result.status === 'rejected')
  if (failed) {
    if (isAuthError(failed.reason)) {
      leaveForStaffLogin()
      return
    }
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

async function loadZones({ force = false } = {}) {
  if (zonesLoaded.value && !force) return
  const data = await staffRequest('/api/hygiene/staff/daily-catalog')
  zones.value = data.zones || []
  zonesLoaded.value = true
}

async function loadBoards({ force = false } = {}) {
  if (boardsLoaded.value && !force) return
  boards.value = await staffRequest('/api/hygiene/staff/boards')
  boardsLoaded.value = true
}

async function loadTeaching({ force = false } = {}) {
  if (teachingLoaded.value && !force) return
  const data = await staffRequest('/api/hygiene/staff/teaching')
  teaching.value = data.items || []
  teachingLoaded.value = true
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

async function pickAssignment() {
  if (picking.value || !selectedShift.value || !selectedZoneId.value) return
  errorText.value = ''
  picking.value = 'assignment'
  try {
    await staffRequest('/api/hygiene/staff/assignment', {
      method: 'POST',
      body: {
        shift: selectedShift.value,
        zone_id: Number(selectedZoneId.value),
      },
    })
    changingAssignment.value = false
    await loadMe()
  } catch (err) {
    errorText.value = err.message || '选择区域和班次失败'
  } finally {
    picking.value = ''
  }
}

async function openAssignmentPicker() {
  if (!employee.value) return
  changingAssignment.value = true
  tab.value = 'inbox'
  try {
    await loadZones({ force: true })
  } catch (err) {
    errorText.value = err.message || '无法加载卫生责任区'
  }
  await nextTick()
  const main = document.getElementById('hygiene-work-main')
  if (main) main.scrollTo({ top: 0, behavior: 'smooth' })
}

function startProfileEdit() {
  passwordEditing.value = false
  profileEditing.value = true
  profileError.value = ''
  profileFlash.value = ''
  profileName.value = (employee.value && employee.value.name) || ''
  profilePhone.value = (employee.value && employee.value.phone) || ''
}

function cancelProfileEdit() {
  profileEditing.value = false
  profileError.value = ''
}

const PHONE_PATTERN = /^1[3-9]\d{9}$/

/** 手机号是登录账号，改错一位 = 下次登不进来。改号必须先确认。 */
function phoneChanged() {
  const current = String((employee.value && employee.value.phone) || '')
  return profilePhone.value.trim() !== current
}

async function saveProfile() {
  if (profileSaving.value || !employee.value) return
  const nextPhone = profilePhone.value.trim()
  if (!PHONE_PATTERN.test(nextPhone)) {
    profileError.value = '手机号格式不对，应该是 11 位、以 1 开头的号码。'
    return
  }
  // 姓名随便改，手机号不行：它是登录账号，且 30 天内只能靠管理员救回来。
  if (phoneChanged() && !profilePhoneConfirmOpen.value) {
    profilePhoneConfirmOpen.value = true
    return
  }
  profilePhoneConfirmOpen.value = false
  profileSaving.value = true
  profileError.value = ''
  profileFlash.value = ''
  try {
    const data = await staffRequest('/api/hygiene/staff/me', {
      method: 'PATCH',
      body: {
        name: profileName.value.trim(),
        phone: nextPhone,
      },
    })
    employee.value = { ...employee.value, ...(data.employee || {}) }
    profileEditing.value = false
    profileFlash.value = '个人信息已保存，手机号下次登录生效。'
  } catch (err) {
    profileError.value = err.message || '保存个人信息失败'
  } finally {
    profileSaving.value = false
  }
}

function startPasswordEdit() {
  profileEditing.value = false
  passwordEditing.value = true
  passwordError.value = ''
  passwordFlash.value = ''
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
}

function cancelPasswordEdit() {
  passwordEditing.value = false
  passwordError.value = ''
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
}

async function savePassword() {
  if (passwordSaving.value) return
  passwordError.value = ''
  passwordFlash.value = ''
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordSaving.value = true
  try {
    await staffRequest('/api/hygiene/staff/password', {
      method: 'PATCH',
      body: {
        current_password: currentPassword.value,
        new_password: newPassword.value,
        confirm_password: confirmPassword.value,
      },
    })
    passwordEditing.value = false
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    passwordFlash.value = '密码已修改，其他设备上的登录已失效。'
  } catch (err) {
    passwordError.value = err.message || '修改密码失败'
  } finally {
    passwordSaving.value = false
  }
}

// 登出会清掉本机的上传队列（换人用同一台手机必须清），但店员可能是手滑点到的：
// 队列里还有照片时先问一句，别让他白拍一轮。
const logoutConfirmOpen = ref(false)

function askLogout() {
  if (loggingOut.value) return
  if (imageUploads.activeTasks.length || imageUploads.failedTasks.length) {
    logoutConfirmOpen.value = true
    return
  }
  void logout()
}

async function logout() {
  if (loggingOut.value) return
  logoutConfirmOpen.value = false
  loggingOut.value = true
  imageUploads.clearTasksByTransport('staff')
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
  formError.value = ''
  flashText.value = ''
  if (!isManager.value) return
  if (needsAssignment.value) {
    errorText.value = '先选今天的区域和班次。'
    return
  }
  if (!liveOk.value) {
    errorText.value = '无法打开相机。请在浏览器设置中允许相机权限，或换一部手机；卫生拍照必须现场完成。'
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
  if (!sheet.value.zoneId) {
    formError.value = '请选择卫生责任区。'
    formErrorField.value = 'fix-zone'
  } else if (!sheet.value.ticketType) {
    formError.value = '请选择整改类型。'
    formErrorField.value = 'fix-type'
  } else if (!sheet.value.bodyText.trim()) {
    formError.value = '请写明哪里脏、怎么改。'
    formErrorField.value = 'fix-body'
  } else if (!sheet.value.durationHours || sheet.value.durationHours <= 0) {
    formError.value = '请填写大于 0 的整改时限。'
    formErrorField.value = 'fix-duration'
  } else {
    formError.value = ''
    formErrorField.value = ''
    errorText.value = ''
    clearPreview()
    sheet.value = { ...sheet.value, mode: 'fix-camera' }
    return
  }
  nextTick(() => {
    const field = document.getElementById(formErrorField.value)
    field?.focus()
  })
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

function requestCloseSheet() {
  if (sheetDirty.value) {
    confirmCloseOpen.value = true
    return
  }
  closeSheet()
}

function closeSheet(force = false) {
  if (sheetDirty.value && !force) {
    requestCloseSheet()
    return
  }
  confirmCloseOpen.value = false
  clearPreview()
  sheet.value = null
  flashText.value = ''
}

function continueDaily(current) {
  const next = nextShootRow(inbox.value, current)
  if (next) {
    // 「已上传」而不是「已交」：此时只是进了本地上传队列，服务端还没确认。
    flashText.value = `已上传，下一项：${next.zone_name} · ${next.item_name}`
    sheet.value = { mode: 'standard', row: next }
    return
  }
  closeSheet(true)
}

function continueDeep(current) {
  const next = nextDeepShootRow(deepInbox.value, current)
  if (next) {
    openDeepCapture(next)
    flashText.value = `已上传，下一项：${next.item_name}`
    return
  }
  closeSheet(true)
}

function continueFix(current) {
  const next = nextFixWorkRow(fixInbox.value, current, { isManager: isManager.value })
  if (!next) {
    closeSheet(true)
    return
  }
  if (next.status === '待验收' && isManager.value) {
    openFixReview(next)
  } else {
    openFixOriginal(next)
  }
  flashText.value = next.status === '待验收'
    ? `已上传，下一张对照：${next.zone_name}`
    : `已上传，下一张回拍：${next.zone_name}`
}

function submitCapture() {
  if (!sheet.value || !sheet.value.blob || busy.value) return
  if (sheet.value.kind === 'fix') {
    submitFixOpen()
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
  const pendingKey = `daily:${row.item_id}:${row.shift}`
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    form.append('shift', row.shift)
    imageUploads.enqueue({
      pendingKey,
      transport: 'staff',
      path: `/api/hygiene/staff/daily/${row.item_id}/submit`,
      formData: form,
      label: `日常实拍 · ${row.item_name}`,
      detail: `${row.zone_name} · ${row.shift}`,
      onSuccess: loadInbox,
      onError: () => notifySubmitFailed(row.item_name),
    })
    clearPreview()
    continueDaily(row)
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function submitFixOpen() {
  if (!sheet.value || !sheet.value.blob || busy.value) return
  errorText.value = ''
  const current = sheet.value
  const zone = zones.value.find((row) => String(row.id) === String(current.zoneId))
  try {
    const form = new FormData()
    form.append('file', current.blob, 'capture.jpg')
    form.append('live', 'true')
    form.append('zone_id', String(current.zoneId))
    form.append('ticket_type', current.ticketType)
    form.append('body_text', current.bodyText.trim())
    form.append('duration_hours', String(current.durationHours))
    form.append('markup', JSON.stringify(current.markup || []))
    imageUploads.enqueue({
      transport: 'staff',
      path: '/api/hygiene/staff/fix',
      formData: form,
      label: `整改开单 · ${zone ? zone.name : '卫生责任区'}`,
      detail: current.ticketType,
      onSuccess: loadFixTickets,
      // 开单是新增，待办里本来没有这一项，所以只提示失败、不占 pending 位。
      onError: () => {
        errorText.value = '整改开单上传失败，可在上传列表里重试'
      },
    })
    closeSheet(true)
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function submitFixReshoot() {
  if (!sheet.value || !sheet.value.blob || !sheet.value.row || busy.value) return
  errorText.value = ''
  const current = sheet.value.row
  const pendingKey = `fix:${current.id}`
  try {
    const form = new FormData()
    form.append('file', sheet.value.blob, 'capture.jpg')
    form.append('live', 'true')
    imageUploads.enqueue({
      pendingKey,
      transport: 'staff',
      path: `/api/hygiene/staff/fix/${current.id}/reshoot`,
      formData: form,
      label: `整改回拍 · ${current.zone_name || '卫生责任区'}`,
      detail: current.ticket_type || '',
      onSuccess: loadFixTickets,
      onError: () => notifySubmitFailed(`整改回拍 · ${current.zone_name || ''}`.trim()),
    })
    clearPreview()
    continueFix(current)
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function submitDeepPair() {
  if (!sheet.value || !sheet.value.beforeBlob || !sheet.value.afterBlob || busy.value) return
  const row = sheet.value.row
  const pendingKey = `deep:${row.item_id}`
  errorText.value = ''
  try {
    const form = new FormData()
    form.append('before', sheet.value.beforeBlob, 'before.jpg')
    form.append('after', sheet.value.afterBlob, 'after.jpg')
    form.append('live', 'true')
    imageUploads.enqueue({
      pendingKey,
      transport: 'staff',
      path: `/api/hygiene/staff/deep-clean/${row.item_id}/submit`,
      formData: form,
      label: `专项前后 · ${row.item_name}`,
      detail: '清理前 + 清理后',
      onSuccess: loadDeepClean,
      onError: () => notifySubmitFailed(row.item_name),
    })
    clearPreview()
    continueDeep(row)
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

// 管理员在手机上验收时的驳回：与管理后台的三个视图保持一致——不可撤销的操作要确认，
// 并且可以写一句原因（员工端会显示在待办行上）。这屏的"驳回"紧挨着"通过"，误触代价一样。
const rejectConfirmOpen = ref(false)

function askReject() {
  if (busy.value || !sheet.value) return
  rejectConfirmOpen.value = true
}

async function confirmReject(reason) {
  rejectConfirmOpen.value = false
  await decide('reject', reason)
}

async function decide(action, reason = '') {
  if (!sheet.value || busy.value) return
  if (sheet.value.kind !== 'fix' && !sheet.value.review) return
  const row = sheet.value.row
  const deep = isDeepSheet()
  busy.value = true
  errorText.value = ''
  // 驳回可以带一句原因，员工端会显示出来。管理员在手机上（走这条路径）与在管理后台
  // 走的是同一套后端接口，行为要一致。
  const rejectBody = action === 'reject' && reason ? { reason } : undefined
  try {
    if (deep) {
      await staffRequest(`/api/hygiene/staff/deep-clean/${row.item_id}/${action}`, {
        method: 'POST',
        body: rejectBody,
      })
      closeSheet(true)
      await loadDeepClean()
      const next = openDeep.value.find((item) => item.status === '待验收' && item.item_id !== row.item_id)
      if (next && isManager.value) await openDeepReview(next)
    } else if (sheet.value.kind === 'fix') {
      await staffRequest(`/api/hygiene/staff/fix/${row.id}/${action}`, {
        method: 'POST',
        body: rejectBody,
      })
      closeSheet(true)
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
        body: { shift: row.shift, ...(rejectBody || {}) },
      })
      closeSheet(true)
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
    <HygieneImageLightbox
      v-if="lightboxOpen"
      :src="lightbox.src"
      :alt="lightbox.alt"
      :markup="lightbox.markup"
      :watermark="lightbox.watermark"
      @close="lightboxOpen = false"
    />
    <a class="hy-skip" href="#hygiene-work-main">跳到内容</a>
    <header class="hy-work-header" :inert="Boolean(sheet)">
      <div class="hy-work-header-inner">
        <div class="hy-brand">
          <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
          <span class="hy-brand-text">
            <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
            <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
          </span>
        </div>
        <button
          v-if="employee"
          type="button"
          class="hy-work-shift"
          aria-label="重新选择区域和班次"
          @click="openAssignmentPicker"
        >
          {{ employee.name || employee.phone }} · {{ employee.zone_name || '未选区域' }} · {{ hygieneShiftLabel(employee.shift) }}
        </button>
      </div>
    </header>

    <main id="hygiene-work-main" class="hy-work-main" :inert="Boolean(sheet)">
      <p v-if="connectionLost && !sheet" class="hy-staff-alert hy-staff-offline" role="status">
        和服务器断了，这一页上的数据可能不是最新的。
        <button type="button" class="btn" @click="refreshAll">刷新</button>
      </p>
      <p v-if="errorText && !sheet" class="hy-staff-alert" role="alert">{{ errorText }}</p>
      <p
        v-if="needsAssignment && tab !== 'inbox'"
        class="hy-staff-alert"
        role="status"
      >
        交日常前先选今天区域和班次。
        <button type="button" class="btn btn-primary" @click="tab = 'inbox'">去选择</button>
      </p>

      <section v-if="tab === 'inbox'">
        <template v-if="showAssignmentPicker">
          <h1>{{ changingAssignment ? '重新选择区域和班次' : '今天负责哪个区域、上哪一班？' }}</h1>
          <p class="hy-staff-lead">区域和班次可以在当天随时重新选择。只显示和允许提交当前所选区域的日常与整改。</p>
          <h2>卫生责任区</h2>
          <div class="hy-shift-choices">
            <button
              v-for="zone in assignableZones"
              :key="zone.id"
              type="button"
              class="btn"
              :class="{ 'btn-primary': String(selectedZoneId) === String(zone.id) }"
              :disabled="Boolean(picking)"
              @click="selectedZoneId = zone.id"
            >{{ zone.name }}</button>
          </div>
          <p v-if="selectedShift && !assignableZones.length" class="hy-staff-lead">这个班次暂时没有责任区。</p>
          <h2>班次</h2>
          <div class="hy-shift-choices">
            <button
              v-for="shift in HYGIENE_SHIFTS"
              :key="shift"
              type="button"
              class="btn"
              :class="{ 'btn-primary': selectedShift === shift }"
              :disabled="Boolean(picking)"
              @click="selectedShift = shift"
            >{{ shift }}</button>
          </div>
          <button
            type="button"
            class="btn btn-primary btn-block hy-staff-submit"
            :disabled="Boolean(picking) || !selectedShift || !assignableZones.some((zone) => String(zone.id) === String(selectedZoneId))"
            @click="pickAssignment"
          >{{ picking ? '正在保存…' : (changingAssignment ? '保存区域和班次' : '确认区域和班次') }}</button>
        </template>
        <template v-else-if="employee">
          <h1>今天还差什么</h1>
          <div class="hy-work-facts">
            <span>日常 {{ dailyStats.passed }}/{{ dailyStats.total }}</span>
            <span v-if="queueSummary.overdue" class="is-overdue">超时 {{ queueSummary.overdue }}</span>
            <span v-else-if="queueSummary.soon" class="is-soon">快到时 {{ queueSummary.soon }}</span>
            <span v-if="queueSummary.waiting">等验收 {{ queueSummary.waiting }}</span>
          </div>
          <p class="hy-staff-lead">
            <template v-if="shiftDue">本班 {{ shiftDue }} 前交。专项和整改在各自那一屏。</template>
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

          <div v-else class="hy-staff-done">
            <p v-if="dailyStats.total">今天的卫生待办都交了。等验收不算逾期。</p>
            <template v-else>
              <!-- 说清是「你选的这个区没有」：原来只写「还没有带标准图的日常检查项」，
                   管理者在别的区建好标准图后到这一屏核对，会以为图丢了。 -->
              <p>当前区域「{{ employee.zone_name || '未选区域' }}」没有带标准图的日常检查项。</p>
              <p class="hy-staff-lead">
                日常检查项挂在责任区下，这一屏只列你选的这个区；管理端能看到全部区。
              </p>
              <button type="button" class="btn" @click="openAssignmentPicker">换个区域看看</button>
            </template>
          </div>

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
              <span v-if="task.rejected" class="hy-work-reject">
                {{ task.rejectReason ? `已驳回：${task.rejectReason}` : '已驳回，请重拍' }}
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
        <p class="hy-staff-lead">全店专项，不按责任区或班次。每项拍清理前和清理后。</p>
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
        <p class="hy-staff-lead">按所选区域显示和提交。先看开单原图再拍，镜头不叠图。</p>
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
          <p class="hy-staff-lead">只展示超级管理员手动标记的合格对照。</p>
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
          <p v-if="profileFlash" class="staff-flash" role="status">{{ profileFlash }}</p>
          <p v-if="passwordFlash" class="staff-flash" role="status">{{ passwordFlash }}</p>
          <p v-if="profileError" class="hy-staff-alert" role="alert">{{ profileError }}</p>
          <p v-if="passwordError" class="hy-staff-alert" role="alert">{{ passwordError }}</p>
          <div v-if="profileEditing" class="staff-profile-edit">
            <label class="staff-field">
              姓名
              <input
                v-model="profileName"
                class="staff-input"
                type="text"
                maxlength="40"
                autocomplete="name"
              >
            </label>
            <label class="staff-field">
              手机号
              <input
                v-model="profilePhone"
                class="staff-input"
                type="tel"
                inputmode="numeric"
                maxlength="11"
                pattern="1[3-9]\d{9}"
                autocomplete="username"
              >
            </label>
            <p class="hy-staff-lead">手机号也是登录账号；保存后请用新手机号登录。改号前会再确认一次。</p>
            <div class="staff-decide">
              <button type="button" class="btn btn-primary" :disabled="profileSaving" @click="saveProfile">
                {{ profileSaving ? '正在保存…' : '保存' }}
              </button>
              <button type="button" class="btn" :disabled="profileSaving" @click="cancelProfileEdit">取消</button>
            </div>
          </div>
          <div v-else-if="passwordEditing" class="staff-profile-edit">
            <label class="staff-field">
              当前密码
              <input
                v-model="currentPassword"
                class="staff-input"
                type="password"
                autocomplete="current-password"
              >
            </label>
            <label class="staff-field">
              新密码
              <input
                v-model="newPassword"
                class="staff-input"
                type="password"
                minlength="8"
                autocomplete="new-password"
              >
            </label>
            <label class="staff-field">
              确认新密码
              <input
                v-model="confirmPassword"
                class="staff-input"
                type="password"
                minlength="8"
                autocomplete="new-password"
              >
            </label>
            <p class="hy-staff-lead">修改后保留当前设备登录，其他设备上的登录会失效。</p>
            <div class="staff-decide">
              <button type="button" class="btn btn-primary" :disabled="passwordSaving" @click="savePassword">
                {{ passwordSaving ? '正在保存…' : '修改密码' }}
              </button>
              <button type="button" class="btn" :disabled="passwordSaving" @click="cancelPasswordEdit">取消</button>
            </div>
          </div>
          <dl v-else class="hy-meta">
            <div>
              <dt>姓名</dt>
              <dd>{{ employee.name || '未设置' }}</dd>
            </div>
            <div>
              <dt>手机号</dt>
              <dd>{{ employee.phone }}</dd>
            </div>
            <div>
              <dt>当天区域</dt>
              <dd>{{ employee.zone_name || '未选' }}</dd>
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
          <p v-if="!profileEditing && !passwordEditing" class="hy-staff-lead">专项全店可用；整改按所选责任区显示。所有现场照片都需实拍。</p>
          <button
            v-if="!profileEditing && !passwordEditing"
            type="button"
            class="btn btn-block hy-staff-submit"
            @click="startProfileEdit"
          >修改个人信息</button>
          <button
            v-if="!profileEditing && !passwordEditing"
            type="button"
            class="btn btn-block hy-staff-submit"
            @click="startPasswordEdit"
          >修改密码</button>
          <button
            v-if="!profileEditing && !passwordEditing"
            type="button"
            class="btn btn-block hy-staff-submit"
            :disabled="loggingOut"
            @click="askLogout"
          >
            {{ loggingOut ? '正在退出…' : '退出登录' }}
          </button>
          <button
            v-if="!profileEditing && !passwordEditing"
            type="button"
            class="btn btn-block hy-staff-submit"
            @click="openAssignmentPicker"
          >重新选择区域和班次</button>
        </template>
        <p v-else class="hy-staff-lead">正在确认登录…</p>
      </section>
    </main>

    <nav v-if="employee" class="hy-tabbar" aria-label="卫生入口" :inert="Boolean(sheet)">
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
      @click.self="requestCloseSheet"
    >
      <div class="staff-preview-card">
        <div class="staff-preview-head">
          <h2 id="hygiene-sheet-title">{{ sheetTitle }}</h2>
          <button ref="closeButton" type="button" class="staff-preview-close" @click="requestCloseSheet">关闭</button>
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
          <p v-if="formError" class="staff-alert" role="alert">{{ formError }}</p>
          <label class="staff-field">
            卫生责任区
            <select
              id="fix-zone"
              v-model="sheet.zoneId"
              class="staff-input"
              :aria-invalid="formErrorField === 'fix-zone'"
              @change="formError = ''; formErrorField = ''"
            >
              <option v-for="zone in zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
            </select>
          </label>
          <label class="staff-field">
            类型
            <select
              id="fix-type"
              v-model="sheet.ticketType"
              class="staff-input"
              :aria-invalid="formErrorField === 'fix-type'"
              @change="formError = ''; formErrorField = ''"
            >
              <option v-for="kind in HYGIENE_FIX_TYPES" :key="kind" :value="kind">{{ kind }}</option>
            </select>
          </label>
          <label class="staff-field">
            时限（小时）
            <input
              id="fix-duration"
              v-model.number="sheet.durationHours"
              class="staff-input"
              type="number"
              min="1"
              step="1"
              :aria-invalid="formErrorField === 'fix-duration'"
              @input="formError = ''; formErrorField = ''"
            >
          </label>
          <label class="staff-field">
            哪里脏、怎么改
            <textarea
              id="fix-body"
              v-model="sheet.bodyText"
              class="staff-input"
              rows="3"
              maxlength="400"
              :aria-invalid="formErrorField === 'fix-body'"
              @input="formError = ''; formErrorField = ''"
            ></textarea>
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
              :lightbox-watermark="localWatermark"
              alt="刚拍的整改原图"
              @point="addFixCircle"
            />
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <p class="staff-lead">点图画面圈，可选。</p>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitFixOpen">
            开整改单
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openFixCameraFromForm">重拍</button>
        </template>

        <template v-else-if="sheet.mode === 'reshoot-preview'">
          <div class="capture-preview">
            <button
              v-if="previewUrl"
              type="button"
              class="preview-zoom"
              aria-label="全屏查看刚拍的回拍"
              @click="openImageLightbox(previewUrl, '刚拍的回拍', localWatermark)"
            >
              <img :src="previewUrl" alt="刚拍的回拍">
            </button>
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitFixReshoot">
            提交回拍
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openFixReshootCamera">重拍</button>
        </template>

        <template v-else-if="sheet.mode === 'before-preview'">
          <div class="capture-preview">
            <button
              v-if="beforePreviewUrl"
              type="button"
              class="preview-zoom"
              aria-label="全屏查看清理前照片"
              @click="openImageLightbox(beforePreviewUrl, '清理前', localBeforeWatermark)"
            >
              <img :src="beforePreviewUrl" alt="清理前">
            </button>
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
            提交这一组待验收
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openDeepAfterCamera">重拍清理后</button>
        </template>

        <template v-else-if="sheet.mode === 'preview'">
          <div class="capture-preview">
            <button
              v-if="previewUrl"
              type="button"
              class="preview-zoom"
              aria-label="全屏查看刚拍的实拍"
              @click="openImageLightbox(previewUrl, '刚拍的实拍', localWatermark)"
            >
              <img :src="previewUrl" alt="刚拍的实拍">
            </button>
            <HygieneWatermarkOverlay :watermark="localWatermark" />
          </div>
          <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="submitCapture">
            提交待验收
          </button>
          <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="openCamera">重拍</button>
        </template>

        <template v-else-if="sheet.kind === 'teaching' && sheet.row">
          <HygieneReviewPair
            :left-label="sheet.row.left_label"
            :right-label="sheet.row.right_label"
            :standard-src="teachingShotUrl('staff', sheet.row, 'left', 'preview')"
            :original-standard-src="teachingShotUrl('staff', sheet.row, 'left')"
            :standard-markup="sheet.row.left_markup || []"
            :standard-alt="sheet.row.left_label"
            :capture-src="teachingShotUrl('staff', sheet.row, 'right', 'preview')"
            :original-capture-src="teachingShotUrl('staff', sheet.row, 'right')"
            :capture-alt="sheet.row.right_label"
          />
        </template>

        <template v-else-if="sheet.kind === 'fix' && sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            left-label="开单原图"
            right-label="回拍"
            :standard-src="fixOriginalUrl('staff', sheet.row, 'preview')"
            :original-standard-src="fixOriginalUrl('staff', sheet.row)"
            :standard-markup="sheet.review.markup || []"
            :standard-alt="sheet.row.ticket_type"
            :capture-src="sheet.row.reshoot_capture_id ? fixReshootUrl('staff', sheet.row, 'preview') : ''"
            :original-capture-src="sheet.row.reshoot_capture_id ? fixReshootUrl('staff', sheet.row) : ''"
            :capture-alt="'回拍'"
            :left-watermark="sheet.review.open_watermark"
            :watermark="sheet.review.watermark"
          />
          <p v-if="isManager && !canDecideFix(sheet.row)" class="staff-lead">时限还没到，只有开单人能验。</p>
          <div v-if="canDecideFix(sheet.row)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="askReject">驳回</button>
          </div>
        </template>

        <template v-else-if="sheet.kind === 'deep' && sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            left-label="清理前"
            right-label="清理后"
            :standard-src="deepCleanShotUrl('staff', sheet.row, 'before', 'preview')"
            :original-standard-src="deepCleanShotUrl('staff', sheet.row, 'before')"
            :standard-alt="'清理前'"
            :left-watermark="sheet.review.before_watermark"
            :capture-src="deepCleanShotUrl('staff', sheet.row, 'after', 'preview')"
            :original-capture-src="deepCleanShotUrl('staff', sheet.row, 'after')"
            :capture-alt="'清理后'"
            :watermark="sheet.review.after_watermark || sheet.review.watermark"
          />
          <p v-if="isManager && !canDecide(sheet.review)" class="staff-lead">交这一组的人不能自己验收。</p>
          <div v-if="canDecide(sheet.review)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="askReject">驳回</button>
          </div>
        </template>

        <template v-else-if="sheet.mode === 'review' && sheet.review">
          <HygieneReviewPair
            :standard-src="frozenStandardUrl('staff', sheet.row, 'preview')"
            :original-standard-src="frozenStandardUrl('staff', sheet.row)"
            :standard-markup="sheet.review.frozen_markup || []"
            :standard-alt="sheet.row.item_name"
            :capture-src="dailyCaptureUrl('staff', sheet.row, 'preview')"
            :original-capture-src="dailyCaptureUrl('staff', sheet.row)"
            :capture-alt="'实拍'"
            :watermark="sheet.review.watermark"
          />
          <p v-if="isManager && !canDecide(sheet.review)" class="staff-lead">交这张的人不能自己验收。</p>
          <div v-if="canDecide(sheet.review)" class="staff-decide">
            <button type="button" class="btn btn-primary btn-block staff-submit" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-block staff-submit" :disabled="busy" @click="askReject">驳回</button>
          </div>
        </template>
      </div>
    </div>
    <ConfirmDialog
      v-if="confirmCloseOpen"
      title="放弃未提交的内容？"
      message="关闭后，刚填写的说明、标注和照片不会被保存。"
      confirm-label="放弃"
      danger
      @confirm="closeSheet(true)"
      @cancel="confirmCloseOpen = false"
    />

    <ConfirmDialog
      v-if="logoutConfirmOpen"
      title="还有照片没传完"
      :message="`还有 ${imageUploads.activeTasks.length + imageUploads.failedTasks.length} 张照片在上传队列里。退出登录会清掉它们，需要重新拍。`"
      confirm-label="仍然退出"
      danger
      @confirm="logout"
      @cancel="logoutConfirmOpen = false"
    />

    <ConfirmDialog
      v-if="rejectConfirmOpen"
      title="驳回这一项"
      :message="`驳回「${sheet ? (sheet.row.item_name || sheet.row.zone_name || '这一项') : '这一项'}」后要重新拍；本周红黑榜会记一次驳回。`"
      confirm-label="驳回"
      danger
      :prompt="{ label: '哪里不合格（可选，员工能看到）', placeholder: '例如：台面还有油渍', maxlength: 120 }"
      @confirm="confirmReject"
      @cancel="rejectConfirmOpen = false"
    />

    <ConfirmDialog
      v-if="profilePhoneConfirmOpen"
      title="确认改手机号"
      :message="`手机号是登录账号。改成 ${profilePhone.trim()} 之后，下次登录要用新号；打错一位就得找管理员改回来。`"
      confirm-label="确认改号"
      danger
      @confirm="saveProfile"
      @cancel="profilePhoneConfirmOpen = false"
    />
  </div>
</template>
