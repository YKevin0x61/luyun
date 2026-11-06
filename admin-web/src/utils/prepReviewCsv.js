/**
 * 备货复盘明细 → CSV（票 06 前端）。
 *
 * 抽成纯函数是为了两件事：浏览器里下载与单测用同一份格式化逻辑；
 * NULL 在这里一律留空（不写 0），跟页面上的「—」是同一个口径。
 */

export const REVIEW_CSV_HEADERS = [
  '品名',
  '单位',
  '参考值',
  '实际卖出',
  '偏差',
  '售罄时刻',
  '窗口后+1h',
  '窗口后+2h',
  '次日',
  '退菜',
]

/** `sold_out_at` 是 ISO8601；只取墙上时间的 HH:MM，null 原样返回。 */
export function reviewSoldOutTime(value) {
  if (!value) return null
  const match = String(value).match(/T(\d{2}:\d{2})/)
  return match ? match[1] : null
}

/**
 * 窗口后的增量：轨迹列（sold_1h / sold_2h / sold_next_day）是**累计量**，
 * 减去窗口内的 sold_qty 才是窗口结束后新增的部分。任一侧为 null 就返回 null。
 */
export function reviewAfterWindow(item, key) {
  if (!item) return null
  const total = item[key]
  const base = item.sold_qty
  if (total === null || total === undefined || base === null || base === undefined) return null
  return Number(total) - Number(base)
}

function numberText(value) {
  if (value === null || value === undefined || value === '') return ''
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  // 后端是 double：直接 String() 会导出 -20.599999999999994，先收到分位再抹掉尾零。
  return String(Math.round(n * 100) / 100)
}

function signedText(value) {
  const text = numberText(value)
  if (text === '') return ''
  return Number(value) > 0 ? `+${text}` : text
}

function escapeCell(value) {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** 当前选中日 + 当前钟点组的明细 CSV，带 UTF-8 BOM（Excel 直接打开不乱码）。 */
export function buildReviewCsv({ items = [] } = {}) {
  const lines = [REVIEW_CSV_HEADERS.join(',')]
  for (const item of items) {
    const deviation =
      item.sold_qty === null || item.sold_qty === undefined || item.suggested_qty === null || item.suggested_qty === undefined
        ? null
        : Number(item.sold_qty) - Number(item.suggested_qty)
    lines.push(
      [
        item.item_name,
        item.unit,
        item.suggested_qty,
        item.sold_qty,
        signedText(deviation),
        reviewSoldOutTime(item.sold_out_at),
        reviewAfterWindow(item, 'sold_1h'),
        reviewAfterWindow(item, 'sold_2h'),
        reviewAfterWindow(item, 'sold_next_day'),
        item.refund_qty,
      ]
        .map((value) => escapeCell(numberOrText(value)))
        .join(','),
    )
  }
  return `\ufeff${lines.join('\n')}\n`
}

/** 数字列转文本（保留小数），其余原样；`numberText` 负责 null → 空。 */
function numberOrText(value) {
  if (typeof value === 'number') return numberText(value)
  return value
}

export function reviewCsvFilename(day, clock) {
  const digits = String(clock || '').replace(/\D/g, '').padStart(4, '0').slice(0, 4)
  return `备货复盘_${day}_${digits}.csv`
}
