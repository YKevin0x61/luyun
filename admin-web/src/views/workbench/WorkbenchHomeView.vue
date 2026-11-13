<script setup>
/**
 * 工作台首页（票 06）：子应用根 `/workbench` 那一页 —— 打开就知道今天该干嘛。
 *
 * 它只做**分流与摘要**：按此刻的身份（票 04 的 `stores/workbenchIdentity`）渲染两套视角，
 * 数字全部由**已有接口**在客户端聚合（口径在 `utils/workbenchHome.js`，那儿有逐条出处）。
 * 页面上没有任何业务动作：不批假、不验收、不开整改单 —— 那些仍然在各自专页里，
 * 这里每个数字只是一扇门（spec 故事 14 与「关键交互」的最后一条）。
 *
 * 两套视角互斥，且**探针回来之前一个数字都不渲染**（`view === null`）：身份没定之前
 * 谁也不知道这是谁，先渲染店长那三个数字再被换成员工视角，等于让人看一眼假的。
 *
 * 店长那一档打四个接口（今天谁上班 / 待批请假 / 待验收 / 逾期整改），四个请求并行、
 * **各拉各的**：一块读不出来只让那一格显示「—」，其余照常（跟「今天」页两块卡同一个
 * 口径）。任何一块失败都不改这一页的结构，也不把整页打回错误态。
 *
 * 员工那一档打五个（我的班 / 我的申请 / 日常 / 专项 / 整改）：班次与工作区复用「今天」页
 * 那套翻译（`utils/todayShift.js`），待办条数与「卫生待办」页的三个角标同一口径。
 * 员工会话带能力档时再多打几块（2026-11-09 / 11-10 走查）：`schedule` 给「店里的事」
 * 排班三格；`daily_review` / `deep_review` / `fix` 任一给卫生复核那一行 —— 走员工端
 * 只读接口，卫生 admin 那侧对员工会话是 401。
 * 那一档里还有一块**今天的活**（`components/staff/StaffHygieneCard.vue`）：它自带三条请求
 * （`/staff/me`、`/staff/daily-work`、`/staff/attire`）与自己的实时订阅 —— 首页只摆它一行，
 * 不把它的口径并进这套摘要里（那一块有业务动作，摘要没有）。
 * 401 的处理交给 `staffRequest` 之外的公共路径 —— 这里用的是 `api`（管理端 cookie），
 * 员工那几条走 `staffRequest`，各自与页面自己那次请求同一条链。
 *
 * 2026-11-13 员工餐走查后补两处入口（ADR 0135）：员工档一行「员工餐今天的状态」，
 * 点进单日页；管理端在「下月菜单还没导入」时提醒一句。两处都只读一块、读不到就整行 /
 * 整条不显示，首页不因为这类的失败改结构。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import StaffHygieneCard from '../../components/staff/StaffHygieneCard.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { pageTitle } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../../utils/workbenchIdentity'
import { staffRequest } from '../../utils/hygieneStaff'
import { dayLabel } from '../../utils/todayShift'
import { WORKBENCH_TITLE, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { HOME_LINKS, MANAGER_TODO_LIMIT, countHygieneRejected, countHygieneReview, workbenchHomeSummary } from '../../utils/workbenchHome'
import { PREP_PLAN_PATH } from '../../utils/prepPlanPaths.js'
import { STAFF_MEAL_PATH } from '../../utils/staffMealPaths.js'
import { useStationsStore } from '../../stores/stations'

const identityStore = useWorkbenchIdentityStore()
const identity = computed(() => identityStore.identity)
// 店长用的是员工会话（`schedule` 能力档），进首页落到的是员工视角。他要的画面除了「我的班」
// 还有店里的事：待批的申请、今天几个人上班、备货还差多少。能力档是只读的，这里只决定
// 要不要多读那三块接口 —— 普通店员不读（卫生 admin 接口对他们本来就是 401）。
const manage = computed(() => identityStore.staffCaps.includes('schedule'))
// 卫生复核（2026-11-10，用户裁定）：管理员只要开了三项里任一，就能在员工端卫生页复核。
// 这三条能力决定「店里的事」里要不要多摊一行卫生汇总（不是第四格）。数据走员工端
// 只读接口（`daily-work?review=1` / `boards`），管理端 cookie 的接口对员工会话是 401。
const HYGIENE_REVIEW_CAPS = ['daily_review', 'deep_review', 'fix']
const canHygieneReview = computed(() => HYGIENE_REVIEW_CAPS.some((cap) => identityStore.staffCaps.includes(cap)))

// 四块数据的原始响应（`null` = 还没回来 / 读失败）。页面只把它们交给
// `workbenchHomeSummary`，自己不解释形状 —— 口径在 util 里，能拿真单测压。
const day = ref(null)
const inbox = ref(null)
const queue = ref(null)
const fix = ref(null)
// 人事提醒（第五格）：本月该调工龄奖 + 本月生日，两块都在这条响应里。
const reminders = ref(null)
// 备货计划：今天各档口还差几项（ADR 0117 把入口从「后勤」组提到首页这张卡片上）
const prep = ref(null)
// 任务（票 07）：管理端读任务板的 counts，员工读「我的任务」的 counts —— 两边都只
// 拿数字，不把任务列表搬进首页。读不到时 `tasks` 为 null，页面画「—」。
const tasksBoard = ref(null)
const tasksMine = ref(null)
const stationsStore = useStationsStore()
const deep = ref(null)
const daily = ref(null)
const me = ref(null)
// 店长那一档额外读的三块（排班待办 / 今天谁上班 / 备货）。员工会话能读这三个接口
// （2026-11-09 实测：inbox、day、prep-plan 都是 200；卫生 admin 那三个是 401）。
const teamInbox = ref(null)
const teamDay = ref(null)
const teamPrep = ref(null)
// 卫生复核那一行的两块原始响应（同上：`null` = 没读到）。`daily-work?review=1` 是
// 员工端复核收件箱，`boards` 是本周那张一人一行的板（驳回次数在行上）。
const hygieneWork = ref(null)
const hygieneBoards = ref(null)
// 业务日（服务端那个 06:00 切日的今天）：月历、员工端、卫生三处读的是同一天，首页
// 显示的日期也得是它 —— 手机上「今天」是几号不作数（票 05 已踩过一次时区）。
const businessDate = ref('')
const state = ref('loading') // loading | ready

/** 今天要摊的东西（店长 / 员工两套互斥的视角）。 */
const summary = computed(() => workbenchHomeSummary({
  identity: identity.value,
  day: day.value,
  inbox: inbox.value,
  queue: queue.value,
  fix: fix.value,
  reminders: reminders.value,
  deep: deep.value,
  daily: daily.value,
  me: me.value,
  tasks: identity.value === IDENTITY_ADMIN ? tasksBoard.value : tasksMine.value,
}))

// 员工端「任务」卡：待做数 + （有任务管理能力时）待我验收数；读不到画「—」。
const canManageTasks = computed(() => identityStore.staffCaps.includes('task'))
const taskTodoCount = computed(() => (summary.value.tasks ? summary.value.tasks.todo : null))
const taskReviewCount = computed(() => (summary.value.tasks ? summary.value.tasks.review : null))
const taskCardHint = computed(() => {
  if (canManageTasks.value && typeof taskReviewCount.value === 'number') {
    return `任务 · 待我验收 ${numberText(taskReviewCount.value)} 项 ›`
  }
  return '任务 ›'
})

/** 店长首页「店里的事」那一条：读不到的那格给 `null`（显示 —），别把没读到说成 0。
 *  待批只数请假、且**不含自己提的**：店长自己的假是超管批的，他点进去也批不了。 */
const teamSummary = computed(() => {
  if (!manage.value) return null
  const rows = teamInbox.value && Array.isArray(teamInbox.value.requests)
    ? teamInbox.value.requests
    : null
  return {
    leaves: rows ? rows.filter((row) => row && row.kind === 'leave' && row.is_own !== true).length : null,
    onDuty: teamDay.value ? (teamDay.value.total ?? null) : null,
    remaining: teamPrep.value ? teamPrep.value.remaining : null,
  }
})

/** 卫生复核那一行（2026-11-10 用户裁定）：待我复核条数 + 本周被驳回次数。
 *  只对开了卫生复核能力的人算；读不到给 null，页面画「—」，失败静默不挡首页。 */
const hygieneSummary = computed(() => {
  if (!canHygieneReview.value) return null
  return {
    review: countHygieneReview(hygieneWork.value),
    rejected: countHygieneRejected(hygieneBoards.value),
  }
})

/** 「店里的事」的眉标：这条里现在放的是哪几种管理员权限（排班 / 卫生 / 两者都有）。 */
const teamHint = computed(() => {
  const parts = []
  if (teamSummary.value) parts.push('排班')
  if (hygieneSummary.value) parts.push('卫生')
  return parts.length ? `管理员 · ${parts.join(' + ')}` : '管理员'
})

/** 「店里的事」那颗灯：条里任何一笔待办（排班 / 卫生复核 / 被驳回）有数字就黄，
 *  全 0（或全读不到）就中性 —— 同 D 组 2026-11-09 定下的「0 值不报警」口径。 */
const teamLed = computed(() => {
  const values = []
  if (teamSummary.value) values.push(teamSummary.value.leaves)
  if (hygieneSummary.value) values.push(hygieneSummary.value.review, hygieneSummary.value.rejected)
  return values.some(toneOn) ? 'warn' : 'off'
})

/** 员工首页「我的班」右上角那颗状态（`staffShiftCell().tone` 的六种取值）。 */
const SHIFT_STATES = {
  shift: { text: '在岗', led: 'ok' },
  half: { text: '半天', led: 'warn' },
  leave: { text: '请假', led: 'sig' },
  rest: { text: '休', led: 'off' },
  moved: { text: '已调整', led: 'warn' },
  none: { text: '未排', led: 'off' },
}
// 「上班」那一态在 `todayShift.todayTone` 里用**空串**表示（页面默认色），不是 `shift`；
// 这里把它归一成 `shift`，否则上班的日子右上角会写「未排」、灯也是灰的。
function shiftState(tone) {
  return SHIFT_STATES[tone || 'shift'] || SHIFT_STATES.none
}

const QUICK_LINKS = [
  { label: '排班月历', to: '/workbench/hr/calendar' },
  { label: '备货计划', to: '/workbench/kitchen/prep-plan' },
  { label: '员工餐采购', to: STAFF_MEAL_PATH },
  { label: '配方管理', to: '/workbench/kitchen/recipe/manage' },
  { label: '损耗分析', to: '/workbench/kitchen/loss' },
  { label: '花名册', to: '/workbench/hr/roster' },
  { label: '班次表', to: '/workbench/hr/shifts' },
]

const dateText = computed(() => dayLabel(businessDate.value))

/** 数字为空的三种表现，别混：
 *  `—` 那一块**读不出来**（不是 0，别把「没读到」说成「没有」）；`0` 是真的没有；
 *  其余照原样。 */
function numberText(value) {
  if (value == null) return '—'
  return String(value)
}

/** 灯只在真有事时亮：`0`（真没有）与 `null`（没读到）都不该每天第一屏就报警 ——
 *  四个读数全亮红黄的话，今天到底哪件事急就没人看得出来了。 */
function toneOn(value) {
  return typeof value === 'number' && value > 0
}

/** 几块并行、各拉各的：失败把那块留成 `null`，不抛出去影响别的块。 */
async function safe(promise, sink, pick = (data) => data) {
  try {
    sink.value = pick(await promise) ?? null
  } catch {
    sink.value = null
  }
}

// ── 员工餐（ADR 0135 / 2026-11-13 走查补的两处入口）─────────────────────────
// 员工档：今天那张单的状态；`null` = 没读到（断网 / 401 / 5xx），这一行整行不出现。
// 404 是业务状态（今天还没有菜单），要显示 —— 两者不能混成一句「没有菜单」。
const staffMeal = ref(null)
// 管理端：下月菜单的响应；`null` = 没读到 / 形状不对 → 不提醒，首页不改结构。
const mealNext = ref(null)
const mealNextMonth = ref('')

/**
 * 北京时自然日（`YYYY-MM-DD`）。
 *
 * 员工餐「使用日」按**自然日 00:00 北京时切**（ADR 0125 / 0135），不是首页 `businessDate`
 * 那个 06:00 切的营业日 —— 凌晨 0–6 点拿营业日去问，问到的会是前一天的单。
 * 手机上的今天不可信（时区可能不在东八区，见 `loadManager` 里「不能拿手机上的今天去问
 * 排班」那条注释），所以先把时刻平移到 +08:00 再取 UTC 年月日：北京时没有夏令时，
 * 这个平移是精确的。页面里没有现成的自然日工具（`utils/dateRange.js` 是设备本地时区
 * 口径），就近放这里。
 */
function beijingToday(now = new Date()) {
  const shifted = new Date(now.getTime() + 8 * 60 * 60 * 1000)
  const year = shifted.getUTCFullYear()
  const month = String(shifted.getUTCMonth() + 1).padStart(2, '0')
  const day = String(shifted.getUTCDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/** `YYYY-MM-DD` 的下个月（`YYYY-MM`）；12 月回绕到下一年。 */
function nextMonthOf(dateText) {
  const [year, month] = dateText.split('-').map(Number)
  return month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`
}

/**
 * 今天这张员工餐单的状态：今天没有菜单 / 未填 / 草稿 / 已提交 / 推送失败。
 *
 * 判据跟月历、单日页同一套（`StaffMealView` 的 `cellClass`）：已提交看 `push_status`，
 * 草稿看有没有填过内容 —— `summary` 是汇总行，空数组就是还没动过。失败原因
 * （`push_error`）直接摊在这一行上：首页不显示的话，员工得点进单日页才知道推送没发出去。
 */
function staffMealRow(data) {
  const order = (data && data.order) || null
  if (!order) {
    return data && data.menu_day
      ? { text: '未填', led: 'warn' }
      : { text: '今天没有员工餐菜单', led: 'off' }
  }
  if (order.status === 'submitted') {
    if (order.push_status === 'failed') {
      return { text: '推送失败', led: 'bad', detail: order.push_error || '' }
    }
    return { text: '已提交', led: 'ok' }
  }
  const filled = Array.isArray(data.summary) ? data.summary.length : 0
  return filled > 0 ? { text: '草稿', led: 'warn' } : { text: '未填', led: 'warn' }
}

/**
 * 员工档读今天那张单。失败降级成「不显示这一行」：首页不能因为一块读不出来改结构，
 * 也不该让员工以为「今天没有菜单」（那是 404，是另一回事）。
 */
async function loadStaffMeal() {
  const date = beijingToday()
  try {
    const data = await staffRequest(`/api/staff-meal/days/${date}`)
    staffMeal.value = { date, ...staffMealRow(data) }
  } catch (err) {
    staffMeal.value = err && err.status === 404
      ? { date, text: '今天没有员工餐菜单', led: 'off' }
      : null
  }
}

/** 员工餐单日入口：带上自然日，页面按 `?date=` 打开那一天（不带「今天」以外的口径）。 */
const staffMealLink = computed(() => ({
  path: STAFF_MEAL_PATH,
  query: { date: (staffMeal.value && staffMeal.value.date) || beijingToday() },
}))

/**
 * 管理端读下月菜单：`days` 为空 = 整月还没导入（跨月提前报单就报不了 1 号）。
 * 读失败 / 形状不对就不提醒 —— 这是首页的一条提示，不是待办清单。
 */
async function loadNextMonthMeal() {
  mealNextMonth.value = nextMonthOf(beijingToday())
  mealNext.value = null
  try {
    const data = await api.get(`/api/staff-meal/months/${mealNextMonth.value}`)
    mealNext.value = data && Array.isArray(data.days) ? data : null
  } catch {
    mealNext.value = null
  }
}

/** 下月还没导入才提醒；`mealNext === null`（没读到）不提醒。 */
const mealNextMissing = computed(() => Boolean(
  mealNext.value && Array.isArray(mealNext.value.days) && mealNext.value.days.length === 0,
))

/**
 * 人事提醒那一格的副行：平时是这一格的口径（工龄奖 · 生日 · 健康证），有健康证过期时换成
 * 「健康证过期 N · 生日 M」。
 *
 * 为什么换：7 这个数字里混着「生日快乐」与「证件过期」两件事，后者是检查会罚的、而且能
 * 过期几百天（2026-11-13 副本库实测 3 张全过期，最久 −316 天）。只写一句口径，管理员得
 * 点进人事提醒页才知道这 7 件里有没有要命的；首页本来就是「今天先看哪件事」的地方。
 * 没有过期时仍沿用原口径 —— 不为 0 造一条动线（同「0 值不报警」）。
 */
const reminderHint = computed(() => {
  const r = reminders.value
  if (!r) return '工龄奖 · 生日 · 健康证'
  const items = r.certs && Array.isArray(r.certs.items) ? r.certs.items : []
  const expired = items.filter((item) => item && item.state === 'expired').length
  if (!expired) return '工龄奖 · 生日 · 健康证'
  const birthday = r.birthdays ? r.birthdays.count : 0
  return birthday ? `健康证过期 ${expired} · 生日 ${birthday}` : `健康证过期 ${expired}`
})

async function loadManager() {
  // 备货卡片上的档口名要中文：档口表没拉过就先拉一次（读不到时退回 id，不影响其他块）。
  // 只挂在店长这一档 —— 员工会话调 `/api/stations` 是 401，没必要白打一次。
  void stationsStore.load().catch(() => {})
  // 先拿营业日：`daily-queue` 不带日期时回的就是服务端那个营业日（`date`），三处读的
  // 是同一天。**不能拿手机上的今天去问排班** —— 手机时区可能不在东八区，问出隔壁那天
  // 只会得到一句「这天没铺」，店长看到「今天没人上班」。
  await safe(api.get('/api/hygiene/admin/daily-queue'), queue)
  const date = (queue.value && queue.value.date) || ''
  await Promise.all([
    safe(api.get('/api/scheduling/day', date ? { date } : {}), day),
    safe(api.get('/api/scheduling/inbox'), inbox),
    safe(api.get('/api/hygiene/admin/fix'), fix),
    safe(api.get('/api/hygiene/admin/hr-reminders'), reminders),
    safe(api.get('/api/prep-plan/summary'), prep),
    safe(api.get('/api/tasks/board', { counts_only: 1 }), tasksBoard),
    // 下月的员工餐菜单导入没有：跨月提前报单是这页唯一的「月底前」动作（S7）。
    loadNextMonthMeal(),
  ])
  businessDate.value = date
    || (day.value && day.value.business_date)
    || (inbox.value && inbox.value.today)
    || ''
}

/**
 * 员工「我的班」副行下面那两个小入口的落点：`/workbench/staff/today?act=leave|swap&date=<营业日>`。
 *
 * 为什么落「今天」页而不是直接写月历：员工登录后的默认落点就是「今天」，从首页点过来
 * 先经过它、再转进月历那一天的操作单（`TodayView` 里只做转发，不在这两页摆第二套表单）。
 * 日期取服务端营业日 `businessDate`（`/me` 的 `today`），不拿手机上的今天。
 */
function staffDayLink(act) {
  return {
    path: '/workbench/staff/today',
    query: { act, date: businessDate.value || '' },
  }
}

async function loadStaff() {
  // 先拿自己的班：营业日与「我的班次」都从它来，其余四块并行。
  await safe(staffRequest('/api/scheduling/me'), me)
  businessDate.value = (me.value && me.value.today) || ''
  // 店长多读三块（见 `manage`）。日期用服务端营业日，跟「今天」页同一份口径。
  const team = manage.value
    ? [
      safe(api.get('/api/scheduling/inbox'), teamInbox),
      safe(api.get('/api/scheduling/day', businessDate.value ? { date: businessDate.value } : {}), teamDay),
      safe(api.get('/api/prep-plan/summary'), teamPrep),
    ]
    : []
  // 卫生复核那一行（见 `canHygieneReview`）：只走员工端只读接口，失败就画「—」，
  // 不影响下面那几块，也不把整页打回错误态。
  const hygiene = canHygieneReview.value
    ? [
      safe(staffRequest('/api/hygiene/staff/daily-work?review=1'), hygieneWork),
      safe(staffRequest('/api/hygiene/staff/boards'), hygieneBoards),
    ]
    : []
  await Promise.all([
    safe(staffRequest('/api/scheduling/me/requests'), inbox),
    safe(staffRequest('/api/hygiene/staff/daily-work'), daily),
    safe(staffRequest('/api/hygiene/staff/deep-clean'), deep),
    safe(staffRequest('/api/hygiene/staff/fix'), fix),
    safe(staffRequest('/api/tasks/mine'), tasksMine),
    // 今天这张员工餐单的状态（自然日，见 `beijingToday`）。读不到就整行不显示。
    loadStaffMeal(),
    ...team,
    ...hygiene,
  ])
}

async function load() {
  if (!identity.value) return
  state.value = 'loading'
  if (identity.value === IDENTITY_ADMIN) await loadManager()
  else await loadStaff()
  state.value = 'ready'
}

onMounted(() => {
  document.title = workbenchDocumentTitle(pageTitle('/workbench'))
  void load()
})

// 切档时重取（顶栏那颗切换器改了身份，这一页的两个视角跟着换）。探针还没回来时
// `load()` 自己会跳过 —— 切档与探针都走这条，重复取一次也无妨（都是轻量 GET）。
watch(identity, () => { void load() })

// 实时（nudge + pull，票 10 那套）：店长改了排班、员工交了卫生、有人提了申请 ——
// 首页上那些数字跟着变。nudge 不带数据，收到就整页重取一次（四个轻量 GET）。
// `when` 那一层：身份还没探出来时不重取 —— 那会儿 `load()` 本来就什么都不做，
// 断线兜底（`useConnectionFallback`）在挂载时就会 pull 一次，不挡的话每次进页面都白跑。
useNudgePull({
  id: 'workbench-home',
  topics: ['scheduling', 'hygiene', 'tasks'],
  pull: () => { void load() },
  fallback: { when: () => Boolean(identity.value) },
})
</script>

<template>
  <div class="wbh">
    <header class="wbh-head">
      <div>
        <p class="wbh-kicker">{{ dateText }} · 营业日</p>
        <h1 class="wbh-title">今天</h1>
      </div>
      <div v-if="summary.view === IDENTITY_ADMIN && summary.duty" class="wbh-pills">
        <span class="wbh-pill"><i class="wbh-led ok"></i>上班 <b>{{ summary.duty.total }}</b></span>
        <span v-if="summary.leaves" class="wbh-pill is-warn"><i class="wbh-led warn"></i>待批 <b>{{ summary.leaves }}</b></span>
      </div>
    </header>

    <p v-if="!summary.view" class="wbh-wait">{{ state === 'loading' ? '正在确认身份…' : '身份还没定，稍后再看' }}</p>

    <!-- ── 店长：四个读数 + 今天的班 ─────────────────────────────────── -->
    <template v-else-if="summary.view === IDENTITY_ADMIN">
      <div class="wbh-kpis">
        <router-link class="wbh-kpi wbh-cell leaves" :class="{ 'is-warn': toneOn(summary.leaves) }" :to="HOME_LINKS.leaves">
          <div class="wbh-kh"><span>待批请假</span><i class="wbh-led" :class="toneOn(summary.leaves) ? 'warn' : 'off'"></i></div>
          <b class="wbh-num" :class="{ 'is-warn': toneOn(summary.leaves) }">{{ numberText(summary.leaves) }}</b>
          <p class="wbh-kf">排班待办 · 员工已提</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell reviews" :class="{ 'is-warn': toneOn(summary.reviews) }" :to="HOME_LINKS.reviews">
          <div class="wbh-kh"><span>待验收</span><i class="wbh-led" :class="toneOn(summary.reviews) ? 'warn' : 'off'"></i></div>
          <b class="wbh-num" :class="{ 'is-warn': toneOn(summary.reviews) }">{{ numberText(summary.reviews) }}</b>
          <p class="wbh-kf">日常检查 · 现在能验收</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell fixes" :class="{ 'is-bad': toneOn(summary.fixes) }" :to="HOME_LINKS.fixes">
          <div class="wbh-kh"><span>逾期整改</span><i class="wbh-led" :class="toneOn(summary.fixes) ? 'bad' : 'off'"></i></div>
          <b class="wbh-num" :class="{ 'is-bad': toneOn(summary.fixes) }">{{ numberText(summary.fixes) }}</b>
          <p class="wbh-kf">超过期限 · 未回拍</p>
        </router-link>
        <router-link class="wbh-kpi wbh-cell reminders" :to="HOME_LINKS.reminders">
          <div class="wbh-kh"><span>人事提醒</span><i class="wbh-led" :class="toneOn(summary.reminders) ? 'sig' : 'off'"></i></div>
          <b class="wbh-num">{{ numberText(summary.reminders) }}</b>
          <p class="wbh-kf">{{ reminderHint }}</p>
        </router-link>
      </div>

      <!-- 跨月提前报单（S7）：下月菜单还没导入，员工报不了 1 号的单。只在 `days` 为空时
           出现；读不到就不提示 —— 这是首页的一句提醒，不是待办清单，别让它变成噪音。 -->
      <router-link v-if="mealNextMissing" class="wbh-meal-next" :to="STAFF_MEAL_PATH">
        <i class="wbh-led warn"></i>
        <span class="wbh-meal-next-text">{{ mealNextMonth }} 员工餐菜单还没导入，月底前导入员工才能提前报 1 号的单</span>
        <span class="wbh-meal-next-go wbh-go">去导入 ›</span>
      </router-link>

      <div class="wbh-cols">
        <router-link class="wbh-panel wbh-duty" :to="HOME_LINKS.duty">
          <header class="wbh-ph">
            <i class="wbh-led ok"></i><h2>今天谁上班</h2>
            <span class="wbh-hint">{{ summary.duty ? summary.duty.total : '—' }} 人上班</span>
            <!-- 整块已经是链接，这里用 span：`<a>` 里再套 `<a>` 是非法 HTML（读屏会念成两个
                 同目标的链接）。点这一格任何地方都进月历，文字只是「点哪去」的提示。 -->
            <span class="wbh-more">月历 ›</span>
          </header>
          <p v-if="!day" class="wbh-empty">今天的排班读不出来，点进月历看</p>
          <template v-else-if="!summary.duty.groups.length && !summary.duty.off.length">
            <p class="wbh-empty">今天还没排班</p>
          </template>
          <template v-else>
            <div v-for="group in summary.duty.groups" :key="group.shift" class="wbh-shift">
              <div class="wbh-sh"><i class="wbh-led sig"></i><b>{{ group.shift }}</b><em>{{ group.count }} 人</em></div>
              <p>{{ group.names.join('、') }}</p>
            </div>
            <ul v-if="summary.duty.off.length" class="wbh-offs">
              <li v-for="group in summary.duty.off" :key="group.label" :class="group.tone">
                <b>{{ group.label }}</b><span>{{ group.count }} 人</span><i>{{ group.names.join('、') }}</i>
              </li>
            </ul>
          </template>
        </router-link>

        <div class="wbh-side">
          <!-- 备货计划（ADR 0117）：首页第一眼就该看到「今天还差什么」，不用记着去后勤组里翻。 -->
          <section class="wbh-panel">
            <header class="wbh-ph">
              <i class="wbh-led sig"></i><h2>备货计划</h2>
              <!-- 分母不是装饰：「还差 70」在 70/70 与 58/70 两种处境下读起来一样，
                   而一个说明谁都没动、一个说明快收尾了。 -->
              <span v-if="prep" class="wbh-hint">
                还差 {{ prep.remaining }}<template v-if="prep.total">/{{ prep.total }}</template> 项
              </span>
              <router-link class="wbh-more" :to="PREP_PLAN_PATH">去备货 ›</router-link>
            </header>
            <ul v-if="prep && prep.stations && prep.stations.length" class="wbh-prep">
              <li v-for="row in prep.stations" :key="row.station">
                <b>{{ row.label || row.station }}</b>
                <span>{{ row.remaining }}</span>
              </li>
            </ul>
            <p v-else class="wbh-empty">今天还没算（进备货计划页就开始算）</p>
          </section>

          <!-- 等我处理：把四个读数展开成「具体哪几件事」（2026-10-18 实用性改进）。
               一条一行、点行直达那个专页；数据全部来自本页已经在拉的三份响应。 -->
          <section class="wbh-panel">
            <header class="wbh-ph">
              <i class="wbh-led warn"></i><h2>等我处理</h2>
              <span v-if="summary.todos && summary.todos.length" class="wbh-hint">共 {{ summary.todos.length }} 件</span>
            </header>
            <ul v-if="summary.todos && summary.todos.length" class="wbh-todos">
              <li v-for="row in summary.todos.slice(0, MANAGER_TODO_LIMIT)" :key="row.key">
                <router-link class="wbh-todo" :to="row.to">
                  <span class="wbh-kind" :class="row.tone">{{ row.kind }}</span>
                  <span class="wbh-todo-main"><b>{{ row.title }}</b><i>{{ row.meta }}</i></span>
                  <span class="wbh-chev">›</span>
                </router-link>
              </li>
            </ul>
            <p v-if="summary.todos && summary.todos.length > MANAGER_TODO_LIMIT" class="wbh-more-line">
              还有 {{ summary.todos.length - MANAGER_TODO_LIMIT }} 件：去对应页面看全部
            </p>
            <!-- 空态只说这四类（整改 / 验收 / 任务 / 请假）都清了，不写「没有等你处理的事」：
                 页面上面还挂着人事提醒与备货两笔数，一句全局否定会让管理员以为今天真没事。 -->
            <p v-if="!summary.todos || !summary.todos.length" class="wbh-empty wbh-todo-empty">
              验收、整改、任务、请假都没有等你处理的。
              <template v-if="toneOn(summary.reminders)">人事提醒 {{ numberText(summary.reminders) }} 件在上面的格子里。</template>
            </p>
          </section>
          <section class="wbh-panel">
            <header class="wbh-ph"><i class="wbh-led sig"></i><h2>常用</h2></header>
            <div class="wbh-quick">
              <router-link v-for="link in QUICK_LINKS" :key="link.to" :to="link.to">{{ link.label }}</router-link>
            </div>
          </section>
        </div>
      </div>
    </template>

    <!-- ── 员工：我的班次 / 工作区 / 待办（B 案：我的班 + 今天要交 + 两个数字）── -->
    <template v-else>
      <!-- 读班次的那几秒不要先报「今天没有你的班」：`staffShiftCell(null)` 的默认文案就是
           这句。身份探针先回、`/me` 后回，从别的页点回「今天」时每次都会闪一下。 -->
      <p v-if="state !== 'ready'" class="wbh-wait">正在读你的班…</p>
      <template v-else>
      <!-- 我的班：卡片本体整块点进「整月」页（2026-10-30 用户裁定，原来进的是「今天」页；
           测试挂点 `.wbh-me` 保持是那个链接）；
           副行下面多一行四个入口（2026-10-29 用户裁定加上「加班」与「损耗」）：请假 / 换班
           一次点击直达「今天」页并带上动作与营业日 —— 那一页把 `?act=&date=` 转进月历当天的
           操作单（发起只有那一处表单）；加班登记、损耗登记本来就在自己那一页
           （`/workbench/staff/overtime`、`/workbench/staff/loss`），直接过去，不在这儿再
           摆第二张表单。 -->
      <!-- 管理员（员工会话 + 能力档）：除了「我的班」，店里那几件他今天要处理的事也该在
           第一屏。排班三格走 schedule 能力（待批 / 上班人数 / 备货）；卫生两笔走
           daily_review / deep_review / fix 任一，用员工端只读接口（卫生 admin 那侧对
           员工会话是 401）。读不到就显示 —，不假装成 0；这一条里没有任何验收 / 批准
           动作 —— 点进去，动作在专页里做。 -->
      <section v-if="teamSummary || hygieneSummary" class="wbh-team">
        <header class="wbh-ph">
          <i class="wbh-led" :class="teamLed"></i>
          <h2>店里的事</h2>
          <span class="wbh-hint">{{ teamHint }}</span>
        </header>
        <div v-if="teamSummary" class="wbh-team-grid">
          <router-link class="wbh-team-cell" :class="{ 'is-warn': toneOn(teamSummary.leaves) }" to="/workbench/hr/inbox">
            <b class="wbh-num" :class="{ 'is-warn': toneOn(teamSummary.leaves) }">{{ numberText(teamSummary.leaves) }}</b>
            <span class="wbh-team-lab">件待批请假 ›</span>
          </router-link>
          <router-link class="wbh-team-cell" to="/workbench/hr/calendar">
            <b class="wbh-num">{{ numberText(teamSummary.onDuty) }}</b>
            <span class="wbh-team-lab">人今天上班 ›</span>
          </router-link>
          <router-link class="wbh-team-cell" :class="{ 'is-warn': toneOn(teamSummary.remaining) }" to="/workbench/kitchen/prep-plan">
            <b class="wbh-num" :class="{ 'is-warn': toneOn(teamSummary.remaining) }">{{ numberText(teamSummary.remaining) }}<small v-if="teamPrep && teamPrep.total" class="wbh-den">/{{ teamPrep.total }}</small></b>
            <span class="wbh-team-lab">项备货还差 ›</span>
          </router-link>
        </div>
        <!-- 卫生复核（2026-11-10 用户裁定）：一行小条而不是第四格 —— 排班三格是同一件事，
             卫生是另一件；390 下四格数字会挤成一团。点进员工端卫生页，复核动作在那边做。 -->
        <router-link
          v-if="hygieneSummary"
          class="wbh-team-row"
          :class="{ 'is-warn': toneOn(hygieneSummary.review) || toneOn(hygieneSummary.rejected) }"
          to="/workbench/staff/clean"
        >
          <i class="wbh-led" :class="toneOn(hygieneSummary.review) || toneOn(hygieneSummary.rejected) ? 'warn' : 'off'"></i>
          <!-- 数据标注（控件文本），文案预算按 `.wbh-team-lab` / `.wbh-go` 排除，与上面三格同口径。 -->
          <span class="wbh-team-row-lab">卫生复核</span>
          <b class="wbh-team-lab">待我复核 {{ numberText(hygieneSummary.review) }} 项</b>
          <span class="wbh-team-row-sep">·</span>
          <b class="wbh-team-lab" :class="{ 'is-warn': toneOn(hygieneSummary.rejected) }">本周被驳回 {{ numberText(hygieneSummary.rejected) }} 次</b>
          <span class="wbh-team-go wbh-go">去卫生页 ›</span>
        </router-link>
      </section>

      <section class="wbh-me-card">
        <router-link class="wbh-me" :to="HOME_LINKS.myMonth">
          <div class="wbh-kh">
            <span>我的班</span>
            <!-- 状态不能再写死「在岗」：休 / 请假 / 未排的那天大字写「休」、右上角却亮绿灯，
                 是员工每天第一眼就能看到的错话。tone 与「今天」页那五态同一份口径。 -->
            <em class="wbh-onsite" :class="'is-' + (summary.me.shift.tone || 'shift')">
              <i class="wbh-led" :class="shiftState(summary.me.shift.tone).led"></i>{{ shiftState(summary.me.shift.tone).text }}
            </em>
          </div>
          <b class="wbh-shift-big">{{ summary.me.shift.headline }}</b>
          <p class="wbh-sub">{{ summary.me.shift.subline }}</p>
        </router-link>
        <div class="wbh-me-acts">
          <router-link class="wbh-me-act" :to="staffDayLink('leave')">请假</router-link>
          <router-link class="wbh-me-act" :to="staffDayLink('swap')">换班</router-link>
          <router-link class="wbh-me-act" :to="HOME_LINKS.myOvertime">加班</router-link>
          <router-link class="wbh-me-act" :to="HOME_LINKS.myWastage">损耗</router-link>
        </div>
      </section>

      <!-- 今天要交（卫生）排在员工餐前面：它是这一屏唯一带钟点的事（日常 18:00 / 夜班
           23:30，见 `StaffHygieneCard` 的「前交」），员工餐单没有系统截止（ADR 0135 不做
           催办），往后放一屏不误事。 -->
      <StaffHygieneCard />

      <!-- 员工餐（S2）：今天这张单的状态，点进单日页填 / 改。日期必须取**自然日**
           （`beijingToday`），不能用上面那个 06:00 切的营业日 `businessDate`。 -->
      <router-link
        v-if="staffMeal"
        class="wbh-meal"
        :class="'is-' + staffMeal.led"
        :to="staffMealLink"
      >
        <i class="wbh-led" :class="staffMeal.led"></i>
        <span class="wbh-meal-lab">员工餐</span>
        <b class="wbh-meal-state">{{ staffMeal.text }}</b>
        <span v-if="staffMeal.detail" class="wbh-meal-detail wbh-kf">{{ staffMeal.detail }}</span>
        <span class="wbh-meal-go wbh-go">›</span>
      </router-link>

      <!-- 任务（票 07）：独立一张卡，不并进「我的待办」—— 卫生那三块与工作任务
           是两条动线（一个在卫生页、一个在任务页），混在一个数字里没人知道点去哪。 -->
      <div class="wbh-cells wbh-cells-task">
        <router-link class="wbh-cell myTasks" :class="{ 'is-warn': toneOn(taskTodoCount) }" :to="HOME_LINKS.myTasks">
          <div class="wbh-kh"><span>任务</span><i class="wbh-led" :class="toneOn(taskTodoCount) ? 'warn' : 'off'"></i></div>
          <b class="wbh-num" :class="{ 'is-warn': toneOn(taskTodoCount) }">{{ numberText(taskTodoCount) }}</b>
          <p class="wbh-kf">{{ taskCardHint }}</p>
        </router-link>
      </div>

      <div class="wbh-cells">
        <router-link class="wbh-cell myItems" :class="{ 'is-warn': toneOn(summary.work.pending) }" :to="HOME_LINKS.myItems">
          <div class="wbh-kh"><span>我的待办</span><i class="wbh-led" :class="toneOn(summary.work.pending) ? 'warn' : 'off'"></i></div>
          <b class="wbh-num" :class="{ 'is-warn': toneOn(summary.work.pending) }">{{ numberText(summary.work.pending) }}</b>
          <p class="wbh-kf">卫生待办 ›</p>
        </router-link>
        <router-link class="wbh-cell myRequests" :to="HOME_LINKS.myRequests">
          <div class="wbh-kh"><span>等我回应</span><i class="wbh-led" :class="toneOn(summary.incoming) ? 'sig' : 'off'"></i></div>
          <b class="wbh-num">{{ numberText(summary.incoming) }}</b>
          <p class="wbh-kf">我的申请 ›</p>
        </router-link>
      </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* B 案「钢面 · 仪」（2026-10-17 重构）：读数 + 面板 + LED。全部走语义令牌，
   浅 / 深两套主题各自生效；这里不写裸色值。 */
.wbh {
  padding-bottom: calc(40px + var(--safe-b));
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.wbh-head { display: flex; align-items: flex-end; gap: 16px; }
.wbh-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.wbh-title {
  margin: 0;
  font: 600 26px/1 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.wbh-wait { margin: 18px 0 0; font-size: 12px; color: var(--ink-dim); }

.wbh-pills { margin-left: auto; display: flex; flex-wrap: wrap; gap: 8px; }
.wbh-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink-dim);
  font: 500 10px/1 var(--font-mono);
}
.wbh-pill b { color: var(--ink); font-weight: 600; }
.wbh-pill.is-warn b { color: var(--warn); }

.wbh-led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.wbh-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.wbh-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.wbh-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.wbh-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.wbh-led.off { background: var(--line-strong); }
.wbh-onsite.is-rest, .wbh-onsite.is-none { color: var(--ink-faint); }
.wbh-onsite.is-leave { color: var(--info); }
.wbh-onsite.is-half, .wbh-onsite.is-moved { color: var(--warn); }

.wbh-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.wbh-kpi {
  position: relative; overflow: hidden;
  display: flex; flex-direction: column; gap: 9px;
  padding: 13px 15px 12px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  text-decoration: none;
}
.wbh-kpi::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--action); }
.wbh-kpi.is-warn::after { background: var(--warn); }
.wbh-kpi.is-bad::after { background: var(--danger); }
.wbh-kh {
  display: flex; align-items: center; gap: 7px;
  font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint);
}
.wbh-kh .wbh-led { margin-left: auto; }
.wbh-num { font: 500 30px/1 var(--font-mono); letter-spacing: -.03em; color: var(--ink); }
.wbh-num.is-warn { color: var(--warn); }
.wbh-num.is-bad { color: var(--danger-bright); }
/* 备货的分子/分母（2026-11-13 走查）：「还差 70」在 70/70 与 58/70 下读起来一样，分母才
   说得清是没人动还是快收尾。字号压小一档，不跟主数字抢。 */
.wbh-den { margin-left: 1px; font-size: 13px; font-weight: 500; letter-spacing: 0; color: var(--ink-faint); }
.wbh-gauge { display: flex; gap: 2px; }
.wbh-gauge i { flex: 1; height: 3px; border-radius: 1px; background: var(--line); }
.wbh-gauge i.on { background: var(--action); }
.wbh-kpi.is-warn .wbh-gauge i.on { background: var(--warn); }
.wbh-kpi.is-bad .wbh-gauge i.on { background: var(--danger); }
.wbh-kf { margin: 0; font-size: 9.5px; color: var(--ink-faint); }

.wbh-cols { display: grid; grid-template-columns: 1.6fr 1fr; gap: 14px; align-items: start; }
.wbh-side { display: flex; flex-direction: column; gap: 14px; }
.wbh-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
}
.wbh-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbh-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbh-hint { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbh-more { margin-left: auto; font-size: 10.5px; color: var(--ink-dim); text-decoration: none; }
.wbh-empty { margin: 0; padding: 14px; font-size: 11px; color: var(--ink-dim); }
.wbh-more-line { margin: 0; padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08); font-size: 10.5px; color: var(--ink-faint); }

.wbh-shift { padding: 12px 14px 13px; border-bottom: 1px solid var(--wb-ink-a08); }
.wbh-shift:last-child { border-bottom: 0; }
.wbh-sh { display: flex; align-items: center; gap: 8px; }
.wbh-sh b { font-size: 11.5px; font-weight: 600; letter-spacing: .04em; color: var(--ink); }
.wbh-sh em { margin-left: auto; font-style: normal; font: 500 11px/1 var(--font-mono); color: var(--ink); }
.wbh-shift p { margin: 7px 0 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }

.wbh-offs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.wbh-duty { text-decoration: none; display: block; }
.wbh-offs li {
  display: flex; align-items: baseline; gap: 8px;
  padding: 9px 14px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 10.5px; color: var(--ink-dim);
}
.wbh-offs li b { min-width: 26px; font-weight: 600; color: var(--ink); }
.wbh-offs li i { font-style: normal; margin-left: auto; color: var(--ink-dim); }

/* 等我处理：类型 / 主副两行 / 箭头，行高 56（触控），一行一条。 */
.wbh-todos { list-style: none; margin: 0; padding: 0; }
.wbh-todos li { border-bottom: 1px solid var(--wb-ink-a08); }
.wbh-todos li:last-child { border-bottom: 0; }
.wbh-todo {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 9px 14px;
  text-decoration: none;
}
.wbh-todo:hover { background: var(--surface-2); }
.wbh-kind {
  font: 500 9px/1 var(--font-sans);
  letter-spacing: .16em;
  color: var(--ink-dim);
  border-left: 2px solid currentColor;
  padding-left: 6px;
}
.wbh-kind.warn { color: var(--warn); }
.wbh-kind.bad { color: var(--danger-bright); }
.wbh-todo-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.wbh-todo-main b { font-size: 12px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbh-todo-main i { font-style: normal; font: 400 9.5px/1.2 var(--font-mono); color: var(--ink-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbh-chev { color: var(--ink-faint); font-size: 14px; }
.wbh-empty { margin: 0; padding: 14px; font-size: 11px; color: var(--ink-dim); }
.wbh-more-line { margin: 0; padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08); font-size: 10.5px; color: var(--ink-faint); }

.wbh-rem { display: flex; flex-direction: column; }
.wbh-rem-row { display: flex; align-items: baseline; gap: 10px; padding: 12px 14px; }
.wbh-rem-row b { font: 500 22px/1 var(--font-mono); color: var(--ink); }
.wbh-rem-row span { font-size: 10px; color: var(--ink-dim); }
.wbh-rem-go {
  display: block; padding: 9px 14px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 10.5px; color: var(--ink-dim); text-decoration: none;
}
.wbh-quick { display: grid; grid-template-columns: 1fr 1fr; }
.wbh-quick a {
  padding: 10px 14px; font-size: 11px; color: var(--ink-dim); text-decoration: none;
  border-bottom: 1px solid var(--wb-ink-a08);
}
.wbh-quick a:nth-child(odd) { border-right: 1px solid var(--wb-ink-a08); }

/* 备货计划（ADR 0117）：一行一个档口，右边是「还差几项」。 */
.wbh-prep { list-style: none; margin: 0; padding: 0; }
.wbh-prep li {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  min-height: 44px; padding: 0 14px;
  border-bottom: 1px solid var(--wb-ink-a08);
}
.wbh-prep li:last-child { border-bottom: 0; }
.wbh-prep b { font-weight: 600; font-size: 12px; color: var(--ink); }
.wbh-prep span {
  font-size: 13px; font-weight: 600; color: var(--action);
  font-variant-numeric: tabular-nums;
}

/* 员工档：卡片外壳（底 / 边 / 阴影 / 左侧语义条）+ 里面的班次链接 + 一行直达入口。
   外壳不是链接：请假 / 换班 是两张碰得到的入口，链接不能套链接。 */
.wbh-me-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
  position: relative;
}
.wbh-me-card::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--action); }
.wbh-me {
  display: flex; flex-direction: column; gap: 10px;
  padding: 14px 16px 12px;
  text-decoration: none;
}
.wbh-me-acts {
  display: flex;
  border-top: 1px solid var(--wb-ink-a08);
}
.wbh-me-act {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  font-size: 13px;
  font-weight: 600;
  color: var(--action);
  text-decoration: none;
}
.wbh-me-act + .wbh-me-act { border-left: 1px solid var(--wb-ink-a08); }
.wbh-onsite { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; font-style: normal; font-size: 9.5px; color: var(--ok); letter-spacing: .1em; }
.wbh-shift-big { font: 600 30px/1.1 var(--font-sans); letter-spacing: .02em; color: var(--ink); }
.wbh-sub { margin: 0; font-size: 11.5px; color: var(--ink-dim); }
.wbh-next { margin: 0; padding-top: 10px; border-top: 1px solid var(--wb-ink-a08); display: flex; flex-wrap: wrap; gap: 12px; font-size: 10.5px; color: var(--ink-dim); }

.wbh-cells { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
/* 任务卡单独一行：它是另一条动线，不跟卫生那两块挤两列。 */
.wbh-cells-task { grid-template-columns: 1fr; }
.wbh-cell {
  display: flex; flex-direction: column; gap: 8px;
  padding: 12px 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  text-decoration: none;
}

/* 管理员（员工会话 + schedule 权限）的「店里的事」：一条三格（待批请假 / 今天上班 / 备货）。
   三格等宽、手机上也是一行 —— 它是管理摘要，不该比下面的「我的班」更占地方。 */
.wbh-team {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  overflow: hidden;
}
.wbh-team-grid { display: grid; grid-template-columns: repeat(3, 1fr); }
.wbh-team-cell {
  display: flex; flex-direction: column; gap: 6px;
  min-height: 64px; padding: 12px 14px;
  border-left: 1px solid var(--wb-ink-a08);
  text-decoration: none;
}
.wbh-team-cell:first-child { border-left: 0; }
.wbh-team-cell .wbh-num { font-size: 24px; }
.wbh-team-cell span { font-size: 10px; line-height: 1.4; color: var(--ink-dim); }
.wbh-team-cell:hover { background: var(--surface-2); }

/* 卫生复核那一行（2026-11-10 用户裁定）：管理员（员工会话 + 卫生复核能力）的
   「待我复核 / 本周被驳回」，点进员工端卫生页。做成一行小条而不是第四格 —— 排班
   三格是同一件事，卫生是另一件；390 下四格数字会挤成一团。 */
.wbh-team-row {
  display: flex; align-items: center; gap: 7px;
  min-height: 44px; padding: 10px 14px;
  border-top: 1px solid var(--wb-ink-a08);
  font-size: 11px; color: var(--ink-dim);
  text-decoration: none;
}
.wbh-team-row b { font-size: 12px; font-weight: 600; color: var(--ink); white-space: nowrap; }
.wbh-team-row b.is-warn { color: var(--warn); }
.wbh-team-row:hover { background: var(--surface-2); }
.wbh-team-row-lab { white-space: nowrap; }
.wbh-team-row.is-warn .wbh-team-row-lab { color: var(--warn); }
.wbh-team-row-sep { color: var(--ink-faint); }
.wbh-team-go { margin-left: auto; font-size: 10px; color: var(--action); white-space: nowrap; }

/* 员工档「员工餐」一行（2026-11-13 走查 S2）：今天这张单的状态 + 直达单日页。
   独立一条而不是塞进「我的班」卡片：卡片本体整块是链接，链接不能套链接。 */
.wbh-meal {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; padding: 0 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  text-decoration: none;
}
.wbh-meal:hover { background: var(--surface-2); }
.wbh-meal-lab { font-size: 11px; color: var(--ink-dim); white-space: nowrap; }
.wbh-meal-state { font-size: 12px; font-weight: 600; color: var(--ink); white-space: nowrap; }
.wbh-meal.is-warn .wbh-meal-state { color: var(--warn); }
.wbh-meal.is-bad .wbh-meal-state { color: var(--danger-bright); }
.wbh-meal-detail { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbh-meal-go { margin-left: auto; font-size: 13px; color: var(--action); white-space: nowrap; }

/* 管理端「下月员工餐菜单还没导入」（S7）：左侧黄条一行提醒，点进员工餐页导入。 */
.wbh-meal-next {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; padding: 10px 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-left: 3px solid var(--warn);
  border-radius: var(--r-2);
  text-decoration: none;
}
.wbh-meal-next:hover { background: var(--surface-2); }
.wbh-meal-next-text { font-size: 11px; line-height: 1.55; color: var(--ink-dim); }
.wbh-meal-next-go { margin-left: auto; font-size: 10.5px; color: var(--action); white-space: nowrap; }

@media (max-width: 900px) {
  .wbh-cols { grid-template-columns: 1fr; }
}
/* 四个读数在 720 以上保持一行：834（iPad 竖屏）是门店常见的看板尺寸，压成两列会让四个
   大卡各占半屏、一个数字孤零零地摆在中间。 */
@media (max-width: 720px) {
  .wbh-kpis { grid-template-columns: repeat(2, 1fr); }
  .wbh-head { align-items: flex-start; flex-direction: column; gap: 10px; }
  .wbh-pills { margin-left: 0; }
  .wbh-title { font-size: 22px; }
  .wbh-kpi { padding: 11px 13px 10px; }
  .wbh-num { font-size: 26px; }
  .wbh-team-row { gap: 5px; padding: 10px 12px; }
}
</style>
