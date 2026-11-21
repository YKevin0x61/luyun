#!/usr/bin/env node
/**
 * 厨房采购页面共用的小工具：日期 / 数量 / 状态文案。
 *
 * 不引第三方日期库：采购的时间字段都是北京时间 ISO 文本，直接截字符串比
 * 经过 Date 再格式化更稳（手机时区不一定是东八区，转一道可能差一天）。
 */

export const ORDER_STATUS_TEXT = {
  pending: '待处理',
  completed: '已完成',
  cancelled: '已取消',
}

export const SEVERITY_TEXT = {
  high: '高',
  normal: '中',
  low: '低',
}

export function pad2(value) {
  return String(value).padStart(2, '0')
}

/** 本机自然日的 YYYY-MM-DD。 */
export function todayText(now = new Date()) {
  return now.getFullYear() + '-' + pad2(now.getMonth() + 1) + '-' + pad2(now.getDate())
}

export function statusText(status) {
  return ORDER_STATUS_TEXT[status] || status || ''
}

export function severityText(severity) {
  return SEVERITY_TEXT[severity] || severity || ''
}

/** 数量最多两位小数；空值给破折号，0 要显示成 0（不能当没填）。 */
export function formatQty(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return String(Math.round(number * 100) / 100)
}

/** ISO 文本 -> MM-DD HH:mm（不经过 Date，避免时区把日期挪走）。 */
export function formatDateTime(raw) {
  const match = String(raw || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  return match ? match[2] + '-' + match[3] + ' ' + match[4] + ':' + match[5] : ''
}

/** ISO 日期或日期时间 -> MM-DD。 */
export function formatMonthDay(raw) {
  const match = String(raw || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  return match ? match[2] + '-' + match[3] : ''
}

/** ISO 日期 -> M月D日（下单页头部与天气卡用）。 */
export function formatMonthDayChinese(raw) {
  const match = String(raw || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return ''
  return Number(match[2]) + '月' + Number(match[3]) + '日'
}

/** ISO 时间 -> HH:mm（天气「更新于」）。 */
export function formatClock(raw) {
  const match = String(raw || '').match(/[T ](\d{2}):(\d{2})/)
  return match ? match[1] + ':' + match[2] : ''
}

const LUNAR_DIGITS = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九']

function lunarDayText(value) {
  const day = Number(value)
  if (!Number.isInteger(day) || day <= 0 || day > 30) return ''
  if (day < 10) return '初' + LUNAR_DIGITS[day]
  if (day === 10) return '初十'
  if (day < 20) return '十' + LUNAR_DIGITS[day % 10]
  if (day === 20) return '二十'
  if (day < 30) return '廿' + LUNAR_DIGITS[day % 10]
  return '三十'
}

const LUNAR_FORMATTER =
  typeof Intl !== 'undefined' && typeof Intl.DateTimeFormat === 'function'
    ? new Intl.DateTimeFormat('zh-CN-u-ca-chinese', {
        timeZone: 'Asia/Shanghai',
        month: 'long',
        day: 'numeric',
      })
    : null

/**
 * 真实农历的「月日」，如 2026-11-22 -> 十月十四。
 * 固定用北京时间取农历（农历按东八区换日）；浏览器不支持中国农历时返回空串，
 * 页面只隐藏这一行，不影响下单。
 */
export function lunarMonthDay(raw) {
  const match = String(raw || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match || !LUNAR_FORMATTER) return ''
  try {
    // 北京中午对应 UTC 04:00，避开任何时区的换日边界。
    const instant = new Date(
      Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 4),
    )
    if (Number.isNaN(instant.getTime())) return ''
    const parts = LUNAR_FORMATTER.formatToParts(instant)
    const month = (parts.find((part) => part.type === 'month') || {}).value || ''
    const day = (parts.find((part) => part.type === 'day') || {}).value || ''
    return month + lunarDayText(day)
  } catch (err) {
    return ''
  }
}

export function weekdayText(weekday) {
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][Number(weekday)] || ''
}

/** 异常里的「是参考值的几倍」；没有参考值时给空。 */
export function ratioText(ratio) {
  if (ratio === null || ratio === undefined || ratio === '') return ''
  return formatQty(Number(ratio) * 100) + '%'
}

/** 把 ?id= 解析成正整数；缺省 / 脏值给 0。 */
export function parseOrderId(raw) {
  const value = Array.isArray(raw) ? raw[0] : raw
  const number = Number(String(value || '').trim())
  return Number.isInteger(number) && number > 0 ? number : 0
}

/**
 * 与 services/purchase/forecast.py 的 suggested_quantity 同口径：
 * 参考值 × 安全系数 − 当前盘点，按 order_step 向上取整；raw < 1 保留 1 位。
 * 页面在用户改了盘点库存后用这个函数实时重算参考值（下单量）。
 */
export function suggestedQuantity(reference, options = {}) {
  const ref = Number(reference)
  if (!Number.isFinite(ref) || ref <= 0) return null
  const factor = Number(options.safetyFactor)
  const safety = Number.isFinite(factor) && factor > 0 ? factor : 1.2
  const stepValue = Number(options.orderStep)
  const step = Number.isFinite(stepValue) && stepValue > 0 ? stepValue : 1
  const stockValue = Number(options.currentStock)
  const stock = Number.isFinite(stockValue) && stockValue > 0 ? stockValue : 0
  const raw = Math.max(0, ref * safety - stock)
  if (raw <= 0) return 0
  if (raw < 1) return Math.round((raw + 1e-9) * 10) / 10
  return Math.round((Math.ceil(raw / step) * step + 1e-9) * 100) / 100
}
