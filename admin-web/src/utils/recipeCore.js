// 从 public/recipe-core.js 移植的纯函数（配方阅读页用量换算/主题/搜索匹配等），
// 无 DOM 依赖，故直接改写为 ES module 而非跨构建体系共享原文件。

export function slugify(text) {
  let s = (text == null ? '' : String(text)).trim().toLowerCase()
  s = s.replace(/\s+/g, '-').replace(/[/\\:*?"<>|.#]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  return 'sec-' + s
}

export function makeUniqueSlugger() {
  const seen = {}
  return (text) => {
    const base = slugify(text)
    if (!(base in seen)) {
      seen[base] = 1
      return base
    }
    seen[base] += 1
    return `${base}-${seen[base]}`
  }
}

export function matchRecipe(haystack, term) {
  const t = (term == null ? '' : String(term)).trim().toLowerCase()
  if (!t) return true
  return (haystack == null ? '' : String(haystack)).toLowerCase().includes(t)
}

export function normalizeTheme(v) {
  return v === 'dark' || v === 'light' || v === 'auto' ? v : 'auto'
}

const THEME_ORDER = ['auto', 'light', 'dark']
export function nextTheme(v) {
  const cur = normalizeTheme(v)
  return THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length]
}
export function themeLabel(v) {
  return { auto: '跟随系统', light: '浅色', dark: '深色' }[normalizeTheme(v)]
}

export function clampFontPx(v) {
  const n = parseInt(v, 10)
  if (Number.isNaN(n)) return 14
  return Math.max(12, Math.min(20, n))
}

export function clampFactor(v) {
  const n = parseFloat(v)
  if (Number.isNaN(n)) return 1
  return Math.max(0.1, Math.min(99, n))
}

export function formatQty(n) {
  return `${parseFloat((Math.round(n * 100) / 100).toFixed(2))}`
}

const SCALE_UNIT = '千克|毫升|kg|mg|mL|ml|cc|克|斤|两|钱|升|杯|勺|滴|只|个|块|片|张|条|根|瓶|包|袋|盒|颗|粒|份|g|L'
const SCALE_RE = new RegExp(
  `(\\d+(?:\\.\\d+)?)\\s*([-~\u2013])\\s*(\\d+(?:\\.\\d+)?)(\\s*)(${SCALE_UNIT})` +
    `|(\\d+)\\s*/\\s*(\\d+)(\\s*)(${SCALE_UNIT})` +
    `|(\\d+(?:\\.\\d+)?)(\\s*)(${SCALE_UNIT})`,
  'g',
)

export function scaleText(text, factor) {
  const src = text == null ? '' : String(text)
  const f = clampFactor(factor)
  if (f === 1) return src
  return src.replace(SCALE_RE, (m, r1, dash, r2, spR, uR, fn, fd, spF, uF, s1, spS, uS) => {
    if (r1 != null && r2 != null && dash) {
      return formatQty(parseFloat(r1) * f) + dash + formatQty(parseFloat(r2) * f) + spR + uR
    }
    if (fn != null && fd != null) {
      return formatQty((parseFloat(fn) / parseFloat(fd)) * f) + spF + uF
    }
    return formatQty(parseFloat(s1) * f) + spS + uS
  })
}

export function readPref(storage, key, fallback) {
  try {
    const v = storage.getItem(key)
    return v == null ? fallback : v
  } catch (e) {
    return fallback
  }
}
