<script setup>
// 超级管理员端「排班」。版式按原型的 B · 月历（`.scratch/scheduling/prototype/admin-variants.html`
// 的 `[data-variant="B"]`）：一屏是本月人手网格（每格写当天白班、夜班各几人），
// 下面一张卡是选中那天「谁上哪个班」，底下一根「还没配规则的人」的条。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
// 票 02 做「配固定班次」、票 03 加工作区、票 04 加轮转周期编辑、票 07 加单日覆盖
// （点当天卡里的一个人就地改那一天）、票 08 加底下那根「请假待办」的条
// （批假在 `/workbench/hr/inbox` 那一页：这一页只管排班怎么铺）；班次表的增删改在票 11。
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { api } from '../../api/client'
// 共享样式表由壳加载（`SchedulingLayout.vue`）：三个子页各加载一份会挂出重复的
// <link>，壳一层管住就跟卫生管理端一个做法。
import { useNudgePull } from '../../composables/useNudgePull'
import { SHIFT_COLOR_NEUTRAL, mergeShiftList, shiftColorClass } from '../../utils/shiftTable'
// 天数的写法只有一份（`12 天` / `19.5 天` / `0 天`，负数带全角减号 = 欠班）：
// 周表成员列那行存假与考勤表卡片写同一个字面，别在这里另起一套格式。
import { formatDays } from '../../utils/attendanceSheet'
import { eachDayInRange } from '../../utils/dateRange'
// 「那一天要顶的档」的清单（票 02）：一行一条、`can_pen` / `blocked_reason` 全在那边算，
// 这一页只渲染（票 03 的长按弹层与票 04 的名单面板那一节吃同一份输出）。
import { BLOCKED_SHIFT_INACTIVE, BLOCKED_ZONE_DELETED, coverSlotsFor } from '../../utils/weekCoverSlots'
// 周表的落笔与选区（票 14 / ADR 0107，2026-10-13 方向修订）：纯逻辑在
// `utils/schedulingWeekPick.js` 里 —— 这一页只负责把手柄接上去、把笔与回执接上去。
// `cellHandle` 不再被这一页用：格子从「选区手柄」改回「点一下就落笔」，手柄只剩
// 日期列头与人名两种（纯逻辑那边仍覆盖格子手柄，见它的单测）。
import {
  PICK_HALF_REST,
  PICK_MAX_CELLS,
  PICK_REST,
  PICK_RESTORE,
  PICK_SHIFT,
  PICK_SKIP_TEXT,
  PICK_VOID,
  batchPayload,
  colHandle,
  normalizeResult,
  overLimit,
  pastDatesIn,
  pickAt,
  pickPending,
  pickRegion,
  predictSkips,
  rowHandle,
  snapshotCells,
  undoPlan,
} from '../../utils/schedulingWeekPick'


const router = useRouter()
const route = useRoute()

// ── URL 里的视角 / 月份 / 周起点（v4）────────────────────────────────────────
// 换页 / 翻月 / 翻周都写进 query（`router.replace`，不进历史）；`onMounted` 读回，
// 缺省落在「今天所在的月 + 月历视角」。只同步这三个键，别把页面其它 query 扔掉。
const VIEW_KEYS = ['month', 'week', 'roster']
const QUERY_MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/
const QUERY_DATE_RE = /^\d{4}-\d{2}-\d{2}$/
let queryReady = false

function queryText(value) {
  if (Array.isArray(value)) return typeof value[0] === 'string' ? value[0] : ''
  return typeof value === 'string' ? value : ''
}

function currentQuerySnapshot() {
  return {
    view: VIEW_KEYS.includes(panel.value) ? panel.value : 'month',
    month: monthValue.value || currentMonthValue(),
    // 周起点只在周表视角下有意义；其它视角不写，免得 URL 带着一个看不见的日期。
    start: panel.value === 'week' && weekStart.value ? weekStart.value : '',
  }
}

/** 把当前视角 / 月份 / 周起点同步到 URL（replace，不产生历史）。 */
function syncQuery() {
  if (!queryReady) return
  const next = currentQuerySnapshot()
  const query = route.query
  const same = queryText(query.view) === next.view
    && queryText(query.month) === next.month
    && queryText(query.start) === next.start
  if (same) return
  const merged = { ...query, view: next.view, month: next.month }
  if (next.start) merged.start = next.start
  else delete merged.start
  void router.replace({ query: merged })
}

// 周一开头（2026-09-30 全模块从周日开头改过来）：服务端 `_month_frame` 的 `lead` 与
// 员工端月历的 `MONTH_HEADS` 是同一套，三个地方必须一起动，否则同一页两个周框。
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
const WEEK_HEADS = ['一', '二', '三', '四', '五', '六', '日'] // 周表：表头就是这个顺序
// 班次颜色（0043）：**颜色存在班次表里**，前端不再按序号推断。
//
// 以前这里是 `SHIFT_TONES = ['mint','aqua',…]`，按班次在列表里的位置取色：停用或新增
// 一个班次，全店格子的颜色整体错位；`mint`/`aqua` 还共用同一个底色、浅色主题下两个
// 色值是同一个蓝 —— 店长分不出「早班」和「夜班」，这正是 0043 要修的事。
// 现在班次自带 `color_key`（moss/mint/tide/sky/pine/rose/grass/lilac/neutral），
// 色值在 `theme.workbench.css` 的 `--shift-<key>` 里，深浅主题共用同一套。
// 撞色仍然只算辅助：格子里一定留班次缩写，图例写全名。

const loading = ref(true)
const errorText = ref('')
const shifts = ref([])
const zones = ref([])
const calendar = ref(null)
const selectedDate = ref('')
const dayDetail = ref(null)
const monthValue = ref('')
const panel = ref('month') // 'month' | 'roster'
const roster = ref([])
const busyEmployeeId = ref(null)
// 单日覆盖（票 07）：点当天卡里的一个人，就地改这一天。
// `day` 是**打开编辑器那一刻的那一天**（冻在这里）：换一天看名单时 `selectedDate` 会变，
// 而保存/撤销必须写回他点开的那一天 —— 读实时的 `selectedDate` 会把改动写到隔壁那天去。
// `undoable` 是当天整天的：过去的日子记录撤了也不重写历史（服务层只删记录），
// 那种情况不给「撤销」按钮，改成一句实话。
const editing = ref(null) // { id, name, day, overridden, undoable } | null
const editShift = ref('') // 班次 id 的字符串，或 'rest'（那天休）
const editZone = ref('') // 工作区 id 的字符串；'' = 跟这个班次的固定区
const editError = ref('')
const overrideBusy = ref(false)

const today = computed(() => (calendar.value && calendar.value.today) || '')
const shiftById = computed(() => {
  const index = {}
  for (const shift of shifts.value) index[shift.id] = shift
  return index
})

// 配规则用的班次列表：只出**还在用**的（服务端 `_require_shifts_usable` 是同一条口径，
// 单日覆盖那条路也校验）。`shifts` 那份是显示用的，带着「这个月真有行的停用班次」——
// 它们必须出现在月历和当天名单上（票 11：停用后历史排班照旧显示），但出现在周期解析
// 或固定工作区的选择里，超级管理员选完只会吃一句 400。
const activeShifts = computed(() => shifts.value.filter((shift) => shift.is_active))

// 这个月有几天是被单日覆盖改过的（票 07）：>0 时图例里多一句「青点 = 这天有改动」，
// 免得那个点看起来像装饰。
const overriddenDays = computed(() =>
  ((calendar.value && calendar.value.days) || []).filter((day) => day.overridden > 0).length
)

// 这个月有几天有人「休半天」（票 07 / ADR 0114）：>0 时图例里多一句，免得格子上那个
// 「半N」看起来像装饰。月历**只读**；设置它的门有考勤表页与周表笔栏（ADR 0122）。
const halfRestDays = computed(() =>
  ((calendar.value && calendar.value.days) || []).filter((day) => day.half_rest > 0).length
)

// 没有班的人分两拨：批过假的（`leave`，服务层从覆盖记录的 `kind` 认出来）与本来就休的。
// 两者在结果表里长得一样（`shift_id` 都是空），只有这里分得开 —— 不然超级管理员分不清
// 「这人请假了」和「这人今天本来就休」，也分不清它跟票 07 手动改成的休。
const offPeople = computed(() => (dayDetail.value && dayDetail.value.off_people) || [])
const leavePeople = computed(() => offPeople.value.filter((person) => person.leave))
const restPeople = computed(() => offPeople.value.filter((person) => !person.leave))

// 展开只铺未来若干天（长度归服务层的 `EXPANSION_DAYS`，票 02 的验收项）。翻到窗口尽头那个月时，
// 剩下一周注定是空的 —— 不说清楚的话，超级管理员会把它读成「那天没人上班」。
const beyondNote = computed(() => {
  const data = calendar.value
  if (!data || !data.window_end) return ''
  const last = data.days.length ? data.days[data.days.length - 1].business_date : ''
  if (!last || last <= data.window_end) return ''
  // 只报服务端给的末日（复核 N5：这里原先自己写一句「今天起 90 天」，窗口长度一改就是假的）。
  return `只铺到 ${data.window_end}，之后的格子还没排`
})

function currentMonthValue() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function shiftMonth(value, delta) {
  // 首屏失败时 `monthValue` 还是空串，`''.split('-')` 出来的是 NaN，
  // `new Date(NaN, …)` 会得到 `'NaN-NaN'` —— 那是个**真值字符串**，上层那句
  // `month || monthValue || current` 拦不住，点一下「上月」就发一个 `month=NaN-NaN`
  // 给后端（回一句「月份格式应该是 YYYY-MM」，把真正的病因盖掉）。退回当前月。
  const [year, month] = (value || currentMonthValue()).split('-').map(Number)
  const base = new Date(year, month - 1 + delta, 1)
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}`
}

function monthTitle(value) {
  const [year, month] = value.split('-').map(Number)
  return `${year} 年 ${month} 月`
}

/** `shiftId → 色键`。认不出这个班次（列表还没读回来、或历史行指向已删的班次）走中性。 */
const colorByShift = computed(() => {
  const index = {}
  for (const shift of shifts.value) index[shift.id] = shift.color_key || SHIFT_COLOR_NEUTRAL
  return index
})

/** 颜色这一列在不在（0043 还没应用时服务端回 `color_key: null`）。

 * 单独露出来是给页顶那条提示条用的：读路径降级成中性灰，但「为什么全是灰」得说一句，
 * 不然店长以为是配色坏了。写颜色的那条路不降级（服务端会报 not_migrated）。
 */
const colorReady = computed(() => shifts.value.every((shift) => !!shift.color_key))

function toneOf(shiftId) {
  return colorByShift.value[shiftId] || SHIFT_COLOR_NEUTRAL
}

function toneClass(shiftId) {
  // class 名从 util 里取（`tone-<键>`）：班次表页与员工端用的是同一个函数，
  // 三处不能各拼一遍字符串。
  return shiftColorClass(toneOf(shiftId))
}

function formatDayLabel(businessDate) {
  if (!businessDate) return ''
  const [year, month, day] = businessDate.split('-').map(Number)
  // `WEEKDAYS` 是**周一开头**（索引 0 = 周一），而 `getDay()` 是周日=0 —— 直接拿它索引
  // 会整体错一天（10/5 周一显示成「周二」）。跟 `weekStart()`、服务端 `date.weekday()`
  // 一样先换算成周一=0。
  const weekday = WEEKDAYS[(new Date(year, month - 1, day).getDay() + 6) % 7]
  return `${month}/${day} 周${weekday}`
}

function formatTodayLabel() {
  if (!today.value) return ''
  // 跟格子里的日期同一套写法（「9/24 周四」），别再自己拼一遍 —— 之前那版多绕了
  // 一次 replace + 正则，结果是「今天 9/24/四」。
  return `今天 ${formatDayLabel(today.value)}`
}

// 每格的人数：第一个班次用 <b>，其余用 <i>，中间用 · 分隔（原型 B 的写法）。
function cellCounts(day) {
  return shifts.value.map((shift) => ({
    id: shift.id,
    // 月历色条用完整的 class（`tone-moss`），跟周表格子同一套 token。
    tone: toneClass(shift.id),
    count: day.counts[String(shift.id)] || 0,
  }))
}

// 月历请求的序号（见 `loadCalendar`）：慢网下只认最后一次请求的响应。
let calendarSeq = 0

async function loadCalendar(month, silent = false) {
  errorText.value = ''
  const value = month || monthValue.value || currentMonthValue()
  // 请求序号：慢网下连点「上月」（或「上月」与「本月」并发）时，先到的旧响应会把后点的
  // 那个月覆盖掉（审查发现的 A9）。只有最后一次请求的响应算数。
  const seq = (calendarSeq += 1)
  // 月份**立刻**更新：原来只在成功之后才写 `monthValue`，于是连点两次算出来的目标是
  // 同一个月，第二次点击看起来像没反应。
  monthValue.value = value
  // `silent`：实时 nudge 触发的重读不翻 loading —— 每来一条就闪一下白，很吵。
  if (!silent) loading.value = true
  try {
    const data = await api.get('/api/scheduling/calendar', { month: value })
    if (seq !== calendarSeq) return
    calendar.value = data
    shifts.value = data.shifts || []
    monthValue.value = data.month
    // 选中那天：今天在这个月就选今天，否则选 1 号（原型 B 一进来就是「今天」那格）。
    const inMonth = (data.days || []).some((day) => day.business_date === data.today)
    selectedDate.value = inMonth && data.today ? data.today : (data.days[0] || {}).business_date || ''
    await loadDay(selectedDate.value)
  } catch (err) {
    if (seq !== calendarSeq) return
    errorText.value = err.message || '排班月历加载失败'
  } finally {
    if (seq === calendarSeq) {
      if (!silent) loading.value = false
      syncQuery()
    }
  }
}

async function loadDay(businessDate) {
  if (!businessDate) {
    dayDetail.value = null
    return
  }
  selectedDate.value = businessDate
  // 换一天先把上一天的人清掉（模板对 null 有「读取中…」兜底）：请求失败时表头已经是
  // 新那天、名单却还列着旧那天的人，超级管理员会照着错的名单改班（审查发现的 A2）。
  dayDetail.value = null
  try {
    dayDetail.value = await api.get('/api/scheduling/day', { date: businessDate })
    // 成功就把上一次那句错误收掉：原来只有 `loadCalendar` 清 `errorText`，一条失败提示
    // 会一直挂在页面上，看着像页面坏了。
    errorText.value = ''
  } catch (err) {
    errorText.value = err.message || '这一天读不出来'
  }
}

async function loadRoster() {
  try {
    const data = await api.get('/api/scheduling/roster')
    roster.value = data.employees || []
    // 并集，不是替换：名单这份只有启用的班次，直接换上去会把「这个月还有行」的停用
    // 班次从月历的格子、图例和当天卡上挤掉（`mergeShiftList` 里写了为什么不能那么走）。
    if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
    // 工作区名单来自公共层（读的是卫生建的那份），所以卫生那边新建一个区，
    // 这里刷新一下就能选到 —— 不需要重启，也不用在排班这边再建一份。
    zones.value = data.zones || []
    if (data.max_cycle_days) maxCycleDays.value = data.max_cycle_days
  } catch (err) {
    errorText.value = err.message || '名单读不出来'
  }
}

async function openPanel(next) {
  panel.value = next
  // 切走就把选区与展开的笔栏收掉：动作条只在周表这一栏有意义。
  if (next !== 'week') {
    clearPick()
    penOpen.value = false
  }
  if (next === 'roster') {
    await loadRoster()
    // 名单顶部那一节（票 04）算的是「今天要顶的档」，吃的是 `/week` 那份数据（`week.today`
    // 与 `week.employees`）。这一节说的**永远是今天**，可 `/week` 只回打开那一周的 7 天：
    // 直接落在 `?view=roster`（刷新 / 书签）时周表还没读过，翻过周 / 用 `?start=` 深链到
    // 旧周时手上那份又不含今天 —— 两种情况下 `rosterCoverRows` 都拿不到今天的格子，整节
    // 静默消失，看起来像「今天没人休」。所以只要已加载的那一周不含 `week.today` 就补拉
    // 今天那一周；拿到别的周的数据只会得出「今天没人休」这种假话。只补数据，不渲染周表。
    const loadedWeek = week.value
    const coversToday = !!loadedWeek
      && (loadedWeek.days || []).some((day) => day.business_date === loadedWeek.today)
    if (!coversToday) await loadWeek(mondayOf(today.value))
  }
  // 周表按需读：默认落在月历上，没切过去就不发这个请求。
  else if (next === 'week') {
    await loadWeek(weekStart.value || mondayOf(today.value))
    // 单格摘要要写「这个人的轮转规则」，规则与工作区名单都在名单那一份数据里：懒加载补一次。
    if (!roster.value.length || !zones.value.length) await loadRoster()
    // 待批提醒按天取（周表每格没有这个字段）：进周表时把待办读一遍。
    await loadPendingMarks()
    // 成员列名字底下那行存假（2026-11-01 用户要的）：与待办一样，切到周表才读。
    await loadLeaveBalances()
  }
}

// 「还没配的人」= 在职（已批准、没停用）但一条规则都没有。名单面板和底下那根
// 提示条要用同一个口径，所以只写这一份纯函数。
function needsRule(employee) {
  // 「配了但读不出来」也要算进来：那种人在月历上同样不会有新班（`expand()` 会跳过他们），
  // 而名单上只写「还没配」的话，超级管理员会以为是没配过 —— 两拨都是要处理的人。
  return (!employee.rule || employee.rule.invalid) && !employee.resigned && employee.approved
}

function countPending(employees) {
  return (employees || []).filter(needsRule).length
}

// 名单上还没配的人数 —— 进页面时单独问一次，给底下那根条用（原型 B 的 .gPend）。
//
// 这里**不碰 `shifts`**：那位是「月历显示用的班次列」，归 `/calendar` 管。名单这份只有
// 启用的班次（`list_shifts()` 不带停用的），覆盖过去会让停用班次从这个月的格子、图例和
// 当天卡的颜色里凭空消失 —— 而它在这个月明明还有行（票 11 验收②：停用之后历史排班照旧
// 显示）。数人只需要 `employees`。
//
// 读不出来时置 `null`（**不是 0**）：显示成「全员都配好了」是一句假话，超级管理员会照着它放心。
const pendingCount = ref(null)
/** 手机：点一天后当天名单走底部抽屉（桌面是右栏常驻）。 */
const mobileDayOpen = ref(false)
/** 周表「搜成员」收进成员列头的放大镜。 */
const weekFindOpen = ref(false)
/** 笔栏工具条上「选中：…」那一行（最近点过的那一格）。 */
const lastCellText = ref('')

function pickDay(date) {
  mobileDayOpen.value = true
  loadDay(date)
}

/** 待批请假影响的日子：待批请假覆盖到的未来日子（每多一条请假，那天就可能少一个人）。
 *  后端没有「需求人数」这个字段，所以这里只报**已提申请**的缺口，口径写在面板提示里。 */
const gapDays = computed(() => {
  const counts = {}
  for (const request of inboxRequests.value) {
    if (request.kind !== 'leave') continue
    const days = eachDayInRange(request.start_date, request.end_date || request.start_date)
    for (const day of days) {
      if (today.value && day < today.value) continue
      counts[day] = (counts[day] || 0) + 1
    }
  }
  return Object.entries(counts)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .slice(0, 6)
    .map(([date, count]) => ({ date, count }))
})

/** 本月空档：当月**所有班次都 0 人**的日子（月历窗口之外的不算 —— 那叫「还没铺到」）。 */
const monthEmptyDays = computed(() => {
  const data = calendar.value
  if (!data || !Array.isArray(data.days)) return []
  const shiftList = shifts.value
  if (!shiftList.length) return []
  const windowEnd = data.window_end || '9999-12-31'
  return data.days.filter((day) => {
    if (!day || !day.business_date || day.business_date > windowEnd) return false
    return shiftList.every((shift) => !(day.counts && day.counts[String(shift.id)]))
  })
})

/** 名单卡上的规则摘要：当前这一周的 7 格（周一起），跟周表同宽。
 *  只用于展示：相位按服务端的口径（营业日与起点之差取模）现算。 */
function ruleWeekCells(employee) {
  const rule = employee && employee.rule
  if (!rule || rule.invalid || !Array.isArray(rule.cycle) || !rule.cycle.length) return []
  const anchor = toLocalDate(rule.anchor_date || today.value)
  const monday = toLocalDate(mondayOf(today.value))
  if (Number.isNaN(anchor.getTime()) || Number.isNaN(monday.getTime())) return []
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(monday)
    day.setDate(day.getDate() + index)
    const diff = Math.round((day - anchor) / 86400000)
    const length = rule.cycle.length
    const shiftId = rule.cycle[((diff % length) + length) % length]
    if (shiftId === null || shiftId === undefined) return '休'
    const shift = shiftById.value[shiftId]
    return shift ? shift.name.slice(0, 2) : '?'
  })
}
const pendingText = computed(() => {
  if (pendingCount.value === null) return '名单没读出来：点开重试一次'
  return pendingCount.value ? '点这里给谁配固定班' : '全员都配好了'
})
async function loadPendingCount() {
  try {
    const data = await api.get('/api/scheduling/roster')
    pendingCount.value = countPending(data.employees)
  } catch (err) {
    pendingCount.value = null
  }
}

// 月历上的**待批角标**（spec US 11）：哪天有等着批的请假 / 换班。
//
// 数据现成 —— `/inbox` 每条申请带 `start_date` / `end_date`，这里按天摊开数一遍就够了，
// 不用后端再出一个接口。这条验收在票 02 与票 06 之间被转手两次、一直没人接，结果是超级管理员
// 只能滑到底、点进待办页才知道有几条在等他批。
//
// 读不出来就**不标**（空表）：宁可少一个角标，也不能在一个没读到的日子上标错数字。
const pendingMarks = ref({})
// 待办那一份原样留一手：周表每格没有「待批」字段，票 14 的待批提醒只能从这儿按天 / 按人取
// （`pendingCells` 就是它摊平出来的）（多读一次接口不值当）。
const inboxRequests = ref([])
async function loadPendingMarks() {
  try {
    const data = await api.get('/api/scheduling/inbox')
    const marks = {}
    for (const request of data.requests || []) {
      // 换班只有一天（起止同值），请假是一段 —— 同一个展开函数覆盖两种。
      const days = eachDayInRange(
        request.start_date,
        request.end_date || request.start_date,
      )
      for (const day of days) marks[day] = (marks[day] || 0) + 1
    }
    pendingMarks.value = marks
    inboxRequests.value = data.requests || []
  } catch (err) {
    pendingMarks.value = {}
    inboxRequests.value = []
  }
}

/** 底下那根条与月历角标一起刷：批一条、撤回一条都会同时动到这两个。 */
async function refreshNotes() {
  await Promise.all([loadPendingCount(), loadPendingMarks()])
}

// ── 周表：员工 × 周（2026-09-30 加，按门店在用的那套排班页的形式）──────────────
// 月历回答「哪天缺人」，当天卡回答「这天是谁、在哪个区」，名单回答「谁的规则配错了」——
// 这一块回答第四句：**这个人这一周怎么上**。数据一直都在（`staff_assignments` 一行就是
// 「人 × 营业日 × 班次 + 区」），只是一次按「人 × 7 天」读出来（`GET /api/scheduling/week`）。
//
// 周起点是**周一**：2026-09-30 全模块从周日开头改过来（店里的口头习惯），跟月历的
// `lead`、员工端月历的 `MONTH_HEADS` 是同一套。服务端会把 `start` 归一到周一，
// 下面这两个函数只是为了让「上周 / 下周」点下去算的是同一周。
const week = ref(null)
const weekStart = ref('')
const weekError = ref('')

function toLocalDate(value) {
  const [year, month, day] = String(value || '').split('-').map(Number)
  return new Date(year, month - 1, day)
}

function isoDate(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/** 归一到那一周的周一（`getDay()` 周日=0，这里换成周一=0，跟服务端 `date.weekday()` 对齐）。 */
function mondayOf(value) {
  const base = toLocalDate(value || today.value || isoDate(new Date()))
  if (Number.isNaN(base.getTime())) return isoDate(new Date())
  base.setDate(base.getDate() - ((base.getDay() + 6) % 7))
  return isoDate(base)
}

function shiftWeek(start, delta) {
  const base = toLocalDate(start || mondayOf(today.value))
  base.setDate(base.getDate() + delta * 7)
  return isoDate(base)
}

function shortDate(value) {
  const [, month, day] = String(value || '').split('-').map(Number)
  return month && day ? `${month}/${day}` : ''
}

/** 回执里点名的日期写法（「10月14日」）：与周表格子里的日期同一套，不加年份也不写星期。 */
function monthDayText(value) {
  const [, month, day] = String(value || '').split('-').map(Number)
  return month && day ? `${month}月${day}日` : ''
}

// 标题写**日期区间**，不写「10 月第一周」：跨月周（9/28–10/4）那种写法天生有歧义。
const weekRangeLabel = computed(() => {
  const data = week.value
  if (!data) return ''
  const same = data.today ? data.start === mondayOf(data.today) : false
  return `${shortDate(data.start)}–${shortDate(data.end)}${same ? ' · 本周' : ''}`
})

async function loadWeek(start) {
  weekError.value = ''
  const previousStart = weekStart.value
  try {
    // `start` 空着就交给服务端按「今天所在周」算：首屏还没读到 today 时也点得动。
    const data = await api.get('/api/scheduling/week', { start: start || '' })
    week.value = data
    weekStart.value = data.start
    // 并集：周表这份带的是「这 7 天真有行的停用班次」，直接替换会把月历那边的班次列挤掉
    // （`mergeShiftList` 里写了为什么不能那么走）。
    if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
    // 翻到别的周就把选区收起来：它冻着的是上一周那几天，留在屏幕上会让人以为刷的是新这一周
    // 的同一天（票 07 那条「换一天就收编辑器」是同一个道理）。**同一周重读不收** ——
    // 应用完接着点下一列继续刷，正是这个交互的意义（ADR 0107 决策 3）。
    if (previousStart && data.start !== previousStart) clearPick(true)
  } catch (err) {
    weekError.value = err.message || '周表读不出来'
  }
  syncQuery()
}

const weekDays = computed(() => (week.value && week.value.days) || [])
const weekEmployees = computed(() => (week.value && week.value.employees) || [])

// 筛人（图片里那个「搜索成员」）：手机上网格一屏只放得下 4 行多，而名单有十几号人，
// 找人靠翻页太慢。只按姓名筛（前端过滤，服务端一次就给全量），筛掉几个要说出来。
const weekFind = ref('')
const weekRows = computed(() => {
  const keyword = weekFind.value.trim()
  if (!keyword) return weekEmployees.value
  return weekEmployees.value.filter((employee) => (employee.name || '').includes(keyword))
})
const weekFilteredOut = computed(() => weekEmployees.value.length - weekRows.value.length)

// ── 成员列的存假（2026-11-01 用户要的）────────────────────────────────────────
// 店长在周表上排人的时候要同时看「这个人这个月还攒着几天假」—— 不然排到第 5 天
// 才想起来去考勤表翻一眼。**数只有一个来源**：考勤表那张 `GET /api/attendance/sheet`
// 里的 `leave.balance`（有效值 = 手填期末 ?? 公式，扣过已批抵扣）。这里不重算：
// 存假那条算式（`_leave_terms` + `_with_manual_balance`）拆成两份就一定会漂，
// 员工手机上的「存假卡」与店长这张周表必须是同一个数。
//
// 为什么读得到：考勤表与排班三页**同一道门**（`_require_scheduler`），所以能打开
// 这一页的人就一定能读那张表，不需要新接口、也不需要放开权限。
// 为什么是当月：那条接口刻意没有月份参数（考勤表页只算当月），周表跟着这个口径；
// 跨月的周（10/26–11/1）两边的格子都在，但数字仍是服务器「今天」那个月 ——
// 存假本来就是月度概念，硬按周切反而会给出一个考勤表上没有的数。
const leaveBalance = ref({})   // employeeId → 天数（负数是欠班）
const leaveMonth = ref('')     // 这份数字属于哪个月（'YYYY-MM'），只进 title

async function loadLeaveBalances() {
  try {
    const data = await api.get('/api/attendance/sheet')
    const map = {}
    for (const person of data.employees || []) {
      const days = person.leave && person.leave.balance
      if (typeof days === 'number') map[person.id] = days
    }
    leaveBalance.value = map
    leaveMonth.value = data.month || ''
  } catch {
    // **静默失败**：存假是这一列的附加信息，读不到就是不显示那一行小字，
    // 不该把整张周表拖成报错（周表本身是这一页的主件）。
    leaveBalance.value = {}
    leaveMonth.value = ''
  }
}

/** 名字后面那个带圈的数字：**只写数**（`36.5` / `4` / `−0.5`），单位与「存假 / 欠班」两个字
 *  都不占版面 —— 含义交给颜色（绿=攒下的假、橙=欠班）与悬停提示（`leaveTitle`）。
 *  数一律借 `formatDays` 的写法再摘掉「天」：整数不带 `.0`、小数留一位、负数是全角减号，
 *  这些规则只该有一份（考勤表卡片与这里写同一个字面）。 */
function leaveBadge(days) {
  const value = Number(days)
  if (!Number.isFinite(value)) return ''
  return formatDays(value).replace(' 天', '')
}

/** 徽标只有数字，说清楚它是什么靠这一句（悬停可见）。 */
function leaveTitle(days) {
  const value = Number(days)
  if (!Number.isFinite(value)) return ''
  const said = value < 0 ? `欠班 ${formatDays(Math.abs(value))}` : `存假 ${formatDays(value)}`
  return `${leaveMonth.value} 月${said}（与考勤表同一个数）`
}

function cellOf(employee, day) {
  return (employee.cells && employee.cells[day.business_date]) || null
}

/** 这一格没有班可显示时，**为什么**：写清楚，别让空格子读成「那天全员休」。
 *  `day` 可能是 null（抽屉开着的时候切了周，那一天已经不在当周里）—— 那种情况也得回答。 */
function blankReason(day, cell) {
  if (!day) return '这一格不在当周了'
  if (!day.in_window) return `还没铺到（只铺到 ${week.value ? week.value.window_end : ''}）`
  if (!day.row_count) return '那天还没有排班数据'
  if (!cell || !cell.scheduled) return '还没排到'
  return ''
}

// 格子里的班次缩写：**取能把它与别的班次分开的最短前缀**（最多 3 个字）。
// 原先是死取第一个字，可「主管A两头班」与「主管B两头班」第一个字都是「主」——
// 有这两个班的店里，两行主管班在格子上就长得一模一样。班次是数据（能改名、能加到第 6 个），
// 缩写也只能算出来，不能写死。全名在格子的 aria-label 与下面的图例里。
const weekShortNames = computed(() => {
  const list = shifts.value
    .filter((shift) => shift.is_active)
    .map((shift) => ({ id: shift.id, name: String(shift.name || '') }))
  const short = {}
  for (const item of list) {
    const limit = Math.max(1, Math.min(3, item.name.length))
    for (let size = 1; size <= limit; size += 1) {
      const prefix = item.name.slice(0, size)
      const clash = list.some((other) => other.id !== item.id && other.name.slice(0, size) === prefix)
      if (!clash || size === limit) {
        short[item.id] = prefix || '班'
        break
      }
    }
  }
  return short
})

function weekCellText(employee, day) {
  const cell = cellOf(employee, day)
  if (!cell || !cell.scheduled) return ''
  if (cell.shift_id === null || cell.shift_id === undefined) return cell.leave ? '假' : '休'
  return weekShortNames.value[cell.shift_id] || String(cell.shift_name || '班').slice(0, 1)
}

/** 格子里第二行那个工作区名（2026-10-08 用户要的）：这个人这一天在哪个区。
 *
 *  只有「这天真有班次」才谈得上区 —— 休 / 请假 / 还没排到的格子没有区可言，与
 *  `gW-zone`、`weekCellLabel` 是同一套判据。**没配区的格子不写「未配」**：
 *  七列平分下来一格只有 ~39px 宽，那一行留给真有区的人；空着不代表没信息可查 ——
 *  点开抽屉的下拉能当场改区，`aria-label` 里也照旧说着「未配工作区」。 */
function weekCellZone(employee, day) {
  const cell = cellOf(employee, day)
  if (!cell || !cell.scheduled) return ''
  if (cell.shift_id === null || cell.shift_id === undefined) return ''
  return String(cell.zone_name || '')
}

/** 这一格是不是「休半天」（票 07 / ADR 0114）：那天仍然挂着班次，只是只上半天 ——
 *  格子里在班次缩写后面加一个「半」。2026-10-29 起周表自己也能标它（笔栏那颗「休半天」），
 *  考勤表页那条 PUT 仍是另一条路；两边写的是同一个 `staff_assignments.half_rest`。 */
function weekCellHalf(employee, day) {
  const cell = cellOf(employee, day)
  return Boolean(cell && cell.half_rest)
}

/** 这一格有没有班次（休 / 请假 / 空白都没有）：标「休半天」得有班次可挂，与 `weekCellZone`
 *  是同一套判据。点「休半天」笔落到这种格子上时本地就拦下来、不发请求。 */
function cellHasShift(employee, day) {
  const cell = cellOf(employee, day)
  return Boolean(cell && cell.scheduled && cell.shift_id !== null && cell.shift_id !== undefined)
}

function weekCellClass(employee, day) {
  const cell = cellOf(employee, day)
  const blank = !cell || !cell.scheduled
  const off = !blank && (cell.shift_id === null || cell.shift_id === undefined)
  return {
    // 两种「空」分开：整列没有数据 / 还没铺到是 `mute`（虚线），
    // 这天有数据但这个人没排到是 `none`（淡框）。休与假另算。
    mute: blank && (!day.in_window || !day.row_count),
    none: blank && day.in_window && !!day.row_count,
    off: off && !cell.leave,
    leave: off && !!cell.leave,
    [blank ? 'tone-blank' : toneClass(cell.shift_id)]: !off,
    over: !!cell && cell.overridden,
    // 三个字的缩写（「主管A」这种）在 40px 的格子里会折成两行，缩一号字就一行放得下。
    long: weekCellText(employee, day).length > 2,
    today: !!day.is_today,
    // 选中高亮：复用既有的选中外环（`.gW-cell.on`），不新增任何颜色引用。
    on: pickKeys.value.has(`${employee.id}|${day.business_date}`),
  }
}

function weekCellLabel(employee, day) {
  const cell = cellOf(employee, day)
  const when = `${day.month}月${day.day}日 周${WEEK_HEADS[day.weekday]}`
  // 格子是**落笔点**（票 14 修订：点一下就用笔栏上那支笔改写这一格）：名字里那半句是给
  // 读屏的（角色是 button，光有内容读不出点下去会发生什么）。
  const pick = '，点一下就用笔栏上的班次、休、休半天或清改写这一格'
  // 长按是**只读**入口（票 03 / ADR 0127）：读屏用户从名字里知道它存在，也知道它不改东西。
  const look = '，长按看那一天要顶的档'
  if (!cell || !cell.scheduled) {
    return `${employee.name} ${when}：${blankReason(day, cell) || '还没排到'}${pick}${look}`
  }
  const what = cell.shift_id === null || cell.shift_id === undefined
    ? (cell.leave ? '请假' : '休')
    : `${cell.shift_name}${cell.half_rest ? '（休半天）' : ''}` +
      `${cell.zone_name ? `，${cell.zone_name}` : '，未配工作区'}`
  return `${employee.name} ${when}：${what}${cell.overridden ? '，已手动调整' : ''}${pick}${look}`
}

// 这一周有几格是「休半天」（票 07）：>0 时图例里多一句，免得格子里那个「半」没人解释。
const weekHalfCount = computed(() => {
  let count = 0
  for (const employee of weekEmployees.value) {
    for (const day of weekDays.value) {
      if (weekCellHalf(employee, day)) count += 1
    }
  }
  return count
})

const weekOverriddenCount = computed(() => {
  let count = 0
  for (const employee of weekEmployees.value) {
    for (const day of weekDays.value) {
      const cell = cellOf(employee, day)
      if (cell && cell.overridden) count += 1
    }
  }
  return count
})

const weekBeyondDays = computed(() => weekDays.value.filter((day) => !day.in_window))
const weekEmptyDays = computed(() => weekDays.value.filter((day) => day.in_window && !day.row_count))
const weekNoDataNote = computed(() => {
  if (!weekEmptyDays.value.length) return ''
  const names = weekEmptyDays.value.map((day) => shortDate(day.business_date)).join('、')
  return `${names} 这一列一条排班数据都没有 —— 那是「还没铺到」，不是「那天全员休」。`
})
const weekExcludedNote = computed(() => {
  const data = week.value
  if (!data || !data.excluded) return ''
  const parts = []
  if (data.excluded.resigned) parts.push(`${data.excluded.resigned} 人已离职`)
  if (data.excluded.pending) parts.push(`${data.excluded.pending} 人还没批准`)
  return parts.length ? `另有 ${parts.join('、')}，不在表里（去「名单」看）。` : ''
})

// ── 笔栏与选区（票 14 修订，2026-10-13 用户真机裁定）────────────────────────
// 「还是之前点击方格就改写班次那种比较好」—— 于是回到**一支笔**：
//
//   · 底部一条**常驻**的笔栏（`刷` 标签 + 班次按钮组 + 工作区 + ↺ 撤销），折叠态 ~70px；
//   · **点一格 = 立刻用当前笔写这一格**，没有「先选中、再点应用」那一步；
//   · 选区还在，但只为批量存在：点日期列头 = 整列、点人名 = 整行，这时笔栏多出一行摘要与
//     一个「刷」按钮；不选区时这一行完全不出现（不用批量的人不该看见它）；
//   · 提示一律走**一行** `aria-live`，几秒后自己消失、下一次操作替换它 —— 不再同时挂五六行
//     把表格压掉半屏（跳过与待批的信息收进「看全部」，**不丢**）。
//
// 笔上的班次 / 工作区是**样式**：选一次就一直用（这就是用户要的「一支笔」）。工作区按钮点开
// 才展开一行横滑的区名，默认收起 —— 少一行就少压掉一行员工。班次用按钮组而不是原生
// `<select>`：那个弹层由系统画，在这个贴底的位置会飘到页面中间（2026-10-13 用户截图），
// 而那层我们一个像素都改不了。
//
// 不变量（这一票一个字都不动）：行序照周表给（前端不重排）、撤销只留最近一批且是内存级、
// 过去 30 天内补记要先弹一次确认（ADR 0108 决策 3）、`skipped` / `reminders` 照旧点名。
const penShiftId = ref(null) // 笔上的班次；null = 还没选过，用第一个在用的班次
const penRest = ref(false) // 笔是「休」：写下去的是 action=rest
const penHalfRest = ref(false) // 笔是「休半天」：写下去的是 action=half_rest（该格有班次才写）
const penClear = ref(false) // 笔是「清」：写下去的是 action=void（把这一格清成空白，不再是「退回规则」）
const penZoneId = ref('') // '' = 跟这个班次的固定区
const penZoneOpen = ref(false) // 工作区按钮点开才多展开一行
// 悬浮球（2026-10-29 用户要求）：底部那条常驻笔栏收成右下角一颗球，点球才展开。
// 收起时页面不再被 76px 的笔栏占掉；球面写着当前那支笔，省得展开才知道。
const penOpen = ref(false)
const pick = ref(null) // { anchor, target } | null；手柄只有日期列头与人名两种
const pickBusy = ref(false)
// 撤销只留**最近一批**、内存级（ADR 0107 决策 7）：刷新或换设备就没了。
const pickUndo = ref(null) // { plan: [...] } | null
const pastConfirm = ref(null) // { action, options, cells, dates, voiding }：过去的二次确认（只弹一次）
/** 区域「退回规则」的二次确认（4 秒不点自动复位）。 */
const regionRestoreArmed = ref(false)
let regionRestoreTimer = 0
function askRegionRestore() {
  if (regionRestoreArmed.value) {
    regionRestoreArmed.value = false
    clearTimeout(regionRestoreTimer)
    void applyRegionRestore()
    return
  }
  regionRestoreArmed.value = true
  clearTimeout(regionRestoreTimer)
  regionRestoreTimer = setTimeout(() => { regionRestoreArmed.value = false }, 4000)
}

/** 笔上此刻的班次：没选过（或选中的那个停用了）就是**第一个还在用的班次** —— 与旧口径一致。 */
const penShift = computed(
  () => activeShifts.value.find((shift) => shift.id === penShiftId.value) || activeShifts.value[0] || null
)
// 工作区按钮上写的就是**当前值**（「跟固定区」/「案板」）：点它才展开那一行区名。
const penZoneName = computed(() => {
  if (penZoneId.value === '') return '跟固定区'
  const zone = zones.value.find((item) => String(item.id) === penZoneId.value)
  return zone ? zone.name : '跟固定区'
})

/** 球面文字最多三个字（与格子里的班次缩写同一套口径）。球本身还受 `.gPick-fabText`
 *  的 `max-width: 52px` + ellipsis 兜底，所以「早班·明档2」真放不下时会被截成省略号 ——
 *  能看见「班次·区」的前半截，比只写班次名强。 */
function shortPenText(value) {
  const text = String(value || '')
  return text.length > 3 ? text.slice(0, 3) : text
}

/** 悬浮球上写什么：当前那支笔。班次写名字（最多三个字），休 / 休半天 / 清各写一个字 ——
 *  收起状态下也要一眼看出下一笔会写什么。
 *
 *  装过区（`penZoneId !== ''`）时把区名接在班次后面：这一笔写下去带着区，而「哪个区」
 *  正是这个功能的意义（真机截图：点一行装笔后球面只剩「早班」，装载的区看不见）。
 *  「跟固定区」（`penZoneId === ''`）不接 —— 它不需要挤进球面，写班次名就够；区查不到
 *  （名单还没读到 / 区刚被删）也退回只写班次，拼一句「跟固定区」是假话。 */
const penLabel = computed(() => {
  if (penClear.value) return '清'
  if (penHalfRest.value) return '休半天'
  if (penRest.value) return '休'
  const name = shortPenText(penShift.value ? penShift.value.name : '班') || '班'
  const zone = penZoneId.value === ''
    ? null
    : zones.value.find((item) => String(item.id) === penZoneId.value)
  return zone ? `${name}·${shortPenText(zone.name)}` : name
})

/** 球上那个小圆点的记号：班次 / 休 / 休半天 / 清。**只分这四档、不按班次配色**
 *  （`designGuard` 的棘轮只减不增：球上再挂六个 `--hy-*` 就是给这个文件加 6 条存量债务，
 *  而球面上的**文字**已经写着是哪个班次，颜色不必重复那个信息）。返回空串 = 还没拿到班次。 */
const penDotClass = computed(() => {
  if (penClear.value) return 'clear'
  if (penHalfRest.value) return 'half'
  if (penRest.value) return 'rest'
  return penShift.value ? 'shift' : ''
})

/** 选一支班次笔 = **一笔从头配**：工作区跟着回到「跟固定区」。
 *
 *  每个班次有自己的固定区（`scheduling_zone_defaults`），早班配的是案板、夜班配的是灶台 ——
 *  换班次却留着上一个班次点名过的区，刷下去就是「夜班挂在案板」，而店长根本没为夜班选过区
 *  （2026-10-29 用户：「班次按钮，选择了班次后还要选工作区」——要选区就选完班次再点工作区，
 *  不该继承上一次的）。「跟固定区」按钮本身仍是按每个人的规则刷（`zoneId: ''` → 服务端
 *  回填这个人这个班次的固定区）。 */
function pickPenShift(shift) {
  penShiftId.value = shift.id
  penRest.value = false
  penHalfRest.value = false
  penClear.value = false
  penZoneId.value = ''
}

/** 休 / 休半天 / 清：这几笔不带班次，也就没有「这个人这个班次的固定区」可言 ——
 *  同样把工作区拨回「跟固定区」，免得笔上挂着一个上一支班次留下的区名。 */
function pickPenRest() {
  penRest.value = true
  penHalfRest.value = false
  penClear.value = false
  penZoneId.value = ''
}

/** 「休半天」也是一支笔：点一格 = 把那一格标成半天休；**再点同一格 = 取消**（见 `penOptions`）。 */
function pickPenHalfRest() {
  penHalfRest.value = true
  penRest.value = false
  penClear.value = false
  penZoneId.value = ''
}

/** 「清」也是一支笔：点一格 = 把这一格**清空**（写 action=void，空白 = 还没排到）；
 *  不是「退回规则」（那是选区摘要行那颗，发 action=restore）。 */
function pickPenClear() {
  penClear.value = true
  penRest.value = false
  penHalfRest.value = false
  penZoneId.value = ''
}

function penAction() {
  if (penClear.value) return PICK_VOID
  if (penHalfRest.value) return PICK_HALF_REST
  return penRest.value ? PICK_REST : PICK_SHIFT
}

/** 笔上的选项。
 *
 *  「休半天」按**这一格的现值取反**（点一下标、再点取消）：所以它必须知道点的是哪一格；
 *  选区那一行整片刷时没有「这一格」，统一标上（`true`），没有班次的格子由服务端跳过。
 */
function penOptions(employee, day) {
  const base = { shiftId: penShift.value ? penShift.value.id : null, zoneId: penZoneId.value }
  if (!penHalfRest.value) return base
  const cell = employee && day ? cellOf(employee, day) : null
  return { ...base, halfRest: cell ? !cell.half_rest : true }
}

// 表里看得见的那几行 / 那几列就是选区的轴：筛了人之后选区跟着缩，不会偷偷刷到看不见的人。
const pickAxes = computed(() => ({
  rows: weekRows.value.map((employee) => employee.id),
  cols: weekDays.value.map((day) => day.business_date),
}))
const pickView = computed(() => pickRegion(pick.value, pickAxes.value))
// 一格都选不到（换了一周、筛掉了人）就是**没有选区**：摘要行与「刷」都不出现。
const pickActive = computed(() => (pickView.value && pickView.value.count ? pickView.value : null))
// 高亮按集合查（19 人 × 7 天 = 133 格，逐格 `includes` 太笨）。
const pickKeys = computed(() => {
  const keys = new Set()
  const view = pickView.value
  if (view) for (const cell of view.cells) keys.add(`${cell.employeeId}|${cell.date}`)
  return keys
})
// 表头 / 人名列的高亮：只有**整列 / 整行被完整覆盖**时才亮（`.gW-th.on`，与格子同一条外环
// 规则）。用「覆盖到就亮」的话，选一整列会让 19 个人名一起描框 —— 比选区本身还抢眼。
const pickedCols = computed(() => (pickView.value ? pickView.value.fullCols : []))
const pickedRows = computed(() => (pickView.value ? pickView.value.fullRows : []))

// 摘要（选区那一行）：选了什么 + 影响多少格 —— 防误刷就靠这一行（「周三 · 全店 19 人 · 19 格」）。
const pickTitle = computed(() => {
  const view = pickActive.value
  if (!view) return ''
  if (view.kind === 'cols') {
    return view.cols.length === 1
      ? formatDayLabel(view.cols[0])
      : `${shortDate(view.cols[0])}–${shortDate(view.cols[view.cols.length - 1])}`
  }
  if (view.kind === 'rows') {
    return view.rows.length === 1
      ? employeeName(view.rows[0])
      : `${employeeName(view.rows[0])} 等 ${view.rows.length} 人`
  }
  // 混合手柄（列头 + 人名）会落到这里：那是交叉出来的一个格子。
  return `${employeeName(view.rows[0])} · ${formatDayLabel(view.cols[0])}`
})

const pickCountText = computed(() => {
  const view = pickActive.value
  if (!view) return ''
  // 「全店」只在真选了全店时说：筛了人之后那句是假话，而这一行正是用来防误刷的。
  if (view.kind === 'cols' && view.rows.length === weekEmployees.value.length) {
    return `全店 ${view.rows.length} 人 · ${view.count} 格`
  }
  if (view.kind === 'rows' && view.cols.length === weekDays.value.length) {
    return `这一周 · ${view.count} 格`
  }
  return `${view.rows.length} 人 × ${view.cols.length} 天 · ${view.count} 格`
})

function employeeAt(employeeId) {
  return weekEmployees.value.find((item) => item.id === employeeId) || null
}

function employeeName(employeeId) {
  const employee = employeeAt(employeeId)
  return employee ? employee.name : `#${employeeId}`
}

function dayAt(date) {
  return weekDays.value.find((day) => day.business_date === date) || null
}

/** 那一格的当前值：原值快照（撤销）、跳过预判（已批的假）都要它。
 *  `cellOf` 收的是**当天那一列**（用 `day.business_date` 取格），不是日期字符串。 */
function weekCellAt(employeeId, date) {
  const employee = employeeAt(employeeId)
  const day = dayAt(date)
  return employee && day ? cellOf(employee, day) : null
}

function onColPick(day) {
  if (!day.in_window) return
  nextPick(colHandle(day.business_date))
  // 选了整列 / 整行：笔栏要展开，不然「刷」「退回规则」藏在一颗收起的球后面，看不见。
  penOpen.value = true
}

function onRowPick(employee) {
  nextPick(rowHandle(employee.id))
  penOpen.value = true
}

/** 点一个手柄：已有的选区还在就以它锚点展开，否则从这一点重新开始。
 *
 *  「已经不在了」= 当前选区一格都选不到（锚点那行被筛掉了、或者换了一批人）——那种时候
 *  接着用旧锚点展开只会得到又一片空，点几次都像没反应，所以直接从这一点重开。 */
function nextPick(handle) {
  const current = pickActive.value ? pick.value : null
  pick.value = pickAt(current, handle)
}

/** 取消选中：点回锚点、点表格外的空白、切走这一栏，都走这里。
 *  `dropUndo` 只在**换了周**时给：那一批属于上一周，留着撤的是一个看不见的选区。 */
function clearPick(dropUndo = false) {
  pick.value = null
  pastConfirm.value = null
  if (dropUndo) pickUndo.value = null
}

// 筛一次人 = 重新选一遍：选区是按**看得见的行**算的（见 `pickAxes`），筛完还留着，
// 店长会以为刷的还是筛之前那一片。
watch(weekFind, () => {
  if (pick.value) clearPick()
})

/** 点表格外的空白 = 取消（ADR 0107 决策 3）：控件上的点击各有各的意思，不算「空白」。
 *  2026-10-29 起顺带收起悬浮球展开的笔栏 —— 点空白本来就是要「退出这一下」。 */
function onPageClick(event) {
  // 长按那一下的尾音 click 会落到页根上（弹层盖住了手指，target 是 pointerdown / pointerup
  // 两者的共同祖先）：不在这里也吞掉，「看一眼」就会顺手收掉笔栏、清掉正在用的选区。
  if (swallowLongPressTail()) return
  const target = event.target
  const skip = target && typeof target.closest === 'function'
    && target.closest('.gW, .gPick, .gD, button, input, select, label, a')
  if (skip) return
  if (pick.value) clearPick()
  if (penOpen.value) penOpen.value = false
}

// ── 提示：一行，几秒后自己消失（票 14 修订口径 4）─────────────────────────────
// 原来同时挂 4~6 行（预判、回执、待批提醒、撤销说明、错误）把表格压掉半屏。现在只有一个槽：
// 最新的那条说了算，几秒后自己消失。逐格点名（谁被跳过、谁有待批申请）收进 `details`，
// 由「看全部」展开 —— 跳过与待批的信息**不能丢**，但也不必常驻。
const PICK_NOTICE_MS = 6000
const pickNotice = ref(null) // { text, details: [], warn } | null
const noticeOpen = ref(false)
let noticeTimer = null

function sayNotice(text, details = [], warn = false) {
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = null
  noticeOpen.value = false
  pickNotice.value = text || details.length ? { text, details, warn } : null
  if (!pickNotice.value) return
  noticeTimer = setTimeout(() => {
    pickNotice.value = null
    noticeTimer = null
  }, PICK_NOTICE_MS)
}

onUnmounted(() => {
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = null
  // 长按的两个计时器（300ms 按压态 / 550ms 触发）也得收掉：页面切走时还挂着一个，
  // 回来就会在没按任何格子的情况下弹出上一页那一格的清单。
  cancelPress()
})

function skipText(items) {
  return items.map((item) => {
    const why = PICK_SKIP_TEXT[item.reason] || item.reason
    return `${employeeName(item.employeeId)} ${shortDate(item.date)} ${why}`
  }).join('、')
}

function skipContext(action) {
  return {
    action: action || PICK_SHIFT,
    today: week.value ? week.value.today : '',
    windowEnd: week.value ? week.value.window_end : '',
    cellOf: weekCellAt,
  }
}

// 待批提醒的数据来源：周表每格没有「待批」这个字段，只有既有的待办那一份（按天粒度）。
const pendingCells = computed(() => {
  const map = {}
  for (const request of inboxRequests.value) {
    const days = eachDayInRange(request.start_date, request.end_date || request.start_date)
    for (const day of days) map[`${request.employee_id}|${day}`] = request.kind
  }
  return map
})

function reminderText(items) {
  const who = items.map((item) => `${employeeName(item.employeeId)} ${shortDate(item.date)}`).join('、')
  return `待批提醒：${who} 那天有申请等着批，批了会盖掉你刚刷的班。`
}

/** 一次落笔之后的回执（**一行**）：写了几格、跳过几格、能不能撤。
 *  逐格点名的那几行收进 `details`（「看全部」展开）—— 跳过与待批的信息不丢。 */
function sayResult(result, cells, action, options, who = '') {
  const undoable = !!pickUndo.value && pickUndo.value.plan.length > 0
  const parts = []
  // 回执说清**这一笔是什么**：原来一律「已刷 N 格」，选错笔（清当成了休）也看不出来。
  // 「休半天」按这一笔实际写的值说话（单格就是那一格的新值，整片刷统一标上）。
  const verb = action === PICK_VOID
    ? '已清空'
    : action === PICK_RESTORE
      ? '已退回规则'
      : action === PICK_HALF_REST
        ? (options && options.halfRest ? '已标休半天' : '已取消休半天')
        : action === PICK_REST ? '已排休' : '已改班'
  if (result.applied) parts.push(`${verb} ${result.applied} 格${who ? ` · 写给 ${who}` : ''}`)
  if (result.skipped.length) parts.push(`跳过 ${result.skipped.length} 格`)
  if (!parts.length) parts.push('没有格子被改动')
  // 「撤销只留最近一批、只在这台手机上」这句说明跟着回执走（ADR 0107 决策 7）。
  const text = `${parts.join(' · ')}${undoable ? ' · 可撤销（只留最近一批、也只在这台手机上）' : ''}`
  const details = []
  if (result.skipped.length) details.push(`跳过：${skipText(result.skipped)}`)
  // 待批提醒：先信服务端回执里那一份（它知道谁那天真有申请），它没给就退回既有的待办那一份。
  let reminders = result.reminders
  if (!reminders.length) {
    const missed = new Set(result.skipped.map((item) => `${item.employeeId}|${item.date}`))
    reminders = pickPending(cells, pendingCells.value)
      .filter((item) => !missed.has(`${item.employeeId}|${item.date}`))
  }
  if (reminders.length) details.push(reminderText(reminders))
  sayNotice(text, details)
}

/** 连点不丢格：上一笔还在写时，点到的格子进队列，等这一笔落库后再按点击顺序写。
 *  同一格重复点只留最后一次（用户要的是最后那个班次）；上限 20，超了只提示、不排队。
 *  **不做乐观更新**：格子的样子仍以服务端回执 + 重读为准。 */
const PENDING_PAINT_LIMIT = 20
const pendingPaints = ref([]) // [{ key, employee, day, action, options }]
let drainingPaints = false

function queuePaint(employee, day, action, options) {
  const key = `${employee.id}|${day.business_date}`
  const rest = pendingPaints.value.filter((item) => item.key !== key)
  if (rest.length >= PENDING_PAINT_LIMIT) {
    sayNotice('点太快了，等这一笔写完', [], true)
    return
  }
  rest.push({ key, employee, day, action, options })
  pendingPaints.value = rest
}

/** 按序把队列里的格子写完：每写完一格，`runPaint` 会再回来 drain 一次。 */
async function drainPendingPaints() {
  if (drainingPaints || pickBusy.value) return
  drainingPaints = true
  try {
    while (pendingPaints.value.length && !pickBusy.value && !pastConfirm.value) {
      const next = pendingPaints.value[0]
      pendingPaints.value = pendingPaints.value.slice(1)
      await executePaint(next.employee, next.day, next.action, next.options)
      // 队列里的格子也可能是过去的日子：确认框先处理，余下的等下一笔完成再继续。
      if (pastConfirm.value) break
    }
  } finally {
    drainingPaints = false
  }
}

/** 真写一格（paintCell 与队列共用）：跳过预判 → requestPaint。 */
async function executePaint(employee, day, action, options) {
  const cells = [{ employeeId: employee.id, date: day.business_date }]
  const [skip] = predictSkips(cells, skipContext(action))
  if (skip) {
    sayNotice(`跳过：${skipText([skip])}`)
    return
  }
  await requestPaint(action, options, cells)
}

/** 点一格 = 立刻用当前笔写这一格（票 14 修订口径 2）：不选区、不点「应用」，一步。
 *
 *  会被跳过的那种格子**不发请求**：超 30 天、已批的假（词表与回执同一套 `PICK_SKIP_TEXT`）。
 *  「休半天」还有一条本地闸：休 / 空白那格没有班次可挂，直接说一句、不发请求。
 *  整片刷的时候跳过由服务端回执点名（它才是权威），这里只是省掉一次注定被跳过的往返。
 *  **上一笔还在写时不再静默丢弃**：进队列，写完这一笔按顺序补（见上面 `queuePaint`）。 */
async function paintCell(employee, day) {
  lastCellText.value =
    `周${WEEK_HEADS[day.weekday]} · ${employee.name} ${weekCellText(employee, day)}` +
    `${weekCellHalf(employee, day) ? '半' : ''}`
  const action = penAction()
  if (action === PICK_HALF_REST && !cellHasShift(employee, day)) {
    sayNotice(`跳过：${monthDayText(day.business_date)} 那天没有班次，标不上休半天`)
    return
  }
  const options = penOptions(employee, day)
  if (pickBusy.value) {
    queuePaint(employee, day, action, options)
    return
  }
  await executePaint(employee, day, action, options)
}

// ── 长按一格 = 只看「那一天要顶的档」（票 03 / ADR 0127）─────────────────────
// 格子的**写入口仍然只有一条**：点一下落笔。长按是只读入口 —— 按住 550ms 弹一张底部弹层，
// 列出那一天休 / 请假的人本来站哪个档、那个档现在还有谁；点一行把那个档的班次 + 工作区
// 一次装进笔，落笔仍要店长自己点下去。ADR 0107 决策 8 退役的是「长按展开看 / 改这一格」
// 那套连着写路径的旧语义，不是长按本身（ADR 0127 决策 1）。
const LONG_PRESS_MS = 550
const PRESS_FEEDBACK_MS = 300
const MOVE_CANCEL_PX = 8
// 长按之后**紧跟着**的那一次 click 必须吞掉：格子的 @click 是落笔，不吞就是「长按看一眼，
// 顺手把这一格改成笔上的班次」——一次查看变成事故。标记带过期时间：万一浏览器没把 click
// 发到格子上（弹层已经盖住手指），它也不会留到下一次普通点按。
const LONG_PRESS_SWALLOW_MS = 1000

const coverSheet = ref(null) // { date } | null：弹层开着时记的是**被长按那一格**的日子
const pressKey = ref('') // 按压态挂在哪一格上（pointerdown 起 300ms 才亮，见 PRESS_FEEDBACK_MS）
let pressTimer = 0
let longPressTimer = 0
let pressOrigin = null
let longPressFiredAt = 0

function cellPressKey(employee, day) {
  return `${employee.id}|${day.business_date}`
}

/** 撤掉按压态与两个计时器：取消的每一条路（松手 / 系统收走手势 / 移出格子 / 位移超 8px）都走它。 */
function cancelPress() {
  if (pressTimer) clearTimeout(pressTimer)
  if (longPressTimer) clearTimeout(longPressTimer)
  pressTimer = 0
  longPressTimer = 0
  pressOrigin = null
  pressKey.value = ''
}

/** 按住一格。`disabled` 的格子（还没铺到的那几天）本来就不冒 pointer 事件 —— 判据再写死一道，
 *  免得哪天模板上的 `:disabled` 被挪走，长按就悄悄在「注定没有数据」的格子上生效。 */
function onCellDown(event, employee, day) {
  if (!day.in_window) return
  // 新手势开始：上一手遗留的「尾音」标记作废（它只属于上一次那支手势）。
  longPressFiredAt = 0
  cancelPress()
  pressOrigin = { x: event.clientX, y: event.clientY }
  // 300ms 才亮按压态：手指按下去要**先有回应**，否则长按会变成「一直按、怀疑没按到」。
  pressTimer = setTimeout(() => { pressKey.value = cellPressKey(employee, day) }, PRESS_FEEDBACK_MS)
  longPressTimer = setTimeout(() => {
    longPressFiredAt = Date.now()
    cancelPress()
    openCoverSheet(day, employee)
  }, LONG_PRESS_MS)
}

/** 手指一动就是滚动：8px 之外整条手势作废（ADR 0127 决策 1）—— 扫表时不会老弹窗。 */
function onCellMove(event) {
  if (!pressOrigin) return
  const dx = event.clientX - pressOrigin.x
  const dy = event.clientY - pressOrigin.y
  if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) cancelPress()
}

function onCellUp() {
  cancelPress()
}

/** 弹层开在**被长按的那一格**的日子上（ADR 0127 决策 3）：补记上周漏排的人时，「看到的」
 *  与「列出来的」是同一天。开弹层不发请求、不写任何东西。 */
function openCoverSheet(day, employee) {
  // 2026-11-01 用户要求「直接选择行就可以改班次，不用再装进笔」：长按谁 = 让谁顶。
  // 所以弹层记下被按的那一格是谁，点一行就直接把这个档的班次 + 工作区写给他。
  coverSheet.value = {
    date: day.business_date,
    employeeId: employee.id,
    employeeName: employee.name,
  }
}

/** 被长按的那个人在这一页的最新一条（弹层开着时换了周/被筛掉就找不到 → 视为不能写）。 */
const coverTarget = computed(() => {
  const sheet = coverSheet.value
  if (!sheet) return null
  return weekEmployees.value.find((item) => item.id === sheet.employeeId) || null
})

function closeCoverSheet() {
  coverSheet.value = null
}

/** 遮罩与弹层里的行也要过「尾音」这一道 —— **手机上不吞就是弹层一出来就没了**。
 *
 *  桌面用鼠标时，长按那一下的 click 落在**格子**上（pointerdown / pointerup 同元素），
 *  由 `onCellClick` 吞掉。手机上抬手时弹层已经把手指那一点盖住了，浏览器合成的 click
 *  会落到**最上层**：要么是遮罩（`closeCoverSheet` 立刻把弹层关掉，看着像「长按没反应」），
 *  要么是某一行（一松手就顺手装了笔，比不反应更糟）。所以这两处入口都要先问一次
 *  `swallowLongPressTail()`；真机复现与修法见 2026-11-01 的 `touch_probe.py`。 */
function onCoverScrimClick() {
  if (swallowLongPressTail()) return
  closeCoverSheet()
}

function onCoverRowClick(row) {
  if (swallowLongPressTail()) return
  void assignCoverSlot(row)
}

/** 把一个档直接写给**被长按的那个人**（2026-11-01 用户裁定：省掉「装进笔 → 再点格子」那一步）。
 *
 *  走的是同一条批量写路径（`requestPaint`）：过去 30 天的二次确认、已批请假的跳过与点名、
 *  写完只重拉一次周表、`↺` 撤销 —— 这些一个字都不重写。弹层先关，再落笔：确认框弹在
 *  弹层上面会让人以为「又要多点一下」。
 *
 *  长按仍然是**只读**：写只发生在点行这一下（尾音那一下已由 `swallowLongPressTail` 挡掉）。
 */
async function assignCoverSlot(row) {
  const sheet = coverSheet.value
  const target = coverTarget.value
  if (!row.can_pen || !sheet || !target) {
    if (sheet && !target) sayNotice('这一格对应的那个人已经不在当前这一周里了：刷新后再试', [], true)
    return
  }
  const who = target.name
  closeCoverSheet()
  await requestPaint(
    PICK_SHIFT,
    { shiftId: row.shift_id, zoneId: String(row.zone_id) },
    [{ employeeId: target.id, date: sheet.date }],
    who,
  )
}

/** 新手势一开始就作废「尾音」标记：万一某个浏览器**没**把尾音 click 发出来（标记就一直挂着），
 *  下一次真点按会先过 `pointerdown`，这样它不会被误吞。 */
function clearLongPressTail() {
  longPressFiredAt = 0
}

/** 长按那一下的**尾音 click** 该不该吞掉：吞返回 true，顺手把标记清掉。
 *
 *  两层保险：`Date.now()` 过期自动失效（浏览器万一没把 click 发到格子上，标记也不会留到
 *  下一次普通点按），`onCellDown` 在新手势开始时也会清一次。 */
function swallowLongPressTail() {
  if (!longPressFiredAt) return false
  const tail = Date.now() - longPressFiredAt <= LONG_PRESS_SWALLOW_MS
  longPressFiredAt = 0
  return tail
}

/** 点一格 = 落笔，除非这一下是长按那支手势的尾音（见 `LONG_PRESS_SWALLOW_MS`）。 */
function onCellClick(employee, day) {
  if (swallowLongPressTail()) return
  void paintCell(employee, day)
}

/** 那一天要顶的档：日期 = 被长按那一格的日子，清单由票 02 的 `coverSlotsFor` 算。 */
const coverDate = computed(() => (coverSheet.value ? coverSheet.value.date : ''))

/** 「要顶的档」用的工作区名单：`/week` 带下来的那份（票 01 起带 `day_shift` / `night_shift`）。
 *
 *  **不拿名单面板那份（`/roster`）兜底**：那份只有 `id` / `name`，缺了两个开关就判不出
 *  「这个区跑不跑这一档」—— 拼上去只会把每一条都算成「区已删」或者全跳过，两种都是假话。
 *  后端还没补上这两个字段时清单是空的，比说一句错的强。 */
const coverZones = computed(() => (week.value && week.value.zones) || [])

const coverRows = computed(() => (coverDate.value
  ? coverSlotsFor({
      employees: weekEmployees.value,
      shifts: shifts.value,
      zones: coverZones.value,
      date: coverDate.value,
    })
  : []))

/** 名单面板顶部那一节（票 04）：同一份 `coverSlotsFor`，但日期**固定今天** —— 这一节是
 *  键盘 / 读屏 / 桌面用户的等价入口，不是按日浏览的入口（ADR 0127 决策 3：只有弹层跟着
 *  被长按的那一格走）。今天还没有数据（`week.today` 为空）时返回空数组，模板据此整节不
 *  渲染：不占位、也不写空文案 —— 名单面板本来就有自己的空态（「名单是空的」是**人名列表**
 *  的空态，别把两件事混成一句）。 */
const rosterCoverRows = computed(() => {
  const date = week.value ? week.value.today : ''
  if (!date) return []
  return coverSlotsFor({
    employees: weekEmployees.value,
    shifts: shifts.value,
    zones: coverZones.value,
    date,
  })
})

const coverIsToday = computed(() => !!coverDate.value && !!week.value && coverDate.value === week.value.today)
const coverIsPast = computed(() => !!coverDate.value && !!week.value && coverDate.value < week.value.today)

const coverTitle = computed(() => (coverIsToday.value
  ? '今天要顶的档'
  : `${monthDayText(coverDate.value)} 要顶的档`))
// 空态跟标题**同一套日期口径**：今天说「今天」，其它天说「这一天」—— 两处说法不一致时，
// 店长会以为弹层列错了日子（2026-11-01 裁定）。
const coverEmptyText = computed(() => (coverIsToday.value ? '今天没有要顶的档' : '这一天没有要顶的档'))

/** 行右侧那句「这个档现在还有谁」。没人就直说「没人」—— 留空会被读成「还没算」。 */
function coverOthersText(row) {
  return row.others.length ? `还有：${row.others.join(' / ')}` : '没人'
}

/** 不能装笔的两条理由（票 02 的常量 → 界面上的话）。店长要做的是去班次表把它启回来 /
 *  去配置里补一个区，不是以为系统漏了这一档。 */
const COVER_BLOCKED_TEXT = {
  [BLOCKED_SHIFT_INACTIVE]: '班次已停用',
  [BLOCKED_ZONE_DELETED]: '工作区已删',
}

function coverBlockedText(row) {
  return COVER_BLOCKED_TEXT[row.blocked_reason] || ''
}

/** 行上读给读屏的一句话：区 × 档、谁休 / 请假、这个档还有谁；不能装笔的补一句原因。 */
function coverRowLabel(row) {
  const parts = [
    `${row.zone_name || '工作区已删'} × ${row.shift_name}`,
    `${row.employee_name}${row.leave ? '请假' : '休'}`,
    coverOthersText(row),
  ]
  if (!row.can_pen) parts.push(coverBlockedText(row))
  return parts.join('；')
}

/** 点一行 = 把那个档的**班次 + 工作区**一次装进笔（ADR 0127 决策 4），关弹层、给一行提示。
 *
 *  直接写 `penShiftId` / `penZoneId`，**不走 `pickPenShift`** —— 它会把 `penZoneId` 拨回
 *  「跟固定区」，正好把刚装上的区清掉（那是「换班次」的语义，不是「装一支配好的笔」）。
 *  笔球**不展开**：球面本来就写着当前那支笔，展开只会再把表格压掉一行。
 *  停用班次 / 已删工作区的行在入口就挡下来：笔只认启用中的班次，硬装会默默退化成第一个
 *  启用班次 —— 那会写到别人身上。 */
function penFromCoverSlot(row) {
  if (!row.can_pen) return
  penShiftId.value = row.shift_id
  penZoneId.value = String(row.zone_id)
  penRest.value = false
  penHalfRest.value = false
  penClear.value = false
  coverSheet.value = null
  sayNotice(`已装笔：${row.shift_name} × ${row.zone_name}，去点要顶的那一格`)
}

/** 名单面板那一节点一行：装笔这件事只写一份 —— `penFromCoverSlot` 自带关弹层与那句提示，
 *  这里只多一步「切回周表」。装完必须回到能落笔的地方，停在名单没有可点的格子
 *  （票 04 的「不做」项）。不能装笔的行照旧先挡下来：切到周表却没有笔可用是一个死点击。 */
function penFromRosterCover(row) {
  if (!row.can_pen) return
  penFromCoverSlot(row)
  void openPanel('week')
}

/** 选区那一行的「刷」：把当前笔整片写下去（选区唯一的存在理由）。 */
async function applyRegion() {
  const view = pickActive.value
  if (!view) return
  await requestPaint(penAction(), penOptions(), view.cells)
}

/** 选区那一行的「退回规则」：撤掉这一片的手动覆盖，让他们回到轮转规则（action=restore；
 *  过去的日子服务端照旧跳过）。注意它**不是**笔上那颗「清」——那颗是把格子清成空白。 */
async function applyRegionRestore() {
  const view = pickActive.value
  if (!view) return
  await requestPaint(PICK_RESTORE, {}, view.cells)
}

/** 落笔前的三道闸（有没有格子 / 超没超 200 格 / 请求体全不全）；这一笔里有过去的日子
 *  再弹**一次**确认并点名是哪几天（ADR 0108 决策 3），确认之后才真写。 */
async function requestPaint(action, options, cells, who = '') {
  if (!cells.length) return
  if (overLimit(cells)) {
    sayNotice(`一次最多 ${PICK_MAX_CELLS} 格，现在选了 ${cells.length} 格：缩小选区再刷。`, [], true)
    return
  }
  if (!batchPayload(action, options, cells)) {
    sayNotice('这一笔没有班次可写：先在笔栏上选一个班次。', [], true)
    return
  }
  const dates = pastDatesIn(cells, { today: week.value ? week.value.today : '' })
  if (dates.length) {
    pastConfirm.value = { action, options, cells, dates, voiding: action === PICK_VOID, who }
    return
  }
  await runPaint(action, options, cells, who)
}

const pastConfirmText = computed(() => {
  const confirm = pastConfirm.value
  if (!confirm) return ''
  const days = confirm.dates.map((date) => formatDayLabel(date)).join('、')
  // 单格也走这一条（点一格就是一笔）：一句里说清「哪一天过去了」，不写「这一批」。
  const when = confirm.cells.length > 1
    ? `这一批 ${confirm.cells.length} 格里有 ${confirm.dates.length} 天已经过去了（${days}）`
    : `这天已经过去了（${days}）`
  if (confirm.voiding) {
    return `${when}：「清空」会把过去那几天清成空白（还没排到），员工看到的记录会跟着变。照清吗？`
  }
  return `${when}：改了以后员工看到的记录也会跟着变，而且没有「退回上一次」这一步。照改吗？`
})

/** 真写：一次请求写完（单格也只是 `items` 里的一条），写完只重拉一次周表。
 *  撤销要的是**应用前**的原值 —— 服务端不留快照（`staff_assignments` 是原地 upsert）。 */
async function runPaint(action, options, cells, who = '') {
  pastConfirm.value = null
  pickBusy.value = true
  const snapshot = snapshotCells(cells, weekCellAt)
  try {
    const result = normalizeResult(
      await api.post('/api/scheduling/overrides/batch', batchPayload(action, options, cells)),
    )
    // 这一笔顶掉上一笔：撤销只留最近一批（ADR 0107 决策 7）。
    // 「休半天」那一笔改的不是班次，撤销按原值写回 half_rest（见 `undoPlan` 的第三个参数）。
    const plan = undoPlan(snapshot, result.skipped, action)
    pickUndo.value = plan.length ? { plan } : null
    sayResult(result, cells, action, options, who)
    await loadWeek(week.value ? week.value.start : weekStart.value)
    await refreshMonthCounts()
  } catch (err) {
    // 服务端把「为什么刷不上」说全了（超限、没这个班次…）：原话转过来，别拿一句「保存失败」盖掉。
    sayNotice(err.message || '这一笔没写上去', [], true)
  } finally {
    pickBusy.value = false
    void drainPendingPaints()
  }
}

/** 撤销刚才那一笔：逐格还原（原本没覆盖的走删除覆盖、原本有覆盖的写回原值）。 */
async function undoLast() {
  const undo = pickUndo.value
  if (!undo || !undo.plan.length) return
  pickBusy.value = true
  let undone = 0
  let kept = 0
  let processed = 0
  const failed = []
  const total = undo.plan.length
  const UNDO_BATCH = 5
  sayNotice(`撤销中 0/${total}`)
  try {
    // 每批 5 格并发（原来逐格串行，19 格要等 19 个来回）；批与批之间仍按顺序。
    for (let start = 0; start < undo.plan.length; start += UNDO_BATCH) {
      const batch = undo.plan.slice(start, start + UNDO_BATCH)
      await Promise.all(batch.map(async (step) => {
        if (step.kind === 'keep') {
          // 那一格原本是**批出来的假**：覆盖行的 kind=leave 不是这两条接口写得回去的，
          // 如实说「撤不回来」，不拿一个「休」把它盖掉。
          kept += 1
          return
        }
        try {
          if (step.kind === 'clear') {
            await api.delete(`/api/scheduling/overrides/${step.employeeId}/${step.date}`)
          } else if (step.kind === 'half_rest') {
            // 那一笔只改了半天休：按原值发一条单格的批量（与落笔同一个端点，不多开一条路）。
            await api.post('/api/scheduling/overrides/batch', batchPayload(
              PICK_HALF_REST,
              { halfRest: step.payload.half_rest },
              [{ employeeId: step.employeeId, date: step.date }],
            ))
          } else {
            await api.put(`/api/scheduling/overrides/${step.employeeId}/${step.date}`, step.payload)
          }
          undone += 1
        } catch (err) {
          failed.push(step)
        }
      }))
      processed += batch.length
      if (processed < total) sayNotice(`撤销中 ${processed}/${total}`)
    }
    // 只留最近一批：撤过就没了（内存级，刷新或换设备也不在）。
    pickUndo.value = null
    sayNotice(`撤销中 ${total}/${total}`)
    await loadWeek(week.value ? week.value.start : weekStart.value)
    await refreshMonthCounts()
  } finally {
    pickBusy.value = false
    void drainPendingPaints()
  }
  const details = []
  // 失败逐格点名收进「看全部」（不是只报一个总数）。
  if (failed.length) {
    details.push(`没撤回来：${failed.map((step) => `${employeeName(step.employeeId)} ${shortDate(step.date)}`).join('、')}`)
  }
  if (kept) details.push(`有 ${kept} 格原本是批过的假，撤不回原样`)
  sayNotice(`已撤销 ${undone} 格`, details, failed.length > 0)
}

/** 单日覆盖（票 07）写完之后，月历那一份的人数跟着变。 */
async function refreshMonthCounts() {
  if (!monthValue.value) return
  try {
    const data = await api.get('/api/scheduling/calendar', { month: monthValue.value })
    calendar.value = data
    if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
  } catch (err) {
    // 周表已经是最新的；月历这一份下次进页面再补，不拿一句错盖住刚做成的改动。
  }
}

// 图例里那条「有申请等着批」：有角标才显示（没角标时图例列一个用不上的记号反而费解）。
const hasPendingMarks = computed(() => Object.keys(pendingMarks.value).length > 0)

function ruleLabel(rule) {
  // 三种状态分开说：没配过（`null`）/ 配了但读不出来（`invalid`）/ 正常。
  // 以前 `!rule.cycle` 一律说「还没配」—— 规则坏掉的人被显示成没配过，超级管理员照着配一遍
  // 也修不好（那条坏行还在，得先清空再配），真正的原因（脏数据）也看不出来。
  if (!rule) return '还没配'
  if (rule.invalid) return '规则坏了，重配一条'
  if (!rule.cycle || !rule.cycle.length) return '还没配'
  const parts = rule.cycle.map((id) => (id === null ? '休' : (shiftById.value[id] || {}).name || '?'))
  const unique = new Set(parts)
  if (unique.size === 1) return `固定${parts[0]}`
  // 长周期（最多 60 格）不能整串铺在名单行上：只出前 8 格 + 一共几天。
  if (parts.length > 8) return `${parts.slice(0, 8).join('')}…（${parts.length} 天）`
  return `${parts.join('')}（${parts.length} 天）`
}

async function setFixedShift(employee, shiftId) {
  busyEmployeeId.value = employee.id
  errorText.value = ''
  try {
    await api.put(`/api/scheduling/rules/${employee.id}`, { cycle: [shiftId] })
    await loadRoster()
    await loadCalendar(monthValue.value)
    await refreshNotes()
  } catch (err) {
    errorText.value = err.message || '规则没存上'
  } finally {
    busyEmployeeId.value = null
  }
}

async function clearRule(employee) {
  busyEmployeeId.value = employee.id
  errorText.value = ''
  try {
    await api.delete(`/api/scheduling/rules/${employee.id}`)
    await loadRoster()
    await loadCalendar(monthValue.value)
    await refreshNotes()
  } catch (err) {
    errorText.value = err.message || '规则没清掉'
  } finally {
    busyEmployeeId.value = null
  }
}

// ── 轮转周期（票 04）─────────────────────────────────────────────────
// 一格 = 一个营业日，格子里是班次名或「休」。超级管理员在输入框里按天写下来（空格隔开），
// 起点默认今天；周期长度 1 就是「固定某个班」，上面那些「固定白班」按钮是这条路径的
// 快捷方式，走的还是同一个 PUT —— 不是另一套逻辑。
const cycleOpenId = ref(null)
const cycleText = ref('')
const cycleAnchor = ref('')
const cycleError = ref('')
// 最多写多少天由服务端说了算（`MAX_CYCLE_DAYS`，随 /roster 一起下来）。
// 拿不到就**不设上限**（`null`）：前端不再猜一个 60 写死第二份 —— 超了由服务端回
// 那句中文 400，它的文案里带的才是真实上限。
const maxCycleDays = ref(null)
const REST_WORDS = ['休', '休息', '空', 'x', 'X', '-', '—']
// 输入框的示例按**当前班次名**拼，不把「白班/夜班」写死在文案里（班次可配置，票 11）。
// 名字取的是**还在用**的那份（`activeShifts`）：示例里出现一个停用班次，等于教超级管理员
// 写一个保存必被拒的周期。
const cyclePlaceholder = computed(() => {
  const names = (activeShifts.value || []).map((shift) => shift.name)
  const first = names[0] || '班次'
  const second = names[1] || first
  return [first, first, second, '休'].join(' ')
})

function openCycle(employee) {
  cycleOpenId.value = employee.id
  cycleError.value = ''
  const rule = employee.rule
  // 查不到的班次 id 写成 `#5` 这种**解析器一定会拒**的占位，而不是空串：
  // 空串会在保存时被安静地丢掉，周期就少一格、后面每一格整体前移一天。
  // 「查不到」= 这个月没有它的行、又不在启用列表里（显示用的 `shifts` 只带这个月
  // 真有行的停用班次）。停用但还查得到的，会照名字写出来 —— 那一格由 `parseCycle`
  // 拦下来（它只认启用班次），超级管理员照样得把它改掉。
  cycleText.value = rule && rule.cycle
    ? rule.cycle.map((id) => (id === null ? '休' : (shiftById.value[id] || {}).name || `#${id}`)).join(' ')
    : ''
  cycleAnchor.value = (rule && rule.anchor_date) || ''
}

function closeCycle() {
  cycleOpenId.value = null
  cycleError.value = ''
}

// 输入框里的字 → 服务端要的 `cycle` 数组。错在哪一格就用「第 N 格」说出来，
// 别让超级管理员对着「排班参数不合法」猜。
function parseCycle(text) {
  const tokens = String(text || '').split(/[\s,，、·/]+/).filter(Boolean)
  // 例子按**当前班次名**拼：班次是数据（票 11 能改名、能加第三个），文案里写死
  // 「白班 夜班 休」会让改了名的店对着一句不存在的话猜。名字只取还在用的那份。
  const names = (activeShifts.value || []).map((shift) => shift.name).join('、')
  if (!tokens.length) {
    return { error: `周期不能空着：至少写一天，例如「${names || '班次'} 休」` }
  }
  if (maxCycleDays.value && tokens.length > maxCycleDays.value) {
    return { error: `周期最多 ${maxCycleDays.value} 天，现在写了 ${tokens.length} 天` }
  }
  // 只认**还在用**的班次名：停用班次写进周期，服务端 `_require_shifts_usable` 会拒
  // （`unknown_shift_in_cycle`），不如在这一层就说清是第几格 —— 页面上那份不该让
  // 超级管理员写出一个保存必失败的周期。
  const byName = new Map((activeShifts.value || []).map((shift) => [shift.name, shift.id]))
  const cycle = []
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    // 班次名**先于**「休」的别名判：班次名是数据（票 11 能改），万一店里真有个
    // 班次叫「休」，写「休」应当指那个班次而不是静默变成休息日。代价是那种店里
    // 不能再用「休」写休息（还有 休息/空/x/… 这些别名）—— 所以**建班次时就挡住了
    // 这些保留词**（`RESERVED_SHIFT_NAMES`，服务层的 `_clean_shift_name`）。
    if (byName.has(token)) {
      cycle.push(byName.get(token))
    } else if (REST_WORDS.includes(token)) {
      cycle.push(null)
    } else if (token.startsWith('#')) {
      // `openCycle` 给已停用/已不存在的班次留的占位。这里必须挡住，不能当没写。
      return { error: `第 ${index + 1} 格引用的班次已停用或不存在（${token}）：请把这一格改成现在的班次` }
    } else {
      return { error: `第 ${index + 1} 格「${token}」不是班次：只能填 ${names || '班次名'}，或「休」` }
    }
  }
  return { cycle }
}

async function saveCycle(employee) {
  // 输入框上挂着 `@keyup.enter`，而按钮的 disabled 拦不住回车：一个人正在保存时
  // 再按一次回车会发第二条 PUT（服务端幂等，但界面会跟着重读两次）。
  if (busyEmployeeId.value) return
  const parsed = parseCycle(cycleText.value)
  if (parsed.error) {
    cycleError.value = parsed.error
    return
  }
  busyEmployeeId.value = employee.id
  cycleError.value = ''
  errorText.value = ''
  try {
    await api.put(`/api/scheduling/rules/${employee.id}`, {
      cycle: parsed.cycle,
      anchor_date: cycleAnchor.value || null,
    })
    closeCycle()
    await loadRoster()
    await loadCalendar(monthValue.value)
    await refreshNotes()
  } catch (err) {
    cycleError.value = err.message || '周期没存上'
  } finally {
    busyEmployeeId.value = null
  }
}

// 每人每班次一个固定区（票 03）：白班在案板的人就一直排案板。没配 = 空串，
// 下拉里显示「未配区」—— 那是个正常状态，不是错误。
function zoneOf(employee, shiftId) {
  const value = (employee.zone_defaults || {})[shiftId]
  return value === undefined || value === null ? '' : String(value)
}

async function setZoneDefault(employee, shiftId, value) {
  busyEmployeeId.value = employee.id
  errorText.value = ''
  try {
    await api.put(`/api/scheduling/zone-defaults/${employee.id}`, {
      shift_id: shiftId,
      zone_id: value === '' ? null : Number(value),
    })
    await loadRoster()
    // 后端把今天以后已经铺好的行一起改了，所以底下那张当天卡要重读一遍。
    await loadDay(selectedDate.value)
  } catch (err) {
    errorText.value = err.message || '工作区没存上'
    await loadRoster()
  } finally {
    busyEmployeeId.value = null
  }
}

// 单日覆盖（票 07）：这一天跟规则不一样 —— 换班次、改成休、或只换工作区。
// 覆盖是**整天的快照**：保存时把这一天的班次和工作区一起定下来，规则以后怎么变都不再
// 动它（想让它回到规则就「撤销覆盖」）。所以「只改工作区」也走同一条路：班次不动、
// 只把下拉里的区换掉，提交时带着当天那个班次一起发过去。
async function openDayEdit(person, shiftId) {
  // 工作区名单只在名单面板里读过（`loadRoster`）：**先把名单补齐，再决定下拉停在哪一项**。
  // 反过来的话，这个会话里第一次打开编辑器时 `zones` 还是空的 → 预填必然落回「跟固定区」
  // → 保存时 `zone_id: null` → 服务层按固定区回填：那天原本自己挑过的区被悄悄退回。
  if (!zones.value.length) await loadRoster()
  editing.value = {
    id: person.id,
    name: person.name,
    day: selectedDate.value,
    overridden: !!person.overridden,
    // 过去的日子：记录还在，但撤销不重写历史 —— 那种情况不摆「撤销」按钮。
    undoable: !!dayDetail.value && dayDetail.value.undoable !== false,
  }
  editShift.value = String(shiftId)
  // 预填按 **id**（不是按名字）：`hygiene_zones` 的名字没有唯一约束，同名两个区时
  // 按名字反查会指到别人身上。休的人没有区，`zone_id` 是 null → 停在「跟固定区」。
  editZone.value = person.zone_id === null || person.zone_id === undefined ? '' : String(person.zone_id)
  editError.value = ''
}

function closeDayEdit() {
  editing.value = null
  editError.value = ''
}

// 换了一天看名单，编辑器就收起来：它冻着的是上一天，留在屏幕上会让人以为改的是新这天。
// （`loadCalendar` 直接给 `selectedDate` 赋值那条路走不到 `loadDay`，只有这里盖得住。）
watch(selectedDate, () => {
  if (editing.value) closeDayEdit()
})

// 写完之后底下那张当天卡要重读：`loadCalendar` 会把选中那天拨回「今天/1 号」，
// 所以再把超级管理员刚才改的那天读回来（他刚改完还要看结果）。
async function refreshAfterWrite(day) {
  await loadCalendar(monthValue.value)
  if (day) await loadDay(day)
}

async function saveOverride() {
  const person = editing.value
  // 写的是**打开编辑器那一刻**那一天，不是「现在选中的那天」。
  const day = person && person.day
  if (!person || !day) return
  overrideBusy.value = true
  editError.value = ''
  try {
    const payload =
      editShift.value === 'rest'
        ? { is_rest: true }
        : {
            shift_id: Number(editShift.value),
            zone_id: editZone.value === '' ? null : Number(editZone.value),
          }
    await api.put(`/api/scheduling/overrides/${person.id}/${day}`, payload)
    closeDayEdit()
    await refreshAfterWrite(day)
  } catch (err) {
    // 后端把「为什么改不了」说全了（过去的日子、还没铺到的天、休不能带班次…），
    // 原话转给超级管理员，别拿一句「保存失败」盖掉。
    editError.value = err.message || '这一天没改上'
  } finally {
    overrideBusy.value = false
  }
}

async function undoOverride() {
  const person = editing.value
  const day = person && person.day
  if (!person || !day) return
  overrideBusy.value = true
  editError.value = ''
  try {
    await api.delete(`/api/scheduling/overrides/${person.id}/${day}`)
    closeDayEdit()
    await refreshAfterWrite(day)
  } catch (err) {
    editError.value = err.message || '撤销没成功'
  } finally {
    overrideBusy.value = false
  }
}

onMounted(async () => {
  // 读回 URL（`?view=week&month=2026-10&start=2026-10-12`），缺省是「今天所在的月 + 月历」。
  const query = route.query
  const rawMonth = queryText(query.month)
  const wantMonth = QUERY_MONTH_RE.test(rawMonth) ? rawMonth : currentMonthValue()
  const rawView = queryText(query.view)
  const wantView = VIEW_KEYS.includes(rawView) ? rawView : 'month'
  const rawStart = queryText(query.start)
  if (QUERY_DATE_RE.test(rawStart)) weekStart.value = rawStart
  monthValue.value = wantMonth
  await loadCalendar(wantMonth)
  await openPanel(wantView)
  await refreshNotes()
  // 首屏把缺省值也写回 URL（replace，不产生历史），刷新后落在同一屏。
  queryReady = true
  syncQuery()
})

// 实时（票 10 收尾）：员工提了申请、撤回了、或者改动了排班 —— 月历、待批角标与底下
// 那根条一起重拉。nudge 不带数据，所以照惯例重读；静默刷新，别每来一条就闪一下。
useNudgePull({
  id: 'scheduling-calendar',
  topics: ['scheduling'],
  pull: async () => {
    await loadCalendar(monthValue.value, true)
    await refreshNotes()
    // 周表开着的时候它也得跟着动：员工那边换了班，超级管理员盯着的正是这张表。
    if (panel.value === 'week') await loadWeek(weekStart.value)
  },
})
</script>

<template>
  <div class="hygiene-admin sched-page">
    <div
      class="gB"
      :class="{ 'pen-week': panel === 'week' }"
      @click="onPageClick"
      @pointerdown.capture="clearLongPressTail"
    >
      <div class="gB-body">
        <header class="gHead">
          <p class="gKicker">人事 · 排班</p>
          <div class="gTitleRow">
            <h1 class="gTitle">排班</h1>
            <div class="gHeadActions">
              <button class="gBtn gLink" type="button" @click="router.push('/workbench/hr/shifts')"><b>班次表</b> ›</button>
            </div>
          </div>
          <div class="gMetaRow">
            <p class="gPeriod">
              <template v-if="panel === 'week'">
                <b>{{ weekRangeLabel || '本周' }}</b>
                <span>{{ weekEmployees.length }} 人 × 7 天</span>
              </template>
              <template v-else>
                <b>{{ monthTitle(monthValue) }}</b>
                <span>{{ shifts.map((s) => s.name).join(' / ') }} 人数</span>
              </template>
              <em>{{ formatTodayLabel() }}</em>
            </p>
            <div class="gSeg" role="group" aria-label="排班视角">
              <button class="gBtn" :class="{ on: panel === 'month' }" type="button" @click="openPanel('month')">月历</button>
              <button class="gBtn" :class="{ on: panel === 'week' }" type="button" @click="openPanel('week')">周表</button>
              <button class="gBtn" :class="{ on: panel === 'roster' }" type="button" @click="openPanel('roster')">名单</button>
            </div>
            <!-- 手机把视角切换与翻月并到同一行；桌面这份收起，翻月在面板头。 -->
            <div class="gNav gNav--head">
              <template v-if="panel === 'roster'">
                <span class="gW-tip">配规则 / 固定区在这里</span>
              </template>
              <template v-else-if="panel === 'week'">
                <button class="gBtn" type="button" @click="loadWeek(shiftWeek(weekStart, -1))">‹ 上周</button>
                <button class="gBtn" type="button" @click="loadWeek(mondayOf(today))">本周</button>
                <button class="gBtn" type="button" @click="loadWeek(shiftWeek(weekStart, 1))">下周 ›</button>
              </template>
              <template v-else>
                <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, -1))">‹ 上月</button>
                <button class="gBtn" type="button" @click="loadCalendar(currentMonthValue())">本月</button>
                <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, 1))">下月 ›</button>
              </template>
            </div>
          </div>
        </header>

        <p v-if="errorText" class="gMsg">{{ errorText }}</p>
        <!-- 0043 还没应用（服务端把 `color_key` 回成 null）：读路径降级成中性灰，
             但得说清原因 —— 不然店长以为配色坏了。写颜色的那条路仍然硬失败。 -->
        <p v-if="!colorReady && shifts.length" class="gNote">
          班次颜色还没启用（数据库迁移 0043 未应用）：格子暂时都是中性灰。到「系统更新 → 数据库迁移」应用 0043 后刷新本页。
        </p>

        <template v-if="panel === 'month'">
          <div class="gDash" :class="{ 'gDash--drawer': mobileDayOpen }">
            <section class="gPanel gCalPanel">
              <div class="gPanel-hd">
                <i class="gLed sig"></i>
                <h2>{{ monthTitle(monthValue) }}</h2>
                <span class="gPanel-hint">{{ formatTodayLabel() }}</span>
                <div class="gNav gNav--panel">
                  <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, -1))">‹ 上月</button>
                  <button class="gBtn" type="button" @click="loadCalendar(currentMonthValue())">本月</button>
                  <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, 1))">下月 ›</button>
                </div>
              </div>
          <div class="gB-head">
            <span v-for="label in WEEKDAYS" :key="label">{{ label }}</span>
          </div>
          <div class="gB-grid">
            <div v-for="n in (calendar ? calendar.lead : 0)" :key="`lead-${n}`" class="gB-d mute"></div>
            <button
              v-for="day in (calendar ? calendar.days : [])"
              :key="day.business_date"
              type="button"
              class="gB-d"
              :class="{
                today: day.is_today,
                sel: day.business_date === selectedDate,
                mute: !!(calendar && calendar.window_end && day.business_date > calendar.window_end),
                over: day.overridden > 0,
              }"
              @click="pickDay(day.business_date)"
            >
              <span class="n">{{ day.day }}</span>
              <span class="c">
                <span
                  v-for="item in cellCounts(day)"
                  :key="item.id"
                  class="cItem"
                  :class="[item.tone, { 'is-empty': !item.count }]"
                ><i class="cBar"></i>{{ item.count }}</span>
              </span>
              <!-- 待批角标（spec US 11）：这天有等着批的申请。票 07 定标记时特意把琥珀留给
                   「待处理」、青点留给「这天被改过」—— 两种记号不能混。 -->
              <!-- 半天休（票 07）：这天有几个人只上半天。月历只读 —— 设置它的门有
                   考勤表页与周表笔栏（ADR 0122）；跟右上角那个「待批」的琥珀圆点分开摆。 -->
              <span v-if="day.half_rest" class="half">半{{ day.half_rest }}</span>
              <span v-if="pendingMarks[day.business_date]" class="pend">
                {{ pendingMarks[day.business_date] }}
              </span>
            </button>
          </div>

          <div class="gB-legend">
            <span v-for="shift in shifts" :key="shift.id">
              <i :class="toneClass(shift.id)"></i>{{ shift.name }}
            </span>
            <span v-if="overriddenDays" class="gB-ov"><i></i>这天有改动</span>
            <span v-if="halfRestDays" class="gB-half"><i></i>半N = 这天 N 人休半天</span>
            <span v-if="hasPendingMarks" class="gB-pend"><i></i>有申请等着批</span>
          </div>
          <p v-if="beyondNote" class="gB-note">{{ beyondNote }}</p>

              <!-- 待批请假影响的日子：待批请假会让那天缺人；点一天直接切过去看名单。 -->
              <div class="gGap">
                <div class="gGap-hd">
                  <span>待批请假影响的日子</span>
                  <em>待批请假 · 点一天看当天名单</em>
                </div>
                <p v-if="!gapDays.length" class="gGap-empty">这个月没有待批请假。</p>
                <ul v-else class="gGapList">
                  <li v-for="gap in gapDays" :key="gap.date">
                    <button type="button" class="gGapRow" @click="pickDay(gap.date)">
                      <b>{{ formatDayLabel(gap.date) }}</b>
                      <span class="gGapN">缺 {{ gap.count }} 人</span>
                      <span class="gGapGo">›</span>
                    </button>
                  </li>
                </ul>
              </div>

              <!-- 本月空档：当月所有班次都 0 人的日子（窗口之外的不算）。也是段，不是面板。 -->
              <div class="gGap gGap--empty">
                <div class="gGap-hd">
                  <span>本月空档 · {{ monthEmptyDays.length }} 天</span>
                  <em>当天所有班次都 0 人 · 点一天看名单</em>
                </div>
                <p v-if="!monthEmptyDays.length" class="gGap-empty">这个月每天都在排人。</p>
                <ul v-else class="gGapList gGapList--scroll">
                  <li v-for="day in monthEmptyDays" :key="`empty-${day.business_date}`">
                    <button type="button" class="gGapRow" @click="pickDay(day.business_date)">
                      <b>{{ formatDayLabel(day.business_date) }}</b>
                      <span class="gGapN">0 人</span>
                      <span class="gGapGo">›</span>
                    </button>
                  </li>
                </ul>
              </div>
            </section>

            <div v-if="mobileDayOpen" class="gDrawerScrim" @click="mobileDayOpen = false"></div>
            <aside class="gSide">
              <!-- 顶部 40px 待批条（零值不渲染）。 -->
              <button class="gPend gTodo" type="button" v-if="Object.keys(pendingMarks).length" @click="router.push('/workbench/hr/inbox')">
                <span class="n">假</span>
                <b>请假待办</b>
                <span>{{ Object.keys(pendingMarks).length }} 条申请等着批</span>
                <span class="go">›</span>
              </button>

          <div class="gB-card">
            <div class="gB-card-hd">
              <i class="gLed sig"></i>
              <b>{{ formatDayLabel(selectedDate) }}</b>
              <span v-if="selectedDate && selectedDate === today">今天</span>
              <em v-if="dayDetail">上班 {{ dayDetail.total }} 人 · 不在岗 {{ offPeople.length }} 人</em>
              <button class="gDrawerClose" type="button" aria-label="收起当天名单" @click="mobileDayOpen = false">收起</button>
            </div>
            <div class="gB-dayBody">
            <template v-if="dayDetail">
              <div v-for="group in dayDetail.groups" :key="group.shift.id" class="gB-grp">
                <div class="gB-grp-t" :class="toneClass(group.shift.id)">
                  <span class="d"></span><b>{{ group.shift.name }}</b><i>{{ group.count }}</i>
                </div>
                <div v-if="group.people.length" class="gB-names">
                  <button
                    v-for="person in group.people"
                    :key="person.id"
                    type="button"
                    class="gB-name"
                    :class="{
                      over: person.overridden,
                      on: editing && editing.id === person.id,
                    }"
                    @click="openDayEdit(person, group.shift.id)"
                  >
                    {{ person.name }}
                    <em v-if="person.half_rest" class="gB-zone half">休半天</em>
                    <em v-if="person.zone" class="gB-zone">{{ person.zone }}</em>
                    <em v-else class="gB-zone off">未配区</em>
                  </button>
                </div>
                <p v-else class="gB-empty">这天没人排{{ group.shift.name }}</p>
              </div>
              <!-- 批过假的人自成一组（票 08）：跟「本来就休」在数据里同形（都没有班次），
                   只有 `person.leave` 分得开。混在一个「休」里，超级管理员就分不清
                   「这人请假了」和「这人今天本来就休」。 -->
              <div v-if="leavePeople.length" class="gB-grp">
                <div class="gB-grp-t leave">
                  <span class="d"></span><b>请假</b><i>{{ leavePeople.length }}</i>
                </div>
                <div class="gB-names">
                  <button
                    v-for="person in leavePeople"
                    :key="person.id"
                    type="button"
                    class="gB-name"
                    :class="{
                      over: person.overridden,
                      on: editing && editing.id === person.id,
                    }"
                    @click="openDayEdit(person, 'rest')"
                  >
                    {{ person.name }}
                    <em class="gB-zone leave">请假</em>
                  </button>
                </div>
              </div>
              <!-- 休的人也要能点开：规则铺出来的休也好、已经被改成休也好，都得有个入口
                   把那天改回班次，或者把「改成休」这个覆盖撤掉（票 07）。 -->
              <div v-if="restPeople.length" class="gB-grp">
                <div class="gB-grp-t off">
                  <span class="d"></span><b>休</b><i>{{ restPeople.length }}</i>
                </div>
                <div class="gB-names">
                  <button
                    v-for="person in restPeople"
                    :key="person.id"
                    type="button"
                    class="gB-name"
                    :class="{
                      over: person.overridden,
                      on: editing && editing.id === person.id,
                    }"
                    @click="openDayEdit(person, 'rest')"
                  >
                    {{ person.name }}
                    <em class="gB-zone off">休</em>
                  </button>
                </div>
              </div>
            </template>
            <p v-else class="gB-empty">{{ loading ? '读取中……' : '选一天看是谁' }}</p>

            </div>
            <div v-if="editing" class="gD">
              <div class="gD-hd">
                <b>{{ editing.name }}</b>
                <span>{{ formatDayLabel(editing.day) }}</span>
                <em v-if="editing.overridden">这天被改过</em>
              </div>
              <label class="gD-pick">
                <span>班次</span>
                <select v-model="editShift" class="gZ-sel">
                  <option value="rest">休</option>
                  <!-- 列的是**显示用**那份：他那天可能就是停用的那个班，下拉里得有它，
                       否则选择框看着是空的。停用的标出来并禁掉 —— 选了服务端会拒
                       （`set_override` 只收还在用的班次），不给一个点了必然报错的选项。 -->
                  <option
                    v-for="shift in shifts"
                    :key="shift.id"
                    :value="String(shift.id)"
                    :disabled="!shift.is_active"
                  >
                    {{ shift.name }}<template v-if="!shift.is_active">（已停用）</template>
                  </option>
                </select>
              </label>
              <label class="gD-pick">
                <span>工作区</span>
                <select v-model="editZone" class="gZ-sel" :disabled="editShift === 'rest'">
                  <option value="">跟固定区</option>
                  <option v-for="zone in zones" :key="zone.id" :value="String(zone.id)">
                    {{ zone.name }}
                  </option>
                </select>
              </label>
              <div class="gD-act">
                <button class="gBtn on" type="button" :disabled="overrideBusy" @click="saveOverride()">
                  只改这一天
                </button>
                <button
                  v-if="editing.overridden && editing.undoable"
                  class="gBtn ghost"
                  type="button"
                  :disabled="overrideBusy"
                  @click="undoOverride()"
                >
                  撤销覆盖，回到规则
                </button>
                <button class="gBtn ghost" type="button" @click="closeDayEdit()">取消</button>
              </div>
              <!-- 过去的日子：记录还在，但历史不重写 —— 不给一个点了也不变的按钮。 -->
              <p v-if="editing.overridden && !editing.undoable" class="gD-hint">
                这天的改动超出可补记窗口了（30 天）：再往前的日子撤不回规则，怎么排就怎么留着。
              </p>
              <p class="gD-hint">
                改的是这一天，不是规则：规则以后怎么变都不动它。撤掉覆盖，这天就回到规则铺出来的样子。
              </p>
              <p v-if="editError" class="gErr">{{ editError }}</p>
            </div>
          </div>


              <!-- 配置缺口：折叠一行；零值不渲染。 -->
              <button
                v-if="pendingCount"
                class="gPend gConfigGap"
                type="button"
                @click="openPanel('roster')"
              >
                <span class="n">{{ pendingCount === null ? '—' : pendingCount }}</span>
                <b>个人还没配规则</b>
                <span>{{ pendingText }}</span>
                <span class="go">›</span>
              </button>
            </aside>
          </div>
        </template>

        <template v-else-if="panel === 'week'">
          <section class="gPanel gWeekPanel">
            <div class="gPanel-hd">
              <i class="gLed sig"></i>
              <h2>周表</h2>
              <span class="gPanel-hint">{{ weekRangeLabel || '本周' }}</span>
              <div class="gNav gNav--panel gNav--week">
                <button class="gBtn" type="button" @click="loadWeek(shiftWeek(weekStart, -1))">‹ 上周</button>
                <button class="gBtn" type="button" @click="loadWeek(mondayOf(today))">本周</button>
                <button class="gBtn" type="button" @click="loadWeek(shiftWeek(weekStart, 1))">下周 ›</button>
              </div>
            </div>
          <!-- 悬浮球（2026-10-29 用户要求）：笔栏常驻太占地方，收成右下角一颗球，点球才展开。
               球面写当前那支笔（班次名 / 休 / 休半天 / 清），收起也一眼知道下一笔会写什么。 -->
          <!-- 弹层开着时不渲染笔球：球是 `--z-queue`（200），会压在弹层（`--z-dialog`）
               某一行上（真机截图）。层级令牌那套语义不动，这里只是让它不在没用的时刻出现 ——
               弹层里点一行本来就是「装笔」，装完回周表，球才重新有意义。 -->
          <button
            v-if="!coverSheet"
            class="gPick-fab"
            type="button"
            :class="{ open: penOpen }"
            :aria-expanded="penOpen ? 'true' : 'false'"
            aria-controls="gPick"
            :aria-label="penOpen ? '收起排班笔' : `排班笔：${penLabel}，点一下展开`"
            @click.stop="penOpen = !penOpen"
          >
            <i v-if="penDotClass" class="dt" :class="penDotClass"></i>
            <span class="gPick-fabText">{{ penLabel }}</span>
          </button>
          <!-- 收起时的回执：落在球上方，不用为了看一眼「已清空 / 跳过几格」又把笔栏展开。 -->
          <div
            v-if="!penOpen && pickNotice"
            class="gPick-toast"
            :class="{ gErr: pickNotice.warn }"
            role="status"
            aria-live="polite"
          >
            <span class="gPick-note-text">{{ pickNotice.text }}</span>
            <button
              v-if="pickNotice.details.length"
              class="gBtn ghost"
              type="button"
              @click.stop="penOpen = true; noticeOpen = true"
            >看全部</button>
          </div>
          <Transition name="gPick">
          <div
        v-if="penOpen && panel === 'week'"
        id="gPick"
        class="gPick"
        role="group"
        aria-label="落笔"
      >
        <!-- 提示只有这一行（票 14 修订口径 4）：出现后几秒自己消失，下一次操作替换它。
             逐格点名（谁被跳过、谁有待批申请）收进「看全部」——信息不丢，但不再常驻五六行。 -->
        <p
          v-if="pickNotice"
          class="gPick-note"
          :class="{ gErr: pickNotice.warn }"
          aria-live="polite"
        >
          <span class="gPick-note-text">{{ pickNotice.text }}</span>
          <button
            v-if="pickNotice.details.length"
            class="gBtn ghost"
            type="button"
            aria-controls="gPick-detail"
            :aria-expanded="noticeOpen ? 'true' : 'false'"
            @click="noticeOpen = !noticeOpen"
          >{{ noticeOpen ? '收起' : '看全部' }}</button>
        </p>
        <div v-if="noticeOpen && pickNotice" id="gPick-detail" class="gPick-detail">
          <p v-for="(line, index) in pickNotice.details" :key="index" class="gHint">{{ line }}</p>
        </div>

        <!-- 工具条：选中对象常驻一行（点过哪一格 / 选了哪一片），选区摘要与「刷」「清」
             只在批量时多一行。 -->
        <p v-if="!pickActive && lastCellText" class="gPick-now">选中：{{ lastCellText }}</p>
        <!-- 选区摘要：**只在选了整列 / 整行时出现**（点「刷」才批量应用）。不选区时这一行
             完全不出现 —— 不用批量的人不该看见它（用户③：我就想逐格改）。 -->
        <div v-if="pickActive" class="gPick-region">
          <b class="gPick-sum">{{ pickTitle }}</b>
          <span class="gW-tip">{{ pickCountText }}</span>
          <button class="gChip" type="button" :disabled="pickBusy" @click="applyRegion()">刷</button>
          <!-- 这一颗原来也叫「清」、与笔上那颗同名又同距，点一下立刻清掉整片（最多 200 格）。
               v5（2026-10-18 审查）：改名「退回规则」+ 点两次（第二次才真写，4 秒不点自动复位）。
               2026-10-29：语义与笔上那颗「清」彻底分开 —— 这里发 action=restore（回到轮转规则），
               笔上那颗发 action=void（把格子清成空白）。 -->
          <button
            class="gChip"
            type="button"
            :class="{ on: regionRestoreArmed, danger: regionRestoreArmed }"
            :disabled="pickBusy"
            :title="regionRestoreArmed
              ? '再点一次就退回规则（最多 200 格）'
              : '退回规则：把选中的日子里手改过的那几天清掉、回到轮转规则'"
            @click="askRegionRestore()"
          >{{ regionRestoreArmed ? '确认退回' : '退回规则' }}</button>
        </div>

        <!-- 工作区：点按钮才展开这一行横滑的区名（默认收起 —— 少一行就少压掉一行员工）。
             按钮组而不是原生下拉控件：那个弹层由系统画，在这个贴底的位置会飘到页面中间
             （2026-10-13 用户截图），而那层我们一个像素都改不了。 -->
        <div v-if="penZoneOpen" class="gPick-zones">
          <button
            class="gChip"
            type="button"
            :class="{ on: penZoneId === '' }"
            :disabled="pickBusy"
            @click="penZoneId = ''"
          >跟固定区</button>
          <button
            v-for="zone in zones"
            :key="zone.id"
            class="gChip"
            type="button"
            :class="{ on: penZoneId === String(zone.id) }"
            :disabled="pickBusy"
            @click="penZoneId = String(zone.id)"
          >{{ zone.name }}</button>
        </div>

        <!-- 笔栏本体：一行。笔上的班次 / 工作区选一次就一直用（用户④：要「一支笔」的感觉）。
             棋子直接排在整行里、由整行换行 —— 班次多于 3 张时两行排开（见 `.gPick-row`），
             而不是在一条窄横滑里把后两个班次藏起来。 -->
        <div class="gPick-row">
          <span class="gPen-tag">刷</span>
          <button
            v-for="shift in activeShifts"
            :key="shift.id"
            type="button"
            class="gChip"
            :class="[toneClass(shift.id), { on: !penRest && !penHalfRest && !penClear && !!penShift && penShift.id === shift.id }]"
            :aria-pressed="!penRest && !penHalfRest && !penClear && !!penShift && penShift.id === shift.id ? 'true' : 'false'"
            :disabled="pickBusy"
            @click="pickPenShift(shift)"
          ><i class="dt"></i>{{ shift.name }}</button>
          <button
            class="gChip"
            type="button"
            :class="{ on: penRest }"
            :aria-pressed="penRest ? 'true' : 'false'"
            :disabled="pickBusy"
            @click="pickPenRest()"
          ><i class="dt rest"></i>休</button>
          <!-- 「休半天」：点一格标上（再点同一格取消）。那天得先有班次 —— 休 / 空白格
               没有班次可挂，点了只回一句提示、不发请求。 -->
          <button
            class="gChip gPen-half"
            type="button"
            :class="{ on: penHalfRest }"
            :aria-pressed="penHalfRest ? 'true' : 'false'"
            :disabled="pickBusy"
            title="休半天：把这一格标成只上半天；再点同一格取消（那天要有班次）"
            @click="pickPenHalfRest()"
          ><i class="dt half"></i>休半天</button>
          <!-- 「清」：把这一格**清成空白**（还没排到），不是「退回规则」—— 后者是选区摘要行那一颗。 -->
          <button
            class="gChip gPen-clear"
            type="button"
            :class="{ on: penClear }"
            :aria-pressed="penClear ? 'true' : 'false'"
            :disabled="pickBusy"
            title="清：把这一格清成空白（还没排到）"
            @click="pickPenClear()"
          ><i class="dt clear"></i>清</button>
          <button
            class="gChip gPen-zone"
            type="button"
            :aria-expanded="penZoneOpen ? 'true' : 'false'"
            :disabled="pickBusy"
            @click="penZoneOpen = !penZoneOpen"
          >{{ penZoneOpen ? '收起' : penZoneName }}</button>
          <!-- 撤销：没有可撤销的那一批时禁用（口径 1）。撤销是内存级的（ADR 0107 决策 7），
               那句说明跟着回执走；按钮上再挂一条 title 兜底。 -->
          <button
            class="gChip gPick-undo"
            type="button"
            :disabled="pickBusy || !pickUndo"
            title="撤销只留最近一批、也只在这台手机上"
            aria-label="撤销刚才那一批"
            @click="undoLast()"
          >↺</button>
        </div>
      </div>
          </Transition>
          <!-- 笔栏结束 -->
          <p v-if="weekError" class="gMsg">{{ weekError }}</p>
          <p v-if="!week" class="gB-empty">周表读取中……</p>
          <template v-else>
            <div v-if="weekFindOpen || weekFind.trim()" class="gW-find">
              <input v-model="weekFind" type="search" placeholder="搜成员" spellcheck="false">
              <span v-if="weekFind.trim()">{{ weekRows.length }} 人 · 筛掉 {{ weekFilteredOut }} 人</span>
              <button class="gBtn" type="button" @click="weekFindOpen = false; weekFind = ''">收起</button>
            </div>
            <div class="gW-wrap">
              <table class="gW">
                <!-- 姓名列定宽，其余七列平分：手机上不横向滚（跟月历那边同一个做法）。 -->
                <colgroup>
                  <col class="gW-cname">
                  <col v-for="day in weekDays" :key="day.business_date">
                </colgroup>
                <thead>
                  <tr>
                    <th class="gW-name">
                      <span>成员</span>
                      <button
                        class="gW-findBtn"
                        type="button"
                        title="搜成员"
                        aria-label="搜成员"
                        :aria-expanded="weekFindOpen ? 'true' : 'false'"
                        @click="weekFindOpen = !weekFindOpen"
                      >⌕</button>
                    </th>
                    <th
                      v-for="day in weekDays"
                      :key="day.business_date"
                      :class="{ today: day.is_today, weekend: day.is_weekend, mute: !day.in_window }"
                    >
                      <!-- 表头是**手柄**：可点、可聚焦、有名字（票 14 的验收）。窗口外那几天
                           照旧灰着不可选（未来窗口的上限没变）。 -->
                      <button
                        type="button"
                        class="gW-th"
                        :class="{ on: pickedCols.includes(day.business_date) }"
                        :disabled="!day.in_window"
                        :aria-pressed="pickedCols.includes(day.business_date) ? 'true' : 'false'"
                        :aria-label="`${formatDayLabel(day.business_date)}：选中这一列全店`"
                        @click="onColPick(day)"
                      >
                        <span class="w">周{{ WEEK_HEADS[day.weekday] }}</span>
                        <span class="d">{{ day.day }}</span>
                        <em v-if="day.month !== week.days[0].month">{{ day.month }}月</em>
                      </button>
                      <!-- 待批的天数是一天一条事实，挂在**这一列的表头**上；挂在每一格里
                           会变成 11 个一模一样的点（一天 11 个人）。它是展示层，放在按钮
                           **外面**：点它不会触发选列（票 14 的验收）。 -->
                      <i
                        v-if="pendingMarks[day.business_date]"
                        class="pend"
                        :title="`${pendingMarks[day.business_date]} 条申请等着批`"
                      >{{ pendingMarks[day.business_date] }}</i>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="employee in weekRows" :key="employee.id">
                    <th class="gW-name">
                      <button
                        type="button"
                        class="gW-th"
                        :class="{ on: pickedRows.includes(employee.id) }"
                        :aria-pressed="pickedRows.includes(employee.id) ? 'true' : 'false'"
                        :aria-label="`${employee.name}：选中这一整周`"
                        @click="onRowPick(employee)"
                      >
                        <b>{{ employee.name }}</b><!--
                          存假（2026-11-01 用户要的）：名字后面跟一个带圈的数字，只写天数 ——
                          「存假 / 欠班」与「天」都不占版面，含义在颜色与悬停提示里。
                          读不到就整条不渲染（`loadLeaveBalances` 静默失败），不占位置。
                      --><span
                          v-if="leaveBalance[employee.id] !== undefined"
                          class="gW-leave"
                          :class="{ debt: leaveBalance[employee.id] < 0 }"
                          :title="leaveTitle(leaveBalance[employee.id])"
                        >{{ leaveBadge(leaveBalance[employee.id]) }}</span>
                        <!-- 没配规则的人整行都是空的 —— 不在这里说一句，看着像这店不给他排班。 -->
                        <em v-if="!employee.has_rule" class="gW-norule" title="这个人还没有轮转规则">没规则</em>
                      </button>
                    </th>
                    <td v-for="day in weekDays" :key="day.business_date">
                      <!-- 格子只有**一条写入口**：点一下 = 落笔。长按是只读入口（票 03 / ADR 0127）：
                           按住 550ms 弹「那一天要顶的档」，松手不写任何东西 —— 随之而来的 click
                           由 `onCellClick` 吞掉。三个禁止系统手势的声明（触摸滚动 / iOS 系统菜单 /
                           安卓选中文字）挂在这一格上，少一条真机上就长按变弹菜单。 -->
                      <button
                        type="button"
                        class="gW-cell"
                        :class="[weekCellClass(employee, day), { press: pressKey === cellPressKey(employee, day) }]"
                        :aria-label="weekCellLabel(employee, day)"
                        :disabled="!day.in_window"
                        @pointerdown="onCellDown($event, employee, day)"
                        @pointermove="onCellMove($event)"
                        @pointerup="onCellUp"
                        @pointercancel="onCellUp"
                        @pointerleave="onCellUp"
                        @contextmenu.prevent
                        @click="onCellClick(employee, day)"
                      >
                        <span>
                          {{ weekCellText(employee, day) }}<i
                            v-if="weekCellHalf(employee, day)"
                            class="gW-half"
                            title="休半天（只上半天）"
                          >半</i>
                        </span>
                        <!-- 第二行：工作区。没配区的格子这一行整条不渲染（见 `weekCellZone`）。 -->
                        <span v-if="weekCellZone(employee, day)" class="gW-zone">{{ weekCellZone(employee, day) }}</span>
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="!weekRows.length" class="gB-empty">没有名字里有「{{ weekFind.trim() }}」的人</p>

            <div class="gB-legend">
              <span v-for="shift in activeShifts" :key="shift.id">
                <i :class="toneClass(shift.id)"></i>{{ shift.name }}
              </span>
              <span><i class="off"></i>休</span>
              <span><i class="leave"></i>假（批过的请假）</span>
              <span v-if="weekOverriddenCount" class="gB-ov"><i></i>青点 = 这天被单独改过（{{ weekOverriddenCount }} 处）</span>
              <span v-if="weekHalfCount" class="gB-half"><i></i>半 = 休半天（{{ weekHalfCount }} 处）</span>
              <span v-if="hasPendingMarks" class="gB-pend"><i></i>有申请等着批</span>
            </div>

            <div class="gW-hintLine">
              点一格落笔；长按一格 = 只看那一天要顶的档；点列头 / 人名选区，再用工具条「刷」批量。
              <details class="gW-help">
                <summary>?</summary>
                <p class="gB-note">
                  点一格 = 用上方工具条上的班次写这一格；点日期列头 / 人名选一片，再用「刷」批量写。
                  「休半天」把那一格标成只上半天（再点一下取消），那天得先有班次。
                  「清」把这一格清成空白（还没排到）；「退回规则」把选中日子里手改过的那几天退回轮转规则；
                  改轮转规则在「名单」里。
                </p>
                <p class="gB-note">
                  长按 = 只看，不改：按住一格约半秒，列出「那一天要顶的档」—— 那天休 / 请假的人
                  本来站哪个档、那个档现在还有谁。点其中一行把那个档的班次与工作区装进笔，
                  再去点要顶的那一格；松手不会改动任何一格。
                </p>
                <p v-if="weekNoDataNote" class="gB-note">{{ weekNoDataNote }}</p>
                <p v-if="weekBeyondDays.length" class="gB-note">
                  这一周里有 {{ weekBeyondDays.length }} 天超出了展开窗口（只铺到 {{ week.window_end }}），
                  那几格还是虚的。
                </p>
                <p v-if="weekExcludedNote" class="gB-note">{{ weekExcludedNote }}</p>
              </details>
            </div>
          </template>
          </section>

          <!-- 长按一格 = 只看那一天要顶的档（票 03 / ADR 0127）：底部弹层 + 遮罩，与月历那张
               当天名单抽屉**同一套位置口径**（`.gDrawerScrim` / 贴底 / `--z-dialog`）。遮罩盖过
               壳底栏（`--z-shell`）就是让位、关掉自然还位 —— 不为它再开一个 body 状态
               （2026-10-29 把笔栏收成悬浮球时，「底栏让位」那条整条退役了，别复活）。 -->
          <template v-if="coverSheet">
            <div class="gDrawerScrim gCoverScrim" @click="onCoverScrimClick"></div>
            <section
              class="gCoverSheet"
              role="dialog"
              aria-modal="true"
              :aria-label="coverTitle"
            >
              <div class="gCoverHd">
                <b>{{ coverTitle }}</b>
                <!-- 2026-11-01 用户改：点一行直接写给**被长按的那个人**，不再经过笔。
                     这一行必须点名写给谁 —— 否则「点一下」看不出去改的是哪一格。 -->
                <span class="gCoverHint">点一行 = 写给 {{ coverSheet.employeeName }}</span>
                <button class="gDrawerClose" type="button" aria-label="收起要顶的档" @click="closeCoverSheet()">收起</button>
              </div>
              <!-- 过去的日子照样能长按、能装笔（ADR 0108 的 30 天补记）：只标一句实话。 -->
              <p v-if="coverIsPast" class="gCoverPast">这一天已经过去</p>
              <p v-if="!coverRows.length" class="gCoverEmpty">{{ coverEmptyText }}</p>
              <ul v-else class="gCoverList">
                <li v-for="row in coverRows" :key="`${row.employee_id}|${row.shift_id}|${row.zone_id}`">
                  <!-- 整行是一颗按钮：点中就把这个档的班次 + 工作区一次装进笔。不能装笔的行
                       `disabled`（停用班次 / 已删工作区）—— 没有按压态，右侧写明原因。 -->
                  <button
                    class="gCoverRow"
                    type="button"
                    :disabled="!row.can_pen"
                    :aria-label="coverRowLabel(row)"
                    @click="onCoverRowClick(row)"
                  >
                    <span class="gCoverSlot">{{ row.zone_name || '（工作区已删）' }} × {{ row.shift_name }}</span>
                    <span class="gCoverMeta">
                      <b class="gCoverWho">{{ row.employee_name }}{{ row.leave ? '请假' : '休' }}</b>
                      <span class="gCoverOthers">{{ coverOthersText(row) }}</span>
                      <span v-if="!row.can_pen" class="gCoverBlocked">{{ coverBlockedText(row) }}</span>
                    </span>
                  </button>
                </li>
              </ul>
            </section>
          </template>
        </template>

        <template v-else-if="panel === 'roster'">
          <section class="gPanel gRosterPanel">
            <div class="gPanel-hd">
              <i class="gLed sig"></i>
              <h2>名单 · 规则</h2>
              <span class="gPanel-hint">一人一条 · {{ roster.length }} 人</span>
              <span class="gPanel-more">固定班 / 编周期</span>
            </div>
            <!-- 今天要顶的档（票 04）：长按是触摸菜单，键盘 / 读屏 / 桌面用户进不去，这一节就是
                 那条不靠长按的路。行是原生 `<button>` —— Tab / Enter / 空格都白拿，不自己写
                 keydown（那会漏掉空格、还会把按钮默认行为再触发一次）。内容与票 03 的弹层同一套
                 （同一份 `coverRowLabel` / `coverOthersText` / `coverBlockedText`），装笔也调同一个
                 函数，点完切回周表：那里才有格子可点。日期**固定今天**（ADR 0127 决策 3）；
                 今天没有要顶的档时整节不渲染，名单面板本来就有自己的空态。 -->
            <section v-if="rosterCoverRows.length" class="gCoverRoster">
              <div class="gCoverRoster-hd">
                <h3>今天要顶的档</h3>
                <span class="gCoverRoster-n">{{ rosterCoverRows.length }} 档</span>
              </div>
              <ul class="gCoverList">
                <li v-for="row in rosterCoverRows" :key="`${row.employee_id}|${row.shift_id}|${row.zone_id}`">
                  <button
                    class="gCoverRow"
                    type="button"
                    :disabled="!row.can_pen"
                    :aria-label="coverRowLabel(row)"
                    @click="penFromRosterCover(row)"
                  >
                    <span class="gCoverSlot">{{ row.zone_name || '（工作区已删）' }} × {{ row.shift_name }}</span>
                    <span class="gCoverMeta">
                      <b class="gCoverWho">{{ row.employee_name }}{{ row.leave ? '请假' : '休' }}</b>
                      <span class="gCoverOthers">{{ coverOthersText(row) }}</span>
                      <span v-if="!row.can_pen" class="gCoverBlocked">{{ coverBlockedText(row) }}</span>
                    </span>
                  </button>
                </li>
              </ul>
            </section>
            <p v-if="!roster.length" class="gB-empty">名单是空的</p>
            <div v-else class="gRosterGrid">
              <div v-for="employee in roster" :key="employee.id" class="gR">
                <div class="gR-main">
                  <b>{{ employee.name || employee.phone }}</b>
                  <span>{{ employee.job_title || '—' }}</span>
                  <span v-if="employee.resigned" class="gR-off">已离职</span>
                  <span v-else-if="!employee.approved" class="gR-off">待批准</span>
                </div>
                <!-- 规则摘要：本周 7 格（一…日），与周表同一套读法。 -->
                <div class="gR-week" :title="ruleLabel(employee.rule)">
                  <span
                    v-for="(cell, index) in ruleWeekCells(employee)"
                    :key="index"
                    class="gR-day"
                    :class="{ off: cell === '休' }"
                  ><i>{{ WEEK_HEADS[index] }}</i><b>{{ cell }}</b></span>
                  <span v-if="!ruleWeekCells(employee).length" class="gR-norule">
                    {{ employee.rule && employee.rule.invalid ? '规则坏了，重配一条' : '还没配规则' }}
                  </span>
                </div>
                <div class="gR-act">
                  <button
                    v-for="shift in activeShifts"
                    :key="shift.id"
                    type="button"
                    class="gBtn"
                    :disabled="busyEmployeeId === employee.id"
                    @click="setFixedShift(employee, shift.id)"
                  >固定{{ shift.name }}</button>
                  <button
                    v-if="employee.rule"
                    type="button"
                    class="gBtn ghost"
                    :disabled="busyEmployeeId === employee.id"
                    @click="clearRule(employee)"
                  >清空</button>
                  <button
                    type="button"
                    class="gBtn ghost"
                    :class="{ on: cycleOpenId === employee.id }"
                    :disabled="busyEmployeeId === employee.id"
                    @click="cycleOpenId === employee.id ? closeCycle() : openCycle(employee)"
                  >{{ cycleOpenId === employee.id ? '收起' : '编周期' }}</button>
                </div>
                <div v-if="cycleOpenId === employee.id" class="gC">
                  <input
                    v-model="cycleText"
                    class="gC-in"
                    type="text"
                    spellcheck="false"
                    :placeholder="cyclePlaceholder"
                    :disabled="busyEmployeeId === employee.id"
                    @keyup.enter="saveCycle(employee)"
                  >
                  <label class="gC-anchor">
                    <span>起点</span>
                    <!-- 日期用站内组件（2026-10-13 用户裁定「日期格式要中国本土化」）：原生
                         `<input type=date>` 的显示格式跟着**浏览器界面语言**走。 -->
                    <LuyunDatePicker
                      v-model="cycleAnchor"
                      placeholder="选择起点"
                      aria-label="轮转周期起点"
                    />
                  </label>
                  <div class="gC-act">
                    <button
                      type="button"
                      class="gBtn"
                      :disabled="busyEmployeeId === employee.id"
                      @click="saveCycle(employee)"
                    >保存周期</button>
                    <button type="button" class="gBtn ghost" @click="closeCycle()">取消</button>
                  </div>
                  <p class="gHint">空格隔开，一格一天，从起点那天算第 1 格；「休」= 那天不排班。起点留空就是今天。</p>
                  <p v-if="cycleError" class="gErr">{{ cycleError }}</p>
                </div>
                <div class="gZ">
                  <label v-for="shift in activeShifts" :key="shift.id" class="gZ-pick">
                    <span>{{ shift.name }}</span>
                    <select
                      class="gZ-sel"
                      :value="zoneOf(employee, shift.id)"
                      :disabled="busyEmployeeId === employee.id"
                      @change="setZoneDefault(employee, shift.id, $event.target.value)"
                    >
                      <option value="">未配区</option>
                      <option v-for="zone in zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
                    </select>
                  </label>
                  <span v-if="!zones.length" class="gZ-none">还没有工作区，先去卫生的工作区页面建一个</span>
                </div>
              </div>
            </div>
            <details class="gW-help">
              <summary>?</summary>
              <p class="gB-note">「固定一个班」和「编周期」（白白白夜夜休休）配的是同一样东西：一人一条轮转规则。规则改了只重排今天以后，过去的日子不动。</p>
            </details>
          </section>
        </template>
      </div>



      <!-- 选区里有过去的日子时，点应用弹**一次**确认并点名是哪几天（ADR 0108 决策 3）：
           不是每次应用都弹，也不是每格弹一次。 -->
      <ConfirmDialog
        v-if="pastConfirm"
        :title="pastConfirm.voiding ? '清空过去的日子' : '改过去的日子'"
        :message="pastConfirmText"
        :confirm-label="pastConfirm.voiding ? '照样清空' : '照改'"
        cancel-label="再想想"
        @confirm="runPaint(pastConfirm.action, pastConfirm.options, pastConfirm.cells, pastConfirm.who)"
        @cancel="pastConfirm = null"
      />
    </div>
  </div>
</template>

<style scoped>
/* B 案 v2 版式（2026-10-17）：页头 = kicker + H1 + 读数胶囊 + 视角 / 周期切换；月历是钢面
   方格（1px 线、--r-1、日期 mono、班次 = 色条 + 人数）；当天名单挪到右侧面板（面板头 LED
   + 人名列）；底部三条待处理改成列表行。周表 / 笔栏 / 选区 / 撤销的交互一个字不动。
   配色只走语义令牌；字体只分 sans / mono。 */
.sched-page {
  position: relative;
  /* **这里不能给 z-index**（原来那条 `z-index: 1` 已删）：一给就自建层叠上下文，把容器内
     `position: fixed` 的笔栏锁在这一层 —— 壳那条底栏（`WorkbenchTabBar`，`--z-shell`=20）
     会整个画在笔栏上面，70px 的笔栏被压掉 57px，等于废掉（票 12 的笔刷条正是这么坏的：
     ADR 0107 的背景里记着「真机截图里那句提示语被压掉一半」）。同一处口径见共享样式表
     `public/hygiene-admin.css` 里 `.hygiene-admin` 那条「这里**不能**给 z-index」。 */
  min-height: 100%;
}
.gB { min-height: 100%; padding-bottom: 28px; }
.gB-body {
  display: flex; flex-direction: column; gap: var(--sp-4);
  width: 100%; max-width: 1052px; margin: 0 auto;
  padding: 18px clamp(16px, 4vw, 32px) 22px;
}
/* ── 页头（3 行：kicker / H1 + 主操作同基线 / 范围 + 视角）─────────────── */
.gHead {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "kick kick" "title action" "meta meta";
  align-items: end; column-gap: var(--sp-4); row-gap: 7px;
}
.gKicker {
  grid-area: kick; margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 600; letter-spacing: .26em; color: var(--ink-faint);
}
.gTitleRow { grid-area: title; display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0; }
.gTitle {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.gHeadActions { margin-left: auto; display: flex; align-items: center; gap: var(--sp-2); }
.gLink { flex: none; min-height: var(--tap); background: transparent; border-color: transparent; color: var(--ink-dim); }
.gLink b { font-weight: 600; color: var(--ink); }
.gMetaRow {
  grid-area: meta; display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap;
  min-width: 0;
}
.gPeriod { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin: 0; }
.gPeriod b { font-family: var(--font-sans); font-size: 13px; font-weight: 600; color: var(--ink); }
.gPeriod span { font-family: var(--font-mono); font-size: 10px; color: var(--ink-dim); }
.gPeriod em { font-style: normal; font-family: var(--font-mono); font-size: 10px; color: var(--ink-faint); }
/* 桌面：翻月在面板头；手机：翻月与视角切换同一行（页头这份才出现）。 */
.gNav { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.gNav--head { display: none; margin-left: auto; }
.gNav--panel { margin-left: auto; }
.gSeg { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; }
.gMsg { margin: 0; font-size: 12px; color: var(--danger-bright); }
.gNote {
  margin: 0; padding: 8px 11px; border-radius: var(--r-1);
  border: 1px solid var(--wb-warn-a45, var(--line-strong)); background: var(--wb-warn-a10);
  font-size: 11.5px; line-height: 1.5; color: var(--ink-dim);
}
/* ── 面板（唯一卡片语言） ─────────────────────────────────────────────── */
.gPanel {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: var(--hy-shadow-sm); overflow: hidden;
}
.gPanel-hd {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border-bottom: 1px solid var(--line);
}
.gPanel-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.gPanel-hint { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1; color: var(--ink-faint); }
.gPanel-more { font-size: 10.5px; color: var(--ink-dim); }
/* 月历 / 周表全宽；名单面板沿用 gB-card 的挂点。 */
.gWeekPanel { overflow: visible; }
/* ── 月历：主面板 + 右侧当天名单 ──────────────────────────────────────── */
.gDash { display: grid; grid-template-columns: 1.6fr 1fr; gap: 14px; align-items: start; }
.gSide { display: flex; flex-direction: column; gap: 10px; }
/* 右栏顶/底两条：与面板同宽、单行 44px。 */
.gSide > .gPend {
  margin: 0; min-height: 44px; padding: 7px 12px;
  border: 1px solid var(--line); border-radius: var(--r-2);
  background: var(--surface-1);
}
.gSide > .gConfigGap { border-style: dashed; margin-top: auto; }
.gB-card { display: flex; flex-direction: column; }
/* 当天名单：固定高度内滚，头部不动。 */
.gB-dayBody { max-height: 460px; overflow-y: auto; padding-bottom: 10px; }
.gDrawerClose { display: none; margin-left: auto; min-height: var(--tap); padding: 0 10px; border: 1px solid var(--line-strong); border-radius: var(--r-1); background: transparent; color: var(--ink-dim); font: inherit; font-size: 10px; cursor: pointer; }
/* 待批请假影响的日子：日卡/笔栏之外的一条段（不是第四块面板）。 */
.gGap { margin-top: 12px; border-top: 1px solid var(--line); }
.gGap-hd {
  display: flex; align-items: baseline; gap: 10px; padding: 9px 14px 5px;
}
.gGap-hd span {
  font-family: var(--font-sans); font-size: 9px; font-weight: 600;
  letter-spacing: .18em; color: var(--ink-faint);
}
.gGap-hd em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 9px; color: var(--ink-faint); }
.gGap-empty { margin: 0; padding: 6px 14px 12px; font-size: 10.5px; color: var(--ink-faint); }
.gGapList { list-style: none; margin: 0; padding: 0 0 8px; }
.gGapList--scroll { max-height: 180px; overflow-y: auto; }
.gGapRow {
  display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center;
  gap: 10px; width: 100%; min-height: var(--tap); padding: 4px 14px;
  border: 0; background: transparent; color: var(--ink);
  font: inherit; text-align: left; cursor: pointer;
}
.gGapRow:hover { background: var(--surface-2); }
.gGapRow b { font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; }
.gGapN { font-family: var(--font-mono); font-size: 10px; color: var(--warn); }
.gGapGo { color: var(--ink-faint); }
.gCalPanel { padding-bottom: 12px; }
.gB-head { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; padding: 12px 14px 6px; }
.gB-head span {
  text-align: center; font-family: var(--font-sans); font-size: 8.5px;
  line-height: 1; letter-spacing: .18em; color: var(--ink-faint);
}
.gB-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; padding: 0 14px; }
/* 钢面方格：1px 线 + --r-1，日期 mono，班次在下面排成「色条 + 人数」。 */
.gB-d {
  position: relative; min-height: 64px; min-width: 0; padding: 6px 5px 5px;
  display: flex; flex-direction: column; gap: 5px;
  border: 1px solid var(--line); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink);
  font: inherit; text-align: left; cursor: pointer;
}
.gB-d:hover { border-color: var(--line-strong); }
.gB-d.mute { background: transparent; border-style: dashed; opacity: .35; }
.gB-d .n { font-family: var(--font-mono); font-size: 11px; line-height: 1; color: var(--ink-dim); }
.gB-d .c { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 7px; }
.gB-d .cItem {
  display: inline-flex; align-items: center; gap: 3px;
  font-family: var(--font-mono); font-size: 10px; line-height: 1;
  /* 人数与色条都用这个班次的色（0043）：`.tone-<键>` 给的是 `--fill`，没有 tone
     类时退回继承色（`.gB-d .cItem` 特异度比 `.tone-*` 高，必须在这里取变量）。 */
  color: var(--shift-fill, inherit);
}
.gB-d .cBar { width: 3px; height: 10px; border-radius: 1px; background: currentColor; flex: none; }
/* 0 人的班次段：灰条 + 淡字（不是「有一个 0」那么醒目的数字）。 */
.gB-d .cItem.is-empty { color: var(--ink-faint); opacity: .55; }
.gB-d .cItem.is-empty .cBar { background: var(--line-strong); }
.gB-d.today { border-color: var(--action); background: var(--action-soft); }
.gB-d.today .n { color: var(--action); font-weight: 700; }
/* 待批角标（spec US 11）：这天有等着批的申请。琥珀留给「待处理」、青点留给「这天被
   手改过」—— 两种记号各说各的事，不能混。 */
.gB-d .pend {
  position: absolute; top: 3px; right: 3px; min-width: 15px; height: 15px;
  padding: 0 3px; border-radius: var(--r-full); background: var(--warn); color: var(--action-ink);
  font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; line-height: 15px;
  text-align: center;
}
/* 半天休（票 07）：这天有几个人只上半天。摆在**左下**（右上留给待批那个圆点）、
   用信息蓝 —— 语义令牌，不新增 `--hy-*` 引用（设计护栏的存量棘轮只减不增）。 */
.gB-d .half {
  position: absolute; bottom: 3px; left: 3px; height: 13px; padding: 0 3px;
  border-radius: var(--r-full); border: 1px solid var(--info); color: var(--info);
  font-family: var(--font-mono); font-size: 9px; line-height: 11px;
}
.gB-d.sel { border-color: var(--action); box-shadow: 0 0 0 1px var(--action); }
/* 这天被单日覆盖改过（票 07）：右下角一个 6px 青点 —— 人数会变，但「改成休」那种改动
   在人数里根本看不出来，得有个跟人数无关的标记。放右下是为了不跟右上「待批」、左下
   「半天休」两个记号叠在一起。 */
.gB-d.over::after {
  content: ""; position: absolute; bottom: 5px; right: 5px; width: 6px; height: 6px;
  border-radius: 50%; background: var(--info);
}
/* 班次色（0043）：`tone-<色键>` 的两个变量声明已上移到 `theme.workbench.css`（班次表与
   员工整月也用同一套名字，各写一份会漂）。这里只留**本页的用法**：图例圆点与月历色条
   取 `--fill` 当文字色（周表格子那套实底在下面的 `.gW-cell.tone-*` 里另声明）。 */
.tone-moss, .tone-mint, .tone-tide, .tone-sky,
.tone-pine, .tone-rose, .tone-grass, .tone-lilac, .tone-neutral { color: var(--shift-fill); }
.gB-legend {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 14px 0; font-size: 10px; color: var(--ink-faint);
}
.gB-legend i {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  margin-right: 5px; vertical-align: middle; background: currentColor;
}
.gB-legend span { color: var(--ink-faint); }
.gB-legend .gB-ov i { background: var(--hy-aqua); }
.gB-legend .gB-pend i { background: var(--hy-amber); }
.gB-legend .gB-half i { background: var(--info); }
.gB-note { margin: 10px 14px 0; font-size: 10px; line-height: 1.7; color: var(--ink-faint); }
/* ── 当天名单（右侧面板） ─────────────────────────────────────────────── */
.gB-card {
  border: 1px solid var(--line); border-radius: var(--r-2);
  background: var(--surface-1); overflow: hidden;
}
.gB-card-hd {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border-bottom: 1px solid var(--line);
}
.gB-card-hd b {
  font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.gB-card-hd span { font-size: 10px; color: var(--ink-dim); }
.gB-card-hd em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint); }
.gB-grp { padding: 10px 14px 0; }
.gB-grp-t { display: flex; align-items: center; gap: 7px; font-size: 10px; margin-bottom: 4px; }
.gB-grp-t .d { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.gB-grp-t b { font-family: var(--font-sans); font-size: 10px; font-weight: 600; letter-spacing: .12em; }
.gB-grp-t i { font-style: normal; font-family: var(--font-mono); font-size: 10px; color: var(--ink); }
.gB-grp-t.off { color: var(--ink-faint); }  /* 「休」那一组：没有班次色，就是不在班上 */
.gB-grp-t.leave { color: var(--hy-aqua); }  /* 「请假」那一组（票 08）：批过的假，跟「休」分开 */
/* 人名列：一行一个人（44px 触控），名字在左、工作区在右。 */
.gB-names { display: flex; flex-direction: column; }
.gB-name {
  width: 100%; min-height: 44px; display: flex; align-items: center; gap: 8px;
  padding: 0 2px; border: 0; border-top: 1px solid var(--wb-ink-a08);
  background: transparent; color: var(--ink);
  font: inherit; font-family: var(--font-sans); font-size: 11.5px;
  text-align: left; cursor: pointer;
}
.gB-name:hover { background: var(--surface-2); }
.gB-name.over { color: var(--ink); }  /* 这天跟规则不一样 */
.gB-name.over::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%;
  background: var(--info);
}
.gB-name.on { background: var(--action-soft); color: var(--action); font-weight: 600; }
.gB-empty { margin: 0; padding: 10px 14px 14px; font-size: 11px; color: var(--ink-faint); }
/* 单日覆盖编辑器（票 07）。当前 / 信息态用 --action 描边。 */
.gD {
  margin: 10px 14px 14px; padding: 10px; display: flex; flex-direction: column; gap: 7px;
  border: 1px solid var(--action); border-radius: var(--r-2); background: var(--surface-2);
}
.gD-hd { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.gD-hd b { font-family: var(--font-sans); font-size: 12px; font-weight: 600; color: var(--ink); }
.gD-hd span { font-family: var(--font-mono); font-size: 10px; color: var(--ink-dim); }
.gD-hd em { margin-left: auto; font-style: normal; font-size: 9.5px; color: var(--info); }
.gD-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; color: var(--ink-faint); }
.gD-act { display: flex; gap: 6px; flex-wrap: wrap; }
.gD-hint { margin: 0; font-size: 10px; color: var(--ink-faint); line-height: 1.6; }
/* ── 待处理：三条列表行 ──────────────────────────────────────────────── */
.gTasks { display: flex; flex-direction: column; }
.gPend {
  display: grid; grid-template-columns: 24px minmax(0, 1fr) auto;
  grid-template-areas: "n b go" "n span go";
  align-items: center; gap: 2px 10px;
  width: 100%; min-height: 44px; padding: 10px 14px;
  border: 0; border-top: 1px solid var(--wb-ink-a08); background: transparent;
  color: var(--ink); font: inherit; text-align: left; cursor: pointer;
}
.gPend:first-child { border-top: 0; }
.gPend:hover { background: var(--surface-2); }
.gPend .n {
  grid-area: n; width: 24px; height: 24px; border-radius: var(--r-1);
  display: grid; place-items: center;
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 700;
  color: var(--action-ink); background: var(--warn);
}
.gPend b { grid-area: b; font-size: 11.5px; font-weight: 600; color: var(--ink); }
.gPend span { grid-area: span; font-size: 9.5px; line-height: 1.5; color: var(--ink-dim); }
.gPend .go { grid-area: go; color: var(--ink-faint); font-size: 14px; }
/* 请假待办（票 08）：同一根条换个色 —— 青=请假，琥珀=要配规则。
   两根条的活不一样：这根是去另一页批假，那根是在本页开名单。 */
.gTodo { border-color: var(--hy-aqua); background: transparent; }
.gTodo .n { color: var(--action-ink); background: var(--hy-aqua); }
.gTodo .go { color: var(--hy-aqua); }
/* ── 小控件 ───────────────────────────────────────────────────────────── */
.gBtn {
  font: inherit; font-family: var(--font-sans); font-size: 10.5px; color: var(--ink-dim);
  background: var(--surface-1); border: 1px solid var(--line-strong);
  border-radius: var(--r-1); padding: 6px 12px; min-height: var(--tap);
  cursor: pointer;
}
.gBtn:disabled { opacity: .45; cursor: default; }
.gBtn.ghost { color: var(--ink-faint); }
.gBtn.on { color: var(--action); border-color: var(--action); background: var(--action-soft); }
.gSeg .gBtn.on { color: var(--action); border-color: var(--action); background: var(--action-soft); }
.gAct {
  margin-left: auto; font: inherit; font-size: 10px; color: var(--action);
  border: 1px solid var(--action); background: var(--action-soft);
  border-radius: var(--r-full); padding: 4px 10px; cursor: pointer;
}
/* ── 名单（规则 / 固定区） ───────────────────────────────────────────── */
.gB-card-plain { margin-top: 0; }
.gB-card-plain .gB-card-hd span { font-size: 10px; color: var(--ink-dim); }
.gB-card-plain .gB-card-hd em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint); }
.gRosterGrid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px; padding: 14px; align-items: stretch;
}
.gR {
  display: flex; flex-direction: column; gap: 8px; min-width: 0;
  padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--r-2);
  background: var(--surface-2);
}
.gR-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.gR-main b { font-family: var(--font-sans); font-size: 12.5px; font-weight: 600; color: var(--ink); }
.gR-main span { font-size: 10px; color: var(--ink-faint); }
.gR-main .gR-off { color: var(--danger-bright); }
.gR-week {
  display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px;
}
.gR-day {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  padding: 4px 0; border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); min-width: 0;
}
.gR-day i { font-style: normal; font-size: 8px; color: var(--ink-faint); }
.gR-day b {
  font-family: var(--font-mono); font-size: 10px; font-weight: 600; color: var(--ink);
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.gR-day.off { border-style: dashed; }
.gR-day.off b { color: var(--ink-faint); }
.gR-norule { grid-column: 1 / -1; font-size: 10px; color: var(--warn); }
.gR-act { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
/* 每个班的固定工作区（票 03）。一行两个班次的下拉，窄屏自动换行。 */
.gZ { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 7px; }
.gZ-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; color: var(--ink-faint); }
.gZ-sel {
  font: inherit; font-family: var(--font-mono); font-size: 10.5px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line-strong);
  border-radius: var(--r-1); padding: 6px; min-height: var(--tap); cursor: pointer;
}
.gZ-sel:disabled { opacity: .45; cursor: default; }
.gZ-none { font-size: 10px; color: var(--ink-faint); }
/* 轮转周期编辑器（票 04）。原型 B 的名单行里没有这一块 —— 展开后长在那一行下面。 */
.gC {
  margin-top: var(--sp-2); padding: 9px 10px; display: flex; flex-direction: column; gap: 7px;
  border: 1px solid var(--line); border-radius: var(--r-2); background: var(--surface-2);
}
.gC-in {
  font: inherit; font-family: var(--font-mono); font-size: 12px; color: var(--ink);
  background: var(--surface-1); border: 1px solid var(--line-strong);
  border-radius: var(--r-1); padding: 6px 8px; min-height: var(--tap); letter-spacing: .06em;
}
.gC-anchor { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; color: var(--ink-faint); }
.gC-anchor input {
  font: inherit; font-size: 10.5px; color: var(--ink); background: var(--surface-1);
  border: 1px solid var(--line-strong); border-radius: var(--r-1); padding: 3px 6px;
}
/* 日期组件替掉原生 date（2026-10-13）：`.gC-anchor input` 那几条对组件里那个
   `.dp--input` 不生效（scoped 样式够不进子组件），所以要补的只有**尺寸** ——
   底色 / 边框 / 文字色共享表已经给了（`.hygiene-admin .luyun-date-picker .dp--input`），
   在这里再写一遍既多余、又会让这份文件的 `--hy-*` 引用数越过 designGuard 的棘轮。
   这是排班表里的一小格，字号与内边距得跟旁边的文字对齐。 */
.gC-anchor :deep(.luyun-date-picker .dp--input) {
  font: inherit; font-size: 10.5px; width: 140px; min-height: 0;
}
.gC-act { display: flex; gap: 6px; }
.gHint { margin: 0; font-size: 10px; color: var(--ink-faint); line-height: 1.6; }
.gErr { margin: 0; font-size: 10.5px; color: var(--danger-bright); line-height: 1.6; }
.gB-zone {
  margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 9.5px;
  color: var(--action);
}
.gB-zone.off { color: var(--ink-faint); }
.gB-zone.leave { color: var(--hy-aqua); }  /* 批过的假：跟「休」和「未配区」都不一个色 */
.gB-zone.half { color: var(--info); }  /* 休半天（票 07）：那天仍然上班，只是半天 */
/* ── 周表（员工 × 周，2026-09-30）────────────────────────────────────────
   版式：表头粘住、姓名列定宽、七列平分 —— 手机上不横向滚（跟月历那边同一个做法）。
   颜色只做辅助：格子里一定留一个字，因为 `SHIFT_TONES` 只有 4 个，第 5 个班次起会
   与第 1 个撞色（门店那套的图例就有 5 个班次）。 */
.gW-tip { font-size: 10px; color: var(--ink-faint); }
/* 筛人：一屏只放得下四行多，名单十几号人时靠翻页找人太慢（图片里那套也有这个框）。 */
.gW-findBtn {
  /* 原来靠 flex 的 `margin-left: auto` 推到右边；成员列改成 table-cell 之后它跟
     「成员」两个字同一行排下来（`vertical-align` 对齐），`margin-left` 只留一点点缝。 */
  margin-left: 4px; min-width: var(--tap); min-height: var(--tap); padding: 0;
  vertical-align: middle;
  border: 0; border-radius: var(--r-1);
  background: transparent; color: var(--ink-faint); font: inherit; font-size: 14px; line-height: 1; cursor: pointer;
}
.gW-find { display: flex; align-items: center; gap: 9px; padding: 8px 14px; border-bottom: 1px solid var(--line); }
.gW-hintLine { margin: 10px 14px 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-faint); }
.gW-help { display: inline; }
.gW-help[open] { display: block; border-top: 1px solid var(--line); margin-top: 4px; }
.gW-help summary {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; margin: 8px 14px; cursor: pointer;
  border: 1px solid var(--line-strong); border-radius: 50%;
  font-size: 10px; color: var(--ink-faint);
}
.gW-help .gB-note { margin: 0 14px 10px; }
.gW-find input {
  flex: 1; min-width: 0; min-height: var(--tap);
  font: inherit; font-family: var(--font-sans); font-size: 12px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line-strong);
  border-radius: var(--r-1); padding: 6px 12px;
}
.gW-find input::placeholder { color: var(--ink-faint); }
.gW-find span { flex: none; font-family: var(--font-mono); font-size: 10px; color: var(--ink-faint); }
.gW { width: 100%; table-layout: fixed; border-collapse: separate; border-spacing: 4px; }
.gW-cname { width: 4.6em; }
/* 表头粘住：滚下去以后还得知道哪一列是哪天（门店那套滚动时表头也在）。
   `top` 在宽屏要让开壳的顶栏（见文件末尾那条 `@media (min-width: 721px)`）——
   设成 0 会让日期行钻进 `.wb-top` 底下被盖住；手机上那条栏整条不显示，所以还是 0。 */
.gW thead th {
  position: sticky; top: 0; z-index: var(--z-shell);
  padding: 3px 0 5px; border-radius: var(--r-1);
  background: var(--surface-2);
  /* 粘住时**不能让根露出来**：这一格有 8px 圆角，格与格之间还有 4px 的 border-spacing，
     滚过去的内容会从两个圆角（对角缺口约 2.3px）和格缝里钻上来。外扩 4px 的同色阴影
     把这两处一起盖住 —— 看上去像表头连成一条，布局上仍是各自独立的 th。 */
  box-shadow: 0 0 0 4px var(--surface-2);
  font-family: var(--font-sans); font-weight: 500; font-size: 9px;
  letter-spacing: .06em; color: var(--ink-faint); line-height: 1.3;
}
.gW thead th .d { display: block; font-family: var(--font-mono); font-size: 12px; color: var(--ink); }
.gW thead th .m { display: block; font-style: normal; font-size: 9px; color: var(--ink-faint); }
/* 粘住以后这一格必须**不透明**：`--action-soft` 是 12% 的色，滚动时下面的格子会透上来
   （表头其余格本来就是实底 `--surface-2`）。把软色叠在实底上，深浅两档主题都成立。 */
.gW thead th.today {
  background-color: var(--surface-2);
  background-image: linear-gradient(var(--action-soft), var(--action-soft));
  /* 外扩那一圈与相邻格同色（不是绿），否则今天这一列会在表头里镶一圈绿边。 */
  box-shadow: 0 0 0 4px var(--surface-2), inset 0 0 0 1px var(--action);
}
.gW thead th.today .w { color: var(--action); }
.gW thead th.today .d { color: var(--action); font-weight: 700; }
/* 同上：`opacity` 会让整格（连底一起）半透明，粘住时透出下面的内容 —— 改成只降字色。 */
.gW thead th.mute { color: var(--ink-faint); }
.gW thead th.mute .d { color: var(--ink-faint); }
.gW thead th.weekend .w { color: var(--ink-dim); }
/* 表头与人名列是**手柄**（票 14 / ADR 0107 决策 9）：它们原来是完全不可交互的 <th>，
   现在是一个真按钮 —— 键盘 Tab 得到、读屏有名字，聚焦圈用浏览器默认那一圈（共享表没给
   这几个类写 focus 规则，这里也**不新写**：这一份的令牌配额只减不增）。 */
.gW-th {
  width: 100%; min-height: var(--tap); padding: 0; display: block; font: inherit; color: inherit;
  text-align: inherit; background: none; border: 0; cursor: pointer;
}
.gW-th:disabled { cursor: default; }
.gW-name {
  padding: 0 2px; text-align: left; vertical-align: middle;
  /* **不能是 flex**：`display: flex` 会让这个 `<th>` 不再是 table-cell，
     表头那一行里它的 `position: sticky` 于是完全不生效（2026-11-01 实测：
     同排的日期列都粘住了，只有这一格跟着内容滚出去）。名字 / 带圈数字 / 「没规则」
     本来就是 inline + block 排得出来，用不着 flex。 */
  font-family: var(--font-sans); font-weight: 500; font-size: 11px; color: var(--ink); line-height: 1.35;
  overflow-wrap: anywhere;
}
.gW-name b { font-weight: 600; }
/* 存假（2026-11-01 用户要的）：名字后面一个**带圈的数字**，紧跟着名字、不换行 ——
   圈里只放数（`36.5` / `4` / `−0.5`），「存假 / 欠班」与「天」交给颜色和悬停提示。
   正数（攒下的假）走 action 那一档绿，负数（欠班，ADR 0124）走 warn 橙；
   两档的 soft 底在深浅两个主题里都有声明，所以换主题不掉色。
   `min-width` 撑住一位数那个正圆，两位数 / 带小数时它自然长成胶囊。 */
.gW-leave {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 16px; height: 16px; padding: 0 5px; margin-left: 4px;
  border-radius: 999px; vertical-align: 1px;
  font-size: 9px; font-weight: 600; line-height: 1; font-variant-numeric: tabular-nums;
  background: var(--action-soft); color: var(--action);
}
.gW-leave.debt { background: var(--warn-soft); color: var(--warn); }
/* 没配规则的人整行都是空的 —— 行内就得说一句，不然看着像这店不给他排班。 */
.gW-norule { display: block; font-style: normal; font-size: 9px; color: var(--warn); }
.gW-cell {
  position: relative; width: 100%; min-height: var(--tap); padding: 0;
  display: grid; place-items: center;
  border: 1px solid transparent; border-radius: var(--r-1);
  background: var(--surface-2); color: var(--ink);
  font: inherit; font-family: var(--font-sans); font-size: 11.5px; cursor: pointer;
  /* 长按（票 03 / ADR 0127）：`touch-action: manipulation` 去掉双击缩放、把指针事件按时给到
     这一格；`-webkit-touch-callout: none` 压住 iOS 长按的系统菜单；`user-select: none`
     挡安卓的选中文字。三条少一条，真机上长按就变成系统菜单 / 选字（模板上的
     `@contextmenu.prevent` 是最后一道）。 */
  touch-action: manipulation;
  -webkit-touch-callout: none;
  user-select: none;
}
.gW-cell:disabled { cursor: default; }
/* 班次缩写（2026-11-01）：字色已经是「同色相深字」，再把字重与字号提一档 —— 11.5px 的
   常规体在亮底上，色相读不出来，看着像一格黑字。工作区那行不动（9px 再粗就糊）。 */
.gW-cell > span:first-child { font-size: 12.5px; font-weight: 700; letter-spacing: .01em; }
/* 按住 300ms 起的按压态（`PRESS_FEEDBACK_MS`）：一圈内描边 + 轻微缩小，不引入新颜色 ——
   格子的底色是班次色（0043），按压态不能跟「选中 / 今天 / 休半天」那几个记号抢。用
   `outline` 而不是 `box-shadow`：`.today` 与 `.on` 都拿 `box-shadow` 画环，撞上去就会被
   后定义的那条盖掉，今天那格反而没有按压反馈。 */
.gW-cell.press { outline: 2px solid var(--line-control); outline-offset: -2px; transform: scale(.96); }
.gW-cell.long { font-size: 10px; letter-spacing: -.02em; }
/* 班次下面那行工作区名（2026-10-08 用户要的）：格子只有 ~39px 宽，所以是 9px 的小字、
   跟着班次那个色弱一档 —— 主信息仍然是班次那一个字。三个字的区名（明档1）单行放得下，
   再长的截断，不让它撑破格子。没配区的格子这一行不渲染（见 `weekCellZone`）。 */
/* 周表格子里的「半」（票 07）：那天仍然挂着这个班次，只是只上半天 —— 跟在班次缩写
   后面，小一号、信息蓝（语义令牌）。 */
.gW-cell .gW-half {
  /* 「半」= 反白小胶囊（2026-11-01 用户反馈「休半天的『半』看不清」）：底色用班次字色、
     字用班次底色 —— 在任何一档上对比度都跟正文一样（≥ 4.7:1），而且一眼能看出它是
     「贴在班次上的标记」，不是班次名的一部分。原来这里是 9px 的 --info 蓝字，
     落在潮青/薄荷这些亮底上几乎看不见。 */
  display: inline-flex; align-items: center; justify-content: center;
  margin-left: 2px; padding: 1px 2px; border-radius: 3px;
  font-style: normal; font-family: var(--font-mono); font-size: 8.5px; line-height: 1;
  font-weight: 700; letter-spacing: 0;
  background: var(--shift-ink); color: var(--shift-fill);
}
.gW-cell .gW-zone {
  display: block; max-width: 100%; margin-top: 1px;
  /* 不压透明度（2026-11-01）：9px 的小字在 75% 下只剩 ~3.5:1，跟「看字更清晰」反过来。
     它跟班次缩写同一个色相，靠字号（9 vs 11.5）分主次，不靠变淡。 */
  font-size: 9px; line-height: 1.1; letter-spacing: -.02em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 班次格子（0043）：实底 + 深字 + 一根深发丝。8 档都是亮底，所以边界统一走
   `--shift-edge`，不再按档重复定义；文字色是每档自带的「同色相深一档」，实测最低 4.72:1。 */
.gW-cell.tone-moss, .gW-cell.tone-mint, .gW-cell.tone-tide, .gW-cell.tone-sky,
.gW-cell.tone-pine, .gW-cell.tone-rose, .gW-cell.tone-grass, .gW-cell.tone-lilac {
  color: var(--shift-ink);
  background: var(--shift-fill);
  border-color: var(--shift-edge);
}
/* 中性档：超出调色板的第 9 个班次、以及「迁移还没应用」时的降级。 */
.gW-cell.tone-neutral {
  color: var(--ink-dim); background: var(--shift-neutral); border-color: var(--line);
}
.gW-cell.off { color: var(--ink-faint); background: var(--wb-ink-a08); border-color: var(--line); }
/* 请假（0043）：以前借的是 aqua 那档班次色，于是「请假」看着像某个班次。
   现在退回中性底 + 信息蓝文字（`--info` 是语义令牌，「这天有改动/待批」也在用），
   跟「休」的灰、跟班次的彩色都分得开。 */
.gW-cell.leave { color: var(--info); background: var(--wb-ink-a04); border-color: var(--line-strong); }
/* 两种「空」分开：整列没有数据 / 还没铺到 = 虚线淡掉；这天有数据但这个人没排到 = 淡框。 */
.gW-cell.mute { background: transparent; border: 1px dashed var(--line); opacity: .5; }
.gW-cell.none { background: transparent; border: 1px dashed var(--line-strong); }
.gW-cell.today { box-shadow: inset 0 0 0 1px var(--action); }
.gW-cell.on, .gW-th.on { box-shadow: 0 0 0 2px var(--hy-mint); }
/* 这天被单日覆盖改过（票 07 的青点，跟月历同一个记号）。 */
.gW-cell.over::after {
  content: ""; position: absolute; top: 3px; right: 3px;
  width: 6px; height: 6px; border-radius: 50%; background: var(--info);
}
/* 这一列有申请等着批（票 02 的琥珀记号）：挂在表头上，一格一个数。 */
.gW thead th .pend {
  position: absolute; top: 0; right: 2px;
  min-width: 14px; height: 14px; padding: 0 3px;
  border-radius: var(--r-full); background: var(--warn); color: var(--action-ink);
  font-family: var(--font-mono); font-size: 9.5px; font-weight: 700;
  line-height: 14px; text-align: center; font-style: normal;
}
.gW-em { color: var(--ink); }
.gB-legend i.off { background: var(--ink-faint); }
.gB-legend i.leave { background: var(--hy-aqua); }
/* 周表这一栏右下角常驻一颗悬浮球（2026-10-29 用户要求）：页面只给它让出那一小圈，
   不再为整条笔栏留 76px。桌面与手机同一套；手机档壳另外让出工作台底栏（见
   `WorkbenchShell.vue` 的 `contentInsetStyle`），这里只管球自己那一段。 */
.gB.pen-week { padding-bottom: 92px; }
/* 班次多于 3 张时展开的面板会多占一行，但它是浮层、不参与页面排版，所以这一档不用再让位。 */

/* 笔栏上的每一颗棋子（班次 / 休 / 工作区 / ↺ / 摘要行的「刷」「清」）：一套外观，
   不另起第二套视觉。这一族从票 12 的笔刷条就在，原样留着。 */
.gChip {
  display: inline-flex; align-items: center;
  font: inherit; font-family: var(--font-sans); font-size: 12px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line-strong);
  border-radius: var(--r-full); padding: 7px 14px; cursor: pointer;
}
.gChip:disabled { opacity: .45; cursor: default; }
.gChip.on { box-shadow: 0 0 0 2px var(--action); }
.gChip.tone-moss, .gChip.tone-mint, .gChip.tone-tide, .gChip.tone-sky,
.gChip.tone-pine, .gChip.tone-rose, .gChip.tone-grass, .gChip.tone-lilac {
  color: var(--shift-ink); background: var(--shift-fill); border-color: var(--shift-edge);
}
.gChip.tone-neutral {
  color: var(--ink-dim); background: var(--shift-neutral); border-color: var(--line);
}
.gChip .dt { width: 9px; height: 9px; flex: none; margin-right: 7px; border-radius: 50%; background: currentColor; }
.gChip .dt.rest { background: var(--ink-faint); }
/* 休半天：圆点用 info（与图例里那个「半 = 休半天」同色），选中外环也走 info ——
   其余棋子仍用 `.gChip.on` 的 `--action`，这一颗是半天休自己的记号。 */
.gChip .dt.half { background: var(--info); }
.gPen-half.on { box-shadow: 0 0 0 2px var(--info); }
/* ── 排班笔：悬浮球 + 展开面板（2026-10-29 用户要求，替换原来的常驻笔栏）──────────
   原来是一条常驻在周表顶部的笔栏，太占地方；现在收成右下角一颗球、点球才展开。展开的是
   **浮层**（不参与页面排版），落笔交互一个字没改，只是把这条栏从文档流搬到了 fixed 层。
   层级：`fixed` + `--z-queue`（200），叠在底部导航之上（底栏是 `--z-shell`=20）。
   控件一律 44px 触控下限；颜色全蹭既有的 `--hy-*` / `.gChip` 那几族（`designGuard` 的棘轮只减不增）。 */
.gPick-fab {
  position: fixed; right: 18px; bottom: calc(18px + var(--safe-b));
  z-index: var(--z-queue);
  width: 58px; height: 58px; padding: 0;
  display: inline-flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  background: var(--surface-1);
  color: var(--ink);
  box-shadow: 0 10px 28px var(--wb-scrim);
  font: inherit; cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.gPick-fab:active { transform: scale(.96); }
.gPick-fab.open { border-color: var(--action); }
.gPick-fab .dt { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); }
.gPick-fab .dt.rest { background: var(--ink-faint); }
.gPick-fab .dt.half { background: var(--info); }
.gPick-fab .dt.clear { background: var(--ink); }
.gPick-fabText {
  max-width: 52px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-sans); font-size: 10.5px; line-height: 1; color: var(--ink-dim);
}
.gPick-fab.open .gPick-fabText { color: var(--action); }

/* 收起时的回执：球上方一条小气泡，几秒后自己消失（`sayNotice` 管计时）——不用为了看一眼
   「已清空 / 跳过几格」又把笔栏展开。 */
.gPick-toast {
  position: fixed; right: 18px; bottom: calc(88px + var(--safe-b)); z-index: var(--z-queue);
  display: flex; align-items: center; gap: 8px;
  max-width: min(340px, calc(100vw - 36px));
  padding: 10px 12px;
  border: 1px solid var(--line-strong); border-radius: var(--r-2);
  background: var(--surface-1);
  box-shadow: 0 10px 28px var(--wb-scrim);
  font-size: 10.5px; color: var(--ink-dim);
}

/* 展开面板：从球上方浮出。两列（左摘要 / 右笔），与旧版同构。 */
.gPick {
  position: fixed; right: 18px; bottom: calc(88px + var(--safe-b));
  z-index: var(--z-queue);
  width: min(880px, calc(100vw - 36px));
  /* 面板是**单列堆叠**（2026-10-29 用户截图：两列时选区摘要把右边那列挤到一百来像素，
     班次棋子被逼成一行一个竖排）。浮层高度不再是问题，通栏排下去最稳：
     提示 / 选中 / 选区摘要 / 工作区 / 笔，各占一行，笔那一行自己换行。 */
  display: flex; flex-direction: column; align-items: stretch;
  gap: 6px;
  padding: 8px 14px;
  background: var(--surface-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-2);
  box-shadow: 0 18px 46px var(--wb-scrim);
}
/* 展开 / 收起：从球的方向升起 8px，不缩放（缩放会让 44px 的棋子点起来抖）。 */
.gPick-enter-active, .gPick-leave-active { transition: opacity .16s ease, transform .16s ease; }
.gPick-enter-from, .gPick-leave-to { opacity: 0; transform: translateY(8px); }
/* 选区摘要 / 工作区 / 提示：通栏一行；提示与工作区各自换行。 */
.gPick-note, .gPick-detail, .gPick-zones { width: 100%; }

/* 班次按钮组**换行**，不横滑：面板是浮层，多一行不挤表格；横滑会把后面的「清 / 工作区 /
   ↺」藏起来，得滑一下才知道还有（2026-10-29 用户截图里那版更糟：两列时这一列被压到
   一百来像素，棋子竖排成一行一个）。 */
.gPick-row {
  display: flex; align-items: center; gap: 6px;
  flex-wrap: wrap;
}
/* `刷` 标签：不带颜色（继承正文色），只做个记号。 */
.gPen-tag { flex: none; font-size: 11px; letter-spacing: .08em; color: var(--ink-faint); }
/* 笔栏里所有控件（班次 / 休 / 工作区 / ↺ / 摘要行那两个）的触控下限；水平方向比通用 chip
   收一档，好让最常见的情况（两个班次 ＋ 休 ＋ 工作区 ＋ 撤销）在一行里排得下、不产生横滑。 */
.gPick-row .gChip, .gPick-region .gChip, .gPick-zones .gChip { min-height: 44px; flex: none; }
.gPick-row .gChip { padding: 7px 11px; }
.gPen-zone { max-width: 9em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 撤销那颗是圆形的：要压过上面那条 `.gPick-row .gChip` 的水平内边距（同权重才比顺序）。 */
.gPick-row .gPick-undo { min-width: 44px; padding: 0; justify-content: center; font-size: 15px; }
/* 工作区的展开行：也是一条横滑的按钮组（原生 `<select>` 的弹层在这个位置会飘到页面中间）。 */
.gPick-zones {
  display: flex; gap: 6px; overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}
.gPick-zones::-webkit-scrollbar { display: none; }
/* 选区摘要行（只在选了整列 / 整行时出现）：摘要让位给「刷」「清」两个按钮，窄屏截断。 */
.gPick-region { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.gPick-region .gW-tip {
  flex: 1 1 auto; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.gPick-sum { flex: none; font-family: var(--font-sans); font-size: 13px; font-weight: 600; letter-spacing: .06em; }
/* 一行提示：不加颜色（正文色），出错时蹭既有的 `.gErr`（朱砂）—— 那一族已经有了。 */
.gPick-now { margin: 0; max-width: min(100%, 420px); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-mono); font-size: 10px; color: var(--ink-dim); }
.gPick-note { margin: 0; display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--ink-dim); }
.gPick-note-text { flex: 1 1 auto; min-width: 0; }
.gPick-detail { display: flex; flex-direction: column; gap: 4px; }
/* ── 长按弹层「要顶的档」（票 03 / ADR 0127）────────────────────────────
   与月历那张当天名单抽屉（`.gDash--drawer .gSide` + `.gDrawerScrim`）**同一套位置口径**：
   贴底、圆角、`--z-dialog`，遮罩是 `--z-scrim`。壳底栏在 `--z-shell`，遮罩一盖就是「让位」，
   关掉自然「还位」—— 不再开一个 body 状态去挪底栏。
   位置口径写成 base 而不是只写在手机媒体查询里：长按在桌面（鼠标按住）同样有效，而
   `.gDrawerScrim` 那份只写了手机档 —— 遮罩这里补一条同值的 base（`.gCoverScrim`），
   否则桌面上弹层是悬空的，点外面关不掉。
   零新增颜色引用：只用 `--surface-*` / `--ink-*` / `--line-*` / `--wb-scrim` 这些既有令牌。 */
.gCoverScrim {
  position: fixed; inset: 0; z-index: var(--z-scrim);
  background: var(--wb-scrim);
}
.gCoverSheet {
  position: fixed; left: 0; right: 0; bottom: 0;
  z-index: var(--z-dialog);
  max-width: 520px; margin: 0 auto;
  max-height: 72vh; overflow-y: auto;
  padding: 14px 14px calc(14px + var(--safe-b));
  background: var(--surface-0);
  border-top: 1px solid var(--line-strong);
  border-radius: var(--r-2) var(--r-2) 0 0;
  box-shadow: 0 -18px 40px var(--wb-scrim);
}
.gCoverHd { display: flex; align-items: center; gap: 8px; }
.gCoverHd b { font-family: var(--font-sans); font-size: 12.5px; font-weight: 600; letter-spacing: .04em; color: var(--ink); }
.gCoverHint { font-size: 10.5px; color: var(--ink-dim); }
/* 「收起」在手机档由 `.gDrawerClose` 的媒体查询点亮；桌面档长按一样能用，所以这里补到所有宽度。 */
.gCoverSheet .gDrawerClose { display: inline-flex; }
.gCoverPast, .gCoverEmpty {
  margin: 10px 0 0; font-size: 11px; color: var(--ink-dim);
}
.gCoverPast { color: var(--info); }
.gCoverList { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.gCoverRow {
  width: 100%; min-height: var(--tap); padding: 9px 11px;
  display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--ink);
  font: inherit; font-family: var(--font-sans); text-align: left; cursor: pointer;
}
/* 不能装笔的行（停用班次 / 已删工作区）没有按压态：`disabled` 的按钮不冒 `:active`，
   右侧那行原因才是它要说的话。 */
.gCoverRow:disabled { cursor: default; opacity: .72; }
.gCoverRow:not(:disabled):active { background: var(--surface-3); }
.gCoverSlot { flex: 0 0 auto; font-size: 12px; font-weight: 600; letter-spacing: .02em; }
/* 右侧三行：谁休 / 请假、这个档还有谁、不能装笔的原因。窄屏也不折到左边来。 */
.gCoverMeta { margin-left: auto; min-width: 0; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.gCoverWho { font-size: 11.5px; font-weight: 600; white-space: nowrap; }
.gCoverOthers { font-size: 10.5px; color: var(--ink-dim); text-align: right; }
.gCoverBlocked { font-size: 10.5px; color: var(--ink-faint); }
/* 名单面板顶部那一节（票 04）—— 长按的键盘等价入口。行样式直接复用上面 `.gCoverRow`
   那一套（同一个 `.vue` 的 scoped 样式，两处都能用），这里只给节的壳：它要读起来是
   「另一件事」，不是人名列表最上面的一列人。零新增颜色引用，仍只用既有令牌。 */
.gCoverRoster {
  margin: 12px 14px 0; padding: 10px 12px;
  border: 1px solid var(--line); border-radius: var(--r-2);
  background: var(--surface-0);
}
.gCoverRoster-hd { display: flex; align-items: baseline; gap: 8px; }
.gCoverRoster-hd h3 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px;
  font-weight: 600; letter-spacing: .04em; color: var(--ink);
}
.gCoverRoster-n { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint); }
/* 列表在弹层里是节的第一块，上边距 10px；放进节里收一点，别把这块撑高。 */
.gCoverRoster .gCoverList { margin-top: 8px; }
/* ── 窄屏：单列 ───────────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .gDash { grid-template-columns: 1fr; }
  .gB-body { padding-bottom: 22px; }
  .gRosterGrid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  /* 笔栏在手机上铺满一行：留 12px 边，别贴着屏幕缘。球要**抬到底栏之上**（底栏 57px，
     见 `utils/workbenchInsets.js` 的 TAB_BAR_HEIGHT_PX），否则会压住「今天 / 人事 / 现场」；
     面板与回执再往上让出球的高度。 */
  .gPick { left: 12px; right: 12px; bottom: calc(57px + 84px + var(--safe-b)); width: auto; }
  .gPick-fab { right: 14px; bottom: calc(57px + 14px + var(--safe-b)); }
  .gPick-toast { left: 14px; right: 14px; bottom: calc(57px + 84px + var(--safe-b)); max-width: none; }
  .gHead {
    grid-template-columns: 1fr;
    grid-template-areas: "kick" "title" "meta";
  }
  /* 窄屏页头别只挤在左边：班次表靠右（与标题同一行的两端），「今天」靠右（与日期 / 人数
     同一行的两端），视角切换与翻周同一行、一左一右 —— 648 这种小窗宽度下右侧原来空出
     四百多像素，白瞎。用 flex 而不是 grid：`gPeriod` 要独占一行，grid 的隐式列会把它的
     宽度压到 421（第二行那两个按钮组各占一列）。 */
  .gHeadActions { margin-left: auto; }
  .gTitle { font-size: 22px; }
  .gMetaRow { display: flex; flex-wrap: wrap; gap: 7px; }
  .gPeriod { width: 100%; }
  .gPeriod em { margin-left: auto; }
  .gSeg { margin-left: 0; }
  .gNav--head { display: flex; margin-left: auto; }
  .gNav--panel { display: none; }
  .gB-d { min-height: 56px; padding: 5px 4px 4px; }
  .gB-d .cItem { font-size: 9px; }
  /* 当天名单走底部抽屉：点日期后从底部升起，点遮罩或「收起」关闭。 */
  .gSide { display: none; }
  .gDash--drawer .gSide {
    display: flex; position: fixed; left: 0; right: 0; bottom: 0;
    z-index: var(--z-dialog); max-height: 72vh; overflow-y: auto;
    padding-bottom: var(--safe-b);
    background: var(--surface-0);
    border-top: 1px solid var(--line-strong);
    border-radius: var(--r-2) var(--r-2) 0 0;
    box-shadow: 0 -18px 40px var(--wb-scrim);
  }
  .gDash--drawer .gSide > .gPend { display: none; }
  .gDash--drawer .gSide > .gB-card { border-radius: var(--r-2) var(--r-2) 0 0; border-left: 0; border-right: 0; }
  .gDrawerScrim {
    position: fixed; inset: 0; z-index: var(--z-scrim);
    background: var(--wb-scrim);
  }
  .gDrawerClose { display: inline-flex; }
  .gB-dayBody { max-height: none; }
}
/* 周表的横向滚动条**不能**加：`overflow-x: auto` 会让 `.gW-wrap` 变成滚动容器
   （另一轴 `visible` 被算成 `auto`），表头那个 `position: sticky` 于是相对它吸附 ——
   而它高度随内容撑开、自己永远不滚，结果是表头**完全不粘**（2026-11-01 实测：
   手机滚 682px / 桌面滚 527px 后 `th` 的 `top` 已经是 −412 / −254）。
   也不用担心横向溢出：`.gW` 是 `table-layout: fixed; width: 100%`，七列等分、不会撑宽。 */
@media (min-width: 721px) {
  /* 宽屏 `.wb-top` 粘在顶上：min-height 52 + 上内边距 6 + 1px 分隔线 = 55（实测）。
     ≤720px 那条栏 `display: none`，表头贴屏幕顶即可。 */
  .gW thead th { top: 55px; }
}
</style>
