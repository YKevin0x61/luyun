// 系统健康页的数值格式化：从 useSystemHealth.js 抽出来，供量表派生（systemHealthCharts）与展示层共用。
// 纯函数，无 Vue 依赖，便于在 node 环境下直接单测。

export function formatUptime(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d} 天 ${h} 小时 ${m} 分`
  if (h > 0) return `${h} 小时 ${m} 分`
  if (m > 0) return `${m} 分 ${s} 秒`
  return `${s} 秒`
}

export function formatMb(mb) {
  if (typeof mb !== 'number' || !Number.isFinite(mb)) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`
}

/** 大整数加千分位：182345 -> 182,345；非法值返回 `—`。 */
export function formatCount(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** 百分比文字：76.63 -> '76.6%'；非法值返回 `—`。 */
export function formatPct(pct, digits = 1) {
  if (typeof pct !== 'number' || !Number.isFinite(pct)) return '—'
  return `${pct.toFixed(digits)}%`
}
