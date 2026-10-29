/** 节假日日历的月份网格与名称校验（票 02）。
 *
 *  页面（`views/hr/HolidayCalendarView.vue`）只负责画与点；「一个月怎么摆成 7 列网格」
 *  与「什么算一个能保存的名称」这两件事在这里，各有单测钉住。
 *
 *  **页面一次只显示一个月**（2026-10-29 改版：默认当月、左右翻月），`yearGrid` 留着
 *  给「一年 12 张」的老口径与既有单测，两者共用同一份 `monthGrid`。
 *
 *  **一天一格的键就是 `YYYY-MM-DD`**：与后端那张表的 `day` 同一份字符串 —— 页面上点
 *  哪一格、请求里发哪一天、日历里存哪一天，中间不再有一次格式转换。
 *
 *  网格的列从**周一**起（店里看排班表的习惯）：`Date.getUTCDay()` 是周日起的，所以要
 *  错一位（见 `mondayIndex`）。用 UTC 的 `Date` 只为了算星期几与天数，与本地时区无关。
 */

/** 名称上限，与 `services/holiday_calendar.py` 的 `MAX_NAME` 同一口径（1~20 字）。 */
export const MAX_HOLIDAY_NAME = 20

export const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

const pad2 = (value) => String(value).padStart(2, '0')

/** 列下标：周一起算（0=周一 … 6=周日）。 */
function mondayIndex(year, month) {
  const sundayFirst = new Date(Date.UTC(year, month - 1, 1)).getUTCDay()
  return (sundayFirst + 6) % 7
}

function daysInMonth(year, month) {
  // 下个月的第 0 天 = 这个月的最后一天。闰年由 Date 自己判。
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

export function monthLabel(month) {
  return `${month} 月`
}

/** 某一天的键（也是请求路径里那一段）。 */
export function dayKey(year, month, day) {
  return `${year}-${pad2(month)}-${pad2(day)}`
}

/** 单月的网格：`{ month, label, weeks }`，每个 `weeks` 是若干行、每行 7 格。
 *
 *  格子里要么是 `YYYY-MM-DD`、要么是 `null`（本月第一天之前 / 最后一天之后的空位）——
 *  `null` 的格子不渲染成按钮，所以点不出一个不存在的日子。
 */
export function monthGrid(year, month) {
  const lead = mondayIndex(year, month)
  const total = daysInMonth(year, month)
  const cells = [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: total }, (_, day) => dayKey(year, month, day + 1)),
  ]
  while (cells.length % 7 !== 0) cells.push(null)
  const weeks = []
  for (let start = 0; start < cells.length; start += 7) {
    weeks.push(cells.slice(start, start + 7))
  }
  return { month, label: monthLabel(month), weeks }
}

/** 一年的网格：12 个 `monthGrid` 按月份顺序排开。 */
export function yearGrid(year) {
  return Array.from({ length: 12 }, (_, index) => monthGrid(year, index + 1))
}

/** 名称的问题（空串 = 没问题）。与后端同一口径：先剥两端空白，再数 1~20 个字。 */
export function nameError(raw) {
  const name = String(raw ?? '').trim()
  if (!name) return '请写这一天的节假日名称：比如「国庆」「春节」'
  if (name.length > MAX_HOLIDAY_NAME) return `名称最多 ${MAX_HOLIDAY_NAME} 个字`
  return ''
}

/** 接口给的 `days` 摆成「日期 → 名称」，格子照它上色写字。 */
export function holidayMap(days) {
  const map = {}
  for (const row of days || []) {
    if (row && row.day) map[row.day] = row.name || ''
  }
  return map
}
