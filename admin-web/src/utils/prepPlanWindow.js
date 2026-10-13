import { UNCATEGORIZED_STATION } from './prepPlanCopy'

export const PRESET_FUTURE_24H = 'future24'
export const PRESET_MORNING = 'morning'
export const PRESET_AFTERNOON = 'afternoon'
export const PRESET_CUSTOM = 'custom'

function atTime(base, hours, minutes) {
  const next = new Date(base)
  next.setHours(hours, minutes, 0, 0)
  return next
}

function addDays(base, days) {
  const next = new Date(base)
  next.setDate(next.getDate() + days)
  return next
}

export function resolvePrepWindow(preset, now = new Date(), customStart, customEnd) {
  const current = new Date(now)
  if (preset === PRESET_CUSTOM) {
    const start = customStart ? new Date(customStart) : current
    const end = customEnd ? new Date(customEnd) : new Date(start.getTime() + 24 * 3600 * 1000)
    if (!(end > start)) {
      return { start, end: new Date(start.getTime() + 24 * 3600 * 1000) }
    }
    return { start, end }
  }
  if (preset === PRESET_MORNING) {
    const open = atTime(current, 7, 30)
    const start = current.getTime() > open.getTime() ? current : open
    return { start, end: atTime(addDays(current, 1), 7, 30) }
  }
  if (preset === PRESET_AFTERNOON) {
    const open = atTime(current, 14, 0)
    const start = current.getTime() > open.getTime() ? current : open
    // 终点撑到第二天 13:00（午市 11:00–14:00 的三分之二），不是午市刚开始的 12:00：
    // 一次备货要盖住整个上午段的午市需求，只算一小时等于把午市少算三分之二。
    return { start, end: atTime(addDays(current, 1), 13, 0) }
  }
  return { start: current, end: new Date(current.getTime() + 24 * 3600 * 1000) }
}

export function toDatetimeLocalValue(date) {
  const value = new Date(date)
  const pad = (n) => String(n).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`
}

export function formatPrepTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function itemKey(item) {
  const station = (item?.station || '').trim() || UNCATEGORIZED_STATION
  return `${station}|${item?.item_name || ''}|${item?.unit || ''}`
}
