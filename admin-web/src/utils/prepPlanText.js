import {
  EMPTY_LIST_TEXT,
  PREP_PLAN_TITLE,
} from './prepPlanCopy'
import { formatPrepTime } from './prepPlanWindow'

function qtyLine(item) {
  const unit = item.unit || ''
  const recommended = Number(item.recommended_qty || 0)
  if (recommended <= 0) {
    return `${item.item_name}  不用做`
  }
  return `${item.item_name}  建议 ${recommended}${unit}`
}

export function buildPrepPlanText({ windowStart, windowEnd, stations, missingRules }) {
  const lines = []
  const startText = formatPrepTime(windowStart)
  const endText = formatPrepTime(windowEnd)
  lines.push(`【${PREP_PLAN_TITLE}】${startText} ~ ${endText}`)
  lines.push('')

  if (!stations || !stations.length) {
    lines.push(EMPTY_LIST_TEXT)
  } else {
    stations.forEach((group) => {
      lines.push(`【${group.stationLabel}】`)
      group.items.forEach((item) => {
        lines.push(qtyLine(item))
      })
      lines.push('')
    })
  }

  if (missingRules && missingRules.length) {
    lines.push('【缺规则】')
    missingRules.forEach((row) => {
      lines.push(`${row.dish_name}  ${row.reason || ''}`)
    })
  }

  while (lines[lines.length - 1] === '') lines.pop()
  return lines.join('\n')
}
