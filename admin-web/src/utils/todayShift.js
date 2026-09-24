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
  if (!day || !day.scheduled) return '还没排'
  if (day.shift_id == null) return '休'
  // 行上写着班次、但那条班次已经没了：说「已调整」，不要假装那天是休。
  return day.shift_name || '班次已调整'
}

/** 排班卡上那行大字。 */
export function todayHeadline(day) {
  if (!day || !day.scheduled) return '今天没有你的班'
  if (day.shift_id == null) return '休'
  return day.shift_name || '班次已调整'
}

/** 大字的语气：'none'（没有你的班）/ 'rest'（休）/ ''（这天有班次）。 */
export function todayTone(day) {
  if (!day || !day.scheduled) return 'none'
  if (day.shift_id == null) return 'rest'
  return day.shift_name ? '' : 'none'
}

/** 大字下面那行小字。 */
export function todaySubline(day) {
  if (!day || !day.scheduled) return '店长还没排到你'
  if (day.shift_id == null) return '今天休息'
  // 验收 2：班次和责任区都落在这张卡上（原型里责任区挂在下面那张卫生卡上，
  // 那张卡这一票还没有）。责任区没配就只说班次。
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
