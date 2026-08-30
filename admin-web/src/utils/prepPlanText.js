import {
  EMPTY_LIST_TEXT,
  PREP_PLAN_TITLE,
  SKIP_LABEL,
} from './prepPlanCopy'
import { formatPrepTime } from './prepPlanWindow'

function qtyLine(item) {
  const unit = item.unit || ''
  const recommended = Number(item.recommended_qty || 0)
  if (recommended <= 0) {
    return `${item.item_name}  ${SKIP_LABEL}  可用 ${Number(item.available_fresh_qty || 0)}${unit}  临期 ${Number(item.available_near_expiry_qty || 0)}${unit}  已做 ${Number(item.produced_qty || 0)}${unit}`
  }
  return `${item.item_name}  建议 ${recommended}${unit}  可用 ${Number(item.available_fresh_qty || 0)}${unit}  临期 ${Number(item.available_near_expiry_qty || 0)}${unit}  已做 ${Number(item.produced_qty || 0)}${unit}`
}

export function buildPrepPlanText({ windowStart, windowEnd, stations, expiring, missingRules }) {
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

  if (expiring && expiring.length) {
    lines.push('【临期】')
    expiring.forEach((batch) => {
      lines.push(`${batch.item_name}  剩余 ${Number(batch.remaining_qty || 0)}${batch.unit || ''}  ${formatPrepTime(batch.expires_at)}`)
    })
    lines.push('')
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
