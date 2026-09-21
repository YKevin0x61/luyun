/** Standard-photo overlay marks. Positions are 0–1 fractions of the image. */

import { formatHygieneStamp } from './hygieneTime'

export const MARK_KINDS = ['circle', 'arrow', 'caption']

export function clamp01(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  if (n < 0) return 0
  if (n > 1) return 1
  return n
}

export function createCircleMark(x, y, r = 0.08) {
  const radius = Number(r)
  return {
    kind: 'circle',
    x: clamp01(x),
    y: clamp01(y),
    r: Number.isFinite(radius) ? Math.min(0.4, Math.max(0.02, radius)) : 0.08,
  }
}

export function createArrowMark(x1, y1, x2, y2) {
  return {
    kind: 'arrow',
    x1: clamp01(x1),
    y1: clamp01(y1),
    x2: clamp01(x2),
    y2: clamp01(y2),
  }
}

export function createCaptionMark(x, y, text) {
  return {
    kind: 'caption',
    x: clamp01(x),
    y: clamp01(y),
    text: String(text || '').trim(),
  }
}

export function isMarkupMark(item) {
  if (!item || typeof item !== 'object') return false
  return MARK_KINDS.includes(item.kind)
}

export function parseMarkup(raw) {
  let value = raw
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw)
    } catch {
      return []
    }
  }
  if (!Array.isArray(value)) return []
  return value.filter(isMarkupMark)
}

export function appendImageVariant(url, variant) {
  if (!url || !variant || variant === 'original') return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}variant=${encodeURIComponent(variant)}`
}

export function standardImageUrl(_kind, item, variant = 'original') {
  const standardId = item && item.current_standard_id
  if (!standardId) return ''
  return appendImageVariant(
    `/api/hygiene/standards/${standardId}/image`,
    variant,
  )
}

export function dailyItemStandardUrl(kind, row, variant = 'original') {
  return standardImageUrl(kind, {
    id: row && row.item_id,
    current_standard_id: row && row.current_standard_id,
  }, variant)
}

export function dailyCaptureUrl(kind, row, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const shift = encodeURIComponent((row && row.shift) || '')
  const version = encodeURIComponent((row && row.capture_id) || '')
  return appendImageVariant(
    `${prefix}/daily/${itemId}/capture?shift=${shift}&v=${version}`,
    variant,
  )
}

export function frozenStandardUrl(kind, row, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const shift = encodeURIComponent((row && row.shift) || '')
  const version = encodeURIComponent((row && row.frozen_standard_id) || '')
  return appendImageVariant(
    `${prefix}/daily/${itemId}/frozen-standard?shift=${shift}&v=${version}`,
    variant,
  )
}

export function deepCleanShotUrl(kind, row, which, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const key = which === 'before' ? 'before_capture_id' : 'after_capture_id'
  const version = encodeURIComponent((row && row[key]) || '')
  return appendImageVariant(
    `${prefix}/deep-clean/${itemId}/${which}?v=${version}`,
    variant,
  )
}

export function fixOriginalUrl(kind, ticket, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const id = ticket && ticket.id
  const version = encodeURIComponent((ticket && ticket.capture_id) || '')
  return appendImageVariant(
    `${prefix}/fix/${id}/original?v=${version}`,
    variant,
  )
}

export function fixReshootUrl(kind, ticket, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const id = ticket && ticket.id
  const version = encodeURIComponent((ticket && ticket.reshoot_capture_id) || '')
  return appendImageVariant(
    `${prefix}/fix/${id}/reshoot?v=${version}`,
    variant,
  )
}

export function teachingShotUrl(kind, example, which, variant = 'original') {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const id = example && example.id
  const side = which === 'right' ? 'right' : 'left'
  const captureKey = side === 'right' ? 'right_capture_id' : 'left_capture_id'
  const version = encodeURIComponent((example && (example[captureKey] || example.id)) || '')
  return appendImageVariant(
    `${prefix}/teaching/${id}/${side}?v=${version}`,
    variant,
  )
}

export function formatWatermarkTime(value) {
  return formatHygieneStamp(value)
}

export function chinaNowIso(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now)
  const pick = (type) => {
    const part = parts.find((item) => item.type === type)
    return part ? part.value : '00'
  }
  return `${pick('year')}-${pick('month')}-${pick('day')}T${pick('hour')}:${pick('minute')}:${pick('second')}+08:00`
}
