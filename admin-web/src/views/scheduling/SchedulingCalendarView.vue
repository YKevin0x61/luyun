<script setup>
// 店长端「排班」。版式按原型的 B · 月历（`.scratch/scheduling/prototype/admin-variants.html`
// 的 `[data-variant="B"]`）：一屏是本月人手网格（每格写当天白班、夜班各几人），
// 下面一张卡是选中那天「谁上哪个班」，底下一根「还没配规则的人」的条。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
// 票 02 做「配固定班次」、票 03 加工作区、票 04 加轮转周期编辑、票 07 加单日覆盖
// （点当天卡里的一个人就地改那一天）、票 08 加底下那根「请假待办」的条
// （批假在 `/workbench/hr/inbox` 那一页：这一页只管排班怎么铺）；班次表的增删改在票 11。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
// 共享样式表由壳加载（`SchedulingLayout.vue`）：三个子页各加载一份会挂出重复的
// <link>，壳一层管住就跟卫生管理端一个做法。
import { useNudgePull } from '../../composables/useNudgePull'
import { mergeShiftList } from '../../utils/shiftTable'
import { eachDayInRange } from '../../utils/dateRange'
import { BRUSH_REST, BRUSH_SHIFT, brushPayload, canPaintOn } from '../../utils/schedulingBrush'


const router = useRouter()

// 周一开头（2026-09-30 全模块从周日开头改过来）：服务端 `_month_frame` 的 `lead` 与
// 员工端月历的 `MONTH_HEADS` 是同一套，三个地方必须一起动，否则同一页两个周框。
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
const WEEK_HEADS = ['一', '二', '三', '四', '五', '六', '日'] // 周表：表头就是这个顺序
// 每个班次一个颜色，按排序位循环取（原型里白班薄荷、夜班水青）。
// 六个 tone 覆盖到 6 个班次：门店那种「A/B/C + 主管A两头班 + 主管B两头班」是 5 个，
// 只有四个色时第 5 个会与第 1 个撞色（A班 与 主管B两头班 长得一模一样）。
// 撞色这件事**永远只能算辅助**：格子里一定留字，图例写全名。
const SHIFT_TONES = ['mint', 'aqua', 'amber', 'seal', 'violet', 'rose']

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
// 或固定工作区的选择里，店长选完只会吃一句 400。
const activeShifts = computed(() => shifts.value.filter((shift) => shift.is_active))

// 这个月有几天是被单日覆盖改过的（票 07）：>0 时图例里多一句「青点 = 这天有改动」，
// 免得那个点看起来像装饰。
const overriddenDays = computed(() =>
  ((calendar.value && calendar.value.days) || []).filter((day) => day.overridden > 0).length
)

// 没有班的人分两拨：批过假的（`leave`，服务层从覆盖记录的 `kind` 认出来）与本来就休的。
// 两者在结果表里长得一样（`shift_id` 都是空），只有这里分得开 —— 不然店长分不清
// 「这人请假了」和「这人今天本来就休」，也分不清它跟票 07 手动改成的休。
const offPeople = computed(() => (dayDetail.value && dayDetail.value.off_people) || [])
const leavePeople = computed(() => offPeople.value.filter((person) => person.leave))
const restPeople = computed(() => offPeople.value.filter((person) => !person.leave))

// 展开只铺未来若干天（长度归服务层的 `EXPANSION_DAYS`，票 02 的验收项）。翻到窗口尽头那个月时，
// 剩下一周注定是空的 —— 不说清楚的话，店长会把它读成「那天没人上班」。
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

function toneOf(shiftId) {
  const index = shifts.value.findIndex((item) => item.id === shiftId)
  return SHIFT_TONES[(index < 0 ? 0 : index) % SHIFT_TONES.length]
}

function toneClass(shiftId) {
  return `tone-${toneOf(shiftId)}`
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
    tone: toneOf(shift.id),
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
    if (seq === calendarSeq && !silent) loading.value = false
  }
}

async function loadDay(businessDate) {
  if (!businessDate) {
    dayDetail.value = null
    return
  }
  selectedDate.value = businessDate
  // 换一天先把上一天的人清掉（模板对 null 有「读取中…」兜底）：请求失败时表头已经是
  // 新那天、名单却还列着旧那天的人，店长会照着错的名单改班（审查发现的 A2）。
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
  if (next === 'roster') await loadRoster()
  // 周表按需读：默认落在月历上，没切过去就不发这个请求。
  else if (next === 'week') {
    await loadWeek(weekStart.value || mondayOf(today.value))
    // 读完再开笔袋：班次名单在这份数据里，先开就会面对一个空下拉。
    await openBrush()
  }
}

// 「还没配的人」= 在职（已批准、没停用）但一条规则都没有。名单面板和底下那根
// 提示条要用同一个口径，所以只写这一份纯函数。
function needsRule(employee) {
  // 「配了但读不出来」也要算进来：那种人在月历上同样不会有新班（`expand()` 会跳过他们），
  // 而名单上只写「还没配」的话，店长会以为是没配过 —— 两拨都是要处理的人。
  return (!employee.rule || employee.rule.invalid) && !employee.disabled && employee.approved
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
// 读不出来时置 `null`（**不是 0**）：显示成「全员都配好了」是一句假话，店长会照着它放心。
const pendingCount = ref(null)
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
// 不用后端再出一个接口。这条验收在票 02 与票 06 之间被转手两次、一直没人接，结果是店长
// 只能滑到底、点进待办页才知道有几条在等他批。
//
// 读不出来就**不标**（空表）：宁可少一个角标，也不能在一个没读到的日子上标错数字。
const pendingMarks = ref({})
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
  } catch (err) {
    pendingMarks.value = {}
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

// 标题写**日期区间**，不写「10 月第一周」：跨月周（9/28–10/4）那种写法天生有歧义。
const weekRangeLabel = computed(() => {
  const data = week.value
  if (!data) return ''
  const same = data.today ? data.start === mondayOf(data.today) : false
  return `${shortDate(data.start)}–${shortDate(data.end)}${same ? ' · 本周' : ''}`
})

async function loadWeek(start) {
  weekError.value = ''
  try {
    // `start` 空着就交给服务端按「今天所在周」算：首屏还没读到 today 时也点得动。
    const data = await api.get('/api/scheduling/week', { start: start || '' })
    week.value = data
    weekStart.value = data.start
    // 并集：周表这份带的是「这 7 天真有行的停用班次」，直接替换会把月历那边的班次列挤掉
    // （`mergeShiftList` 里写了为什么不能那么走）。
    if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
    // 翻到别的周就把抽屉收起来：它冻着的是上一周的某一天，留在屏幕上会让人以为
    // 改的是新这周的同一天（票 07 那条「换一天就收编辑器」是同一个道理）。
    if (sheet.value && !(data.days || []).some((day) => day.business_date === sheet.value.date)) {
      closeSheet()
    }
  } catch (err) {
    weekError.value = err.message || '周表读不出来'
  }
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
    on: !!sheet.value && sheet.value.employeeId === employee.id
      && sheet.value.date === day.business_date,
  }
}

function weekCellLabel(employee, day) {
  const cell = cellOf(employee, day)
  const when = `${day.month}月${day.day}日 周${WEEK_HEADS[day.weekday]}`
  if (!cell || !cell.scheduled) {
    return `${employee.name} ${when}：${blankReason(day, cell) || '还没排到'}`
  }
  const what = cell.shift_id === null || cell.shift_id === undefined
    ? (cell.leave ? '请假' : '休')
    : `${cell.shift_name}${cell.zone_name ? `，${cell.zone_name}` : '，未配工作区'}`
  return `${employee.name} ${when}：${what}${cell.overridden ? '，已手动调整' : ''}`
}

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
  if (data.excluded.disabled) parts.push(`${data.excluded.disabled} 人已停用`)
  if (data.excluded.pending) parts.push(`${data.excluded.pending} 人还没批准`)
  return parts.length ? `另有 ${parts.join('、')}，不在表里（去「名单」看）。` : ''
})

// ── 底部抽屉：点一格从底下弹出来选班次（照门店那套的做法）────────────────────
// 与图片那套的一处刻意不同：抽屉弹出来时给页面**留出底部空间**（`.gB.sheet-open` 的
// padding-bottom）—— 不这么做，它压住的正是下面几行员工，而那几行恰好是店长要对照的。
const sheet = ref(null) // { employeeId, date } | null
const sheetTab = ref('shift') // 'shift' | 'cycle'
const sheetFolded = ref(false)
const sheetError = ref('')
const sheetBusy = ref(false)
// 抽屉里的「工作区」下拉（2026-10-04 补）：区字符串 id，'' = 跟这个班次的固定区。
// 原先抽屉里改不了区，店长得跳去月历那页点这个人才行 —— 周表上发现"今天他不在案板"，
// 却要换个页面才改得动，路太绕。写路径没变：还是单日覆盖那条（带上当天的班次一起提交）。
const sheetZone = ref('')

// ── 笔刷：抽屉折叠成那一行时手里的笔（2026-10-04 用户要的交互）──────────────
// 折叠**不只是收起来**：折叠之后表上单击 = 落笔，长按 = 展开看/改这一格。
// 为什么要分：给一个人改一天原来是三步（点格子 → 抽屉 → 选班次），排一周要来回二十几次；
// 但「这一格现在到底是什么」也必须有地方去，所以长按留着。
const brushKind = ref(BRUSH_SHIFT) // 'shift' | 'rest'
const brushShiftId = ref(null) // 班次 id；null = 还没选，落笔前先提示
const brushZoneId = ref('') // 工作区 id 字符串；'' = 跟这个班次的固定区

const sheetEmployee = computed(() => {
  if (!sheet.value) return null
  return weekEmployees.value.find((item) => item.id === sheet.value.employeeId) || null
})

const sheetDay = computed(() => {
  if (!sheet.value) return null
  return weekDays.value.find((day) => day.business_date === sheet.value.date) || null
})

const sheetCell = computed(() => {
  const employee = sheetEmployee.value
  const day = sheetDay.value
  return employee && day ? cellOf(employee, day) : null
})

const sheetIsRest = computed(() => {
  const cell = sheetCell.value
  return !!cell && cell.scheduled && (cell.shift_id === null || cell.shift_id === undefined) && !cell.leave
})

// 过去的日子不改写（口径 5）：那天可以看，但不能改 —— 服务端也会拒（`past_day`）。
const sheetUndoable = computed(() => {
  const data = week.value
  return !!(sheet.value && data && data.today && sheet.value.date >= data.today)
})

const sheetNowText = computed(() => {
  const cell = sheetCell.value
  if (!cell || !cell.scheduled) return blankReason(sheetDay.value, cell) || '还没排'
  if (cell.shift_id === null || cell.shift_id === undefined) return cell.leave ? '请假' : '休'
  return `${cell.shift_name}${cell.zone_name ? ` · ${cell.zone_name}` : ' · 未配区'}`
})

// 只有"这天真有班次"才谈得上工作区：休、请假、还没排到的格子，区是没有意义的。
const sheetHasShift = computed(() => {
  const cell = sheetCell.value
  return !!cell && cell.scheduled && cell.shift_id !== null && cell.shift_id !== undefined
})

/** 下拉停在哪一项：按 **id** 预填（区名没有唯一约束，按名字反查会指到别人身上）。 */
function syncSheetZone() {
  const cell = sheetCell.value
  sheetZone.value = cell && cell.zone_id !== null && cell.zone_id !== undefined
    ? String(cell.zone_id)
    : ''
}

/** 班次名单是异步来的：到货时给笔补一个默认班次（早班）。 */
watch(activeShifts, () => {
  if (sheetFolded.value) ensureBrushShift()
})

const sheetRuleText = computed(() => {
  const employee = sheetEmployee.value
  if (!employee) return ''
  const row = roster.value.find((item) => item.id === employee.id)
  if (row) return ruleLabel(row.rule)
  return employee.has_rule ? '（去「名单」看这条规则）' : '还没配规则'
})

async function openSheet(employee, day) {
  // 点同一格 = 收起来：抽屉不会自己走开，得有个关掉的手势。
  const same = sheet.value && sheet.value.employeeId === employee.id
    && sheet.value.date === day.business_date
  if (same) {
    closeSheet()
    return
  }
  sheet.value = { employeeId: employee.id, date: day.business_date }
  sheetTab.value = 'shift'
  sheetFolded.value = false
  sheetError.value = ''
  // 工作区名单只在名单面板里读过：**先把名单补齐，再决定下拉停在哪一项**。反过来的话，
  // 这个会话里第一次开抽屉时 `zones` 还是空的 → 下拉里没有选项 → 保存时区被悄悄退回
  // 固定区（跟票 07 当天编辑器踩过的是同一个坑）。
  if (!zones.value.length) await loadRoster()
  syncSheetZone()
  syncBrushFromCell()
}

/** 打开某一格时把笔**跟着这一格走**：展开看的是它，折叠起来刷的就是"跟它一样"。
 *  格子里没有班次（休 / 假 / 还没排）时不动笔 —— 看了一眼休假日就把笔也换成"休"，
 *  接着往下刷会把别人刷成休，那不是店长要的。 */
function syncBrushFromCell() {
  const cell = sheetCell.value
  if (cell && cell.scheduled && cell.shift_id !== null && cell.shift_id !== undefined) {
    brushShiftId.value = cell.shift_id
    brushZoneId.value = cell.zone_id === null || cell.zone_id === undefined ? '' : String(cell.zone_id)
    brushKind.value = BRUSH_SHIFT
    return
  }
  ensureBrushShift()
}

/** 笔还没选班次时给一个默认值（第一个在用的班次）：进周表笔袋就是开的，
 *  总不能让店长先面对一个"选班次"的空下拉再点格子。班次表还没读回来时先留空，
 *  读回来了由下面那个 watch 补上。 */
function ensureBrushShift() {
  if (brushShiftId.value === null && activeShifts.value.length) {
    brushShiftId.value = activeShifts.value[0].id
  }
}

/** 开笔袋（2026-10-05 用户要的：**一进周表它就是开的**）。
 *
 *  不用先点一格再收起 —— 一进周表底部就是那支笔，选好班次/工作区直接点格子。
 *  `sheet` 置空：这时候还没有"正在看的那一格"，长按哪一格才展开到哪一格。 */
async function openBrush() {
  sheet.value = null
  sheetFolded.value = true
  sheetError.value = ''
  // 工作区名单跟抽屉是同一份、也是懒加载的：不先补齐，笔袋里那个下拉就只有
  // 「跟固定区」一项 —— 店长选不到区，会以为笔刷不支持选区。
  if (!zones.value.length) await loadRoster()
  ensureBrushShift()
}

function closeSheet() {
  sheet.value = null
  sheetFolded.value = false // 收起 = 连笔袋一起收（不然关不干净）
  sheetError.value = ''
}

async function openCycleTab() {
  sheetTab.value = 'cycle'
  // 周期那栏要的是这个人的规则文本，规则在名单那份数据里（`/roster`）：没读过就补一次，
  // 不为它多开一个接口。
  if (!roster.value.length) await loadRoster()
}

/** 抽屉里改完一天：重读当周（格子与抽屉里的现状都跟着变），再把月历那一份人数补上。 */
async function refreshAfterWeekWrite(date) {
  await loadWeek(week.value ? week.value.start : weekStart.value)
  // 抽屉里的「工作区」下拉按服务端**回写后的结果**重新落位：不给区时后端会回填这个班次的
  // 固定区，下拉不能停在店长刚才点的那个值上（那会跟格子里显示的对不上）。
  syncSheetZone()
  // 月历那一份只补**人数**，不动选中那天（`loadCalendar` 会把选中拨回今天/1 号，
  // 店长从周表切回月历不该发现自己在月历上挑的那天被换掉了）。
  await refreshMonthCounts()
  if (date && date === selectedDate.value) await loadDay(date)
}

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

/** 写一天的覆盖。抽屉里点班次、表上落笔，走的都是这一条 —— 不新开接口。 */
async function writeOverride(employeeId, date, payload) {
  sheetBusy.value = true
  sheetError.value = ''
  try {
    await api.put(`/api/scheduling/overrides/${employeeId}/${date}`, payload)
    await refreshAfterWeekWrite(date)
  } catch (err) {
    // 服务端把「为什么改不了」说全了（过去的日子、还没铺到的天、休不能带班次…），
    // 原话转给店长，别拿一句「保存失败」盖掉。
    sheetError.value = err.message || '这一天没改上'
  } finally {
    sheetBusy.value = false
  }
}

async function writeCell(payload) {
  const target = sheet.value
  if (!target) return
  await writeOverride(target.employeeId, target.date, payload)
}

function pickShift(shift) {
  // 工作区留空 = 跟这个班次的固定区（票 03）。要单独改这一天在哪个区，去月历那页点这个人
  // —— 票 07 的编辑器管那件事，职责没变。
  writeCell({ shift_id: shift.id, zone_id: null })
}

function pickRest() {
  writeCell({ is_rest: true })
}

/** 只改这一天的区：班次不动，把当天那个班次一起提交（后端「只改区」走的就是这条路）。 */
function pickZone(value) {
  const cell = sheetCell.value
  if (!cell || cell.shift_id === null || cell.shift_id === undefined) return
  sheetZone.value = value
  writeCell({ shift_id: cell.shift_id, zone_id: value === '' ? null : Number(value) })
}

function toggleBrushRest() {
  if (brushKind.value === BRUSH_REST) {
    brushKind.value = BRUSH_SHIFT
    if (brushShiftId.value === null && activeShifts.value.length) {
      brushShiftId.value = activeShifts.value[0].id
    }
    return
  }
  brushKind.value = BRUSH_REST
}

/** 点一格 = 落笔。落完**不关抽屉**：下一格接着点 —— 这就是这个交互的全部意义。 */
async function paintCell(employee, day) {
  if (!canPaintOn(day.business_date, week.value ? week.value.today : '')) {
    sheetError.value = '过去的日子不改写：往前翻只能看。'
    return
  }
  const payload = brushPayload({
    kind: brushKind.value,
    shiftId: brushShiftId.value,
    zoneId: brushZoneId.value,
  })
  if (!payload) {
    sheetError.value = '先在上面选一个班次（或点「休」），再点格子。'
    return
  }
  await writeOverride(employee.id, day.business_date, payload)
}

// 长按 = 展开看/改这一格。用 pointer 计时，`@click` 仍是唯一语义入口：
// 长按已经处理过，就把它后面那一次 click 吞掉 —— 不然会"开了抽屉又立刻落笔"。
const LONG_PRESS_MS = 550
let pressTimer = null
let pressFired = false

function onCellDown(employee, day) {
  pressFired = false
  if (!sheetFolded.value) return // 展开时点一下本来就是开抽屉
  pressTimer = setTimeout(() => {
    pressTimer = null
    pressFired = true
    unfoldToCell(employee, day)
  }, LONG_PRESS_MS)
}

function cancelCellPress() {
  if (pressTimer) {
    clearTimeout(pressTimer)
    pressTimer = null
  }
}

function onCellClick(employee, day) {
  if (pressFired) {
    pressFired = false
    return
  }
  if (sheetFolded.value) {
    paintCell(employee, day)
    return
  }
  openSheet(employee, day)
}

async function unfoldToCell(employee, day) {
  const same = sheet.value && sheet.value.employeeId === employee.id
    && sheet.value.date === day.business_date
  sheetFolded.value = false
  sheetError.value = ''
  if (same) return // 就是当前这一格：展开就行，别再走一次"同一格 = 收起来"
  await openSheet(employee, day)
}

async function clearCell() {
  const target = sheet.value
  if (!target) return
  sheetBusy.value = true
  sheetError.value = ''
  try {
    await api.delete(`/api/scheduling/overrides/${target.employeeId}/${target.date}`)
    await refreshAfterWeekWrite(target.date)
  } catch (err) {
    sheetError.value = err.message || '没撤掉'
  } finally {
    sheetBusy.value = false
  }
}

/** 「周期」栏那句「去名单改」：把人带到名单页，并直接展开他的周期编辑器。 */
async function gotoRoster() {
  const id = sheet.value && sheet.value.employeeId
  closeSheet()
  await openPanel('roster')
  const row = roster.value.find((item) => item.id === id)
  if (row) openCycle(row)
}

// 图例里那条「有申请等着批」：有角标才显示（没角标时图例列一个用不上的记号反而费解）。
const hasPendingMarks = computed(() => Object.keys(pendingMarks.value).length > 0)

function ruleLabel(rule) {
  // 三种状态分开说：没配过（`null`）/ 配了但读不出来（`invalid`）/ 正常。
  // 以前 `!rule.cycle` 一律说「还没配」—— 规则坏掉的人被显示成没配过，店长照着配一遍
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
// 一格 = 一个营业日，格子里是班次名或「休」。店长在输入框里按天写下来（空格隔开），
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
// 名字取的是**还在用**的那份（`activeShifts`）：示例里出现一个停用班次，等于教店长
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
  // 拦下来（它只认启用班次），店长照样得把它改掉。
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
// 别让店长对着「排班参数不合法」猜。
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
  // 店长写出一个保存必失败的周期。
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
// 所以再把店长刚才改的那天读回来（他刚改完还要看结果）。
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
    // 原话转给店长，别拿一句「保存失败」盖掉。
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
  monthValue.value = currentMonthValue()
  await loadCalendar(monthValue.value)
  await refreshNotes()
})

// 实时（票 10 收尾）：员工提了申请、撤回了、或者改动了排班 —— 月历、待批角标与底下
// 那根条一起重拉。nudge 不带数据，所以照惯例重读；静默刷新，别每来一条就闪一下。
useNudgePull({
  id: 'scheduling-calendar',
  topics: ['scheduling'],
  pull: async () => {
    await loadCalendar(monthValue.value, true)
    await refreshNotes()
    // 周表开着的时候它也得跟着动：员工那边换了班，店长盯着的正是这张表。
    if (panel.value === 'week') await loadWeek(weekStart.value)
  },
})
</script>

<template>
  <div class="hygiene-admin sched-page">
    <div class="gB" :class="{ 'sheet-open': !!sheet && !sheetFolded, 'brush-open': sheetFolded && panel === 'week' }">
      <div class="gB-body">
        <div class="gB-mon">
          <b v-if="panel === 'week'">{{ weekRangeLabel || '本周' }}</b>
          <b v-else>{{ monthTitle(monthValue) }}</b>
          <span v-if="panel === 'week'">{{ weekEmployees.length }} 人 × 7 天</span>
          <span v-else>{{ shifts.map((s) => s.name).join(' / ') }} 人数</span>
          <em>{{ formatTodayLabel() }}</em>
        </div>

        <div class="gB-bar">
          <!-- 三个视角：月历（哪天缺人）· 周表（这个人这周怎么上）· 名单（配规则）。 -->
          <div class="gSeg">
            <button class="gBtn" :class="{ on: panel === 'month' }" type="button" @click="openPanel('month')">月历</button>
            <button class="gBtn" :class="{ on: panel === 'week' }" type="button" @click="openPanel('week')">周表</button>
            <button class="gBtn" :class="{ on: panel === 'roster' }" type="button" @click="openPanel('roster')">名单</button>
          </div>
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

        <p v-if="errorText" class="gMsg">{{ errorText }}</p>

        <template v-if="panel === 'month'">
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
              @click="loadDay(day.business_date)"
            >
              <span class="n">{{ day.day }}</span>
              <span class="c">
                <template v-for="(item, index) in cellCounts(day)" :key="item.id">
                  <em v-if="index">·</em>
                  <b v-if="!index" :class="item.tone">{{ item.count }}</b>
                  <i v-else :class="item.tone">{{ item.count }}</i>
                </template>
              </span>
              <!-- 待批角标（spec US 11）：这天有等着批的申请。票 07 定标记时特意把琥珀留给
                   「待处理」、青点留给「这天被改过」—— 两种记号不能混。 -->
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
            <span v-if="hasPendingMarks" class="gB-pend"><i></i>有申请等着批</span>
          </div>
          <p v-if="beyondNote" class="gB-note">{{ beyondNote }}</p>

          <div class="gB-card">
            <div class="gB-card-hd">
              <b>{{ formatDayLabel(selectedDate) }}</b>
              <span v-if="selectedDate && selectedDate === today">今天</span>
              <em>合计 {{ dayDetail ? dayDetail.total : 0 }} 人</em>
            </div>
            <template v-if="dayDetail">
              <div v-for="group in dayDetail.groups" :key="group.shift.id" class="gB-grp">
                <div class="gB-grp-t" :class="toneOf(group.shift.id)">
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
                    <em v-if="person.zone" class="gB-zone">{{ person.zone }}</em>
                    <em v-else class="gB-zone off">未配区</em>
                  </button>
                </div>
                <p v-else class="gB-empty">这天没人排{{ group.shift.name }}</p>
              </div>
              <!-- 批过假的人自成一组（票 08）：跟「本来就休」在数据里同形（都没有班次），
                   只有 `person.leave` 分得开。混在一个「休」里，店长就分不清
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
                这天的改动已经是历史了：撤销只对今天以后的日子生效，过去怎么排就怎么留着。
              </p>
              <p class="gD-hint">
                改的是这一天，不是规则：规则以后怎么变都不动它。撤掉覆盖，这天就回到规则铺出来的样子。
              </p>
              <p v-if="editError" class="gC-err">{{ editError }}</p>
            </div>
          </div>

          <button class="gPend gTodo" type="button" @click="router.push('/workbench/hr/inbox')">
            <span class="n">假</span>
            <b>请假待办</b>
            <span>员工提的请假在这儿批：批完那天记成请假，先看清批了还剩几个人</span>
            <span class="go">›</span>
          </button>

          <button class="gPend" type="button" @click="router.push('/workbench/hr/shifts')">
            <span class="n">班</span>
            <b>班次表</b>
            <span>加一个班次、改名字、调顺序、停用 —— 加完月历和员工卡片自己就多一种班别</span>
            <span class="go">›</span>
          </button>

          <button class="gPend" type="button" @click="openPanel('roster')">
            <span class="n">{{ pendingCount === null ? '—' : pendingCount }}</span>
            <b>个人还没配规则</b>
            <span>{{ pendingText }}</span>
            <span class="go">›</span>
          </button>
        </template>

        <!-- 周表：员工 × 周。列是七天，行是人；点一格从底下弹抽屉选班次。 -->
        <template v-else-if="panel === 'week'">
          <p v-if="weekError" class="gMsg">{{ weekError }}</p>
          <p v-if="!week" class="gB-empty">周表读取中……</p>
          <template v-else>
            <div class="gW-find">
              <input v-model="weekFind" type="search" placeholder="搜成员" spellcheck="false">
              <span v-if="weekFind.trim()">{{ weekRows.length }} 人 · 筛掉 {{ weekFilteredOut }} 人</span>
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
                    <th class="gW-name">成员</th>
                    <th
                      v-for="day in weekDays"
                      :key="day.business_date"
                      :class="{ today: day.is_today, weekend: day.is_weekend, mute: !day.in_window }"
                    >
                      <span class="w">周{{ WEEK_HEADS[day.weekday] }}</span>
                      <span class="d">{{ day.day }}</span>
                      <em v-if="day.month !== week.days[0].month">{{ day.month }}月</em>
                      <!-- 待批的天数是一天一条事实，挂在**这一列的表头**上；挂在每一格里
                           会变成 11 个一模一样的点（一天 11 个人）。 -->
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
                      <b>{{ employee.name }}</b>
                      <!-- 没配规则的人整行都是空的 —— 不在这里说一句，看着像这店不给他排班。 -->
                      <em v-if="!employee.has_rule" class="gW-norule" title="这个人还没有轮转规则">没规则</em>
                    </th>
                    <td v-for="day in weekDays" :key="day.business_date">
                      <button
                        type="button"
                        class="gW-cell"
                        :class="weekCellClass(employee, day)"
                        :aria-label="weekCellLabel(employee, day)"
                        :disabled="!day.in_window"
                        @pointerdown="onCellDown(employee, day)"
                        @pointerup="cancelCellPress()"
                        @pointerleave="cancelCellPress()"
                        @pointercancel="cancelCellPress()"
                        @contextmenu.prevent
                        @click="onCellClick(employee, day)"
                      >
                        <span>{{ weekCellText(employee, day) }}</span>
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
              <span v-if="hasPendingMarks" class="gB-pend"><i></i>有申请等着批</span>
            </div>

            <p v-if="weekNoDataNote" class="gB-note">{{ weekNoDataNote }}</p>
            <p v-if="weekBeyondDays.length" class="gB-note">
              这一周里有 {{ weekBeyondDays.length }} 天超出了展开窗口（只铺到 {{ week.window_end }}），
              那几格还是虚的。
            </p>
            <p class="gB-note">
              点一格从底下选班次；「清除班次」只把<span class="gW-em">手改过</span>的那天退回规则。
              改轮转规则（白白白夜夜休休）在「名单」里，它跟单日改动是两件事。
            </p>
            <p v-if="weekExcludedNote" class="gB-note">{{ weekExcludedNote }}</p>
          </template>
        </template>

        <template v-else-if="panel === 'roster'">
          <div class="gB-card gB-card-plain">
            <div class="gB-card-hd">
              <b>名单 · 规则</b>
              <span>一人一条</span>
              <em>{{ roster.length }} 人</em>
            </div>
            <div v-for="employee in roster" :key="employee.id" class="gR">
              <div class="gR-main">
                <b>{{ employee.name || employee.phone }}</b>
                <span>{{ employee.job_title || '—' }}</span>
                <span v-if="employee.disabled" class="gR-off">已停用</span>
                <span v-else-if="!employee.approved" class="gR-off">待批准</span>
                <em :class="{ on: employee.rule }">{{ ruleLabel(employee.rule) }}</em>
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
                  <input v-model="cycleAnchor" type="date">
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
                <p class="gC-hint">空格隔开，一格一天，从起点那天算第 1 格；「休」= 那天不排班。起点留空就是今天。</p>
                <p v-if="cycleError" class="gC-err">{{ cycleError }}</p>
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
            <p v-if="!roster.length" class="gB-empty">名单是空的</p>
            <p class="gB-note">「固定一个班」和「编周期」（白白白夜夜休休）配的是同一样东西：一人一条轮转规则。规则改了只重排今天以后，过去的日子不动。</p>
          </div>
        </template>
      </div>

      <!-- 底部抽屉（照门店那套排班页的做法）：点周表里的一格，从底下弹出来选班次。
           与那套的两处刻意不同：① 弹出来时页面留出底部空间（`.gB.sheet-open`），不然它
           压住的正是下面几行员工；② 不做顶栏那个「保存」—— 本模块是**即时写**，
           点一下就落库，多一个保存按钮只会让人以为不点就不生效。 -->
      <div
        v-if="(sheet || sheetFolded) && panel === 'week'"
        class="gSheet"
        role="dialog"
        :aria-label="sheetFolded ? '笔刷' : '改这一天'"
      >
        <div class="gSheet-bar">
          <button
            v-if="sheet"
            class="gSheet-fold"
            type="button"
            :aria-expanded="sheetFolded ? 'false' : 'true'"
            :title="sheetFolded ? '展开' : '收起'"
            @click="sheetFolded = !sheetFolded"
          >{{ sheetFolded ? '⌃' : '⌄' }}</button>
          <!-- 折叠着 = 手里有笔：这一行就是笔刷条。班次 / 周期那两栏是"看这一格"用的，
               折叠时让位给笔刷控件；展开回去它们还在。 -->
          <template v-if="sheetFolded">
            <span class="gBrush-tag">刷</span>
            <select
              v-model="brushShiftId"
              class="gZ-sel"
              aria-label="笔刷班次"
              :disabled="brushKind === BRUSH_REST"
            >
              <option :value="null" disabled>选班次</option>
              <option v-for="shift in activeShifts" :key="shift.id" :value="shift.id">{{ shift.name }}</option>
            </select>
            <select
              v-model="brushZoneId"
              class="gZ-sel"
              aria-label="笔刷工作区"
              :disabled="brushKind === BRUSH_REST"
            >
              <option value="">跟固定区</option>
              <option v-for="zone in zones" :key="zone.id" :value="String(zone.id)">{{ zone.name }}</option>
            </select>
            <button
              class="gChip"
              type="button"
              :class="{ on: brushKind === BRUSH_REST }"
              :aria-pressed="brushKind === BRUSH_REST ? 'true' : 'false'"
              @click="toggleBrushRest()"
            ><i class="dt rest"></i>休</button>
          </template>
          <div v-else class="gSeg">
            <button class="gBtn" :class="{ on: sheetTab === 'shift' }" type="button" @click="sheetTab = 'shift'">班次</button>
            <button class="gBtn" :class="{ on: sheetTab === 'cycle' }" type="button" @click="openCycleTab()">周期</button>
          </div>
          <button class="gBtn ghost" type="button" @click="closeSheet()">收起</button>
        </div>
        <p v-if="sheetFolded" class="gBrush-hint">点格子就刷上去 · 长按格子看 / 改这一格</p>
        <p v-if="sheetFolded && sheetError" class="gSheet-err">{{ sheetError }}</p>

        <template v-if="!sheetFolded">
          <p class="gSheet-hd">
            <b>{{ sheetEmployee ? sheetEmployee.name : '' }}</b>
            <span>{{ formatDayLabel(sheet.date) }}</span>
            <em>{{ sheetNowText }}</em>
          </p>

          <template v-if="sheetTab === 'shift'">
            <div class="gSheet-row">
              <button
                class="gChip"
                type="button"
                :disabled="sheetBusy || !sheetUndoable || !(sheetCell && sheetCell.overridden)"
                @click="clearCell()"
              >清除班次</button>
              <button
                class="gChip"
                type="button"
                :class="{ on: sheetIsRest }"
                :disabled="sheetBusy || !sheetUndoable"
                @click="pickRest()"
              ><i class="dt rest"></i>休息</button>
            </div>
            <!-- 工作区（2026-10-04 补）：改这里只动这一天，不动这个班次的固定区。
                 休 / 请假 / 还没排到的格子没有区可言 → 禁用并说一句。 -->
            <label class="gZ-pick">
              <span>工作区</span>
              <select
                class="gZ-sel"
                :value="sheetZone"
                :disabled="sheetBusy || !sheetUndoable || !sheetHasShift"
                @change="pickZone($event.target.value)"
              >
                <option value="">跟固定区</option>
                <option v-for="zone in zones" :key="zone.id" :value="String(zone.id)">
                  {{ zone.name }}
                </option>
              </select>
              <em v-if="!sheetHasShift" class="gSheet-pick-note">休 / 请假的日子没有区</em>
            </label>
            <div class="gSheet-list">
              <button
                v-for="shift in activeShifts"
                :key="shift.id"
                type="button"
                class="gChip wide"
                :class="[toneClass(shift.id), { on: !!sheetCell && sheetCell.shift_id === shift.id }]"
                :disabled="sheetBusy || !sheetUndoable"
                @click="pickShift(shift)"
              ><i class="dt"></i>{{ shift.name }}</button>
            </div>
            <p v-if="!sheetUndoable" class="gSheet-hint">过去的日子不改写：这天的记录留着，从这里改不了。</p>
            <p v-else class="gSheet-hint">
              点一下就落库。「工作区」改的是<span class="gW-em">这一天</span>；
              要改长期的（他以后每个早班都在哪）去「名单」。
            </p>
            <p v-if="sheetError" class="gSheet-err">{{ sheetError }}</p>
          </template>

          <template v-else>
            <p class="gSheet-rule">{{ sheetEmployee ? sheetEmployee.name : '' }} 的轮转规则：{{ sheetRuleText }}</p>
            <div class="gSheet-row">
              <button class="gBtn on" type="button" @click="gotoRoster()">去「名单」改规则</button>
            </div>
            <p class="gSheet-hint">规则一人一条，改了只重排今天以后 —— 所以它不在这张周表上改。</p>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 版式照抄原型 B（`.scratch/scheduling/prototype/admin-variants.html` 的 .gB-*），
   令牌来自 /hygiene-admin.css；这里只补原型里没有的两样：月切换条、名单行。 */
.sched-page {
  position: relative;
  z-index: 1;
  min-height: 100%;
}
.gB {
  min-height: 100%;
  background: radial-gradient(90% 40% at 85% 0%, rgba(95, 214, 230, .08), transparent 60%);
  padding-bottom: 28px;
}
.gB-body { max-width: 560px; margin: 0 auto; padding: 4px 16px 22px; }
.gB-mon { display: flex; align-items: baseline; gap: 10px; padding: 2px 2px 10px; }
.gB-mon b { font-family: var(--font-song); font-size: 16px; letter-spacing: .12em; }
.gB-mon span { font-size: 10.5px; color: var(--hy-faint); }
.gB-mon em { font-style: normal; margin-left: auto; font-size: 10.5px; color: var(--hy-faint); }
.gB-bar { display: flex; align-items: center; gap: 6px; padding: 0 2px 10px; flex-wrap: wrap; }
.gMsg { margin: 0 0 10px; font-size: 12px; color: var(--hy-seal-bright); }
.gB-head { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; padding: 0 0 5px; }
.gB-head span { text-align: center; font-size: 10px; color: var(--hy-faint); letter-spacing: .06em; }
.gB-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.gB-d {
  position: relative; min-height: 62px; border-radius: 10px; border: 1px solid transparent;
  background: var(--hy-surface-2); padding: 7px 6px 6px; display: flex; flex-direction: column;
  gap: 5px; font: inherit; color: inherit; text-align: left; cursor: pointer;
  /* 五个班次时「6 · 0 · 2 · 1 · 1」这一行会把格子撑宽（grid 项的 min-width 默认是 auto），
     七列一起涨就把最后两列顶出屏幕 —— 让它自己在格子里换行。 */
  min-width: 0;
}
.gB-d.mute { background: transparent; border-color: var(--hy-line); opacity: .35; }
.gB-d .n { font-family: var(--font-mono); font-size: 11px; color: var(--hy-muted); line-height: 1; }
.gB-d .c { display: flex; flex-wrap: wrap; align-items: baseline; gap: 3px; font-family: var(--font-mono); font-size: 11.5px; line-height: 1; }
.gB-d .c b { font-weight: 600; }
.gB-d .c i { font-style: normal; font-weight: 600; }
.gB-d .c em { font-style: normal; color: var(--hy-faint); font-size: 9px; }
.gB-d.today { border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.gB-d.today .n { color: var(--hy-jade); font-weight: 700; }
/* 待批角标（spec US 11）：这天有等着批的申请。琥珀是票 07 特意留给「待处理」的颜色，
   青点留给「这天被手改过」—— 两种记号各说各的事，不能混。 */
.gB-d .pend {
  position: absolute; top: 3px; right: 3px; min-width: 15px; height: 15px;
  padding: 0 3px; border-radius: 999px; background: var(--hy-amber); color: var(--hy-night);
  font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; line-height: 15px;
  text-align: center;
}
.gB-d.sel { border-color: var(--hy-mint); box-shadow: var(--hy-glow-mint); }
/* 这天被单日覆盖改过（票 07）：右上角一个青点 —— 人数会变，但「改成休」那种改动
   在人数里根本看不出来，得有个跟人数无关的标记。 */
.gB-d.over::after {
  content: ""; position: absolute; top: 6px; right: 6px; width: 5px; height: 5px;
  border-radius: 50%; background: var(--hy-aqua);
}
.tone-mint { color: var(--hy-mint); }
.tone-aqua { color: var(--hy-aqua); }
.tone-amber { color: var(--hy-amber); }
.tone-seal { color: var(--hy-seal-bright); }
.tone-violet { color: var(--hy-violet); }
.tone-rose { color: var(--hy-rose); }
.gB-legend { display: flex; align-items: center; gap: 12px; margin-top: 11px; font-size: 10.5px; color: var(--hy-faint); padding: 0 2px; flex-wrap: wrap; }
.gB-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: middle; background: currentColor; }
.gB-legend span { color: var(--hy-faint); }
.gB-legend .gB-ov i { background: var(--hy-aqua); }
.gB-legend .gB-pend i { background: var(--hy-amber); }
.gB-card { margin-top: 14px; border: 1px solid var(--hy-line); border-radius: var(--hy-radius-lg); background: var(--hy-surface); padding: 13px 14px; }
.gB-card-plain { margin-top: 0; }
.gB-card-hd { display: flex; align-items: baseline; gap: 9px; padding-bottom: 11px; border-bottom: 1px solid var(--hy-line); }
.gB-card-hd b { font-family: var(--font-song); font-size: 15px; letter-spacing: .08em; }
.gB-card-hd span { font-size: 11px; color: var(--hy-muted); }
.gB-card-hd em { margin-left: auto; font-style: normal; font-size: 10.5px; color: var(--hy-faint); }
.gB-grp { padding-top: 11px; }
.gB-grp-t { display: flex; align-items: center; gap: 7px; font-size: 11px; margin-bottom: 7px; }
.gB-grp-t .d { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.gB-grp-t b { font-family: var(--font-song); font-size: 12.5px; letter-spacing: .08em; }
.gB-grp-t i { font-style: normal; font-family: var(--font-mono); color: var(--hy-ink); }
.gB-grp-t.off { color: var(--hy-faint); }  /* 「休」那一组：没有班次色，就是不在班上 */
.gB-grp-t.leave { color: var(--hy-aqua); }  /* 「请假」那一组（票 08）：批过的假，跟「休」分开 */
.gB-names { display: flex; flex-wrap: wrap; gap: 6px; }
/* 名字是一颗可以按的棋子（票 07）：点它就地改这一天。 */
.gB-name {
  font: inherit; font-size: 11.5px; color: var(--hy-ink); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 7px; padding: 3px 8px; cursor: pointer;
}
.gB-name.over { border-color: var(--hy-aqua); }  /* 这天跟规则不一样 */
.gB-name.on { border-color: var(--hy-mint); box-shadow: var(--hy-glow-mint); }
.gB-empty { margin: 6px 0 0; font-size: 11.5px; color: var(--hy-faint); }
.gB-note { margin: 14px 0 0; font-size: 11px; color: var(--hy-faint); line-height: 1.7; }
.gPend {
  margin-top: 12px; width: 100%; display: flex; align-items: center; gap: 9px; padding: 10px 12px;
  border: 1px solid var(--hy-amber-line); border-radius: var(--hy-radius-md);
  background: var(--hy-amber-soft); font-size: 12.5px; color: var(--hy-ink); font: inherit;
  text-align: left; cursor: pointer;
}
.gPend .n { width: 20px; height: 20px; flex: none; border-radius: 6px; display: grid; place-items: center; font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; color: #2a1c05; background: var(--hy-amber); }
.gPend b { font-weight: 600; color: var(--hy-ink); font-size: 12.5px; }
.gPend span { color: var(--hy-muted); font-size: 12.5px; }
.gPend .go { margin-left: auto; color: var(--hy-amber); font-size: 14px; }
/* 请假待办（票 08）：同一根条换个色 —— 青=请假，琥珀=要配规则。
   两根条的活不一样：这根是去另一页批假，那根是在本页开名单。 */
.gTodo { border-color: var(--hy-aqua); background: rgba(94, 234, 212, .08); }
.gTodo .n { color: var(--hy-night); background: var(--hy-aqua); }
.gTodo .go { color: var(--hy-aqua); }
.gBtn {
  font: inherit; font-size: 11px; color: var(--hy-muted); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 999px; padding: 4px 10px; cursor: pointer;
}
.gBtn:disabled { opacity: .45; cursor: default; }
.gBtn.ghost { color: var(--hy-faint); }
.gBtn.on { color: var(--hy-mint); border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.gAct {
  margin-left: auto; font: inherit; font-size: 11px; color: var(--hy-mint);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  border-radius: 999px; padding: 4px 10px; cursor: pointer;
}
.gR { padding-top: 11px; }
.gR + .gR { border-top: 1px solid var(--hy-line); margin-top: 11px; }
.gR-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.gR-main b { font-size: 13px; }
.gR-main span { font-size: 11px; color: var(--hy-faint); }
.gR-main .gR-off { color: var(--hy-seal-bright); }
.gR-main em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 11px; color: var(--hy-faint); }
.gR-main em.on { color: var(--hy-mint); }
.gR-act { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
/* 每个班的固定工作区（票 03）。一行两个班次的下拉，窄屏自动换行。 */
.gZ { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 7px; }
.gZ-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hy-faint); }
.gZ-sel {
  font: inherit; font-size: 11px; color: var(--hy-ink); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 7px; padding: 3px 6px; cursor: pointer;
}
.gZ-sel:disabled { opacity: .45; cursor: default; }
/* 笔刷条（折叠成一行时的那支笔）：控件直接复用工作区下拉的外观，不另起一套视觉。 */
.gBrush-tag {
  font-size: 11px; color: var(--hy-mint); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 999px; padding: 2px 8px;
}
.gBrush-hint { margin: 6px 0 0; font-size: 10.5px; color: var(--hy-faint); line-height: 1.7; }
.gZ-none { font-size: 11px; color: var(--hy-faint); }
/* 轮转周期编辑器（票 04）。原型 B 的名单行里没有这一块 —— 展开后长在那一行下面。 */
.gC {
  margin-top: 8px; padding: 9px 10px; display: flex; flex-direction: column; gap: 7px;
  border: 1px solid var(--hy-line); border-radius: var(--hy-radius-md); background: var(--hy-surface-2);
}
.gC-in {
  font: inherit; font-family: var(--font-mono); font-size: 12px; color: var(--hy-ink);
  background: var(--hy-surface); border: 1px solid var(--hy-line); border-radius: 7px; padding: 6px 8px;
  letter-spacing: .06em;
}
.gC-anchor { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hy-faint); }
.gC-anchor input {
  font: inherit; font-size: 11px; color: var(--hy-ink); background: var(--hy-surface);
  border: 1px solid var(--hy-line); border-radius: 7px; padding: 3px 6px;
}
.gC-act { display: flex; gap: 6px; }
.gC-hint { margin: 0; font-size: 10.5px; color: var(--hy-faint); line-height: 1.6; }
.gC-err { margin: 0; font-size: 11px; color: var(--hy-seal-bright); line-height: 1.6; }
.gB-zone {
  margin-left: 5px; font-style: normal; font-family: var(--font-mono); font-size: 10px;
  color: var(--hy-mint);
}
.gB-zone.off { color: var(--hy-faint); }
.gB-zone.leave { color: var(--hy-aqua); }  /* 批过的假：跟「休」和「未配区」都不一个色 */
/* 单日覆盖编辑器（票 07）：点当天卡里的一个人，就地展开 —— 原型 B 里没有这一块，
   它是「那一天跟规则不一样」的唯一入口。 */
.gD {
  margin-top: 11px; padding: 9px 10px; display: flex; flex-direction: column; gap: 7px;
  border: 1px solid var(--hy-mint-line); border-radius: var(--hy-radius-md);
  background: var(--hy-mint-soft);
}
.gD-hd { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.gD-hd b { font-size: 12.5px; }
.gD-hd span { font-size: 11px; color: var(--hy-muted); }
.gD-hd em { margin-left: auto; font-style: normal; font-size: 10.5px; color: var(--hy-aqua); }
.gD-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hy-faint); }
.gD-act { display: flex; gap: 6px; flex-wrap: wrap; }
.gD-hint { margin: 0; font-size: 10.5px; color: var(--hy-faint); line-height: 1.6; }

/* ── 周表（员工 × 周，2026-09-30）────────────────────────────────────────
   版式：表头粘住、姓名列定宽、七列平分 —— 手机上不横向滚（跟月历那边同一个做法）。
   颜色只做辅助：格子里一定留一个字，因为 `SHIFT_TONES` 只有 4 个，第 5 个班次起会
   与第 1 个撞色（门店那套的图例就有 5 个班次）。 */
.gSeg { display: inline-flex; align-items: center; gap: 4px; }
.gSeg .gBtn.on { color: var(--hy-mint); border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.gW-tip { font-size: 10.5px; color: var(--hy-faint); }
/* 筛人：一屏只放得下四行多，名单十几号人时靠翻页找人太慢（图片里那套也有这个框）。 */
.gW-find { display: flex; align-items: center; gap: 9px; padding: 0 2px 8px; }
.gW-find input {
  flex: 1; min-width: 0; font: inherit; font-size: 12px; color: var(--hy-ink);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 6px 12px;
}
.gW-find input::placeholder { color: var(--hy-faint); }
.gW-find span { flex: none; font-size: 10.5px; color: var(--hy-faint); }
.gW { width: 100%; table-layout: fixed; border-collapse: separate; border-spacing: 4px; }
.gW-cname { width: 4.6em; }
/* 表头粘住：滚下去以后还得知道哪一列是哪天（门店那套滚动时表头也在）。 */
.gW thead th {
  position: sticky; top: 0; z-index: 3;
  padding: 3px 0 5px; border-radius: 8px;
  background: var(--hy-bg);
  font-weight: 400; font-size: 10px; color: var(--hy-faint); line-height: 1.3;
}
.gW thead th .d { display: block; font-family: var(--font-mono); font-size: 12px; color: var(--hy-ink); }
.gW thead th .m { display: block; font-style: normal; font-size: 9px; color: var(--hy-faint); }
.gW thead th.today { background: var(--hy-mint-soft); box-shadow: inset 0 0 0 1px var(--hy-mint-line); }
.gW thead th.today .w { color: var(--hy-mint); }
.gW thead th.today .d { color: var(--hy-mint-bright); font-weight: 700; }
.gW thead th.mute { opacity: .45; }
.gW thead th.weekend .w { color: var(--hy-muted); }
.gW-name {
  padding: 0 2px; text-align: left; vertical-align: middle;
  font-weight: 400; font-size: 11.5px; color: var(--hy-ink); line-height: 1.35;
  overflow-wrap: anywhere;
}
.gW-name b { font-weight: 600; }
/* 没配规则的人整行都是空的 —— 行内就得说一句，不然看着像这店不给他排班。 */
.gW-norule { display: block; font-style: normal; font-size: 9.5px; color: var(--hy-amber); }
.gW-cell {
  position: relative; width: 100%; min-height: 40px; padding: 0;
  display: grid; place-items: center;
  border: 1px solid transparent; border-radius: 9px;
  background: var(--hy-surface-2); color: var(--hy-ink);
  font: inherit; font-size: 12px; cursor: pointer;
  /* 长按要看/改这一格：别让手机把它当成"选中文字"或弹放大镜。 */
  -webkit-touch-callout: none; user-select: none;
}
.gW-cell:disabled { cursor: default; }
.gW-cell.long { font-size: 10px; letter-spacing: -.02em; }
.gW-cell.tone-mint { color: var(--hy-mint); background: rgba(63, 224, 176, .14); border-color: rgba(63, 224, 176, .3); }
.gW-cell.tone-aqua { color: var(--hy-aqua); background: rgba(95, 214, 230, .14); border-color: rgba(95, 214, 230, .3); }
.gW-cell.tone-amber { color: var(--hy-amber); background: rgba(227, 164, 74, .14); border-color: rgba(227, 164, 74, .3); }
.gW-cell.tone-seal { color: var(--hy-seal-bright); background: rgba(239, 106, 79, .14); border-color: rgba(239, 106, 79, .3); }
.gW-cell.tone-violet { color: var(--hy-violet); background: rgba(177, 140, 240, .14); border-color: rgba(177, 140, 240, .3); }
.gW-cell.tone-rose { color: var(--hy-rose); background: rgba(240, 122, 176, .14); border-color: rgba(240, 122, 176, .3); }
.gW-cell.off { color: var(--hy-faint); background: rgba(133, 205, 198, .07); border-color: var(--hy-line); }
.gW-cell.leave { color: var(--hy-aqua); background: rgba(95, 214, 230, .1); border-color: rgba(95, 214, 230, .28); }
/* 两种「空」分开：整列没有数据 / 还没铺到 = 虚线淡掉；这天有数据但这个人没排到 = 淡框。 */
.gW-cell.mute { background: transparent; border: 1px dashed var(--hy-line); opacity: .5; }
.gW-cell.none { background: transparent; border: 1px dashed var(--hy-line-strong); }
.gW-cell.today { box-shadow: inset 0 0 0 1px var(--hy-mint-line); }
.gW-cell.on { box-shadow: 0 0 0 2px var(--hy-mint); }
/* 这天被单日覆盖改过（票 07 的青点，跟月历同一个记号）。 */
.gW-cell.over::after {
  content: ""; position: absolute; top: 3px; right: 3px;
  width: 5px; height: 5px; border-radius: 50%; background: var(--hy-aqua);
}
/* 这一列有申请等着批（票 02 的琥珀记号）：挂在表头上，一格一个数。 */
.gW thead th .pend {
  position: absolute; top: 0; right: 2px;
  min-width: 14px; height: 14px; padding: 0 3px;
  border-radius: 999px; background: var(--hy-amber); color: var(--hy-night);
  font-family: var(--font-mono); font-size: 9.5px; font-weight: 700;
  line-height: 14px; text-align: center; font-style: normal;
}
.gW-em { color: var(--hy-ink); }
.gB-legend i.off { background: var(--hy-faint); }
.gB-legend i.leave { background: var(--hy-aqua); }
/* 抽屉弹出来时给页面留出底部空间：不留的话它压住的正是下面几行员工
   （门店那套就是这个毛病，图片里能看到第 4、5 行被压掉一半）。 */
.gB.sheet-open { padding-bottom: 320px; }
/* 笔袋开着（折叠成一行）时只留一条的高度：不占地方，最后一行也滚得上来。 */
.gB.brush-open { padding-bottom: 88px; }

/* ── 底部抽屉 ─────────────────────────────────────────────────────────── */
.gSheet {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 30;
  max-width: 560px; margin: 0 auto;
  display: flex; flex-direction: column; gap: 9px;
  max-height: 64vh; overflow-y: auto;
  padding: 10px 16px calc(14px + env(safe-area-inset-bottom));
  background: var(--hy-surface);
  border-top: 1px solid var(--hy-line-strong);
  border-radius: var(--hy-radius-lg) var(--hy-radius-lg) 0 0;
  box-shadow: 0 -18px 40px rgba(0, 0, 0, .55);
}
.gSheet-bar { display: flex; align-items: center; gap: 8px; }
.gSheet-bar .gSeg { margin: 0 auto; }
.gSheet-fold {
  width: 30px; height: 26px; flex: none;
  display: grid; place-items: center;
  font: inherit; font-size: 13px; line-height: 1; color: var(--hy-muted);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; cursor: pointer;
}
.gSheet-hd { display: flex; align-items: baseline; gap: 8px; margin: 0; flex-wrap: wrap; }
.gSheet-hd b { font-family: var(--font-song); font-size: 13.5px; letter-spacing: .06em; }
.gSheet-hd span { font-size: 11.5px; color: var(--hy-muted); }
.gSheet-hd em { margin-left: auto; font-style: normal; font-size: 11px; color: var(--hy-mint); }
.gSheet-row { display: flex; gap: 8px; flex-wrap: wrap; }
.gSheet-list { display: flex; gap: 8px; flex-wrap: wrap; }
.gChip {
  display: inline-flex; align-items: center;
  font: inherit; font-size: 12px; color: var(--hy-ink);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 7px 14px; cursor: pointer;
}
.gChip.wide { min-width: 104px; }
.gChip:disabled { opacity: .45; cursor: default; }
.gChip.on { box-shadow: 0 0 0 2px var(--hy-mint); }
.gChip.tone-mint { color: var(--hy-mint); background: rgba(63, 224, 176, .14); border-color: rgba(63, 224, 176, .3); }
.gChip.tone-aqua { color: var(--hy-aqua); background: rgba(95, 214, 230, .14); border-color: rgba(95, 214, 230, .3); }
.gChip.tone-amber { color: var(--hy-amber); background: rgba(227, 164, 74, .14); border-color: rgba(227, 164, 74, .3); }
.gChip.tone-seal { color: var(--hy-seal-bright); background: rgba(239, 106, 79, .14); border-color: rgba(239, 106, 79, .3); }
.gChip.tone-violet { color: var(--hy-violet); background: rgba(177, 140, 240, .14); border-color: rgba(177, 140, 240, .3); }
.gChip.tone-rose { color: var(--hy-rose); background: rgba(240, 122, 176, .14); border-color: rgba(240, 122, 176, .3); }
.gChip .dt { width: 9px; height: 9px; flex: none; margin-right: 7px; border-radius: 50%; background: currentColor; }
.gChip .dt.rest { background: var(--hy-faint); }
.gSheet-rule { margin: 0; font-size: 11.5px; color: var(--hy-ink); line-height: 1.7; }
.gSheet-pick-note { font-style: normal; color: var(--hy-faint); font-size: 10.5px; }
.gSheet-hint { margin: 0; font-size: 10.5px; color: var(--hy-faint); line-height: 1.7; }
.gSheet-err { margin: 0; font-size: 11px; color: var(--hy-seal-bright); line-height: 1.7; }
</style>
