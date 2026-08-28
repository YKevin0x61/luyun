import { SCALE_UNIT, clampFactor, formatQty } from './recipeCore.js'

const AMOUNT_RE = new RegExp(
  `(\\d+(?:\\.\\d+)?)\\s*([-~\u2013])\\s*(\\d+(?:\\.\\d+)?)(\\s*)(${SCALE_UNIT})?`
    + `|(\\d+)\\s*/\\s*(\\d+)(\\s*)(${SCALE_UNIT})?`
    + `|(\\d+(?:\\.\\d+)?)(\\s*)(${SCALE_UNIT})?`,
  'g',
)

function formatScaled(n) {
  if (!Number.isFinite(n)) return null
  const text = formatQty(n)
  if (text === 'NaN' || !Number.isFinite(Number(text))) return null
  return text
}

export function servingsFactor(targetQty, baseQty) {
  const base = parseFloat(baseQty)
  if (!Number.isFinite(base) || base <= 0) return 1
  const target = parseFloat(targetQty)
  if (!Number.isFinite(target)) return 1
  return clampFactor(target / base)
}

export function parseBaseServingsQty(value) {
  const qty = parseFloat(value)
  if (!Number.isFinite(qty) || qty <= 0) return null
  return qty
}

export function scaleAmount(amount, factor) {
  const src = amount == null ? '' : String(amount)
  const f = clampFactor(factor)
  if (f === 1) return src
  return src.replace(AMOUNT_RE, (match, r1, dash, r2, spR, uR, fn, fd, spF, uF, s1, spS, uS) => {
    if (r1 != null && r2 != null && dash) {
      const left = formatScaled(parseFloat(r1) * f)
      const right = formatScaled(parseFloat(r2) * f)
      if (left == null || right == null) return match
      return left + dash + right + (spR || '') + (uR || '')
    }
    if (fn != null && fd != null) {
      const denom = parseFloat(fd)
      if (!denom) return match
      const scaled = formatScaled((parseFloat(fn) / denom) * f)
      if (scaled == null) return match
      return scaled + (spF || '') + (uF || '')
    }
    const scaled = formatScaled(parseFloat(s1) * f)
    if (scaled == null) return match
    return scaled + (spS || '') + (uS || '')
  })
}
