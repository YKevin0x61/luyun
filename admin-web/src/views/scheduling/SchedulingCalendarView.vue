<script setup>
// 店长端「排班」。版式按原型的 B · 月历（`.scratch/scheduling/prototype/admin-variants.html`
// 的 `[data-variant="B"]`）：一屏是本月人手网格（每格写当天白班、夜班各几人），
// 下面一张卡是选中那天「谁上哪个班」，底下一根「还没配规则的人」的条。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
// 票 02 做「配固定班次」、票 03 加责任区、票 04 加轮转周期编辑、票 07 加单日覆盖
// （点当天卡里的一个人就地改那一天）、票 08 加底下那根「请假待办」的条
// （批假在 `/scheduling/inbox` 那一页：这一页只管排班怎么铺）；班次表的增删改在票 11。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useNudgePull } from '../../composables/useNudgePull'
import { mergeShiftList } from '../../utils/shiftTable'
import { eachDayInRange } from '../../utils/dateRange'

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
// 每个班次一个颜色，按排序位循环取（原型里白班薄荷、夜班水青）。N 个班次都够用。
const SHIFT_TONES = ['mint', 'aqua', 'amber', 'seal']

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
const editZone = ref('') // 责任区 id 的字符串；'' = 跟这个班次的固定区
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
// 或固定责任区的选择里，店长选完只会吃一句 400。
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
  const weekday = WEEKDAYS[new Date(year, month - 1, day).getDay()]
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
    // 责任区名单来自公共层（读的是卫生建的那份），所以卫生那边新建一个区，
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
    errorText.value = err.message || '责任区没存上'
    await loadRoster()
  } finally {
    busyEmployeeId.value = null
  }
}

// 单日覆盖（票 07）：这一天跟规则不一样 —— 换班次、改成休、或只换责任区。
// 覆盖是**整天的快照**：保存时把这一天的班次和责任区一起定下来，规则以后怎么变都不再
// 动它（想让它回到规则就「撤销覆盖」）。所以「只改责任区」也走同一条路：班次不动、
// 只把下拉里的区换掉，提交时带着当天那个班次一起发过去。
async function openDayEdit(person, shiftId) {
  // 责任区名单只在名单面板里读过（`loadRoster`）：**先把名单补齐，再决定下拉停在哪一项**。
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
  },
})
</script>

<template>
  <div class="hygiene-admin sched-page">
    <div class="gB">
      <div class="gB-body">
        <div class="gB-mon">
          <b>{{ monthTitle(monthValue) }}</b>
          <span>{{ shifts.map((s) => s.name).join(' / ') }} 人数</span>
          <em>{{ formatTodayLabel() }}</em>
        </div>

        <div class="gB-bar">
          <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, -1))">‹ 上月</button>
          <button class="gBtn" type="button" @click="loadCalendar(currentMonthValue())">本月</button>
          <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, 1))">下月 ›</button>
          <button
            class="gAct"
            type="button"
            @click="openPanel(panel === 'month' ? 'roster' : 'month')"
          >{{ panel === 'month' ? '名单 ›' : '‹ 月历' }}</button>
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
                <span>责任区</span>
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

          <button class="gPend gTodo" type="button" @click="router.push('/scheduling/inbox')">
            <span class="n">假</span>
            <b>请假待办</b>
            <span>员工提的请假在这儿批：批完那天记成请假，先看清批了还剩几个人</span>
            <span class="go">›</span>
          </button>

          <button class="gPend" type="button" @click="router.push('/scheduling/shifts')">
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

        <template v-else>
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
                <span v-if="!zones.length" class="gZ-none">还没有责任区，先去卫生的责任区页面建一个</span>
              </div>
            </div>
            <p v-if="!roster.length" class="gB-empty">名单是空的</p>
            <p class="gB-note">「固定一个班」和「编周期」（白白白夜夜休休）配的是同一样东西：一人一条轮转规则。规则改了只重排今天以后，过去的日子不动。</p>
          </div>
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
  position: relative; height: 62px; border-radius: 10px; border: 1px solid transparent;
  background: var(--hy-surface-2); padding: 7px 6px 0; display: flex; flex-direction: column;
  gap: 5px; font: inherit; color: inherit; text-align: left; cursor: pointer;
}
.gB-d.mute { background: transparent; border-color: var(--hy-line); opacity: .35; }
.gB-d .n { font-family: var(--font-mono); font-size: 11px; color: var(--hy-muted); line-height: 1; }
.gB-d .c { display: flex; align-items: baseline; gap: 3px; font-family: var(--font-mono); font-size: 11.5px; line-height: 1; }
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
/* 每个班的固定责任区（票 03）。一行两个班次的下拉，窄屏自动换行。 */
.gZ { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 7px; }
.gZ-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hy-faint); }
.gZ-sel {
  font: inherit; font-size: 11px; color: var(--hy-ink); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 7px; padding: 3px 6px; cursor: pointer;
}
.gZ-sel:disabled { opacity: .45; cursor: default; }
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
</style>
