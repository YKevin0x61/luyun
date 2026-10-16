/**
 * 考勤表（考勤表导出票 01 / 假期台账票 03）的界面口径：逐日三态、卡片上那两项，
 * 以及假期台账那一块（天数的写法、可展开的算式、待批加班那一句）。
 *
 * 服务端下发的是**状态码**（`work` / `rest` / `todo` / `null`）与派生值（`new_hire`
 * 可能是 `null`）：「是什么」在服务端算，「怎么写」只在这一处翻。
 *
 * **导出件不是同一批字。** 卡片上这三个词只服务这一页；导出件（票 08）是 Python、
 * import 不到这份文件，于是自己在 `services/attendance/export.py` 里另存了一份
 * `DAY_TEXT` / `HALF_REST_TEXT` / `TODO_TEXT`（一份 JS、一份 Python，物理上不可能只有
 * 一份）。两边因此**靠一条断言钉住**，不是靠人记得：
 * `views/attendance/__tests__/attendanceSheet.test.js` 里那条读源码的用例把两份源文件
 * 都读出来逐字比 —— 改一边忘另一边就红。「上班」是唯一一处**有意**不同：它在导出件的
 * 日格里是**空格子**（出勤不写字），所以只比另外三种写法。
 */

const DAY_STATE_TEXT = {
  work: '上班',
  rest: '休',
  todo: '待补',
}

/** 半天休（票 07 / ADR 0114）：那天仍然上本来那个班，只是只上半天。
 *
 *  卡片上那一格写这四个字 —— 只写班次名的话，店长（与导出的表）会以为他要上整天。
 *  它跟 `dayStateText` 的「上班 / 休 / 待补」不是一回事：那三态说的是**那天上不上班**，
 *  这一位说的是**上多久**，所以是叠在「上班」上面的一个词，不是第四个状态码。
 */
export const HALF_REST_TEXT = '休半天'

/** 那天这个人是什么：上班 / 休 / 待补。今天之后（`null`）**留空** —— 那是「还没到」，
 *  不是「要人工确认」，两者在纸面上必须长得不一样。 */
export function dayStateText(state) {
  return DAY_STATE_TEXT[state] || ''
}

/** 是否新入职：`null` = 入职日期为空、这一项算不出来 —— 留空，不是「否」。 */
export function newHireText(value) {
  if (value === true) return '是'
  if (value === false) return '否'
  return ''
}

/** 当月净时长（小时，可为负、可带 .5）：`+2 小时` / `−1.5 小时` / `0 小时`。
 *  带符号是因为「这个月净欠的」与「这个月净加的」要一眼分得开；减号用全角，与
 *  `utils/overtimeEntry.js` 的 `formatHalfHours` 同一个字符。 */
export function formatNetHours(hours) {
  const value = Number(hours) || 0
  if (value === 0) return '0 小时'
  const sign = value > 0 ? '+' : '−'
  const size = Math.abs(value)
  return `${sign}${Number.isInteger(size) ? size : size.toFixed(1)} 小时`
}

/** 天数（假期台账那一套，可带 .5、可为负）：`12 天` / `19.5 天` / `−10.5 天` / `0 天`。
 *
 *  正数不写 `+`（「实际出勤 12 天」不是一笔增减），负数写全角减号 —— 与
 *  `formatNetHours` 同一个字符：**欠班**与攒下的假在纸面上必须分得开
 *  （`GLOSSARY.md` 的存假：负数是欠班，不是脏数据）。 */
export function formatDays(days) {
  const value = Number(days) || 0
  if (value === 0) return '0 天'
  const sign = value > 0 ? '' : '−'
  const size = Math.abs(value)
  return `${sign}${Number.isInteger(size) ? size : size.toFixed(1)} 天`
}

/** 假期算式的展开文字（票 03 验收 9 的「可展开看算式」）。
 *
 *  六项照着 `spec.md` 的公式相加得到可用天数池，再拿池减本月自然天数就是存假。
 *  每一项都带单位：「哪一项算错了」要能一眼看出是哪一项。 */
export function leaveFormulaText(leave) {
  if (!leave) return ''
  const terms = [
    ['实际出勤', leave.actual_days],
    ['月例休', leave.monthly_rest],
    ['法定节假日补休', leave.holiday_makeup],
    ['生日假', leave.birthday_leave],
    ['年假', leave.annual_leave],
    ['上月存假', leave.previous_balance],
  ]
  const sum = terms.map(([label, value]) => `${label} ${formatDays(value)}`).join(' + ')
  return `${sum} = 可用 ${formatDays(leave.pool)}；本月 ${leave.month_days} 天，存假 ${formatDays(leave.balance)}`
}

/** 未审批的加班 / 补钟（故事 14）：有单子等着批就把这一格标色，并说清待批几小时。
 *  `null`（没有待批的单）时不留任何痕迹 —— 与「待批 0 小时」是两件事。 */
export function pendingOvertimeText(count, hours) {
  if (!count) return ''
  return `待批 ${formatNetHours(hours)}`
}

/** 手填的那几列（票 05）在卡片上的**只读写法**：空值显示为 `—`，不是留一个空白的窟窿。
 *  「没填」与「填了个空字符串」在页面上必须长得一样 —— 读侧已经把空串收敛成 `null`。 */
export function manualText(value) {
  const text = value === null || value === undefined ? '' : String(value).trim()
  return text || '—'
}

/** 「本月班次」那一格的**默认值**：台账里没填过（或清空了）时，服务端给的是当月众数
 *  （并列取班次显示顺序），写在输入框的提示语里；当月没排班的人没有这一项。
 *
 *  为什么输入框里要放空的、把默认写在提示语里：那四个值都是「手填」，填过与没填过在
 *  台账里是两件事（一个存了字、一个是 NULL）。给没填过的人预填上默认值，店长一存就把
 *  这条默认**固化**成他定的值，以后再改排班这一格也不会跟着变了。
 */
export function shiftNameDefault(person) {
  const landed = person?.default_shift_name
  return landed ? `默认：当月最多 ${landed}` : '这个月还没有排班'
}

/** 手填的四项那一块（票 05）在卡片上的摆法：标签 + **台账里存的那一列**（空 = `—`）。
 *
 *  「本月班次」念的是 `shift_name_stored`（台账的原始列，没填过就是 `null` → `—`），
 *  **不是** `shift_name`：那是读侧的回填值（没填过时是当月众数），导出件用它，
 *  但回显拿它当「填过的值」就是在替店长认账。
 */
export function manualStats(person) {
  return [
    { label: '本月班次', value: person?.shift_name_stored },
    { label: '是否住宿', value: person?.lodging },
    { label: '借调门店', value: person?.borrowed_store },
    { label: '是否离职', value: person?.resigned },
  ]
}

/** ISO 时刻 → `YYYY-MM-DD HH:MM`（票 04 的封账时刻就是这种串）。
 *
 *  只截字符串、**不在这里做时区换算**：服务端给的就是 +08:00 那一刻的墙上时间，
 *  再经一次 `Date` 反而会因为浏览器时区把它挪走。截不出形状就原样显示。 */
export function sealedAtText(value) {
  const text = String(value || '')
  const match = text.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : text
}

/** 上月封账那一句（票 04）：「2026-08 已封账（2026-09-24 10:00）」。
 *
 *  入参是服务端下发的 `previous_month`（`{month, sealed_at}`）。还没封账 / 老载荷没有
 *  这一位时给空串 —— 页面据此决定画「封账上月」那颗按钮，还是画这句已经封过的回执。
 *  封账行「看得出是什么时候封的」（验收 5）就靠这一句。 */
export function sealedMonthText(previous) {
  if (!previous || !previous.sealed_at) return ''
  const when = sealedAtText(previous.sealed_at)
  return previous.month ? `${previous.month} 已封账（${when}）` : `已封账（${when}）`
}
