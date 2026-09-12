/** Standard-photo overlay marks. Positions are 0–1 fractions of the image. */

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

export function standardImageUrl(kind, item) {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const id = item && item.id
  const version = item && item.current_standard_id
  return `${prefix}/items/${id}/standard?v=${version}`
}

export function dailyItemStandardUrl(kind, row) {
  return standardImageUrl(kind, {
    id: row && row.item_id,
    current_standard_id: row && row.current_standard_id,
  })
}

export function dailyCaptureUrl(kind, row) {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const shift = encodeURIComponent((row && row.shift) || '')
  const version = encodeURIComponent((row && row.capture_id) || '')
  return `${prefix}/daily/${itemId}/capture?shift=${shift}&v=${version}`
}

export function frozenStandardUrl(kind, row) {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const shift = encodeURIComponent((row && row.shift) || '')
  const version = encodeURIComponent((row && row.frozen_standard_id) || '')
  return `${prefix}/daily/${itemId}/frozen-standard?shift=${shift}&v=${version}`
}

export function deepCleanShotUrl(kind, row, which) {
  const prefix = kind === 'admin' ? '/api/hygiene/admin' : '/api/hygiene/staff'
  const itemId = row && row.item_id
  const key = which === 'before' ? 'before_capture_id' : 'after_capture_id'
  const version = encodeURIComponent((row && row[key]) || '')
  return `${prefix}/deep-clean/${itemId}/${which}?v=${version}`
}

export function formatWatermarkTime(value) {
  const raw = String(value || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  if (matched) return `${matched[1]} ${matched[2]}`
  return raw
}
