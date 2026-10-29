<script setup>
/**
 * 人事端的「考勤表」页（票 01 + 票 03 假期台账 + 票 06 逐日改格）：**当月、一人一张卡**，
 * 点某一天就地改。
 *
 * 卡片上是这个人本月每一天的「上班 / 休 / 待补」：排了班 = 上班、有排班行但班次为空
 * = 休、**压根没有排班行** = 待补（轮转规则没铺到，要人工确认那几天）。**今天之后的
 * 日子留空** —— 那是「还没到」，不是「待补」，也不给点（`spec.md` 验收 45）。
 *
 * 卡片上另外那几列（职位 / 底薪 / 年限 / 是否新入职 / 当月净时长）都由服务端从花名册与
 * 加班台账里取，这一层只摆出来，一个数都不自己算。
 *
 * 票 06：可编辑的那一格点开是一个下拉（现在挂的班次 / 别的启用中班次 / 休），选完打
 * `PUT /api/attendance/sheet/{员工}/{营业日}`，服务端复用排班的**单日覆盖** —— 只动那一天、
 * 不写轮转规则、不动下个月。拒绝的理由（那天有未决的换班 / 请假、超出可补记窗口、
 * 班次已停用）由服务端给中文，这一层原样显示在那一格的下面。
 *
 * 票 07（ADR 0114）：同一格上还有一颗「休半天」的按钮，走**同一条 PUT** 的另一个字段
 * （`half_rest`）—— 那天仍然挂本来那个班次、仍然只有一行，只是只上半天；标上 / 取消
 * 都只动那一个标志。这一页是**全仓唯一**能设置它的地方（排班月历 / 周表 / 员工端只读地
 * 显示它）。它自己一颗按钮、不塞进那个班次下拉：下拉回答「那天上哪个班」，它回答
 * 「上不上满一整天」。
 *
 * 票 03 往卡片上加的是**假期台账**那一块：出勤（含已排）/ 月例休 / 法定节假日补休 / 生日假 /
 * 年假 / 上月存假，与由它们相加得到的可用天数池、本月出勤天数、存假；展开能看算式。
 * 当月**只算不落库**，所以顶部明写「暂算值（未封账）」—— 这一页的数还会随排班与加班变。
 * 还没批的加班 / 补钟只**标色**并给出待批小时数，一分都不进净时长（故事 14）。
 *
 * 票 04 加了**封账上月**那颗按钮：点一次就把上一个自然月的存假结余定下来（写进月度考勤
 * 台账），卡片上的「上月存假」随即改成结转来的那个数，按钮的位置换成一句「某月已于某时
 * 封账」的回执。当月照旧是暂算值 —— 封的是上月。
 *
 * 票 05 往每张卡上加的是**四个手填列**（本月班次 / 是否住宿 / 借调门店 / 是否离职）：
 * 系统里没有这几项，填一次存进月度考勤台账，只进卡片与**导出件**。
 *
 * 票 08 加了**导出 .xlsx** 那颗按钮：`GET /api/attendance/sheet.xlsx` 直接把工作簿下发
 * （服务端投影，不落服务器），文件名由响应头带回来（`考勤表-YYYY-MM.xlsx`）。这一页
 * 自己不拼文件名、也不碰工作簿内容。
 *
 * 收尾修复（两轴 code-review 之后）：手填那一块多了**期初存假（上月结转）**那个输入口
 * —— 故事 35 让超管在**上线当月手工给一次**「上月末余额」，写的是月度台账**本月那一行**
 * 的 `opening_balance`（票 03 的裁定），走的是与四列手填**同一条**月级 PUT；「借调门店」
 * 那一项旁边注明了它与历史表的偏差（历史表写在某一天的格子上，本版按月记、不落进导出件
 * 的日格）；日格上那三种写法由 `utils/__tests__` 里一条断言与服务端
 * `services/attendance/export.py` 的常量钉在一起（不许前后端各说各话）。
 *
 * 票 09 把那一条**补成一对**：期初旁边多了「期末存假（本月剩余）」—— 当月未封账前随时
 * 可填可改、填了就以它为准（封账把它连同 `sealed_at` 一起写进台账定稿，导出件的「存休
 * 天数」也跟着走）；没填过就照旧用公式算出来的那个数，那个数只出现在「系统建议」那句话
 * 里（两个不一致时一眼看得见）。封账之后这一格锁定：两个输入框都禁用并写清为什么。
 * 台账九项里的「上月存假 / 存假」**两处都留**（用户与父级已确认接受这处重复显示），
 * 九项那个「存假」念的仍是**有效值**（人工优先）。
 *
 * 这一页只算当月：没有月份选择器。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../../api/client'
import {
  HALF_REST_TEXT,
  closeSuggestionText,
  dayStateText,
  formatDays,
  formatNetHours,
  isMonthSealed,
  leaveFormulaText,
  manualStats,
  manualText,
  newHireText,
  pendingOvertimeText,
  sealedMonthText,
  shiftNameDefault,
} from '../../utils/attendanceSheet'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'

const router = useRouter()

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const sheet = ref(null)

/** 正在改的那一格（`{ employeeId, date }` 或 null）与正在等回执的那一格。
 *  编辑态按「**这个人 + 这一天**」记，不按日期记：店长是在某人的那张卡上点某一天的，
 *  只按日期记会让每张含这一天的卡都开一条编辑带（父级裁定，见 64 号截图）。 */
const editing = ref(null)
const savingDate = ref('')
/** 那一格上要显示的话（服务端拒绝的理由）。 */
const dayError = ref(null) // { employeeId, business_date, text } | null

/** 封账（票 04）：正等着服务端回执 / 服务端拒绝的理由。 */
const sealing = ref(false)
const sealError = ref('')

/** 上月封账的那句回执（没封过时是空串，页面上画按钮）。 */
const sealStamp = computed(() => sealedMonthText(sheet.value?.previous_month))

/** 表头那一份月框（哪一天是今天 / 还没到）按营业日索引，卡片上的日子从它取号与标记 ——
 *  页面不自己从 1 数到 30（那样 2 月与 31 天的月份会各错一次）。 */
const frame = computed(
  () => new Map((sheet.value?.days || []).map((day) => [day.business_date, day])),
)

/** 卡片上的日子：把服务端那份月框（今天 / 还没到）并到每人每天的格子上去。
 *
 *  这里刻意**不写成 `computed`**：改完一格会重拉一次整张表，而 `computed` 在
 *  「重挂 / 重新渲染」那一拍上会拿着旧的那一份（实测：页面上的班次名停在改之前，
 *  而 `sheet` 已经是新的了）。一次渲染算一遍这点开销，远不值得拿一个会骗人的缓存换。
 */
function employees() {
  return (sheet.value?.employees || []).map((person) => ({
    ...person,
    // 手填列（票 05；2026-10-29 起「是否离职」退役、只剩三项）原样带过来：它们是台账
    // 那一行上的值，页面只摆出来。
    // 单独列一遍是为了让「这一页认哪几个键」看得见 —— 手滑写错一个键名，输入框会
    // 一直空着（`manualValue` 读不到就是空），看起来像「服务端没给」。
    // `shift_name` 是**读侧的回填值**（没填过时是当月众数，导出件 A 列用它）；
    // 输入框绑的是 `shift_name_stored`（台账的原始列，没填过就是 `null` → 空格子）。
    shift_name: person.shift_name,
    shift_name_stored: person.shift_name_stored,
    default_shift_name: person.default_shift_name,
    lodging: person.lodging,
    borrowed_store: person.borrowed_store,
    // 人工期初（收尾修复 ② / 故事 35）：台账**本月那一行**的 `opening_balance`，
    // 上线当月手工给一次。它与 `leave.previous_balance` 是两件事：后者只在上一月
    // 还没封账时才读它。
    opening_balance: person.opening_balance,
    cells: (person.days || []).map((day) => ({
      ...day,
      ...(frame.value.get(day.business_date) || {}),
    })),
  }))
}

/** 这个人这个月有几天是「待补」—— 规则没铺到的那几天要一眼看得出来（验收 3 的用法）。 */
function todoDays(person) {
  return (person.cells || []).filter((cell) => cell.state === 'todo').length
}

/** 班次名：`shift_id` 去 `shifts` 里找；班次被停用 / 硬删掉时给空串（不编一个名字）。 */
function shiftName(person, shiftId) {
  if (shiftId === null || shiftId === undefined) return ''
  const shift = (person.shifts || []).find((item) => item.id === shiftId)
  return shift ? shift.name : ''
}

/** 那一格上写什么：休 / 休半天 / 上班（跟班次）/ 待补；今天之后留空 —— **除非那天已经排了班**。
 *
 *  2026-10-29 用户报「30/31 都排了班了，但没算上」：未来格原来一律留空，店长在考勤表上
 *  看不到自己刚在周表排的班。现在把 `shift_id` 写出来（计划），但它仍然只是一格计划：
 *  不进任何统计、不能编辑，样式沿用 `.is-future` 的淡色，跟「已经上了班」看得出区别。 */
function cellLabel(person, cell) {
  if (cell.state === 'work') {
    // 半天休（票 07）：那天仍然挂着班次，只是只上半天 —— 这一句必须排在班次名前面，
    // 否则卡片上写个「白班」，店长与员工都以为要上整天（ADR 0114）。
    if (cell.half_rest) return HALF_REST_TEXT
    return shiftName(person, cell.shift_id) || dayStateText(cell.state)
  }
  if (cell.is_future && cell.shift_id !== null && cell.shift_id !== undefined) {
    return shiftName(person, cell.shift_id)
  }
  return dayStateText(cell.state)
}

/** 这一格能不能点：今天之后留空、没到的那天不给改。 */
function editable(cell) {
  return Boolean(cell.state) && !cell.is_future
}

function openEditor(person, cell) {
  if (!editable(cell) || savingDate.value) return
  dayError.value = null
  const same =
    editing.value?.employeeId === person.id && editing.value?.date === cell.business_date
  editing.value = same ? null : { employeeId: person.id, date: cell.business_date }
}

/** 收起编辑带（带子里那颗「取消」）：店长自己撤了，理由也一并收掉。 */
function closeEditor() {
  editing.value = null
  dayError.value = null
}

/** 这一格是不是正被改：一次点击只认「这个人 + 这一天」。格子上的记号与下面的编辑带
 *  都走这一个判据 —— 少一处跟着改，记号与带子就会各自说一套。 */
function isEditing(person, cell) {
  return editing.value?.employeeId === person.id && editing.value?.date === cell.business_date
}

/** 这张卡上正在改的那一天（0 或 1 个）：编辑带就挂在 `.aDays` **下面**、同一张卡里 ——
 *  被点的那一格留在它原来的列位上（B 批返工 ⑥）。写成「哪些格子在被改」而不是
 *  「这张卡在不在改」，是为了让带子直接拿到那一格的 `cell`（班次、半天休标志都在上面）。 */
function editingCells(person) {
  return (person.cells || []).filter((cell) => isEditing(person, cell))
}

function errorOn(person, cell) {
  // **按「这个人 + 这一天」认**（与编辑态同一个口径）：只按日期认的话，某个人某天被拒的
  // 红边会落到**别人同一天**那一格上 —— 编辑带只属于被点的那个人，红边也只该落在他的格子上。
  const err = dayError.value
  return err && err.employeeId === person.id && err.business_date === cell.business_date
    ? err.text
    : ''
}

/** 带子里选了哪个 → 写请求的 body。`rest` 是「那天休」；没有「删掉那天」这个动作。 */
function bodyFor(value) {
  return value === 'rest' ? { is_rest: true } : { shift_id: Number(value) }
}

/** 选完就写：那天立刻按服务端的样子重画（不猜一个本地状态自己改）。 */
async function pick(person, cell, value) {
  savingDate.value = cell.business_date
  dayError.value = null
  try {
    await api.put(
      `/api/attendance/sheet/${person.id}/${cell.business_date}`,
      bodyFor(value),
    )
    // **写成了才收工**：重拉一次整表（不走 `load()` —— 那条路会把 `state` 打回
    // `loading`，整页闪一下），那天（与「待补」计数）立刻变。
    editing.value = null
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    // 被拒时**带子留着**：理由就写在带子里（票 06 的「不装作改成功」），那一格给红边；
    // 页面上已经渲染的那一份一个字都不动 —— 显示一个没落库的班次比报错更糟。
    dayError.value = {
      employeeId: person.id,
      business_date: cell.business_date,
      text: err.message || '这天没改成',
    }
  } finally {
    savingDate.value = ''
  }
}

/** 标 / 取消「休半天」（票 07）：一次**单独**的动作 —— 只改结果行上那个标志，
 *  班次、工作区与「那天上哪个班」都不动（ADR 0114）。
 *
 *  跟 `pick` 同一条 PUT、同一套做法（写成了才收起带子、重拉一次，失败就写在带子里）。 */
async function toggleHalf(person, cell) {
  if (savingDate.value) return
  savingDate.value = cell.business_date
  dayError.value = null
  try {
    await api.put(`/api/attendance/sheet/${person.id}/${cell.business_date}`, {
      half_rest: !cell.half_rest,
    })
    editing.value = null
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    dayError.value = {
      employeeId: person.id,
      business_date: cell.business_date,
      text: err.message || '这天的「休半天」没改成',
    }
  } finally {
    savingDate.value = ''
  }
}
/** 卡片上假期台账那几项的摆法（标签 + 服务端算好的值）：顺序与 `spec.md` 的公式一致，
 *  展开的算式也按这个顺序念 —— 店长对着卡片能一格一格核。 */
function leaveStats(person) {
  const leave = person.leave || {}
  return [
    { label: '出勤（含已排）', value: formatDays(leave.actual_days) },
    { label: '月例休', value: formatDays(leave.monthly_rest) },
    { label: '法定节假日补休', value: formatDays(leave.holiday_makeup) },
    { label: '生日假', value: formatDays(leave.birthday_leave) },
    { label: '年假', value: formatDays(leave.annual_leave) },
    { label: '上月存假', value: formatDays(leave.previous_balance) },
    { label: '可用天数池', value: formatDays(leave.pool) },
    { label: '本月出勤天数', value: formatDays(leave.month_attendance_days) },
    { label: '存假', value: formatDays(leave.balance) },
  ]
}

/** 正在等回执的那张卡（`员工号` 或 null）与这一块上要显示的话（服务端拒绝的理由）。 */
const savingManual = ref(0)
const manualError = ref(null) // { employee_id, text } | null

/** 「借调门店」与历史样表的**唯一**一处有意偏差（收尾修复 ⑥）：样表把它写在**某一天**的
 *  格子上（`城光荟` / `同和` / `调东莞`），而本版的台账是按月记的、没有日期可落 —— 所以
 *  导出件里**不落进日格**（这一列只留在卡片上）。要 1:1 就得给这一项加日期，那是另开票的事。
 *
 *  这处说明**必须留着**，但不必占三行可视小字（那三行会把一排四个输入口拽得参差不齐）：
 *  它压在输入框的 `title` 上，悬停可见。 */
const BORROWED_STORE_NOTE = '历史样表写在某一天的格子上；本版台账按月记，不落进导出件的日格'

/** 四个手填列的输入口（票 05）。**四项一起存**：空着的项发 `null`（清空存 NULL，与从没
 *  填过同义）。逐项自动存会让「清空」在打字的每一拍都发一次请求、也会把没动的那几项
 *  顺手覆盖成空，所以这里只按钮存一次。
 *
 *  字段名 ↔ 台账列名（`0027_attendance_ledger.sql`）的对应就这一处：写错了服务端不认
 *  那一列（`model_fields_set` 只认这四个），页面上看到的是「没存进去」。
 *
 *  `key` 是**发出去的那一位**（服务端只认这四个名字）；`stored` 是输入框**读**的那一位。
 *  两者只有「本月班次」不同：写的是 `shift_name`（台账列），读的是 `shift_name_stored`
 *  —— 台账里的原始值，没填过就是 `null`。**不能读 `shift_name`**：读侧没填过时会把它
 *  回填成当月众数，输入框显示的就是那条默认值，店长只改「是否住宿」点一下「存这三项」
 *  就把这条默认**固化**成他定的值了（以后再改排班也不跟着变）。默认值只写在提示语里。
 *
 *  `hint` 说的就是那份**默认**（当月众数）：空着有含义，店长要看得见用的是哪条。 */
const MANUAL_INPUTS = [
  {
    key: 'shift_name',
    stored: 'shift_name_stored',
    label: '本月班次',
    hint: (person) => shiftNameDefault(person),
  },
  { key: 'lodging', label: '是否住宿', hint: () => '写「是」或「否」' },
  { key: 'borrowed_store', label: '借调门店', hint: () => '如「调东莞」' },
  // 「是否离职」2026-10-29 退役：离职改成花名册上的状态（带日期），导出件从那里派生。
  // 台账那一列还在，但这一页不再读它，也不再写它。
]

/** 输入框里现在是什么：服务端那一份，读出来是空就给空串（受控输入）。
 *  `stored` 缺席时读的就是 `key` 那一位（那三项没有读写的分别）。 */
function manualValue(person, field) {
  const value = person[field.stored || field.key]
  return value === null || value === undefined ? '' : String(value)
}

/** 存：四项一起发。空着的项发 `null`（不是空串）—— 服务端把两者都当清空，但 `null` 更直说。 */
async function saveManual(person, event) {
  if (savingManual.value) return
  const form = event.target.closest('.aManual')
  const read = (key) => {
    const input = form && form.querySelector(`[data-field="${key}"]`)
    return input ? input.value.trim() : ''
  }
  const body = {}
  for (const field of MANUAL_INPUTS) body[field.key] = read(field.key) || null
  manualError.value = null
  savingManual.value = person.id
  try {
    await api.put(`/api/attendance/sheet/${person.id}/manual`, body)
    // 与逐日改格同一条路：自己重拉一次（nudge 不带数据，这一页也不等它）——
    // 写进去的那几项立刻在卡片上；**失败时不改页面上已经渲染的那一份**。
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    // 被拒时这一块要**摊开**：理由写在 body 里，收着的话店长只看到按钮闪了一下，
    // 不知道是没写上还是写上了。
    openManualId.value = person.id
    manualError.value = { employee_id: person.id, text: err.message || '这四项没存进去' }
  } finally {
    savingManual.value = 0
  }
}

function manualErrorOn(employeeId) {
  return manualError.value && manualError.value.employee_id === employeeId
    ? manualError.value.text
    : ''
}

/** 手填那一块展没展开（按**这个人**记：一张卡开一次，不影响别的卡）。
 *
 *  2026-10-29 用户截图说这一块「太占地方」：390px 下手填四格 + 期初 / 期末实测 502px
 *  （约一屏半），而它是**低频**的（填一次就进台账）—— 默认收成一行摘要，点「编辑」才
 *  摊开输入框。摘要念的是台账里存了什么 + 那一对结余，所以收起来也看得见这张卡当前的账。 */
const openManualId = ref(0)
function toggleManual(person) {
  openManualId.value = openManualId.value === person.id ? 0 : person.id
}
function manualOpen(person) {
  return openManualId.value === person.id
}

/** 收起时那一行摘要：与 body 里那份 `aManualNow` 同一批值（`manualStats` + 期初 / 期末），
 *  空值一律写 `—` —— 「收起来就什么都看不见」不是省地方，是丢信息。 */
function manualSummary(person) {
  return [
    ...manualStats(person).map((stat) => ({ label: stat.label, value: manualText(stat.value) })),
    { label: '期初', value: manualText(openingValue(person)) },
    { label: '期末', value: manualText(closingValue(person)) },
  ]
}

/** 人工期初（收尾修复 ② / 故事 35）与人工期末（票 09）那一格的输入口与它自己那一次写。
 *
 *  为什么它**不并进**上面那「存这三项」：那三列是每张卡随时会改的日常项，而期初 / 期末
 *  是台账上的**结余**（上线当月手工给一次、当月随时可定稿），分开一颗按钮，误点
 *  「存这三项」不会把这两格顺手清掉 —— 写口那边的纪律是「点名谁才写谁」，空着就一个
 *  字节都不碰。
 *
 *  两条结余**一起存**（一颗按钮），但**只发真被改过的那几位** —— 与「存这三项」同一条
 *  纪律（写口那边「点名谁才写谁」）：期初那一格没动过时，请求里就没有 `opening_balance`，
 *  既有那两条断言（「只带这一位」）因此一字不用改；期末那一格没动过时同理不发
 *  `closing_balance` —— 免得一次只改期初的保存把台账里的期末顺手覆盖成空。
 */
const savingOpening = ref(0)
const openingError = ref(null) // { employee_id, text } | null

/** 这个月（卡片上这一份表）是不是已经封账：封过账之后**期末这一格**锁定 ——
 *  两个输入框都不给写，并在块里写清为什么（票 09 验收）。判据在 util 里（那一位从哪一层
 *  下发都接得住，另有降级说明）。 */
const sealedNow = computed(() => isMonthSealed(sheet.value))

/** 员工面板默认折叠成一行表格；点排行或点「展开」才出明细。 */
const openEmployeeId = ref(0)
function toggleEmployee(person) {
  openEmployeeId.value = openEmployeeId.value === person.id ? 0 : person.id
}
function focusEmployee(person) {
  openEmployeeId.value = person.id
  nextTick(() => {
    const el = document.getElementById(`emp-${person.id}`)
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }
  })
}
/** 待补排行：按待补天数降序（只列入有缺口的）。 */
const todoRank = computed(() =>
  employees()
    .filter((person) => todoDays(person) > 0)
    .sort((a, b) => todoDays(b) - todoDays(a) || a.id - b.id),
)

/** 读数行要的两个聚合：待补天数合计、本月天数。**只读展示** —— 不改任何判定，
 *  也不参与写口；值仍全部来自服务端下发的 `person.cells` 与 `sheet.days`。 */
const todoTotal = computed(() =>
  employees().reduce((sum, person) => sum + todoDays(person), 0),
)
const monthDayCount = computed(() => (sheet.value?.days || []).length)

/** 输入框里现在是什么：服务端那一份；`null`（没给过）留空，不是 `undefined`。 */
function openingValue(person) {
  const value = person.opening_balance
  return value === null || value === undefined ? '' : String(value)
}

/** 期末那一格读的是**台账里的原始值**（`closing_balance_stored`）：没填过 / 清空 = 空框。
 *  **不能读 `leave.balance`**：那是有效值（人工优先），没填过时它等于公式值 —— 预填进
 *  输入框就等于替店长认了账，他改一下「是否住宿」点个按钮就把公式值固化成手工值了。
 *  公式算出来的那个数只出现在「系统建议」那句话里（`closeSuggestionText`）。 */
function closingValue(person) {
  const value = person.closing_balance_stored
  return value === null || value === undefined ? '' : String(value)
}

/** 期末旁边那句话：没填过只说系统建议，填过就说清「你填的是 X，系统建议 Y」。 */
function closingNote(person) {
  return closeSuggestionText(person.leave)
}

/** 存期初 / 期末（票 09）：一颗按钮，**只发真被改过的那几位**
 *  （`{ opening_balance }` / `{ closing_balance }` / 两条都改了就两位一起），
 *  空着发 `null`（期末清空 = 回公式值）。
 *
 *  为什么不用「两位全发、没动过的回填原值」：那看起来更省事，却把「今天点了一下按钮」
 *  变成一次对另一位结余的**重写**（并发的另一张卡刚好改过它时会被盖回去）。只发改过的
 *  那几位，语义与「存这三项」逐字一致：点名谁才写谁。
 *
 *  填了不是数字的东西**不发请求**：服务端那两位是数字，而 `Number('七点五')` 是 `NaN`，
 *  转成 JSON 会变成 `null` —— 那会把这一格悄悄清掉。前端先说人话（同「存这三项」：
 *  失败时不动页面上已经渲染的那一份）。
 */
async function saveOpening(person, event) {
  if (savingOpening.value) return
  const form = event.target.closest('.aOpening')
  const read = (field) => {
    const input = form && form.querySelector(`[data-field="${field}"]`)
    return input ? input.value.trim() : ''
  }
  // 两条结余现在读出来的是什么（与页面上渲的那一份比「有没有被改过」）。
  const fields = [
    { key: 'opening_balance', read: read('opening_balance'), value: openingValue(person) },
    { key: 'closing_balance', read: read('closing_balance_stored'), value: closingValue(person) },
  ]
  for (const field of fields) {
    if (field.read === '' || field.read === field.value) continue
    if (!Number.isFinite(Number(field.read))) {
      // 拒绝的话写在 body 里，收着就看不见 —— 先把这一块摊开。
      openManualId.value = person.id
      openingError.value = {
        employee_id: person.id,
        text: '期初 / 期末存假都要写数字（可以是负的、也可以带 .5）；要清空就把那一格留空',
      }
      return
    }
  }
  const body = {}
  for (const field of fields) {
    if (field.read === field.value) continue
    body[field.key] = field.read === '' ? null : Number(field.read)
  }
  openingError.value = null
  savingOpening.value = person.id
  try {
    await api.put(`/api/attendance/sheet/${person.id}/manual`, body)
    // 与手填那四列同一条路：自己重拉一次（nudge 不带数据，这一页也不等它）。
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    openManualId.value = person.id
    openingError.value = {
      employee_id: person.id,
      text: err.message || '期初 / 期末存假没存进去',
    }
  } finally {
    savingOpening.value = 0
  }
}

function openingErrorOn(employeeId) {
  return openingError.value && openingError.value.employee_id === employeeId
    ? openingError.value.text
    : ''
}

/** 封账那句确认（B 批返工 ⑦）：封账**不可逆** —— 写的是上月结余，封过不再重算，
 *  所以点下去先问一次，不能只靠按钮的颜色挡人。一句话说清它写的是什么、为什么问了。 */
const SEAL_CONFIRM_TEXT =
  '封账会把上月的存假结余定下来：封过就不再重算，之后改排班或加班也不再影响它。要继续吗？'

/** 封账上月（票 04）：封的是**上一个自然月**，接口上不给月份 —— 按钮只有一个动作。
 *
 *  服务端拒绝时（当月封不了、已经封过、缺迁移）把理由原样写出来；成功之后自己重拉一次
 *  整张表 —— 卡片上的「上月存假」立刻换成刚封下的结余，按钮的位置换成封账时刻那句回执。
 *  这一步**不广播 nudge**（员工手机上的班与假一个字都没变），所以只能自己拉。 */
async function sealLastMonth() {
  if (sealing.value) return
  if (!window.confirm(SEAL_CONFIRM_TEXT)) return
  sealing.value = true
  sealError.value = ''
  try {
    await api.post('/api/attendance/seal')
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    sealError.value = err.message || '这个月没封成'
  } finally {
    sealing.value = false
  }
}

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    sheet.value = await api.get('/api/attendance/sheet')
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '读不出这个月的考勤表'
    state.value = 'error'
  }
}

/** 正在等导出件（票 08）：只是按钮上那句「导出中…」，不挡页面上的别的动作。 */
const exporting = ref(false)
const exportError = ref('')

/** 一键导出当月考勤表（票 08）：一条 GET，服务端把工作簿直接下发。
 *
 *  文件名由**响应头**（`Content-Disposition`）带回来 —— 这一页不拼 `考勤表-YYYY-MM.xlsx`，
 *  否则页面上的月份与服务端算的月份会各说各话。`api.download` 会触发浏览器的保存对话框，
 *  失败（401 / 缺表 503）时把理由写出来，页面不装作导出成功了。 */
async function exportSheet() {
  if (exporting.value) return
  exporting.value = true
  exportError.value = ''
  try {
    await api.download('/api/attendance/sheet.xlsx')
  } catch (err) {
    exportError.value = err.message || '这个月的考勤表没导出来'
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  document.title = workbenchDocumentTitle('考勤表')
  load()
})
</script>

<template>
  <div class="hygiene-admin attendance-page">
    <header class="aTop">
      <div class="aIntro">
        <p class="aKicker">人事 · 考勤</p>
        <div class="aTitleRow">
          <h1>考勤表</h1>
        <div class="aActions">
          <!-- 封过账的月只交代一句「什么时候封的」（验收 5）；没封过才给那颗按钮。
               回执取自服务端下发的封账时刻，页面不自己记一个「我刚封过」的本地状态。 -->
          <span v-if="sealStamp" class="aSealed" role="status">{{ sealStamp }}</span>
          <button
            v-else
            class="btn"
            type="button"
            :disabled="sealing"
            @click="sealLastMonth"
          >
            {{ sealing ? '封账中…' : '封账上月' }}
          </button>
          <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">
            回到月历
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="exporting"
            @click="exportSheet"
          >
            {{ exporting ? '导出中…' : '导出这个月' }}
          </button>
        </div>
        </div>
      </div>
      <div class="aMetaRow">
        <details class="aHelp">
          <summary>怎么用</summary>
          <p class="aSub">
            <template v-if="sheet">当月 {{ sheet.month }} · 今天 {{ sheet.today }} · </template>
            员工面板默认折叠，点「待补排行」里的人或右边「展开」看明细。
            点一个已经过去的日子就能改它 —— 只改那一天，不动轮转规则。
          </p>
        </details>
        <span class="aScope">{{ sheet ? sheet.month : '—' }} 月</span>
      </div>
    </header>

    <p v-if="state === 'loading'" class="aHint">正在读这个月的考勤表…</p>
    <p v-if="state === 'error' && errorText" class="aErr" role="alert">{{ errorText }}</p>
    <p v-if="sealError" class="aErr" role="alert">{{ sealError }}</p>
    <p v-if="exportError" class="aErr" role="alert">{{ exportError }}</p>

    <!-- 卡片这一棵**不进 `v-if/v-else`**（用 `v-show` 藏）：它是改完一格要立刻重画的那
         一棵，而重挂一棵 `v-else` 子树会让里面的格子停在旧值上（实测：整页重新挂载的
         那一拍，格子还是改之前的班次名）。 -->
    <!-- 当月只算不落库（票 03 / ADR 0113）：这一条要看得见，否则半个月的数会被拿去结账。 -->
    <p v-if="sheet && sheet.provisional" class="aProvisional" role="status">
      暂算值（未封账）：这个月的数还会随排班与加班变，封账之后才定稿。
    </p>

    <!-- 读数：一条三格（在职 / 待补 / 本月），高度 ≤ 76px。 -->
    <div v-if="state === 'ready'" class="aKpis">
      <span class="aKpi"><i>在职</i><b>{{ employees().length }}</b><em>人</em></span>
      <span class="aKpi"><i>待补</i><b>{{ todoTotal }}</b><em>天</em></span>
      <span class="aKpi"><i>本月</i><b>{{ monthDayCount }}</b><em>天</em></span>
    </div>

    <!-- 页头下第一块：待补排行（降序，点人展开他的面板）。 -->
    <section v-if="state === 'ready'" class="aRankPanel">
      <div class="aRank-hd">
        <i class="aLed warn"></i>
        <h2>待补排行</h2>
        <span class="aRank-hint">按待补天数降序 · 点人展开</span>
      </div>
      <p v-if="!todoRank.length" class="aEmptyLine">没有待补：这个月每条排班都铺到了。</p>
      <ul v-else class="aRank">
        <li v-for="person in todoRank" :key="person.id">
          <button type="button" class="aRankRow" @click="focusEmployee(person)">
            <b>{{ person.name || '（没有名字）' }}</b>
            <span class="aRankDays">待补 {{ todoDays(person) }} 天</span>
            <span class="aRankNet">{{ formatNetHours(person.net_hours) }}</span>
            <span class="aRankGo">›</span>
          </button>
        </li>
      </ul>
    </section>

    <div v-show="state === 'ready'" class="aSheet">
      <p v-if="!employees().length" class="aEmpty">
        这个月还没有在职员工，也没有人有排班或加班记录。
      </p>
      <article
        v-for="person in employees()"
        :id="`emp-${person.id}`"
        :key="person.id"
        class="aCard"
        :class="{ 'is-open': openEmployeeId === person.id }"
      >
        <header class="aCard-hd">
          <i class="aLed sig"></i>
          <b class="aName">{{ person.name || '（没有名字）' }}</b>
          <span class="aCardMeta">{{ person.job_title || '—' }}</span>
          <span v-if="todoDays(person)" class="aTodo">待补 {{ todoDays(person) }} 天</span>
          <span class="aCardNet">{{ formatNetHours(person.net_hours) }}</span>
          <button
            class="aCardToggle"
            type="button"
            :aria-expanded="openEmployeeId === person.id ? 'true' : 'false'"
            @click="toggleEmployee(person)"
          >{{ openEmployeeId === person.id ? '收起' : '展开' }}</button>
        </header>
        <p class="aStats">
          <span class="aStat"><i>职位</i><b>{{ person.job_title || '—' }}</b></span>
          <span class="aStat">
            <i>底薪</i><b>{{ person.base_salary === null || person.base_salary === undefined ? '—' : `${person.base_salary} 元` }}</b>
          </span>
          <span class="aStat">
            <i>年限</i><b>{{ person.seniority_bonus === null || person.seniority_bonus === undefined ? '—' : `${person.seniority_bonus} 元` }}</b>
          </span>
          <!-- 入职日期为空的人这一格是**空的**（服务端给 `null`）：留空，不写成「否」——
               「算不出来」与「不是新来的」是两件事。 -->
          <span class="aStat"><i>是否新入职</i><b>{{ newHireText(person.new_hire) }}</b></span>
          <!-- 净时长只算**已批准**的单：还没批的那几笔在这儿标色并报出小时数，
               一分都不进这个数字（故事 14）。 -->
          <span class="aStat" :class="{ 'is-pending': person.pending_count > 0 }">
            <i>当月净时长</i><b class="aNet">{{ formatNetHours(person.net_hours) }}</b>
            <em v-if="person.pending_count" class="aPending">
              {{ pendingOvertimeText(person.pending_count, person.pending_hours) }}
            </em>
          </span>
        </p>
        <div class="aDaysHead">
          <span>当月明细</span>
          <span class="aDaysHint">{{ person.cells.length }} 天 · 点过去的日子改</span>
        </div>
        <div class="aDays">
          <span
            v-for="cell in person.cells"
            :key="cell.business_date"
            class="aDay"
            :class="[
              `is-${cell.state || 'future'}`,
              {
                'is-today': cell.is_today,
                'is-half': cell.state === 'work' && cell.half_rest,
                'is-editing': isEditing(person, cell),
                // 服务端拒过的那些天给红边（理由在下面的编辑带里）。
                'is-error': Boolean(errorOn(person, cell)),
              },
            ]"
            :title="cell.business_date"
          >
            <!-- 还没到的日子是一块**死的**格子（不渲染任何控件）：验收 45 说的是
                 「不能编辑」，能点一下再弹一句拒绝不算数。 -->
            <!-- 编辑态那一格**留在它原来的列位上**（B 批返工 ⑥）：日期数字与文案照旧，
                 只多一个 `is-editing` 记号（显眼的边框底色）；控件在网格下面的编辑带里。
                 它自己**不吃点击** —— 收工走带子里那颗「取消」，免得再点一下把带子收了。 -->
            <template v-if="isEditing(person, cell)">
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </template>
            <button
              v-else-if="editable(cell)"
              class="aDayBtn"
              type="button"
              :disabled="Boolean(savingDate)"
              @click="openEditor(person, cell)"
            >
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </button>
            <template v-else>
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </template>
          </span>
        </div>
        <!-- 编辑带（B 批返工 ⑥）：控件摊在网格**下面**这一条带子里，网格一格都不动。
             上一版把被点的那一格 `grid-column: 1 / -1` 拔出来独占整行，于是它离开原列、
             后面的日子被挤到下一行开头，7 格一行的对齐在编辑期间全破（用户截图就是这个）。
             只在有一格进入编辑态时才出现（`editingCells` 给 0 或 1 个）。 -->
        <template v-for="cell in editingCells(person)" :key="`edit-${cell.business_date}`">
          <div class="aEditBar" role="group" :aria-label="`改 ${cell.day} 号的班`">
            <p class="aEditNow">
              <b>{{ cell.day }} 号</b>
              <span v-if="cell.is_today" class="aEditToday">（今天）</span>
              <!-- 那一天**现在**是什么：用格子上的同一套说法（`cellLabel`），不编第二种。 -->
              <span>现在 {{ cellLabel(person, cell) }}</span>
            </p>
            <div class="aEditChips">
              <!-- 该员工**启用中**的班次各一颗（与原先那个下拉里的选项同源）。 -->
              <button
                v-for="shift in person.shifts"
                :key="shift.id"
                class="aChip"
                type="button"
                :class="{ 'is-on': cell.state === 'work' && cell.shift_id === shift.id }"
                :disabled="Boolean(savingDate)"
                @click="pick(person, cell, shift.id)"
              >
                {{ shift.name }}
              </button>
              <button
                class="aChip"
                type="button"
                :class="{ 'is-on': cell.state === 'rest' }"
                :disabled="Boolean(savingDate)"
                @click="pick(person, cell, 'rest')"
              >
                休
              </button>
              <!-- 半天休（票 07 / ADR 0114）：那一格**有班次**（上班那一态）时才谈得上
                   「只上半天」—— 休与待补的两天没有半天可标，所以这颗 chip 不出现。
                   它是**单独的**一次写：班次 chip 回答「那天上哪个班」，这一颗回答
                   「上不上满一整天」，两件事不塞进同一个控件。 -->
              <button
                v-if="cell.state === 'work'"
                class="aChip aHalfBtn"
                type="button"
                :class="{ 'is-on': cell.half_rest }"
                :disabled="Boolean(savingDate)"
                :aria-pressed="cell.half_rest ? 'true' : 'false'"
                @click="toggleHalf(person, cell)"
              >
                {{ cell.half_rest ? '取消休半天' : HALF_REST_TEXT }}
              </button>
              <button class="aChip aChipQuiet" type="button" @click="closeEditor">
                取消
              </button>
            </div>
            <!-- 服务端拒绝的理由写在**这条带子**里（不装作改成功了）。 -->
            <small v-if="errorOn(person, cell)" class="aDayErr" role="alert">
              {{ errorOn(person, cell) }}
            </small>
          </div>
        </template>
        <!-- 四个**手填列**（票 05）：系统里推不出来的那几项（是否住宿 / 借调门店 / 是否离职
             在花名册里压根没有字段），由超管在这儿填一次、存在月度考勤台账当月那一行上。
             只进卡片与（票 08 的）导出件，**不写回员工档案**。

             「本月班次」那一格：填过就显示台账里的值；没填过时输入框是**空的**、把当月
             众数放在提示语里 —— 默认值不能被预填，否则店长一存就把这条默认固化成他定的值，
             以后再改排班这一格也不跟着变了。 -->
        <div class="aManual">
          <!-- 2026-10-29 用户：「太占地方」。这一块在 390px 上是 502px（手填四格 + 期初 /
               期末，约一屏半），而它是**低频**的（填一次就进台账），所以默认收成**一行摘要**，
               点「编辑」才摊开输入框。摘要念的就是台账里存了什么 + 那一对结余 ——
               收起来也看得见这张卡当前的账，不是什么都藏掉。 -->
          <button
            class="aManualHead"
            type="button"
            :aria-expanded="manualOpen(person) ? 'true' : 'false'"
            :aria-controls="`manual-body-${person.id}`"
            @click="toggleManual(person)"
          >
            <span class="aManualTitle">手填列</span>
            <!-- 展开时把摘要收掉：同一批值在 body 里还有一份回显（`.aManualNow`，紧贴
                 「存这三项」），两处都摆就是同一句话说两遍。 -->
            <span v-if="!manualOpen(person)" class="aManualSummary">
              <span v-for="item in manualSummary(person)" :key="item.label" class="aManualSummaryOne">
                <i>{{ item.label }}</i><b>{{ item.value }}</b>
              </span>
            </span>
            <span class="aManualAct">{{ manualOpen(person) ? '收起' : '编辑' }}</span>
          </button>
          <div
            v-show="manualOpen(person)"
            :id="`manual-body-${person.id}`"
            class="aManualBody"
          >
          <p class="aManualHelp">这几项系统里没有，填一次就进台账，导出的表直接用它。</p>
          <div class="aManualRow">
            <label v-for="field in MANUAL_INPUTS" :key="field.key" class="aManualField">
              <span class="aManualLabel">{{ field.label }}</span>
              <input
                class="aManualInput"
                type="text"
                :data-field="field.key"
                :aria-label="`${person.name || ''} 的${field.label}`"
                :value="manualValue(person, field)"
                :title="field.key === 'borrowed_store' ? BORROWED_STORE_NOTE : undefined"
              />
              <small class="aManualHint">{{ field.hint(person) }}</small>
            </label>
          </div>
          <!-- 人工期初（收尾修复 ② / 故事 35）：**上线当月手工给一次**的「上月末余额」，
               写的是月度台账**本月那一行**的 `opening_balance`（票 03 的裁定）。与服务端
               那条月级写口同一扇门，但自己一颗按钮：它是一次性的账，不是日常那一格。
               B 批返工 ③：它排在**回显与「存这三项」之后** —— 那两者是一对（存了什么、存
               在下边那颗按钮上），中间岔进期初这一块，店长会以为这一格也归「存这三项」管。 -->
          <!-- 「这一块现在是什么」由服务端那一份说了算（重拉之后写的就是最新的）。
               2026-10-29 排版：回显与「存这三项」并成一行（回显在左、按钮贴右），
               不再各占一行、按钮孤零零吊在左边。 -->
          <div class="aManualFoot">
            <p class="aManualNow">
              <span v-for="stat in manualStats(person)" :key="stat.label" class="aManualNowOne">
                <i>{{ stat.label }}</i><b>{{ manualText(stat.value) }}</b>
              </span>
            </p>
            <small v-if="manualErrorOn(person.id)" class="aManualErr" role="alert">
              {{ manualErrorOn(person.id) }}
            </small>
            <button
              class="btn btn-sm btn-primary aSave"
              type="button"
              :disabled="savingManual === person.id"
              @click="saveManual(person, $event)"
            >
              存这三项
            </button>
          </div>
          <div class="aOpening">
            <label class="aOpeningField">
              <span class="aOpeningLabel">期初存假（上月结转）</span>
              <input
                class="aOpeningInput"
                type="text"
                inputmode="decimal"
                data-field="opening_balance"
                :aria-label="`${person.name || ''} 的期初存假`"
                :value="openingValue(person)"
                :disabled="sealedNow"
              />
              <small class="aOpeningHint">只在上线当月手工给一次；可为负、可带 .5</small>
            </label>
            <!-- 期末存假（本月剩余，票 09）：当月**随时可填可改**，填了就以它为准
                 （封账把它连同 `sealed_at` 写进台账定稿，导出件的「存休天数」也跟着走）；
                 没填过就照旧用公式算出来的那个数 —— 那一位只出现在下面那句「系统建议」里，
                 绝不预填进输入框（预填等于替店长认账）。 -->
            <label class="aClosingField">
              <span class="aClosingLabel">期末存假（本月剩余）</span>
              <input
                class="aClosingInput"
                type="text"
                inputmode="decimal"
                data-field="closing_balance_stored"
                :aria-label="`${person.name || ''} 的期末存假`"
                :value="closingValue(person)"
                :disabled="sealedNow"
              />
              <small class="aClosingHint">不填就用系统建议；可为负、可带 .5</small>
              <small v-if="closingNote(person)" class="aClosingNote">{{ closingNote(person) }}</small>
            </label>
            <button
              class="btn btn-sm aOpeningSave"
              type="button"
              :disabled="savingOpening === person.id || sealedNow"
              @click="saveOpening(person, $event)"
            >
              存期初 / 期末
            </button>
            <!-- 封账之后这一格锁定（票 09）：两个框都禁用，原因写在块里 ——
                 光把按钮变灰，店长看不出来是「这次没写上」还是「这个月已经定稿」。 -->
            <small v-if="sealedNow" class="aClosingSealed" role="status">
              这个月已经封账，结余定稿 —— 期初 / 期末都不再改。
            </small>
            <small v-if="openingErrorOn(person.id)" class="aOpeningErr" role="alert">
              {{ openingErrorOn(person.id) }}
            </small>
          </div>
          </div>
        </div>
        <!-- 假期台账（票 03）：每张卡把自己的账摊开；算式收在「看算式」后面，
             摊开后能一格一格核（服务端下发的数，页面一个都不自己算）。
             2026-10-29 排版：去掉「假期台账」那行小标题（它自己的 9px 行 + 9px 空隙
             就是纯占位），九项改成自适应网格，390px 下 3 列 3 行、宽屏一行。 -->
        <div v-if="person.leave" class="aLeave">
          <p class="aLvRow">
            <span v-for="stat in leaveStats(person)" :key="stat.label" class="aLv">
              <i>{{ stat.label }}</i><b>{{ stat.value }}</b>
            </span>
          </p>
          <details class="aFormula">
            <summary>看算式</summary>
            <p class="aFormulaText">{{ leaveFormulaText(person.leave) }}</p>
          </details>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
/* 版式照人事组的其它页：深青墨底、一张卡一个人。令牌**只用语义层**（`--ink` / `--surface-1`
   / `--line` / `--ok` / `--warn` / `--r-*` / `--fs-*`，定义在 `theme.workbench.css`）——
   新文件在设计护栏里按 strict 处理（`__tests__/tokenDebt.json` 的棘轮只认存量，
   `src/styles/__tests__/designGuard.test.js` 盯着），写兼容层的 `--hy-*` 或不透明色值会红。 */
/* B 案 v2 版式（2026-10-17）：页头 kicker + H1 + 右侧读数胶囊 / 动作；下面是三块读数，
   再往下是「一人一张面板」。配色不变（语义令牌；本文件 strict，不引 --hy-*）。 */
.attendance-page {
  display: flex; flex-direction: column; gap: var(--sp-4);
  padding: 18px clamp(16px, 4vw, 32px) calc(40px + var(--safe-b));
  width: 100%; max-width: 1052px; margin: 0 auto; font-family: var(--font-sans);
}
/* 页头 3 行：kicker / H1 + 动作同基线 / 怎么用 + 月份 scope。 */
.aTop {
  display: flex; flex-direction: column; gap: 7px; flex-wrap: wrap;
}
.aIntro { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.aTitleRow { display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0; }
.aTop h1 {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.aKicker {
  margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 500; letter-spacing: .26em; color: var(--ink-faint);
}
.aMetaRow { display: flex; align-items: center; gap: var(--sp-4); flex-wrap: wrap; }
.aScope {
  margin-left: auto; padding: 6px 10px; border: 1px solid var(--line-strong);
  border-radius: var(--r-1); background: var(--surface-1); color: var(--ink-dim);
  font-family: var(--font-mono); font-size: 10px; line-height: 1;
}
.aSub { margin: 0; padding: 0 14px 12px; max-width: 760px; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.aHelp {
  border: 1px solid var(--line); border-radius: var(--r-1); background: var(--surface-1);
}
.aHelp summary {
  display: flex; align-items: center; gap: 8px; min-height: var(--tap);
  padding: 0 14px; cursor: pointer;
  font-family: var(--font-sans); font-size: 9.5px; letter-spacing: .18em; color: var(--ink-faint);
}
.aHelp summary::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--action);
}
.aHelp .aSub { padding-top: 2px; }
.aLed { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.aLed.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.aLed.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
/* 读数行：一条三格（在职 / 待补 / 本月），高约 54px。 */
.aKpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.aKpi {
  display: flex; align-items: baseline; gap: 8px; padding: 10px 14px;
  background: var(--surface-1); border: 1px solid var(--line); border-radius: var(--r-2);
}
.aKpi i {
  font-style: normal; font-family: var(--font-sans); font-size: 9.5px;
  letter-spacing: .18em; color: var(--ink-faint);
}
.aKpi b { font-family: var(--font-mono); font-size: 20px; line-height: 1; font-weight: 500; color: var(--ink); }
.aKpi em { font-style: normal; font-size: 9.5px; color: var(--ink-faint); }
/* 待补排行（页头下第一块）：姓名 / 待补天数 / 净时长，点人展开。 */
.aRankPanel {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: none; overflow: hidden;
}
.aRank-hd {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border-bottom: 1px solid var(--line);
}
.aRank-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.aRank-hint { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1; color: var(--ink-faint); }
.aRank { list-style: none; margin: 0; padding: 0; }
.aRankRow {
  display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: center; gap: 12px; width: 100%; min-height: 48px; padding: 6px 14px;
  border: 0; border-top: 1px solid var(--wb-ink-a08); background: transparent;
  color: var(--ink); font: inherit; text-align: left; cursor: pointer;
}
.aRank li:first-child .aRankRow { border-top: 0; }
.aRankRow:hover { background: var(--surface-2); }
.aRankRow b { font-size: 12.5px; font-weight: 600; }
.aRankDays { font-family: var(--font-mono); font-size: 10px; color: var(--warn); }
.aRankNet { font-family: var(--font-mono); font-size: 10px; color: var(--ink-dim); }
.aRankGo { color: var(--ink-faint); }
.aEmptyLine { margin: 0; padding: 12px 14px; font-size: 11px; color: var(--ink-faint); }
.aSheet { display: flex; flex-direction: column; gap: 14px; }.aSheet { display: flex; flex-direction: column; gap: 14px; }
/* 封账（票 04）与「回到月历」并排在右边：回执是一句话、没封过时是一颗按钮。
   `.aTop .btn` 那个 `margin-left: auto` 是给「header 里只有一颗按钮」写的，
   包进这一行之后由这一行的 `margin-left: auto` 顶上，里面的按钮不再各自外推。
   **能缩、能换行**（B 批返工 ①）：这一组最宽的时候是封账回执那句话 + 两颗按钮（约 420px），
   `flex: none` 会让它整条顶出 390px 的屏幕（实测溢出到 438px）；`min-width: 0` 解开
   flex 的 `auto` 下限，窄屏就在这一组内部折成两行，右对齐。 */
.aActions {
  display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end;
  gap: var(--sp-2); margin-left: auto; flex: 0 1 auto; min-width: 0;
}
.aTop .aActions .btn { margin-left: 0; }
.aSealed { margin: 0; font-family: var(--font-mono); font-size: var(--fs-12); color: var(--ink-dim); }
.aHint, .aEmpty { margin: 0; font-size: var(--fs-13); color: var(--ink-dim); }
/* 当月是暂算值（票 03）：这一条不是装饰 —— 它是「别拿这个数去结账」的告示。 */
.aProvisional {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--info); background: var(--info-soft);
  font-size: var(--fs-12); color: var(--ink);
}
.aErr {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--danger); background: var(--danger-soft);
  font-size: var(--fs-13); color: var(--danger-bright);
}
.aCard {
  padding: 0; border: 1px solid var(--line); overflow: hidden;
  /* 圆角与节假日日历页的卡片一致（`--r-2`，B 批返工 ⑧）：同一个人事组里两张卡不该
     一个 16px 一个 12px。 */
  border-radius: var(--r-2); background: var(--surface-1);
}
.aCard-hd {
  display: flex; align-items: center; gap: 9px; min-height: 56px; padding: 8px 14px;
  border-bottom: 1px solid var(--line);
}
.aCard:not(.is-open) .aCard-hd { border-bottom: 0; }
/* 默认折叠：只留头一行表格；点「展开」或排行里的人再出明细。 */
.aCard:not(.is-open) .aStats,
.aCard:not(.is-open) .aDaysHead,
.aCard:not(.is-open) .aDays,
.aCard:not(.is-open) .aEditBar,
.aCard:not(.is-open) .aManual,
.aCard:not(.is-open) .aLeave { display: none; }
/* 长名字不许把「待补」那颗 pill 挤出卡片（B 批返工 ⑧）：名字自己截断、
   `min-width: 0` 让它在 flex 里真的能缩下去（默认 `min-width: auto` 会顶住）。 */
.aName {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 12.5px; line-height: 1; font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.aCardMeta { flex: none; font-size: 10px; color: var(--ink-faint); }
.aCardNet {
  margin-left: auto; flex: none; font-family: var(--font-mono);
  font-size: 10px; line-height: 1; color: var(--ink-dim);
}
.aCardToggle {
  flex: none; min-height: var(--tap); padding: 0 10px;
  border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: transparent; color: var(--ink-dim); font: inherit; font-size: 10px; cursor: pointer;
}
.aTodo {
  flex: none; display: inline-flex; align-items: center; gap: 5px;
  font-size: 9px; letter-spacing: .06em; color: var(--warn); white-space: nowrap;
}
/* 状态 = 6px 圆点 + 小字（待补 / 待批 = --warn）。 */
.aTodo::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%;
  background: var(--warn);
}
.aStats {
  display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px;
  margin: 0; padding: 12px 14px; border-bottom: 1px solid var(--line);
}
.aStat { display: flex; flex-direction: column; gap: 4px; min-width: 0; font-size: var(--fs-11); }
.aStat i {
  font-style: normal; font-family: var(--font-sans); font-size: 8.5px;
  letter-spacing: .12em; color: var(--ink-faint); white-space: nowrap;
}
.aStat b {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono); font-size: 13px; color: var(--ink);
}
.aNet { font-family: var(--font-mono); }
.aDaysHead {
  display: flex; align-items: baseline; gap: 10px; padding: 11px 14px 7px;
  border-top: 1px solid var(--wb-ink-a08);
}
.aDaysHead > span:first-child {
  font-family: var(--font-sans); font-size: 9px; font-weight: 500;
  letter-spacing: .18em; color: var(--ink-faint);
}
.aDaysHint { margin-left: auto; font-family: var(--font-mono); font-size: 9px; color: var(--ink-faint); }
.aDays {
  display: grid; grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: var(--sp-1); padding: 0 14px 14px;
}
.aDay {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 1px; min-height: var(--tap);
  padding: var(--sp-1) 2px; border-radius: var(--r-1);
  border: 1px solid var(--line); background: transparent;
  /* 改不了的那一格（今天之后）不该出现可点的光标。 */
  cursor: default;
}
.aDayNum { font-family: var(--font-mono); font-size: var(--fs-11); font-style: normal; color: var(--ink-faint); }
/* 「今天」的第二个记号（B 批返工 ②）：颜色不是唯一线索，格子里那个数字后面跟一个
   「今」字。它跟日期数字同一行，格子高度不变。 */
.aTodayMark { margin-left: 2px; font-style: normal; color: var(--accent); }
.aDayText { font-size: var(--fs-11); font-weight: 500; color: var(--ink-dim); }
/* 能点的那一格是一颗按钮，但长得跟别的格子一样：卡片是 30 个格子，多一层按钮边框会吵。 */
.aDayBtn {
  display: flex; flex-direction: column; align-items: center; gap: 1px; width: 100%;
  padding: 0; border: 0; background: transparent; color: inherit;
  font: inherit; cursor: pointer;
}
.aDayBtn:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }
.aDay:has(.aDayBtn:hover) { border-color: var(--line-strong); background: var(--ok-soft); }
/* 服务端拒绝的理由写在**编辑带**里（见 `.aEditBar`），这一格只给一条红边。 */
.aDayErr { font-size: var(--fs-11); line-height: 1.5; color: var(--danger-bright); }
/* 三态各自的色：上班是常规（薄荷）、休淡下去、待补最醒目（琥珀，要人工确认）。 */
.aDay.is-work { border-color: var(--line-strong); background: var(--ok-soft); }
.aDay.is-work .aDayText { color: var(--ok); }
/* 半天休（票 07）：仍然上班，只是只上半天 —— 自己一档色（信息蓝），跟「待补」的琥珀
   与「上班」的薄荷都分得开。 */
.aDay.is-half { border-color: var(--info); background: var(--info-soft); }
.aDay.is-half .aDayText { color: var(--info); }
.aDay.is-rest { background: transparent; }
.aDay.is-rest .aDayText { color: var(--ink-faint); }
.aDay.is-todo { border-color: var(--warn); background: transparent; }
.aDay.is-todo .aDayText { color: var(--warn); }
/* 今天之后：**留空**，连边框都不给 —— 一眼看得出那一片还没发生。 */
.aDay.is-future { border-color: transparent; }
/* 「今天」（B 批返工 ②）：描边走 `--accent`。原先它与「待补」同用 `--warn`，而「今天」
   没有任何文字标记 —— 两者在卡片上分不开。琥珀色从此只归「待补」，今天的第二重标记
   是格子里那个「今」字（`--accent` 也用在它身上）。 */
.aDay.is-today { outline: 1px solid var(--accent); outline-offset: 1px; }
.aDay.is-today .aDayNum { color: var(--ink); font-weight: 700; }
/* 就地编辑（票 06/07，B 批返工 ⑥）：被点的那一格**留在它原来的列位上** —— 只给一个
   显眼的边框与底色，让人看得出正在改哪一天。控件**不在这格里**，它们在网格下面的
   编辑带里（`.aEditBar`）。上一版把这一格 `grid-column: 1 / -1` 拔出来独占整行，
   于是它离开原列、后面的日子被挤到下一行开头，7 格一行的对齐在编辑期间全破。
   这两条排在 `is-half` 之后：半天休的那天被点开时，编辑记号要盖过它的蓝。 */
.aDay.is-editing { border-color: var(--accent); background: var(--info-soft); }
.aDay.is-editing .aDayText { color: var(--ink); }
.aDay.is-editing .aDayNum { color: var(--ink); font-weight: 700; }
/* 服务端拒过的那一格给红边：页面上不装作改成功了（理由在编辑带里）。 */
.aDay.is-error { border-color: var(--danger); }
/* 编辑带（B 批返工 ⑥）：控件摊在 `.aDays` **下面**的一条带子里 —— 网格一格都不动。
   抬头一行说清「改哪一天、那一天现在是什么」，下面一排 chips（能换行，390px 下不溢出）。 */
.aEditBar {
  display: flex; flex-direction: column; gap: var(--sp-1);
  margin-top: var(--sp-2); padding: var(--sp-2);
  border: 1px solid var(--accent); border-radius: var(--r-2); background: var(--surface-2);
}
.aEditNow {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 5px; margin: 0;
  font-size: var(--fs-12); color: var(--ink-dim);
}
.aEditNow b { font-size: var(--fs-13); color: var(--ink); }
.aEditToday { color: var(--accent); }
.aEditChips { display: flex; flex-wrap: wrap; gap: var(--sp-1); }
/* 每一颗 chip 都是**自己的按钮**（`type="button"`）：班次 / 休 / 休半天 各是一次写，
   「取消」只是收起带子。当前生效的那一颗给 `is-on`（那天上哪个班、是不是休、是不是只上半天）。 */
.aChip {
  min-height: var(--tap); padding: 2px var(--sp-2);
  border: 1px solid var(--line-strong); border-radius: var(--r-full);
  background: transparent; color: var(--ink-dim);
  font: inherit; font-size: var(--fs-11); line-height: 1.6; cursor: pointer;
}
.aChip:hover:not(:disabled) { border-color: var(--accent); color: var(--ink); }
.aChip:disabled { opacity: .5; cursor: default; }
.aChip:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }
.aChip.is-on { border-color: var(--accent); background: var(--info-soft); color: var(--ink); font-weight: 600; }
/* 「休半天」那一颗（票 07）：跟「半天休」那一档格子同色（信息蓝），与班次 / 休分得开。 */
.aChip.aHalfBtn { border-color: var(--info); color: var(--info); }
.aChip.aHalfBtn.is-on { background: var(--info-soft); color: var(--info); }
/* 「取消」是条安静的路：不跟那几颗写动作抢眼。 */
.aChipQuiet { border-color: var(--line); color: var(--ink-faint); }
/* 未审批的加班 / 补钟（票 03 / 故事 14）：**只标色**，数不进净时长。 */
.aStat.is-pending .aNet { color: var(--warn); }
.aPending {
  display: inline-flex; align-items: center; gap: 5px;
  font-style: normal; font-size: 9px; letter-spacing: .06em; color: var(--warn);
}
.aPending::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--warn);
}
/* 假期台账那一块（票 03）：九项摊在卡片上，算式收在「看算式」后面。
   2026-10-29 排版：去掉「假期台账」那行小标题（它自己的 9px 行 + 9px 空隙纯占位），
   九项改成**自适应网格**（390px 下 3 列 3 行、1440px 下一行九列），标签与值同一基线 ——
   原来 `flex-wrap` 按内容排，窄屏上落成 5 行、198px 高。 */
.aLeave { margin-top: 0; padding: 10px 14px 12px; border-top: 1px solid var(--line); }
.aLvRow {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 4px var(--sp-2); margin: 0;
}
.aLv { display: flex; align-items: baseline; gap: 4px; min-width: 0; font-size: var(--fs-11); }
/* 标签压到 10px：390px 下三列各 115px，「本月出勤天数 31 天」在 11px 时会溢出到
   下一行（实测：九项落成 4 行、168px）。10px 刚好一行放下，且与别处的 9px 小标签
   同档可读。 */
.aLv i { font-style: normal; white-space: nowrap; font-size: 10px; color: var(--ink-faint); }
.aLv b { font-family: var(--font-mono); font-size: var(--fs-12); color: var(--ink); white-space: nowrap; }
.aFormula summary {
  display: inline-flex; align-items: center; min-height: var(--tap);
  cursor: pointer; font-size: var(--fs-12); color: var(--ink-dim);
}
.aFormulaText {
  margin: var(--sp-2) 0 0; font-family: var(--font-mono); font-size: var(--fs-11);
  line-height: 1.9; color: var(--ink-dim);
}
/* 四个手填列（票 05）：系统里没有的那几项，填一次就进台账。
   2026-10-29 排版（第二版）：这一块**默认收成一行摘要**（用户：「太占地方」，390px 上
   502px）。`head` 那一行常显（台账里存了什么 + 期初 / 期末），点「编辑」才出 body 里的
   四格输入 + 两颗保存按钮。四格本身仍是等宽网格（宽屏一行四个、窄屏两行两个）。 */
.aManual { margin-top: 0; border-top: 1px solid var(--line); }
/* `head` 是**三列网格**：标签（9px 小字）/ 摘要（可换行，占满中间）/ 「编辑」贴右。
   用 `grid` 而不是 `flex-wrap`：后者会让「编辑」那颗按钮被 `margin-left: auto`
   单独挤到第三行（390px 实测：摘要折两行 + 按钮一行 = 102px）。 */
.aManualHead {
  display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center; gap: 3px var(--sp-2);
  width: 100%; min-height: var(--tap); padding: 9px 14px;
  border: 0; background: transparent; color: inherit;
  font: inherit; text-align: left; cursor: pointer;
}
.aManualHead:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }
.aManualTitle {
  flex: none; font-family: var(--font-sans); font-size: 9px; line-height: 1.6;
  letter-spacing: .18em; color: var(--ink-faint);
}
.aManualSummary {
  display: flex; flex-wrap: wrap; gap: 3px var(--sp-3);
  min-width: 0;
}
.aManualSummaryOne { display: inline-flex; align-items: baseline; gap: 4px; font-size: var(--fs-11); }
.aManualSummaryOne i { font-style: normal; color: var(--ink-faint); }
.aManualSummaryOne b { font-family: var(--font-mono); font-size: var(--fs-12); color: var(--ink); }
/* 「编辑 / 收起」贴在行尾，样子跟卡片头的「展开 / 收起」一致（同一页两颗开关不该长两样）。
   `justify-self: end` 不能省：展开时摘要那一格（`v-if`）不在了，网格只剩两列，
   这一颗会被丢进中间的 `1fr` 那条、被拉成整行宽。 */
.aManualAct {
  flex: none; justify-self: end; padding: 2px 8px;
  border: 1px solid var(--line-strong); border-radius: var(--r-1);
  font-size: 10px; line-height: 1.6; color: var(--ink-dim);
}
.aManualBody { padding: 2px 14px 14px; }
/* 展开后那一句说明（收起时它不进占位；`title` 那样只挂悬停，触摸屏上读不到）。 */
.aManualHelp { margin: 0 0 10px; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }
.aManualRow {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: var(--sp-2) var(--sp-3);
}
.aManualField { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.aManualLabel { font-size: var(--fs-11); color: var(--ink-faint); }
.aManualInput {
  width: 100%; min-height: var(--tap); padding: 4px var(--sp-2); border: 1px solid var(--line);
  border-radius: var(--r-1); background: var(--surface-2); color: var(--ink);
  font-size: var(--fs-12); font-family: inherit;
}
.aManualHint { margin: var(--sp-2) 0 0; font-size: var(--fs-11); color: var(--ink-faint); }
.aManualNow { display: flex; flex-wrap: wrap; gap: 5px var(--sp-4); margin: 0; }
.aManualNowOne { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aManualNowOne i { font-style: normal; color: var(--ink-faint); }
.aManualNowOne b { font-family: var(--font-mono); font-size: var(--fs-13); color: var(--ink); }
/* 回显与「存这三项」同一行：回显占满左边，按钮贴右（2026-10-29 排版）。窄屏回显先换行，
   按钮仍在右下角，不再孤零零吊在左边。 */
.aManualFoot {
  display: flex; align-items: center; flex-wrap: wrap;
  gap: var(--sp-2); margin-top: var(--sp-3);
}
.aManualFoot .aManualNow { flex: 1 1 auto; min-width: 0; }
.aManualFoot .aSave { flex: none; margin-left: auto; }
.aManualErr { font-size: var(--fs-11); color: var(--danger-bright); }
/* 「借调门店」那处与历史表的偏差（收尾修复 ⑥）现在挂在输入框的 `title` 上（见
   `BORROWED_STORE_NOTE`）—— 悬停可见，不占三行可视小字把这一排输入口拽散。 */
/* 人工期初（收尾修复 ②）与人工期末（票 09）：台账上的两条结余摆成**一对**、一颗按钮
   一起存（见 `saveOpening`）—— 期初是上线当月手工给一次的那一位，期末是当月随时能定稿的
   那一位。两条的版式一样（标签 / 输入口 / 提示语 / 建议那一句）。
   2026-10-29 排版：两格**等宽两列**（`grid`，输入框填满列宽），「存期初 / 期末」跨两列
   贴右下 —— 原来 `max-width: 420px` 把这一块框在左边，按钮挤在两条中间，596 宽下右边
   空一大片。
   对齐用 `align-items: start`（**不是** `end`）：期末那一格多一句「系统建议」之后比期初
   高，`end` 会把期初整体下推、两个输入框不再齐平。 */
.aOpening {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  align-items: start; gap: var(--sp-2) var(--sp-3);
  margin-top: var(--sp-3);
}
.aOpeningField,
.aClosingField { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.aOpeningLabel,
.aClosingLabel { font-size: var(--fs-11); color: var(--ink-faint); }
.aOpeningInput,
.aClosingInput {
  width: 100%; min-height: var(--tap); padding: 4px var(--sp-2); border: 1px solid var(--line);
  border-radius: var(--r-1); background: var(--surface-2); color: var(--ink);
  font-size: var(--fs-12); font-family: inherit;
}
/* 封账之后这一格锁定（票 09）：两个框都禁用 —— 只在视觉上弱化（`.btn:disabled` 那套
   是全站既有的写法），真正的闸在服务端（409 `month_sealed`）。 */
.aOpeningInput:disabled,
.aClosingInput:disabled { opacity: .5; cursor: not-allowed; }
.aOpeningHint { margin: var(--sp-2) 0 0; font-size: var(--fs-11); color: var(--ink-faint); }
.aClosingHint { margin: var(--sp-2) 0 0; font-size: var(--fs-11); color: var(--ink-faint); }
/* 「系统建议」那一句（票 09）：公式算出来的那个数只在这里出现 —— 与输入框里那个
   **人工值**用颜色分开（`--accent`：这是一句提示，不是可以照抄进框里的现值）。 */
.aClosingNote { margin: 2px 0 0; font-size: var(--fs-11); color: var(--accent); }
.aClosingSealed { grid-column: 1 / -1; font-size: var(--fs-11); color: var(--warn); }
.aOpeningErr { grid-column: 1 / -1; font-size: var(--fs-11); color: var(--danger-bright); }
/* 按钮跨两列贴右下：与上面那排输入框分开，不会在两条结余中间显得像第三项。 */
.aOpening .aOpeningSave { grid-column: 1 / -1; justify-self: end; }
/* 页内动作（封账 / 回到月历 / 导出 / 存这三项 / 存期初期末）：触控 44px、圆角 --r-1。 */
.aActions .btn,
.aManualFoot .btn,
.aOpening .btn { border-radius: var(--r-1); min-height: var(--tap); }
@media (max-width: 900px) {
  .aStats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .aTop h1 { font-size: 22px; }
  .aTitleRow { flex-wrap: wrap; }
  .aActions { width: 100%; justify-content: flex-start; }
  .aKpis { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .aKpi { flex-direction: column; align-items: flex-start; gap: 4px; padding: 8px 10px; }
  .aKpi b { font-size: 20px; }
  .aDaysHead { padding: 10px 12px 6px; }
  .aDays { gap: 3px; padding: 0 12px 12px; }
  .aDay { min-height: 44px; }
}
</style>
