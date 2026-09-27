export function formatDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function parseLocalDate(dateText) {
  const [year, month, day] = String(dateText).split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function addDays(date, dayCount) {
  const next = new Date(date)
  next.setDate(next.getDate() + dayCount)
  return next
}

export function dayDiff(start, end) {
  const s = parseLocalDate(start)
  const e = parseLocalDate(end)
  return Math.max(1, Math.round((e - s) / 86400000) + 1)
}

/** 把一段日期展开成逐个营业日（**两端都算**），ISO 文本进、ISO 文本出。
 *
 *  排班的日期一律是 `YYYY-MM-DD` 字符串（`business_date`），跨月跨年靠 `Date` 自己进位。
 *  用途：把一条申请的起止区间摊成「哪几天有事」—— 月历上那个待批角标就是照它按天标的
 *  （一条 9/28–9/30 的请假要在三格上都标出来，不是只标开始那天）。
 *
 *  结束日缺省（或早于开始日）时给空数组：调用方自己决定要不要退回单日，
 *  这里不替它猜（申请那边的口径是「不填结束日 = 单日」，见 `LeaveRequest`）。
 */
export function eachDayInRange(start, end) {
  const first = parseLocalDate(start)
  const last = parseLocalDate(end)
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime()) || last < first) return []
  const days = []
  for (let day = first; day <= last; day = addDays(day, 1)) {
    days.push(formatDate(day))
  }
  return days
}

export function quickRange(type) {
  const now = new Date()
  const start = new Date(now)
  const end = new Date(now)
  if (type === 'yesterday') {
    start.setDate(start.getDate() - 1)
    end.setDate(end.getDate() - 1)
  } else if (type === 'week') {
    start.setDate(start.getDate() - 6)
  } else if (type === 'month') {
    start.setDate(1)
  } else if (type === 'lastWeek') {
    const day = now.getDay() || 7
    start.setDate(now.getDate() - day - 6)
    end.setDate(now.getDate() - day)
  } else if (type === 'lastMonth') {
    start.setMonth(now.getMonth() - 1, 1)
    end.setDate(0)
  }
  return { start: formatDate(start), end: formatDate(end) }
}
