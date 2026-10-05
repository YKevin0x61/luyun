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
import { ADMIN_CAP_STAFF_DEFS, hasCap, normalizeCaps } from '../../utils/adminCaps'
import {
  HYGIENE_FIX_TYPES,
  canAcceptFixTicket,
  hasLiveCamera,
  hygieneDocumentTitle,
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
  isPendingReview,
  nextDeepShootRow,
  nextFixWorkRow,
  nextShootRow,
  openRows,
  passedRows,
  queueActionLabel,
  queueGroups,
  shiftClock,
  statusTone,
  tabWorkCount,
} from '../../utils/hygieneWorkFlow'
import { loginRedirectTarget } from '../../utils/loginNext'

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
const imageUploads = useImageUploadQueueStore()
const standardPhotoCache = useStandardPhotoCacheStore()
// **没有页内 tab 了**（2026-10-05 用户裁定）：原来待办 / 专项 / 整改 / 榜 / 我 五格平级，
// 把"任务""信息""设置"三种性质混在一条 tab 条上，员工看不出哪一格才是今天要干的活。
// 现在这一页**依次渲染**：日常 → 专项 → 整改（三组都是任务，组标题各自带条数），
// 「榜」降级成页尾一行信息入口（`boardsOpen`），「我」整块搬到「今天」页（账号设置不属于
// 干活的那一屏）。下面的分节名与那道入口都跟着这套结构走。
const employee = ref(null)
const errorText = ref('')
// 票 10：员工不再自己选班次和区（`selectedShift` / `selectedZoneId` / `assignableZones`
// 那一套状态跟着选择器一起撤了）。今天在哪由排班决定，`employee.shift` / `employee.zone_id`
// 就是从排班结果读来的那两个值。
/** 「榜」那一屏：从平级 tab 降级成页尾一行"信息入口"，点开**在本页展开**（不是跳去另一个
 *  面板）。选它是因为改动最小、也最不容易出错 —— 红黑榜与卫生教材本来就渲染在这一页里，
 *  展开只是把 `v-if` 换成这一位；跳转则要新开一条路由 / 一个页面，还得再搬一次登录与
 *  实时那套（这一屏的数据是员工 cookie 读的，落到店长那侧的 `/workbench/floor/boards`
 *  只会吃 403）。默认收起：它是"信息"，不是今天要干的活。 */
const boardsOpen = ref(false)
const lightboxOpen = ref(false)
const lightbox = ref({ src: '', alt: '', markup: [], watermark: null })
const inbox = ref([])
/** 「待我验收」（只有开了「日常验收」这一项的人会填）：全店今天的待验收、不含自己交的。
 *  它的验收权本来就是全店的，但这条入口以前**不存在** —— 默认队列（`inbox`）被锁在
 *  「本区 + 本班次」，今天别的区有人交了，他一条也看不到，权力等于没有入口
 *  （2026-10-05 用户确认：员工账号的管理员同样有验收权）。
 *  2026-10-05 同日改判据：这一档问的是**「日常验收」那一项开关**，不是「是不是管理员」
 *  —— 只开了整改单的人不该在这里看到日常的待验收活。 */
const reviewInbox = ref([])
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
// 这一屏要显示「今天为什么交不了日常」：就是 `needsAssignment`（没班次 / 没区 /
// 排班的班次跟这个区对不上）。以前这里还有个「正在改分工」的开关，员工自选撤掉之后
// 没有第二种进入方式了，名字也跟着从 `showAssignmentPicker` 换过来。
const showWhyNoDuty = computed(() => needsAssignment.value)

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

// ── 管理权限的判据（2026-10-05 用户裁定：由档位换成逐项开关）──────────────────────────
//
// 以前是 `employee.permission === '管理员'` 一个字符串判到底：勾上「管理员」就同时拿到
// 验收、开单、待我验收三件事，表达不出"只该判日常、不该开整改单"。现在服务端发下来的是
// `employee.admin_caps`（十项开关的数组），**每一件事按自己那一项判**：
// 日常验收 → daily_review，专项验收 → deep_review，整改单 → fix，待我验收 → daily_review。
//
// 判据只在这一处算成 computed，页面里不再散落任何 `permission === '管理员'` 的比较
// （`normalizeCaps` / `hasCap` 都在 `utils/adminCaps.js`，那里也写着为什么标签列还留着）。
const caps = computed(() => normalizeCaps(employee.value && employee.value.admin_caps))
/** 有没有**任意一项**管理权限。它不再是"是不是管理员"那个档位，只用来回答"这个人跟普通
 *  员工有没有区别"这类粗细问题；具体一件事能不能做，一律看下面那几个开关。 */
const isManager = computed(() => caps.value.length > 0)
const canDailyReview = computed(() => hasCap(caps.value, 'daily_review'))
const canDeepReview = computed(() => hasCap(caps.value, 'deep_review'))
const canFix = computed(() => hasCap(caps.value, 'fix'))
/** 他手里**真正能用**的管理能力有几项（就是花名册上可勾的那三项）。
 *  页首那颗身份胶囊用它 —— 顶栏只写「员工（张三）」，被放权的这一档在界面上一直看不出来
 *  （真机走查 O-id：他自己不知道有验收权，也看不到"我在店里能做哪三件事"）。 */
const staffCapCount = computed(
  () => ADMIN_CAP_STAFF_DEFS.filter((item) => hasCap(caps.value, item.key)).length,
)
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

// 日常队列就是这一组：专项、整改各自成组，不再混进这条队列。
// 它们仍照常加载（loadDeepClean / loadFixTickets 喂下面那两组），只是不参与这条队列。
//
// 文案要按**这一项自己的开关**重算一遍：`buildWorkQueue` 是按"一个档位"写的（只收一个
// `isManager`），拿"有没有任意管理权限"去填，只开了整改单的人就会在日常那一行读到
// 「验收」、点开却是个没有决定按钮的面板 —— 正是 2026-10-05 审查 F-04 那个分叉。
// 判据仍是 `dailyAction` / `deepAction` / `fixAction`（与任务卡上的按钮同源），文案仍走
// 队列自己的 `queueActionLabel` 查表，这里只是把"档位"换成"这一项"。
const workQueue = computed(() => buildWorkQueue({
  inbox: inbox.value,
  shiftDue: shiftDue.value,
  now: nowTick.value,
  isManager: isManager.value,
  pendingKeys: pendingKeys.value,
}).map((task) => ({ ...task, primaryLabel: queueLabelFor(task) })))
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
// 那条五格 tab 条撤掉之后，文档标题不再跟着"当前是哪一格"走：这一页整体就叫「卫生」
// （页面清单里 `/workbench/me/clean` 的 title 也是它），与外壳顶栏 / 底栏那一格同名。
document.title = hygieneDocumentTitle('卫生')

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

// 「榜」从平级 tab 降成页尾那一行入口之后，"什么时候去拉榜和教材"也跟着换判据：
// 原来是切到那一格（`watch(tab)`），现在是这一行被展开。收起时不拉 —— 服务端每次提交
// 都会广播 boards，不在看就没必要跟着刷（实时那一支里的判据同步改成 `boardsOpen`）。
watch(boardsOpen, async (open) => {
  if (!open) return
  errorText.value = ''
  await loadBoardsAndTeaching({ force: true })
})

watch(needsAssignment, (needed) => {
  if (needed && !zonesLoaded.value) loadZones()
}, { immediate: true })

/** 一组还有多少件没交。口径仍是 `tabWorkCount`（待办 = 未通过的日常、专项 = 未通过的专项、
 *  整改 = 全部未闭环的整改单），页内三个组标题与顶部汇总那一行都用它 —— 三处各算一遍的话
 *  迟早会和列表对不上。 */
function groupCount(id) {
  return tabWorkCount(id, {
    inbox: inbox.value,
    deepInbox: deepInbox.value,
    fixInbox: fixInbox.value,
  })
}
const dailyCount = computed(() => groupCount('inbox'))
const deepCount = computed(() => groupCount('deep'))
const fixCount = computed(() => groupCount('fix'))
// 顶部那一行汇总：今天要做几项 = 三组待办之和（日常 + 专项 + 整改）。
const todoTotal = computed(() => dailyCount.value + deepCount.value + fixCount.value)
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

/**
 * 待办队列的「现在」：**量化到分钟**。
 *
 * `workQueue` 依赖 `nowTick`，而 buildWorkQueue 的桶（超时 / 快到期 / 等待）判据是
 * 分钟级的 deadline —— 秒级抖动只会让 computed 白重算一遍。量化之后每 30s 的定时器
 * 每分钟最多产生一个新值。
 */
function tickClock() {
  nowTick.value = Math.floor(Date.now() / 60_000) * 60_000
}

/** 起 30s 时钟；重复调用是 no-op（避免可见性切换时叠出多个定时器）。 */
function startClock() {
  if (clockTimer) window.clearInterval(clockTimer)
  tickClock()
  clockTimer = window.setInterval(tickClock, 30_000)
}

/** 停 30s 时钟。页面不可见时不需要它：员工锁屏 / 切走后没人看这些桶。 */
function stopClock() {
  if (clockTimer) window.clearInterval(clockTimer)
  clockTimer = null
}

/**
 * 页面可见性门控（台账 §3.9 / §0 第 18 条）。
 *
 * 不可见时停掉 30s 时钟，回来时立刻补一次 —— 后台那段时间的 deadline 变化在回来的
 * 那一刻一次算清，而不是等下一个 30s（员工切回来看见的会是过期半分钟的队列）。
 */
function onVisibilityChange() {
  if (document.hidden) {
    stopClock()
    return
  }
  startClock()
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
    // 花名册：**超管改这个人的管理权限**（`admin_caps`）时会广播 `roster`（scope 带本人
    // employee_id，服务端只推给他）。2026-10-06 真机实测：不订它的话，超管取消「整改单」
    // 后员工这一页 30 秒零变化，必须手动刷新才生效 —— 放权的最后一公里就断在这里。
    'roster',
  ],
  pull: async (event) => {
    if (!employee.value) return
    try {
      const resource = event && event.scope && event.scope.resource
      if (resource === 'assignment' || resource === 'settings') {
        await loadMe()
        return
      }
      // 权限变了就是 `me` 变了：重拉一次，`canDailyReview` / `canDeepReview` / `canFix`
      // 这些判据全部由 `employee.admin_caps` 派生，拉完按钮与分组自己就跟着变。
      if (resource === 'roster') {
        await loadMe()
        return
      }
      if (resource === 'daily') await loadInbox()
      if (resource === 'deep') await loadDeepClean()
      if (resource === 'fix') await loadFixTickets()
      // 别人的拍照/开单会广播 boards：只有页尾那一行入口**展开着**时才需要跟着拉。
      // 展开那一刻本来就会强制拉一次（见 `watch(boardsOpen)`），所以这里不拉不会漏。
      if (resource === 'boards' && boardsOpen.value) {
        await loadBoards({ force: true })
      }
      if (resource === 'teaching' && boardsOpen.value) {
        await loadTeaching({ force: true })
      }
      // `zones` 这个 resource 承载两件事：工作区列表变更，以及**标准图换版 / 改标注**
      // （action=standard_updated）。已选区的员工不必跟着刷新工作区列表，但必须刷新
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

/** 手动刷新：不挑 resource，全部重拉——断线期间任何变更都可能漏掉。名单逐条列全
 *  （`loadMe` 里也会拉的那几条照样列）：断连时 `/me` 自己会失败并进退避重试，别的几段
 *  不该跟着它一起哑掉。
 *  「待我验收」也在内（票 06）：它原来只挂在 `/me` 那条链路上，于是 `/me` 一失败，日常 /
 *  专项 / 整改 / 工作区都换了新数据，那一段还停在上一次的样子，而界面上没有任何地方提示他
 *  "这段没刷"。判据与 `loadMe` 里那一处相同（「日常验收」那一项开关）：没这一项的人不发
 *  这条请求——服务端对普通员工是 403。 */
async function refreshAll() {
  errorText.value = ''
  const jobs = [
    loadMe(),
    loadInbox(),
    loadDeepClean(),
    loadFixTickets(),
    loadZones({ force: true }),
  ]
  if (canDailyReview.value) jobs.push(loadPendingReviews())
  await Promise.allSettled(jobs)
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
  // 票 03 起员工登录页就是 `/login` 的员工栏：`?next=` 落在员工端前缀内时面板会强制
  // 开员工栏，登回来还是这一页。落点算在 `loginRedirectTarget` 一处（票 12 收的 O2）：
  // 页面上并发的几个请求各拿一个 401 各自调到这里，**已经在登录页上时它返回 null** ——
  // 不返回的话第二个 401 会把「已经是登录页的当前地址」再包一层，`?next=` 里的目标作废。
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
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
  startClock()
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('visibilitychange', onVisibilityChange)
  loadMe()
})

onBeforeUnmount(() => {
  stopClock()
  if (meRetryTimer) window.clearTimeout(meRetryTimer)
  meRetryTimer = null
  if (connectionTimer) window.clearTimeout(connectionTimer)
  connectionTimer = null
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  standardPhotoCache.setTaskSheetOpen(false)
  document.body.style.overflow = ''
  clearPreview()
})

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    const previousZoneId = employee.value ? employee.value.zone_id : null
    employee.value = data.employee
    dailyClocks.value = data.daily_clocks || null
    deepClock.value = data.deep_clock || null
    meRetryIndex = 0
    // 换区之后重新核对标准图缓存（只核对、不下载）：清单是按工作区切片的，不核的话被
    // 临时调到别的区的人，面板上提示的还是上一个区的「待更新」张数。
    //
    // 票 10 之前这件事挂在「员工自己点换区」那一次动作上；自选入口撤了之后，换区由店长
    // 在排班页改（单日覆盖或固定工作区），员工端只能从「读到自己今天在别的区」看出来 ——
    // 所以触发点搬到这里。首次进页面（上一次没有区）也核一次，代价是一次只读请求。
    if (String((employee.value && employee.value.zone_id) || '') !== String(previousZoneId || '')) {
      await standardPhotoCache.checkForUpdates()
    }
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
  if (canDailyReview.value) {
    // 复核是他的**职责**，不是"有班才顺手做的事"：不管今天有没有排他班、排没排区，
    // 都要把待验收拉回来 —— 别人交了活，等的就是他这一眼。
    // 判据是「日常验收」那一项开关（不是"是不是管理员"）：只开了整改单的人不拉这一档。
    jobs.push(loadPendingReviews)
  }
  if (employee.value && employee.value.shift && employee.value.zone_id) {
    // 三个组现在同屏依次渲染（不再是切到哪一格才拉哪一格的数据），所以进页面就得把
    // 整改那一组要用的工作区名单一起拉上 —— 原来它是 `watch(tab)` 在切到「整改」时拉的，
    // 那一步没有了；少了它，「开整改单」那张表单里的工作区下拉是空的。
    jobs.push(loadInbox, loadFixTickets, loadZones)
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

/** 待我验收那一档，只有开了**「日常验收」**那一项的人才会去拉：范围与过滤都在服务层
 *  （`list_daily_work(pending_review_only=True)`）：两个班次、全部区、只要「待验收」、
 *  且不是他自己交的 —— 与验收接口的判据逐字对齐。调用点在 `loadMe` 与 `refreshAll`
 *  （两处都按这一项判），没有这一项的人一次请求都不会发。 */
async function loadPendingReviews() {
  const data = await staffRequest('/api/hygiene/staff/daily-work?review=1')
  reviewInbox.value = data.items || []
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

/** 滚到这一页顶上，让员工看清今天为什么没有日常可交。
 *
 *  票 10 之前它叫 `openAssignmentPicker`（打开「重选区域和班次」的选择器）。自选撤了之后
 *  同样的入口变成「去看那一屏的说明」，所以不再需要拉工作区名单那一步 —— 说明里的话
 *  只跟排班给的值有关。
 *  五格合并之后没有"那一屏"可切了：日常就是第一组，`tab.value = 'inbox'` 那一步跟着
 *  页内 tab 一起撤掉，剩下的只是滚到顶。
 */
async function openDutyNotice() {
  if (!employee.value) return
  await nextTick()
  const main = document.getElementById('hygiene-work-main')
  if (main) main.scrollTo({ top: 0, behavior: 'smooth' })
}

// 「我」那一格（修改个人信息 / 修改密码 / 重新选择区域和班次，连同各自的弹窗与表单状态）
// 整块搬到了「今天」页（`views/today/TodayView.vue` 尾部那三行入口）：
// 账号设置不是"今天要干的卫生活"，混在干活的那一屏里只会把待办推到下面。这一页现在
// 只剩卫生本身 —— 它读的还是同一个员工会话（`/api/hygiene/staff/me`），搬的只是界面。

// 退出登录（含「队列里还有照片」那次确认）已抽到 `composables/useStaffLogout.js`，
// 由工作台外壳顶栏那颗 `WorkbenchExitButton` 承载 —— 员工三页一处实现、一颗按钮（D5：
// 这一页原先页内又挂了一颗 `StaffExitButton`，与外壳那颗同名同功能）。

/** 行点击 / 行内按钮 / 队列文案共用**同一个**判据（`hygieneWorkFlow` 那三个 `*PrimaryAction`）。
 *  判据在页面这一侧只经手这里，模板里不再各写一遍 —— 之前正是两处各判一次，普通员工才会
 *  点到一个没有决定按钮的空壳验收面板（2026-10-05 审查 F-04）。
 *
 *  2026-10-05 起每一项喂的是**它自己那一项开关**：日常验收 → `daily_review`、专项验收 →
 *  `deep_review`、整改单 → `fix`。`hygieneWorkFlow` 那个参数名还叫 `isManager`（那份文件
 *  没动）：它问的其实是"这一项这个人能不能判"，现在由各个开关回答。
 *  日常与专项分开判是必须的 —— 只开了 `deep_review` 的人，日常那一行就该是「查看」。 */
function dailyAction(row) {
  return dailyPrimaryAction(row, { isManager: canDailyReview.value })
}

function deepAction(row) {
  return deepPrimaryAction(row, { isManager: canDeepReview.value })
}

function fixAction(row) {
  return fixPrimaryAction(row, { isManager: canFix.value })
}

/** 队列里那条任务的按钮文案：按 kind 分派到上面同一个判据，再查队列那份文案表。
 *  （`buildWorkQueue` 只收一个布尔，算不出三种人各不相同的文案，见 `workQueue` 的注释。） */
function queueLabelFor(task) {
  if (!task) return ''
  if (task.kind === 'daily') return queueActionLabel('daily', dailyAction(task.row))
  if (task.kind === 'deep') return queueActionLabel('deep', deepAction(task.row))
  return queueActionLabel('fix', fixAction(task.row))
}

function canShootDeep(row) {
  return Boolean(employee.value && row.status !== '已通过')
}

function isDeepSheet() {
  return Boolean(sheet.value && sheet.value.kind === 'deep')
}

/** 对照面板能不能出现「通过 / 驳回」。`cap` 是**这一项**的开关键：日常那一屏传
 *  `daily_review`、专项传 `deep_review` —— 同一个面板，两件事各判各的。
 *  另外自己交的那份不能自己验（与服务端 `_require_reviewer` 同口径）。 */
function canDecide(review, cap) {
  if (!hasCap(caps.value, cap) || !review || !employee.value) return false
  return employee.value.id !== review.submitter_id
}

/** 整改单能不能判：判据在 `canAcceptFixTicket` 里，它收的同样是「整改单」那一项开关
 *  （外加两条例外：自己开的单子自己收、超级管理员开的单子他向来自收）。 */
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
  const action = dailyAction(row)
  // 'view'（没有「日常验收」那一项的人看自己那份待验收的）与 'review'（有那一项的验收）
  // 开的是同一个面板：面板里能不能决定由 `canDecide` 说了算，不是两个面板。
  if (action === 'review' || action === 'view') return openReview(row)
  if (action === 'shoot') return openStandard(row)
  return undefined
}

function runDeepPrimary(row) {
  const action = deepAction(row)
  if (action === 'review' || action === 'view') return openDeepReview(row)
  if (action === 'shoot') return openDeepCapture(row)
  return undefined
}

function runFixPrimary(row) {
  const action = fixAction(row)
  if (action === 'review') return openFixReview(row)
  return openFixOriginal(row)
}

function runQueueTask(task) {
  if (!task) return
  if (task.kind === 'daily') return runDailyPrimary(task.row)
  if (task.kind === 'deep') return runDeepPrimary(task.row)
  return runFixPrimary(task.row)
}

/** 开整改单能选的工作区：**只有他自己今天那个区**。
 *
 *  服务端 `open_fix` 的 `_require_zone_access` 认的就是"今天排给他的那个区"（今天没区
 *  直接拒），而 `/api/hygiene/staff/daily-catalog` 回来的 `zones` 是**全店名单** ——
 *  服务端只把每个区底下的检查项按他的区切了片，名单本身没切。直接拿它当选项，默认
 *  选中的是名单里第一个区，那不是他的区，提交必然吃 `zone_mismatch` 403。所以这里按
 *  `employee.zone_id` 收成一个选项：名字优先取名单里的（跟页头那颗只读胶囊同一个来源），
 *  名单还没回来 / 区刚被删时退到 `zone_name`。 */
const fixZoneOptions = computed(() => {
  const current = employee.value
  if (!current || !current.zone_id) return []
  const listed = zones.value.find((zone) => String(zone.id) === String(current.zone_id))
  return [{
    id: current.zone_id,
    name: (listed && listed.name) || current.zone_name || '今天的卫生工作区',
  }]
})

/** 开整改单：**只认"今天有没有工作区"，不认"今天有没有班"**。
 *
 *  这里原来拦的是 `needsAssignment`（没班次 / 没区 / 排的班次跟这个区对不上）—— 那是
 *  **日常拍摄**的前置条件：班次决定交哪一档日常，所以没班就没日常可交。开整改单是复核
 *  动作，不落在班次上：服务端 `_require_fix_opener` 只看**「整改单」那一项开关**（没有它
 *  → forbidden），能开在哪个区由 `open_fix` 里的 `_require_zone_access` 判 —— 只认
 *  今天排给他的那个区；今天一个区都没排到就没有可开的区。所以前端这一档跟着**工作区**
 *  判：有区就能开，没区才拦。
 *
 *  原本文案让他"先找店长确认今天的排班"，可他自己就是管理员（2026-10-05 审查 F-05）：
 *  现在直接说清缺的是什么、去哪儿补。班次与区都由排班的单日覆盖说了算（票 10），
 *  员工端没有自选入口，所以"找店长在排班页排一个区"是唯一可执行的下一步。 */
function openFixForm() {
  errorText.value = ''
  formError.value = ''
  flashText.value = ''
  // 走 `canFix` 而不是 `isManager`：按钮本来就只给开了这一项的人渲染，这里再判一道是
  // 防手滑（键盘/程序化调用）——判据两处同一个开关，不会出现"按钮在、点了没反应"。
  if (!canFix.value) return
  if (!employee.value || !employee.value.zone_id) {
    errorText.value = '今天没有排到你的工作区，开不了整改单：开单要在自己的区里开。找店长在排班页给你排一个区。'
    return
  }
  if (!liveOk.value) {
    errorText.value = '无法打开相机。请在浏览器设置中允许相机权限，或换一部手机；卫生拍照必须现场完成。'
    return
  }
  clearPreview()
  const firstZone = fixZoneOptions.value[0]
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
    formError.value = '请选择卫生工作区。'
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
  // 下一张挑哪张、挑到待验收的要不要直接开对照，两处都按「整改单」那一项开关判
  // （`hygieneWorkFlow` 的 `nextFixWorkRow` 收的仍叫 `isManager`，问的是同一个问题）。
  const next = nextFixWorkRow(fixInbox.value, current, { isManager: canFix.value })
  if (!next) {
    closeSheet(true)
    return
  }
  if (next.status === '待验收' && canFix.value) {
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
      label: `整改开单 · ${zone ? zone.name : '卫生工作区'}`,
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
      label: `整改回拍 · ${current.zone_name || '卫生工作区'}`,
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
// 并且**必须**写一句原因（2026-10-05 用户裁定；员工端会把它显示在待办行上，他照着改）。
// 这屏的"驳回"紧挨着"通过"，误触代价一样。
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
  // 驳回必须带一句原因（弹窗里必填，服务端也拦）：员工端会显示出来。管理员在手机上
  // （走这条路径）与在管理后台走的是同一套后端接口，行为要一致。
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
      // 连着判下一项：只有还留着「专项验收」那一项的人才会被带进下一个对照面板。
      if (next && canDeepReview.value) await openDeepReview(next)
    } else if (sheet.value.kind === 'fix') {
      await staffRequest(`/api/hygiene/staff/fix/${row.id}/${action}`, {
        method: 'POST',
        body: rejectBody,
      })
      closeSheet(true)
      await loadFixTickets()
      const next = nextFixWorkRow(fixInbox.value, row, { isManager: canFix.value })
      if (next) {
        if (next.status === '待验收' && canFix.value) await openFixReview(next)
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
      if (next && canDailyReview.value) await openReview(next)
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
    <!-- 页头收成一行（2026-10-05 用户裁定）：页名 +「我今天在哪」那颗只读胶囊。
         原来这里还有一条 `‹ 今天`（D9 给页内条留的唯一一条回程），现在去掉 —— 工作台
         底栏那一格「我的」指向的正是 `/workbench/me/today`（`utils/workbenchNav.js` 的
         `me` 那一格，`WorkbenchTabBar` 按身份渲染出来），页内再摆一条就是**同一个目的地
         两个入口**；「今天」页自己也有一条来卫生的路（那张卫生卡上的按钮），两边都不缺路。
         （更早以前这一条还兼职"给没有返回键的 iOS PWA 留一条回程"，那件事现在由底栏接手。）
         品牌仍归外壳顶栏、退出仍归外壳那一颗（D5）——这里只报「这一页叫什么」和「我在哪」。
         页名的样式沿用 `.hy-brand-title`：它就是这条窄栏里那个标题槽（字号由
         `.hygiene-work .hy-work-header .hy-brand-title` 收到 1.02rem）。 -->
    <header class="hy-work-header" :inert="Boolean(sheet)">
      <div class="hy-work-header-inner">
        <h1 class="hy-brand-title">卫生</h1>
        <!-- 今天在哪：**只读**（票 10）。以前点它还能重选，现在班次和工作区由排班决定，
             要改得去排班页改那一天 —— 所以这里只报「排班说你今天在哪」，点不动。
             「还没定区」与「今天没排班」是两件事：前者有班次但店长没给他配这个班的固定区
             （或者那个区被删了），后者是排班压根没排到他。 -->
        <span v-if="employee" class="hy-work-shift">
          {{ employee.name || employee.phone }} · {{ employee.zone_name || (employee.shift ? '还没定区' : '今天没排班') }} · {{ hygieneShiftLabel(employee.shift) }}
        </span>
        <!-- 被放权的那一档在界面上如实标出来：顶栏只有「员工（姓名）」两档可选，而这一页上
             他干的是判别人活的活（真机走查 O-id：角色审查 F-01 记的就是"界面层看不见它"）。
             只在真有可用的那三项时出现，普通员工看不到这一颗。 -->
        <span v-if="employee && staffCapCount" class="hy-work-role" :title="`可判 ${staffCapCount} 项：${ADMIN_CAP_STAFF_DEFS.filter((item) => hasCap(caps, item.key)).map((item) => item.label).join('、')}`">
          现场复核 · {{ staffCapCount }} 项
        </span>
      </div>
    </header>

    <main id="hygiene-work-main" class="hy-work-main" :inert="Boolean(sheet)">
      <p v-if="connectionLost && !sheet" class="hy-staff-alert hy-staff-offline" role="status">
        和服务器断了，这一页上的数据可能不是最新的。
        <button type="button" class="btn" @click="refreshAll">刷新</button>
      </p>
      <p v-if="errorText && !sheet" class="hy-staff-alert" role="alert">{{ errorText }}</p>

      <!-- 顶部一行汇总（2026-10-05 用户裁定）：今天要做几项 = 三组待办之和，后面跟一句
           明细（日常 / 专项 / 整改各几件）。五格合并之后这一行就是这一页的门面 ——
           员工进来看一个数就知道今天还剩多少活，不必先在两格之间挑。
           它原来那条「今天没有排到你的班…」的警示条撤了：那时候它只在**不在待办那一格**
           时才显示（日常那一格自己会说清），现在三组同屏、日常就在最上面，同一句话不必
           再说第二遍 —— 它自带的那条「去看我的班」也还在。
           样式用 `.hy-progress`（单行、mono、tabular-nums）：与专项组那条进度线同一个语义。
           刻意不摆成一排事实胶囊 —— 下面日常组第一行就是那排胶囊，两排一样的看不出哪排是
           「今天的总数」、哪排是「日常这一项的进度」。 -->
      <p v-if="employee" class="hy-progress">
        今天要做 {{ todoTotal }} 项
        <!-- 「待我验收」也进这一行（真机走查 O4）：原来这一行只算自己那三组，于是有验收权、
             今天没人交的人读到「今天要做 0 项」；今天真有人交时下面又列着活 —— 上下自相矛盾。
             它按「日常验收」那一项开关判（不是"是不是管理员"），0 也照报，等于给这一档补上
             一直缺的空状态：有这权力但没人交，和根本没这权力，从此看得出区别。
             **写在同一颗 span 里**：两颗相邻标签之间的空白会被模板编译器消掉，实测渲染成
             「整改 0· 待我验收 0」（分隔号贴在前一个数字上）。 -->
        <span>· 日常 {{ dailyCount }} · 专项 {{ deepCount }} · 整改 {{ fixCount }}<template v-if="canDailyReview"> · 待我验收 {{ reviewInbox.length }}</template></span>
      </p>

      <!-- 「待我验收」：只有开了**「日常验收」**那一项的人有，且**今天真有人交了**才出现
           （拉不拉是这一档的另一半，在 `loadMe` 里、同一个开关）。
           他的验收权是全店的（服务层那一档同样按开关判、不看区），但默认队列锁在他自己的
           区与班次里 —— 今天别的区有人交了，他一条也看不到，权力等于没有入口
           （2026-10-05 用户确认：员工账号的管理员同样有验收权）。
           所以这一组不看他自己的班，只看"有没有别人交的活等着判"；放在最前，因为别人交完
           活等的就是他这一眼，有时效。 -->
      <section v-if="canDailyReview && reviewInbox.length" class="hy-queue-group">
        <h2>待我验收 <span>{{ reviewInbox.length }}</span></h2>
        <p class="hy-staff-lead">同事交上来的，先看原图再判。你自己交的那份不能自己验收。</p>
        <button
          v-for="row in reviewInbox"
          :key="`review-${row.item_id}-${row.shift}`"
          type="button"
          class="hy-work-row"
          @click="openReview(row)"
        >
          <span class="hy-task-kind">{{ row.shift }}</span>
          <span class="hy-work-copy">
            <strong>{{ row.name }}</strong>
            <span>{{ row.zone_name }} · 同事交的</span>
          </span>
          <span class="hy-work-due">待验收</span>
          <SvgIcon name="chevron-right" :size="18" />
        </button>
      </section>

      <!-- 第 1 组 · 日常（原来的「待办」那一格）。三个组依次排下去，每组一个小标题带条数，
           条数与上面那行明细同一口径（都是 `tabWorkCount`）。 -->
      <section class="hy-queue-group">
        <h2>日常 <span>{{ dailyCount }}</span></h2>
        <!-- 票 10：班次和工作区不再由员工当天自己选 —— 排班说今天在哪个班、哪个区，
             卫生就认哪个。这一屏以前是个选择器，现在换成一句实话 + 一条去「今天」页的路：
             没有班次（新人没配规则 / 今天休 / 那条班次还没标卫生档位）就没有日常可交，
             与其让人在这里选出一个不生效的答案，不如说清该找谁。 -->
        <template v-if="showWhyNoDuty">
          <h1>今天交不了日常检查</h1>
          <p class="hy-staff-lead">
            <template v-if="employee && !employee.shift">
              今天排班没有排到你的班（新同事还没配轮转规则，或者今天休）。日常检查按班次分两档，
              没有班次就没有可交的那一份。
            </template>
            <template v-else>
              排班给的班次和这个工作区对不上（这个区可能没开这一档）。日常检查交不了。
            </template>
          </p>
          <p class="hy-staff-lead">
            你的班在「今天」页；要改今天上哪个班、在哪个区，找店长在排班页改那一天。
          </p>
          <button
            type="button"
            class="btn btn-primary btn-block hy-staff-submit"
            @click="router.push('/workbench/me/today')"
          >去看我的班 ›</button>
        </template>
        <template v-else-if="employee">
          <div class="hy-work-facts">
            <span>日常 {{ dailyStats.passed }}/{{ dailyStats.total }}</span>
            <span v-if="queueSummary.overdue" class="is-overdue">超时 {{ queueSummary.overdue }}</span>
            <span v-else-if="queueSummary.soon" class="is-soon">快到时 {{ queueSummary.soon }}</span>
            <span v-if="queueSummary.waiting">等验收 {{ queueSummary.waiting }}</span>
          </div>
          <p class="hy-staff-lead">
            <template v-if="shiftDue">本班 {{ shiftDue }} 前交。专项和整改在下面两组。</template>
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
                日常检查项挂在工作区下，这一屏只列你选的这个区；管理端能看到全部区。
              </p>
              <button type="button" class="btn" @click="openDutyNotice">看看今天怎么安排</button>
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

      <!-- 第 2 组 · 专项。原来的那一格自带一句大标题「专项卫生 · 待办 / 已完成 / 无专项」：
           五格合并之后组标题已经写着「专项 N」，大标题里那两个字就成了重复，所以只把
           **状态**接在组标题后面（它是服务端给的这一轮结论，不能丢）。 -->
      <section class="hy-queue-group">
        <h2>专项 <span>{{ deepCount }}</span><span v-if="deepStatus">{{ deepStatus }}</span></h2>
        <p v-if="deepStats.total" class="hy-progress">
          {{ deepStats.passed }}/{{ deepStats.total }}
          <span v-if="deepDue"> · {{ deepDue }} 前做完</span>
        </p>
        <p class="hy-staff-lead">全店专项，不按工作区或班次。每项拍清理前和清理后。</p>
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
            <!-- 三颗按钮的判据都是同一个 `deepAction`（= `deepPrimaryAction(row, { isManager })`，
                 那里喂的是**「专项验收」那一项开关**）：开了那一项的人在待验收那一档拿到
                 'review'（对照 + 能通过 / 驳回），没开的拿到 'view'（查看：面板里没有决定
                 按钮，也明说了等管理员验收）。「重拍」问的是另一件事
                 —— 这一项是不是已经交了、还在等验收（ADR 0071 允许再交一张替换）—— 它跟谁
                 能验收无关，所以用 `isPendingReview` 判，别挂在 'review' 上（那样没开验收
                 的人的入口会跟着一起消失）。 -->
            <button
              v-if="deepAction(row) === 'review'"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openDeepReview(row)"
            >对照</button>
            <button
              v-else-if="deepAction(row) === 'view'"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openDeepReview(row)"
            >查看</button>
            <button
              v-else-if="canShootDeep(row)"
              type="button"
              class="btn btn-primary"
              :disabled="busy"
              @click="openDeepCapture(row)"
            >拍前后</button>
            <button
              v-if="canShootDeep(row) && isPendingReview(row)"
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

      <!-- 第 3 组 · 整改（原来的「整改」那一格）。同样只留组标题 + 条数：大标题里的
           「整改单」与组标题重复。 -->
      <section class="hy-queue-group">
        <h2>整改 <span>{{ fixCount }}</span></h2>
        <p class="hy-staff-lead">按所选区域显示和提交。先看开单原图再拍，镜头不叠图。</p>
        <button
          v-if="canFix"
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
              v-if="fixAction(row) === 'review'"
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
              v-if="fixAction(row) === 'review'"
              type="button"
              class="btn"
              :disabled="busy"
              @click="openFixOriginal(row)"
            >重拍</button>
          </div>
        </article>
      </section>

      <!-- 信息入口（页尾一行）：红黑榜 + 卫生教材。它原来是与三组任务平级的第五格 tab
           ——「榜」是**信息**（谁被驳回过、哪个区逾期、哪些是合格对照），跟"今天要干的活"
           不是一回事，平级摆着只会让人以为它也要今天做完。降成一行入口、点开在本页展开：
           这一屏的数据本来就是员工 cookie 读的（`/api/hygiene/staff/boards` 等），展开
           只是换一位 `v-if`；跳去店长那侧的 `/workbench/floor/boards` 只会吃 403，
           为它另开一条员工路由又要再搬一遍登录与实时那一套，得不偿失。
           行上那个「展开 / 收起」是**文字**不是箭头：箭头得配一个旋转动画才说得清开没开，
           而这一页没有自己的 scoped 样式块（样式全在共享的 hygiene-admin.css 里，
           本次改动只动页面结构，不动那张表）。 -->
      <section class="hy-queue-group">
        <h2>信息</h2>
        <button
          type="button"
          class="hy-work-row"
          :aria-expanded="boardsOpen"
          aria-controls="hygiene-boards-panel"
          @click="boardsOpen = !boardsOpen"
        >
          <span class="hy-task-kind">榜</span>
          <span class="hy-work-copy">
            <strong>红黑榜 · 卫生教材</strong>
            <span>本周谁被驳回过、哪个工作区逾期、超级管理员标记的合格对照</span>
          </span>
          <span class="hy-work-due">{{ boardsOpen ? '收起' : '展开' }}</span>
        </button>

        <div v-if="boardsOpen" id="hygiene-boards-panel">
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
            <h2>卫生工作区红黑榜</h2>
            <p v-if="!(boards.zones || []).length" class="hy-staff-lead">这一周还没有卫生工作区的次数。</p>
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
        </div>
      </section>
    </main>

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
            卫生工作区
            <select
              id="fix-zone"
              v-model="sheet.zoneId"
              class="staff-input"
              :aria-invalid="formErrorField === 'fix-zone'"
              @change="formError = ''; formErrorField = ''"
            >
              <!-- 只有他自己今天那个区：开单只能开在自己的区上（见 `fixZoneOptions`）。
                   这里曾经列全店名单、默认选中第一个区 —— 那个区不是他的，提交必吃 403。 -->
              <option v-for="zone in fixZoneOptions" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
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
          <p v-if="canFix && !canDecideFix(sheet.row)" class="staff-lead">时限还没到，只有开单人能验。</p>
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
          <p v-if="canDeepReview && !canDecide(sheet.review, 'deep_review')" class="staff-lead">交这一组的人不能自己验收。</p>
          <!-- 没有「专项验收」那一项的人打开的是同一个对照面板，只是没有决定区：不说这一句，
               面板看上去就是"空的"（2026-10-05 审查 F-04 的两张并排截图）。 -->
          <p v-else-if="!canDeepReview" class="staff-lead">等管理员验收：你没有这一项的验收权，这里只能看。</p>
          <div v-if="canDecide(sheet.review, 'deep_review')" class="staff-decide">
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
          <p v-if="canDailyReview && !canDecide(sheet.review, 'daily_review')" class="staff-lead">交这张的人不能自己验收。</p>
          <!-- 同上：没有「日常验收」那一项的人从队列里点进来的就是这一屏。 -->
          <p v-else-if="!canDailyReview" class="staff-lead">等管理员验收：你没有这一项的验收权，这里只能看。</p>
          <div v-if="canDecide(sheet.review, 'daily_review')" class="staff-decide">
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
      v-if="rejectConfirmOpen"
      title="驳回这一项"
      :message="`驳回「${sheet ? (sheet.row.item_name || sheet.row.zone_name || '这一项') : '这一项'}」后要重新拍；本周红黑榜会记一次驳回。`"
      confirm-label="驳回"
      danger
      :prompt="{ label: '哪里不合格（必填，员工能看到）', placeholder: '写一句让他知道改什么，例如：台面还有油渍、角落没擦到', hint: '必填 · 员工照这句重拍', required: true, maxlength: 120 }"
      @confirm="confirmReject"
      @cancel="rejectConfirmOpen = false"
    />

    <!-- 「确认改手机号」那个框跟着「我」那一格搬去 `views/today/TodayView.vue` 了。 -->
  </div>
</template>
