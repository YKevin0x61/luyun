import { EMPTY_LIST_TEXT, PREP_PLAN_TITLE, horizonTitle } from './prepPlanCopy'

/** 复制清单：这张清单的纯文本版，发群里用。
 *
 *  只导出当前页签那一档（页面上「复制清单」就在档口页签下面）。
 *  时间跨度是页面上选的（24 / 48 / 72 / 168 小时）：24 小时就是「现在 → HH:MM」，
 *  更长就给带日期的终点，否则收到清单的人分不清是今天的 13:59 还是后天的。
 */

function qtyLine(item) {
  const raw = Number(item.final_qty ?? item.suggested_qty ?? 0)
  // 半成品按整份写（后端算出来就是整数，这里再兜一层，老清单也不会出现 58.7 份）
  const value = item.kind === 'made' ? Math.ceil(raw) : Math.round(raw * 10) / 10
  const unit = item.unit || ''
  if (value <= 0) return `${item.item_name}  不用做`
  return `${item.item_name}  ${value}${unit}`
}

function endLabel(windowEnd, multiDay) {
  const text = String(windowEnd || '')
  const clock = text.slice(11, 16)
  if (!clock) return ''
  if (!multiDay) return clock
  const month = Number(text.slice(5, 7))
  const day = Number(text.slice(8, 10))
  if (!month || !day) return clock
  return `${month}/${day} ${clock}`
}

export function buildPrepPlanText({ windowEnd, stations, closed, horizonHours = 24 }) {
  const hours = Number(horizonHours) || 24
  const end = endLabel(windowEnd, hours > 24)
  const head =
    closed || !end
      ? `【${PREP_PLAN_TITLE}】今天`
      : hours > 24
        ? `【${PREP_PLAN_TITLE}】${horizonTitle(hours)} · 现在 → ${end}`
        : `【${PREP_PLAN_TITLE}】现在 → ${end}`
  const lines = [head, '']

  if (closed) {
    lines.push('今天已打烊。')
    return lines.join('\n')
  }

  if (!stations || !stations.length) {
    lines.push(EMPTY_LIST_TEXT)
  } else {
    stations.forEach((group) => {
      lines.push(`【${group.stationLabel}】`)
      group.items.forEach((item) => lines.push(qtyLine(item)))
      lines.push('')
    })
  }

  while (lines[lines.length - 1] === '') lines.pop()
  return lines.join('\n')
}
