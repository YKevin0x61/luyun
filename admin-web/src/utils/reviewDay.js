/** 「复盘日」在前端的薄对应（ADR 0140）。
 *
 * 后端 `services/business_day.py` 是 07:00 主口径（`REVIEW_CLOCK`）与品-天窗口的唯一
 * 实现；前端只借它做两件事：主口径展示的兜底值，以及数据质量提示里「今天应该有
 * 多少个整点快照」。预期槽位跟门店当天营业时段（`context.runtime.open/close`）走，
 * 不再硬编码 07:00–21:00 —— 门店改营业时间时这里跟着配置变。
 *
 * 这里**不重算复盘日窗口**：品-天窗口只由后端算，前端多一份就是第二实现。
 */

/** 主口径钟点的兜底（后端 `REVIEW_CLOCK`，ADR 0129）。 */
export const REVIEW_CLOCK_FALLBACK = '07:00'

/** 营业时段的兜底：与后端 `prep_plan_service.DEFAULT_OPEN` / `DEFAULT_CLOSE` 同值。 */
const OPEN_FALLBACK = '07:00'
const CLOSE_FALLBACK = '21:30'

/** `HH:MM` → `{ hour, minute }`；缺失 / 坏值返回 null（不抛）。 */
function parseHhmm(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value ?? '').trim())
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null
  return { hour, minute }
}

/** 数字小时；null / 空串 / 非数字都返回 null。 */
function toHour(value) {
  if (value === null || value === undefined || value === '') return null
  const hour = Number(value)
  return Number.isFinite(hour) ? hour : null
}

/** 预期整点快照槽数。
 *
 * 整点槽 = `floor(close 小时) - ceil(open 小时) + 1`（07:00–21:30 → 15 个；08:00–22:00
 * 也是 15 个；07:30 开门则首槽 08:00，共 14 个）。今天还没过完时只算到当前小时
 * （至少 1 个），不拿「才早上 8 点」告警；`nowHour` 缺失 / 非数字时按 1 个槽算
 * （与旧实现一致，避免早段误报）。`runtime` 缺失 / 坏值 / 打烊不晚于开门（跨零点
 * 或配置错误）统一兜底 07:00–21:30。
 */
export function expectedSnapshotSlots({ runtime = null, isToday = false, nowHour = null } = {}) {
  let open = parseHhmm(runtime?.open)
  let close = parseHhmm(runtime?.close)
  if (!open || !close || close.hour <= open.hour) {
    open = parseHhmm(OPEN_FALLBACK)
    close = parseHhmm(CLOSE_FALLBACK)
  }
  const openHour = open.minute > 0 ? open.hour + 1 : open.hour
  const total = Math.max(1, close.hour - openHour + 1)
  if (!isToday) return total
  const hour = toHour(nowHour)
  if (hour === null) return 1
  return Math.min(total, Math.max(1, hour - openHour + 1))
}
