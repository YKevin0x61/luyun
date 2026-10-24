import { EMPTY_LIST_TEXT, PREP_PLAN_TITLE } from './prepPlanCopy'

/** 复制清单：今天这张清单的纯文本版，发群里用。
 *
 *  只导出当前页签那一档（页面上「复制清单」就在档口页签下面），
 *  不再有「时间窗」这个概念（ADR 0117）。
 */

function qtyLine(item) {
  const value = Number(item.final_qty ?? item.suggested_qty ?? 0)
  const unit = item.unit || ''
  if (value <= 0) return `${item.item_name}  不用做`
  return `${item.item_name}  ${value}${unit}`
}

export function buildPrepPlanText({ windowEnd, stations, closed }) {
  const clock = String(windowEnd || '').slice(11, 16)
  const lines = [
    closed || !clock ? `【${PREP_PLAN_TITLE}】今天` : `【${PREP_PLAN_TITLE}】现在 → ${clock}`,
    '',
  ]

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
