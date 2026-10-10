/** 员工端「今天」页（票 05）的人话翻译层。
 *
 * 服务端（`GET /api/scheduling/me`）一天给三个字段 —— `scheduled` / `shift_id` /
 * `shift_name` —— 合起来是四件事，页面上必须分得开：
 *   scheduled=false                → 还没铺到这一天（店长还没排你）
 *   scheduled=true, shift_id=null  → 那天休
 *   scheduled=true, shift_name     → 那天上这个班
 *   scheduled=true, shift_name 空  → 行上写着班次、那条班次已删（票 11 管班次增删）
 * 「休」和「还没排」是两件事，不能合成一句（验收 3）。
 *
 * 票 08 起多一个 `leave`：批过的请假。它跟「本来就休」在结果表里长得一样（都是没有
 * 班次），区别只在覆盖记录的 `kind` 上 —— 服务端读出来放这里，页面照它说「请假」，
 * 不许把请假说成「休」。
 *
 * 整屏没有钟点（验收 5）：班次没有起止时刻，钟点只有卫生那边才有（逾期点），
 * 所以这里只碰营业日，不碰时刻。放在 util 里而不是页面里，是为了能用真单测钉住
 * 上面那张表 —— 页面模板的 grep 断言抓不住「休」被写成「还没排」。
 */

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** 营业日 → 「9/25 周五」。按 UTC 建：手机时区不该把营业日挪掉一天。 */
export function dayLabel(businessDate) {
  const [year, month, day] = String(businessDate || '').split('-').map(Number)
  if (!year || !month || !day) return ''
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay()
  return `${month}/${day} ${WEEKDAYS[(weekday + 6) % 7]}`
}

/** 那天的格子里写什么：往后三天的三格、卡顶那行都用这句。 */
export function shiftText(day) {
  if (!day) return '还没排'
  // 票 08：批过的请假也是「没有班次」，但要跟「本来就休」分开说。
  if (day.leave) return '请假'
  if (!day.scheduled) return '还没排'
  if (day.shift_id == null) return '休'
  // 行上写着班次、但那条班次已经没了：说「已调整」，不要假装那天是休。
  return day.shift_name || '班次已调整'
}

/** 排班卡上那行大字。 */
export function todayHeadline(day) {
  if (!day) return '今天没有你的班'
  if (day.leave) return '请假'
  if (!day.scheduled) return '今天没有你的班'
  if (day.shift_id == null) return '休'
  return day.shift_name || '班次已调整'
}

/** 那天的语气（五态五色）：'shift' 上班 / 'rest' 休 / 'leave' 请假（票 08）/
 *  'moved' 班次被删 / 'none' 还没排。
 *
 *  票 05 审查记过一条：原来「班次已调整」跟「休」共用最弱的灰色，看着像那天休息。
 *  这里把各态拆开，「已调整」给琥珀色 —— 那天的班次没了，不等于那天不上班；
 *  「请假」给青色：是自己提的、店长批过的，跟「排班给了一天休」不是一回事。
 */
export function shiftTone(day) {
  if (!day) return 'none'
  if (day.leave) return 'leave'
  if (!day.scheduled) return 'none'
  if (day.shift_id == null) return 'rest'
  return day.shift_name ? 'shift' : 'moved'
}

/** 大字那一行的语气类名：上班那态用页面默认色，所以给空串。 */
export function todayTone(day) {
  const tone = shiftTone(day)
  return tone === 'shift' ? '' : tone
}

/** 大字下面那行小字。 */
export function todaySubline(day) {
  if (!day) return '店长还没排到你'
  // 票 08：批过的请假与被排了一天休是两件事，这儿得说出来。
  if (day.leave) return '今天请假'
  if (!day.scheduled) return '店长还没排到你'
  if (day.shift_id == null) return '今天休息'
  // 验收 2：班次和工作区都落在这张卡上（原型里工作区挂在下面那张卫生卡上，
  // 那张卡这一票还没有）。工作区没配就只说班次。
  return day.zone_name ? `今天上班 · ${day.zone_name}` : '今天上班'
}

/** 卡顶那句「明天 白班 · 后天 夜班」；只说明天/后天两个，第三格在「往后三天」里。 */
export function nextTwoLine(afterDays) {
  const words = ['明天', '后天']
  return (afterDays || [])
    .slice(0, 2)
    .map((day, index) => `${words[index]} ${shiftText(day)}`)
    .join(' · ')
}

// ── 整月（票 06）────────────────────────────────────────────────────────

/** 月历表头：**周一开头**（服务端给的 `lead` 就是这个算法：`date.weekday()`，周一=0）。
 *  2026-09-30 全模块统一到周一（原先是 `isoweekday() % 7` 的周日开头）——店里的口头习惯是周一。 */
export const MONTH_HEADS = ['一', '二', '三', '四', '五', '六', '日']

/** 'YYYY-MM' → 「2026年9月」；跟今天同一年就只说「9月」（手机上省一格字）。 */
export function monthLabel(month, today) {
  const [year, mon] = String(month || '').split('-').map(Number)
  if (!year || !mon) return ''
  const thisYear = Number(String(today || '').slice(0, 4))
  return year === thisYear ? `${mon}月` : `${year}年${mon}月`
}

/** 翻月份：'2026-09' + 1 → '2026-10'（跨年也对）。 */
export function shiftMonth(month, delta) {
  const [year, mon] = String(month || '').split('-').map(Number)
  if (!year || !mon) return ''
  // 用月首那天做加减：`Date` 会把 13 月进位成次年 1 月，跨年不用自己算。
  const moved = new Date(Date.UTC(year, mon - 1 + Number(delta || 0), 1))
  const out = `${moved.getUTCFullYear()}-${String(moved.getUTCMonth() + 1).padStart(2, '0')}`
  return out
}

/** 翻月份的下一步：'2026-09' + 1 → '2026-10'；该停下时返回 `null`。
 *
 *  停下的条件只有一个：往后翻会翻出服务端给的展开窗口（`window_end`，随每个响应下来）。
 *  窗口只铺到那一天，再往后整片都是空的 —— 翻过去只会看见一个空月，还白跑一次请求。
 *  往前没有底：早于装机日期的月份本来就是空的（票 06 口径 4），翻回去是员工自己的事。
 */
export function stepMonth(month, delta, windowEnd) {
  const next = shiftMonth(month, delta)
  if (!next) return null
  const last = String(windowEnd || '').slice(0, 7)
  if (last && Number(delta) > 0 && next > last) return null
  return next
}

/** 这一格在展开窗口之外吗（窗口外 = 排班还没铺到，跟「过去没有记录」不是一回事）。 */
export function dayBeyondWindow(day, windowEnd) {
  const end = String(windowEnd || '')
  if (!end || !day || !day.business_date) return false
  return String(day.business_date) > end
}

/** 整月格子里写什么：班别 / 「休」/ 「请假」（票 08）/ 「已调整」/ 空（还没排）。
 *
 *  语气只有一张表（上面的 `shiftTone`）：这里只负责把语气翻成小格子里的话 ——
 *  休 → 「休」，请假 → 「请假」，班次被删 → 「已调整」（不许假装那天是休），
 *  还没铺到 → 空着。
 *  两处各写一份五态的话，改一处忘另一处就会漂（票 02 审查记过同一类问题）。
 */
export function monthCell(day) {
  const tone = shiftTone(day)
  if (tone === 'none') return { text: '', tone }
  if (tone === 'rest') return { text: '休', tone }
  if (tone === 'leave') return { text: '请假', tone }
  if (tone === 'moved') return { text: '已调整', tone }
  return { text: day.shift_name, tone }
}

// ── 全店视角（票 13）：格子上的「N人在班」与点开某天那份名单 ─────────────

/** 格子里那行小字：那天**全店**上班几个人（`staff_count`，服务端已把休排除在外）。
 *
 *  0 与「没给」都不写：0 的意思是那天一条结果行都没有（还没铺到 / 装机前），
 *  写「0人在班」会让人以为店里那天没人上班 —— 那是另一件事（票 06 口径 4）。
 *  也不写成「休」：这个数和「我的班」是两个字段，格子上的班别那行才是我的班。
 */
export function staffCountLabel(count) {
  const n = Number(count)
  if (!Number.isFinite(n) || n <= 0) return ''
  return `${n}人在班`
}

/** 弹层标题下面那行小结：上班几个、休假几个（两个数都来自服务端，不在这儿重算）。 */
export function rosterSummary(total, offCount) {
  const working = Number(total) || 0
  const off = Number(offCount) || 0
  return off > 0 ? `${working} 人在班 · ${off} 人休假` : `${working} 人在班`
}

/** 休假名单里那个人写什么：批过的假 vs 排班给的休（票 08 的口径，跟格子里同一张表）。
 *
 *  判据只有 `person.leave` 一个字段 —— 页面不自己看班次判「他是不是请假」。
 */
export function offLabel(person) {
  return person && person.leave ? '请假' : '休'
}

/** 「我」在这份名单里的那一行：按 id 认人，不按名字（重名会标错行）。 */
export function isMe(person, employeeId) {
  if (!person || employeeId == null) return false
  return Number(person.id) === Number(employeeId)
}

/** 弹层顶上那行「我这天：…」：格子上的班别，加上我那天的工作区。
 *
 *  工作区**只在上班那一态**接上去（「休 · 案板」没有意义）；四态的判据仍在 `monthCell`
 *  一处，这里只决定接不接区名 —— 不重写第二张表。
 */
export function myDayText(day) {
  const { text, tone } = monthCell(day)
  if (!text) return '还没排'
  if (tone !== 'shift') return text
  return day.zone_name ? `${text} · ${day.zone_name}` : text
}
