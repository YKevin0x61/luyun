/** User-facing prep-plan copy. Keep English enums off the chrome. */

export const PREP_PLAN_TITLE = '备货计划'

export const REFRESH_LABEL = '刷新建议'
export const REFRESHING_LABEL = '正在计算建议…'
export const EXPORT_LABEL = '复制清单'
export const CUSTOM_TIME_LABEL = '自定义时间'
export const RECORD_LABEL = '登记'
export const UNDO_LABEL = '撤销'
export const DISCARD_LABEL = '报废'
export const SKIP_LABEL = '不用做'
export const EXTRA_RECORD_LABEL = '多做一笔'
export const FORMULA_LABEL = '怎么算的'
export const BATCHES_LABEL = '本品批次'
export const NO_MASTER_REASON = '没有备货品主数据，不能登记'
export const EMPTY_HINT = '点「刷新建议」，按档口列出备货品。'
export const ALL_STATIONS_LABEL = '全部后厨'
export const UNCATEGORIZED_STATION = '未分类'
export const TODO_COUNT_LABEL = '还要做'
export const RECOMMENDED_LABEL = '建议制作量'
export const FRESH_AVAILABLE_LABEL = '非临期可用'
export const NEAR_AVAILABLE_LABEL = '临期可用'

export const PRESET_LABELS = {
  future24: '未来 24 小时',
  morning: '早班',
  afternoon: '午后补货',
}

export const RISK_LABELS = {
  high: '缺货高',
  medium: '缺货',
  low: '略缺',
  waste_risk: '可能多了',
  expiry_risk: '临期',
  normal: '',
}

export const CONFIDENCE_LABELS = {
  high: '把握高',
  medium: '参考',
  low: '样本少',
  none: '样本不足',
}

export const SLOT_LABELS = {
  morning: '早班',
  lunch: '午市',
  afternoon: '下午',
  dinner: '晚市',
}

export function riskLabel(level) {
  return RISK_LABELS[level] || ''
}

export function confidenceLabel(level) {
  return CONFIDENCE_LABELS[level] || ''
}

export function slotLabel(name) {
  return SLOT_LABELS[name] || name || ''
}

export function discardConfirmCopy(batch) {
  const name = batch?.item_name || '这批'
  const qty = Number(batch?.remaining_qty || 0)
  const unit = batch?.unit || ''
  const expires = batch?.expires_at || ''
  return {
    title: `报废「${name}」？`,
    body: `剩余 ${qty} ${unit}。过期时间 ${expires}。报废后不能再用于备货。`,
    confirmLabel: DISCARD_LABEL,
  }
}
