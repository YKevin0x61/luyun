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
  if (Number.isNaN(n)) return 12
  return Math.max(12, Math.min(20, n))
}

/** 阅读页正文的**默认**字号（用户没手动调过时用的那个）。
 *
 * 窄屏给 14px：厨师把手机/平板架在灶台边、离手一臂看，12px 在蒸汽和弱光下认不动；
 * 桌面维持 12px（近距阅读，且正文区本来就宽）。
 *
 * 放在这里而不是 CSS 的 `@media` 里，是因为详情页的字号是 JS 用
 * `documentElement.style.setProperty('--reader-fs', …)` 写的**内联样式**（见
 * `RecipeDetailView.applyFont`），内联一开始就压过任何选择器 —— 只写 CSS 媒体查询
 * 等于没改（实测三档仍都是 12px）。断点与 `recipe.css` 的 `max-width:40rem` 对齐。
 */
export function defaultFontPx() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 12
  return window.matchMedia('(max-width: 40rem)').matches ? 14 : 12
}

export function clampFactor(v) {
  const n = parseFloat(v)
  if (Number.isNaN(n)) return 1
  return Math.max(0.1, Math.min(99, n))
}

export function formatQty(n) {
  return `${parseFloat((Math.round(n * 100) / 100).toFixed(2))}`
}

export const SCALE_UNIT = '千克|毫升|kg|mg|mL|ml|cc|克|斤|两|钱|升|杯|勺|滴|只|个|块|片|张|条|根|瓶|包|袋|盒|颗|粒|份|g|L'
export const SCALE_UNITS = SCALE_UNIT.split('|')
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
