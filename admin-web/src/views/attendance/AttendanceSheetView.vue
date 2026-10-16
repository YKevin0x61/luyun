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
 * 票 03 往卡片上加的是**假期台账**那一块：实际出勤 / 月例休 / 法定节假日补休 / 生日假 /
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
 * 这一页只算当月：没有月份选择器。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../../api/client'
import {
  HALF_REST_TEXT,
  dayStateText,
  formatDays,
  formatNetHours,
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

/** 正在改的那一格（`营业日` 或 null）与正在等回执的那一格。 */
const editingDate = ref('')
const savingDate = ref('')
/** 那一格上要显示的话（服务端拒绝的理由）。 */
const dayError = ref(null) // { business_date, text } | null

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
    // 四个**手填列**（票 05）原样带过来：它们是台账那一行上的值，页面只摆出来。
    // 单独列一遍是为了让「这一页认哪几个键」看得见 —— 手滑写错一个键名，输入框会
    // 一直空着（`manualValue` 读不到就是空），看起来像「服务端没给」。
    // `shift_name` 是**读侧的回填值**（没填过时是当月众数，导出件 A 列用它）；
    // 输入框绑的是 `shift_name_stored`（台账的原始列，没填过就是 `null` → 空格子）。
    shift_name: person.shift_name,
    shift_name_stored: person.shift_name_stored,
    default_shift_name: person.default_shift_name,
    lodging: person.lodging,
    borrowed_store: person.borrowed_store,
    resigned: person.resigned,
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

/** 那一格上写什么：休 / 休半天 / 上班（跟班次）/ 待补；今天之后留空。 */
function cellLabel(person, cell) {
  if (cell.state === 'work') {
    // 半天休（票 07）：那天仍然挂着班次，只是只上半天 —— 这一句必须排在班次名前面，
    // 否则卡片上写个「白班」，店长与员工都以为要上整天（ADR 0114）。
    if (cell.half_rest) return HALF_REST_TEXT
    return shiftName(person, cell.shift_id) || dayStateText(cell.state)
  }
  return dayStateText(cell.state)
}

/** 这一格能不能点：今天之后留空、没到的那天不给改。 */
function editable(cell) {
  return Boolean(cell.state) && !cell.is_future
}

function openEditor(cell) {
  if (!editable(cell) || savingDate.value) return
  dayError.value = null
  editingDate.value = editingDate.value === cell.business_date ? '' : cell.business_date
}

function errorOn(businessDate) {
  return dayError.value && dayError.value.business_date === businessDate
    ? dayError.value.text
    : ''
}

/** 下拉里选了什么 → 写请求的 body。`rest` 是「那天休」，`todo` 没进下拉（没有「删掉那天」这个动作）。 */
function bodyFor(value) {
  return value === 'rest' ? { is_rest: true } : { shift_id: Number(value) }
}

/** 选完就写：那天立刻按服务端的样子重画（不猜一个本地状态自己改）。 */
async function pick(person, cell, event) {
  const value = event.target.value
  editingDate.value = ''
  savingDate.value = cell.business_date
  dayError.value = null
  try {
    await api.put(
      `/api/attendance/sheet/${person.id}/${cell.business_date}`,
      bodyFor(value),
    )
    // nudge 不带数据，这一页也不等它：自己重拉一次，那天（与「待补」计数）立刻变。
    // **这次重拉不走 `load()`**：那条路会把 `state` 打回 `loading`（整页闪一下），
    // 而改一格只该让那一格动。失败时**不动**页面上已经渲染的那一份 ——
    // 显示一个没落库的班次比报错更糟。
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    dayError.value = { business_date: cell.business_date, text: err.message || '这天没改成' }
  } finally {
    savingDate.value = ''
  }
}

/** 标 / 取消「休半天」（票 07）：一次**单独**的动作 —— 只改结果行上那个标志，
 *  班次、工作区与「那天上哪个班」都不动（ADR 0114）。
 *
 *  跟 `pick` 同一条 PUT、同一套做法（写之前收起编辑、重拉一次、失败就写在那一格
 *  下面）：这样写完那一格立刻写着新的样子，服务端说没写成时也一个字都不改。 */
async function toggleHalf(person, cell) {
  if (savingDate.value) return
  editingDate.value = ''
  savingDate.value = cell.business_date
  dayError.value = null
  try {
    await api.put(`/api/attendance/sheet/${person.id}/${cell.business_date}`, {
      half_rest: !cell.half_rest,
    })
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    dayError.value = {
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
    { label: '实际出勤', value: formatDays(leave.actual_days) },
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
 *  回填成当月众数，输入框显示的就是那条默认值，店长只改「是否住宿」点一下「存这四项」
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
  { key: 'resigned', label: '是否离职', hint: () => '写「是」或「否」' },
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

/** 人工期初（收尾修复 ② / 故事 35）：那一格的输入口与它自己那一次写。
 *
 *  为什么它**不并进**上面那「存这四项」：那四列是每张卡随时会改的日常项，而期初是
 *  上线当月手工给**一次**的账（再往后以封账结转）。分开一颗按钮，误点「存这四项」
 *  不会把这一格顺手清掉 —— 写口那边的纪律是「点名谁才写谁」，空着就一个字节都不碰。
 */
const savingOpening = ref(0)
const openingError = ref(null) // { employee_id, text } | null

/** 输入框里现在是什么：服务端那一份；`null`（没给过）留空，不是 `undefined`。 */
function openingValue(person) {
  const value = person.opening_balance
  return value === null || value === undefined ? '' : String(value)
}

/** 存期初存假：**只发这一格**（`{ opening_balance }`），空着发 `null` = 清空。
 *
 *  填了不是数字的东西**不发请求**：服务端那一位是数字，而 `Number('七点五')` 是 `NaN`，
 *  转成 JSON 会变成 `null` —— 那会把这一格悄悄清掉。前端先说人话（同「存这四项」：
 *  失败时不动页面上已经渲染的那一份）。
 */
async function saveOpening(person, event) {
  if (savingOpening.value) return
  const form = event.target.closest('.aOpening')
  const input = form && form.querySelector('[data-field="opening_balance"]')
  const raw = input ? input.value.trim() : ''
  let value = null
  if (raw !== '') {
    value = Number(raw)
    if (!Number.isFinite(value)) {
      openingError.value = {
        employee_id: person.id,
        text: '期初存假要写一个数字（可以是负的、也可以带 .5）；要清空就把那一格留空',
      }
      return
    }
  }
  openingError.value = null
  savingOpening.value = person.id
  try {
    await api.put(`/api/attendance/sheet/${person.id}/manual`, { opening_balance: value })
    // 与手填那四列同一条路：自己重拉一次（nudge 不带数据，这一页也不等它）。
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    openingError.value = {
      employee_id: person.id,
      text: err.message || '期初存假没存进去',
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
        <p class="aKicker">人事</p>
        <h1>考勤表</h1>
        <p class="aSub">
          <template v-if="sheet">当月 {{ sheet.month }} · 今天 {{ sheet.today }} · </template>
          一人一张卡，打开就是当月：今天之后的日子留空，没铺到排班的那天写「待补」。
          点一个已经过去的日子就能改它 —— 只改那一天，不动轮转规则；那颗「休半天」
          只把那天标成上半天，班次照旧。
        </p>
      </div>
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
        <!-- 导出当月这张表（票 08）：版式与店长用了两年的那份《厨房排班表》一致。
             文件名由服务端在响应头里给（`考勤表-YYYY-MM.xlsx`），这一页不拼。
             它是这一页的主动作（B 批返工 ⑦），给主按钮样式。 -->
        <button
          class="btn btn-primary"
          type="button"
          :disabled="exporting"
          @click="exportSheet"
        >
          {{ exporting ? '导出中…' : '导出这个月' }}
        </button>
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
    <div v-show="state === 'ready'" class="aSheet">
      <p v-if="!employees().length" class="aEmpty">
        这个月还没有在职员工，也没有人有排班或加班记录。
      </p>
      <article v-for="person in employees()" :key="person.id" class="aCard">
        <header class="aCard-hd">
          <b class="aName">{{ person.name || '（没有名字）' }}</b>
          <span v-if="todoDays(person)" class="aTodo">待补 {{ todoDays(person) }} 天</span>
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
                'is-editing': editingDate === cell.business_date,
              },
            ]"
            :title="cell.business_date"
          >
            <!-- 还没到的日子是一块**死的**格子（不渲染任何控件）：验收 45 说的是
                 「不能编辑」，能点一下再弹一句拒绝不算数。 -->
            <template v-if="editingDate === cell.business_date">
              <!-- 编辑态那一格**独占整行**（B 批返工 ⑥）：日期数字照旧摆在最前面，
                   不然展开之后在改哪一天看不出来。 -->
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <select
                class="aDayPick"
                :aria-label="`改 ${person.name} ${cell.business_date} 的班`"
                :value="cell.shift_id === null || cell.shift_id === undefined ? 'rest' : String(cell.shift_id)"
                @change="pick(person, cell, $event)"
              >
                <option v-for="shift in person.shifts" :key="shift.id" :value="String(shift.id)">
                  {{ shift.name }}
                </option>
                <option value="rest">休</option>
              </select>
            </template>
            <button
              v-else-if="editable(cell)"
              class="aDayBtn"
              type="button"
              :disabled="Boolean(savingDate)"
              @click="openEditor(cell)"
            >
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </button>
            <template v-else>
              <i class="aDayNum">{{ cell.day }}<em v-if="cell.is_today" class="aTodayMark">今</em></i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </template>
            <!-- 半天休（票 07 / ADR 0114）：那一格**有班次**（上班那一态）时才谈得上
                 「只上半天」—— 休与待补的两天没有半天可标，所以这颗按钮不出现。
                 它是**单独的**一次写：班次下拉回答「那天上哪个班」，这一颗回答
                 「上不上满一整天」，两件事不塞进同一个控件。 -->
            <button
              v-if="editingDate === cell.business_date && cell.state === 'work'"
              class="aHalfBtn"
              type="button"
              :disabled="Boolean(savingDate)"
              :aria-pressed="cell.half_rest ? 'true' : 'false'"
              @click="toggleHalf(person, cell)"
            >
              {{ cell.half_rest ? '取消休半天' : HALF_REST_TEXT }}
            </button>
            <small v-if="errorOn(cell.business_date)" class="aDayErr" role="alert">
              {{ errorOn(cell.business_date) }}
            </small>
          </span>
        </div>
        <!-- 四个**手填列**（票 05）：系统里推不出来的那几项（是否住宿 / 借调门店 / 是否离职
             在花名册里压根没有字段），由超管在这儿填一次、存在月度考勤台账当月那一行上。
             只进卡片与（票 08 的）导出件，**不写回员工档案**。

             「本月班次」那一格：填过就显示台账里的值；没填过时输入框是**空的**、把当月
             众数放在提示语里 —— 默认值不能被预填，否则店长一存就把这条默认固化成他定的值，
             以后再改排班这一格也不跟着变了。 -->
        <div class="aManual">
          <p class="aManualTitle">
            手填列：这几项系统里没有，填一次就进台账，导出的表直接用它。
          </p>
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
               B 批返工 ③：它排在**回显与「存这四项」之后** —— 那两者是一对（存了什么、存
               在下边那颗按钮上），中间岔进期初这一块，店长会以为这一格也归「存这四项」管。 -->
          <!-- 「这一块现在是什么」由服务端那一份说了算（重拉之后写的就是最新的）。 -->
          <p class="aManualNow">
            <span v-for="stat in manualStats(person)" :key="stat.label" class="aManualNowOne">
              <i>{{ stat.label }}</i><b>{{ manualText(stat.value) }}</b>
            </span>
          </p>
          <div class="aManualFoot">
            <button
              class="btn btn-sm btn-primary aSave"
              type="button"
              :disabled="savingManual === person.id"
              @click="saveManual(person, $event)"
            >
              存这四项
            </button>
            <small v-if="manualErrorOn(person.id)" class="aManualErr" role="alert">
              {{ manualErrorOn(person.id) }}
            </small>
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
              />
              <small class="aOpeningHint">只在上线当月手工给一次；可为负、可带 .5</small>
            </label>
            <button
              class="btn btn-sm aOpeningSave"
              type="button"
              :disabled="savingOpening === person.id"
              @click="saveOpening(person, $event)"
            >
              存期初存假
            </button>
            <small v-if="openingErrorOn(person.id)" class="aOpeningErr" role="alert">
              {{ openingErrorOn(person.id) }}
            </small>
          </div>
        </div>
        <!-- 假期台账（票 03）：每张卡把自己的账摊开；算式收在「看算式」后面，
             摊开后能一格一格核（服务端下发的数，页面一个都不自己算）。 -->
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
.attendance-page { padding: 18px 16px 40px; max-width: 860px; margin: 0 auto; }
/* 顶部那一行（B 批返工 ①）：手机上三颗按钮的 `flex: none` 会吃掉约 287px，不换行就把
   标题与说明挤成一条竖排的字（「考勤 / 表」）。照人事组的节假日日历页（`.hTop`）写：
   **允许换行**；标题块给一个 `flex-basis`，否则它的 max-content 是整段说明、宽屏也会
   把按钮组顶到第二行去；按钮组自己也能换行，窄屏就整行落到标题下面。 */
.aTop {
  display: flex; align-items: flex-start; gap: var(--sp-3); flex-wrap: wrap;
  margin-bottom: var(--sp-4);
}
.aIntro { flex: 1 1 320px; min-width: 0; }
.aTop h1 { margin: 2px 0 var(--sp-1); font-size: var(--fs-20); color: var(--ink); }
.aKicker { margin: 0; font-size: var(--fs-11); letter-spacing: var(--ls-4); color: var(--ink-faint); }
.aSub { margin: 0; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.aTop .btn { margin-left: auto; flex: none; }
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
.aSealed { margin: 0; font-size: var(--fs-12); color: var(--ink-dim); }
.aHint, .aEmpty { margin: 0; font-size: var(--fs-13); color: var(--ink-dim); }
/* 当月是暂算值（票 03）：这一条不是装饰 —— 它是「别拿这个数去结账」的告示。 */
.aProvisional {
  margin: 0 0 var(--sp-3); padding: 9px var(--sp-3); border-radius: var(--r-2);
  border: 1px solid var(--info); background: var(--info-soft);
  font-size: var(--fs-12); color: var(--ink);
}
.aErr {
  margin: 0 0 var(--sp-3); padding: 9px var(--sp-3); border-radius: var(--r-2);
  border: 1px solid var(--danger); background: var(--danger-soft);
  font-size: var(--fs-13); color: var(--danger-bright);
}
.aCard {
  margin-bottom: var(--sp-4); padding: var(--sp-4); border: 1px solid var(--line);
  /* 圆角与节假日日历页的卡片一致（`--r-2`，B 批返工 ⑧）：同一个人事组里两张卡不该
     一个 16px 一个 12px。 */
  border-radius: var(--r-2); background: var(--surface-1);
}
.aCard-hd { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-2); }
/* 长名字不许把「待补」那颗 pill 挤出卡片（B 批返工 ⑧）：名字自己截断、
   `min-width: 0` 让它在 flex 里真的能缩下去（默认 `min-width: auto` 会顶住）。 */
.aName {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: var(--fs-14); color: var(--ink);
}
.aTodo {
  flex: none;
  padding: 1px var(--sp-2); border-radius: var(--r-full); font-size: var(--fs-11);
  color: var(--warn); border: 1px solid var(--warn); background: transparent;
}
.aStats { display: flex; flex-wrap: wrap; gap: 6px var(--sp-4); margin: 0 0 var(--sp-3); }
.aStat { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aStat i { font-style: normal; color: var(--ink-faint); }
.aStat b { font-size: var(--fs-13); color: var(--ink); }
.aNet { font-family: var(--font-mono); }
.aDays {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(38px, 1fr)); gap: var(--sp-1);
}
.aDay {
  display: flex; flex-direction: column; align-items: center; gap: 1px;
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
/* 就地编辑：下拉就摆在这一格里（比弹层少一次「我在改谁」的确认）。 */
.aDayPick {
  width: 100%; max-width: 100%; padding: 0; border: 0; background: transparent;
  color: var(--ink); font-size: var(--fs-11); text-align: center; text-align-last: center;
}
/* 「休半天」那颗（票 07）：跟下拉并排在这一格里。它是**一次单独的写**，所以是自己的
   按钮而不是下拉的第四个选项 —— 下拉那一列是班次，混进一个动作会让人以为那天换班了。 */
.aHalfBtn {
  width: 100%; padding: 1px 2px; border: 1px solid var(--info); border-radius: var(--r-1);
  background: transparent; color: var(--info);
  font: inherit; font-size: var(--fs-11); line-height: 1.4; cursor: pointer;
}
.aHalfBtn:disabled { opacity: .5; cursor: default; }
.aHalfBtn:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }
/* 服务端拒绝的理由写在**那一格**下面（不是页面顶端）：店长点的就是这一格。 */
.aDayErr { font-size: var(--fs-11); line-height: 1.4; color: var(--danger-bright); text-align: center; }
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
/* 就地编辑（票 06/07，B 批返工 ⑥）：被点的那一格**独占整行** —— 43px 宽的格子里塞不下
   一个班次下拉加一颗「休半天」，硬塞会把那一行撑到 ~55px、整张卡跟着跳。独占一行之后
   两颗控件横着摆，这一行反而比别的行矮，卡片一共只多出一行的高度。 */
.aDay.is-editing {
  grid-column: 1 / -1; flex-direction: row; justify-content: center; gap: var(--sp-2);
  border-color: var(--line-strong);
}
.aDay.is-editing .aDayPick { width: auto; max-width: none; }
.aDay.is-editing .aHalfBtn { width: auto; padding: 1px var(--sp-2); }
/* 未审批的加班 / 补钟（票 03 / 故事 14）：**只标色**，数不进净时长。 */
.aStat.is-pending .aNet { color: var(--warn); }
.aPending {
  padding: 0 var(--sp-2); border-radius: var(--r-full); border: 1px solid var(--warn);
  font-style: normal; font-size: var(--fs-11); color: var(--warn);
}
/* 假期台账那一块（票 03）：九项摊在卡片上，算式收在「看算式」后面。 */
.aLeave { margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.aLvRow { display: flex; flex-wrap: wrap; gap: 5px var(--sp-4); margin: 0 0 var(--sp-2); }
.aLv { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aLv i { font-style: normal; color: var(--ink-faint); }
.aLv b { font-family: var(--font-mono); font-size: var(--fs-13); color: var(--ink); }
.aFormula summary { cursor: pointer; font-size: var(--fs-12); color: var(--ink-dim); }
.aFormulaText {
  margin: var(--sp-2) 0 0; font-family: var(--font-mono); font-size: var(--fs-11);
  line-height: 1.9; color: var(--ink-dim);
}
/* 四个手填列（票 05）：系统里没有的那几项，填一次就进台账。 */
.aManual { margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.aManualTitle { margin: 0 0 var(--sp-2); font-size: var(--fs-12); color: var(--ink-dim); }
.aManualRow { display: flex; flex-wrap: wrap; gap: var(--sp-2) var(--sp-3); }
.aManualField { display: flex; flex-direction: column; gap: 2px; }
.aManualLabel { font-size: var(--fs-11); color: var(--ink-faint); }
.aManualInput {
  width: 110px; padding: 4px var(--sp-2); border: 1px solid var(--line);
  border-radius: var(--r-1); background: var(--surface-2); color: var(--ink);
  font-size: var(--fs-12); font-family: inherit;
}
.aManualHint { margin: var(--sp-2) 0 0; font-size: var(--fs-11); color: var(--ink-faint); }
.aManualNow { display: flex; flex-wrap: wrap; gap: 5px var(--sp-4); margin: var(--sp-2) 0 0; }
.aManualNowOne { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aManualNowOne i { font-style: normal; color: var(--ink-faint); }
.aManualNowOne b { font-size: var(--fs-13); color: var(--ink); }
.aManualFoot { display: flex; align-items: center; gap: var(--sp-2); margin-top: var(--sp-2); }
.aManualFoot .aSave { flex: none; }
.aManualErr { font-size: var(--fs-11); color: var(--danger-bright); }
/* 「借调门店」那处与历史表的偏差（收尾修复 ⑥）现在挂在输入框的 `title` 上（见
   `BORROWED_STORE_NOTE`）—— 悬停可见，不占三行可视小字把这一排输入口拽散。 */
/* 人工期初（收尾修复 ②）：上线当月手工给一次的那一位，自己一颗按钮（见 `saveOpening`）。 */
.aOpening {
  display: flex; align-items: flex-end; flex-wrap: wrap; gap: var(--sp-2);
  margin-top: var(--sp-2);
}
.aOpeningField { display: flex; flex-direction: column; gap: 2px; }
.aOpeningLabel { font-size: var(--fs-11); color: var(--ink-faint); }
.aOpeningInput {
  width: 110px; padding: 4px var(--sp-2); border: 1px solid var(--line);
  border-radius: var(--r-1); background: var(--surface-2); color: var(--ink);
  font-size: var(--fs-12); font-family: inherit;
}
.aOpeningHint { margin: var(--sp-2) 0 0; font-size: var(--fs-11); color: var(--ink-faint); }
.aOpeningErr { font-size: var(--fs-11); color: var(--danger-bright); }
</style>
