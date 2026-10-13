/** User-facing prep-plan copy. Keep English enums off the chrome. */

export const PREP_PLAN_TITLE = '备货计划'

export const REFRESH_LABEL = '刷新建议'
export const REFRESHING_LABEL = '正在计算建议…'
export const EXPORT_LABEL = '复制清单'
export const EXPORT_TITLE = '复制清单 — 预览'
export const EXPORT_HINT = '预览后复制，不推企业微信。'
export const EMPTY_LIST_TEXT = '暂无建议'
export const CUSTOM_TIME_LABEL = '自定义时间'
export const FORMULA_LABEL = '怎么算的'
export const FORECAST_LABEL = '预测'
export const SAFETY_LABEL = '安全库存'
export const CONFIDENCE_FIELD_LABEL = '信心'
export const EMPTY_HINT = '点「刷新建议」，按档口列出备货品。'
export const ALL_STATIONS_LABEL = '全部后厨'
export const UNCATEGORIZED_STATION = '未分类'
export const TODO_COUNT_LABEL = '还要做'
export const RECOMMENDED_LABEL = '建议制作量'
/** 员工这一档的页面顶上那一句（票 08）：这一页**看得到、改不了**。备货数量不登记，
 *  计算每次都按 0 库存 —— 所以这一页两种身份都没有写控件，这句话对谁都成立。 */
export const READ_ONLY_HINT = '只读：备货计划由管理端查看与刷新。'

export const PRESET_LABELS = {
  future24: '未来 24 小时',
  morning: '早班',
  afternoon: '午后补货',
}

export const CONFIDENCE_LABELS = {
  high: '把握高',
  medium: '参考',
  low: '样本少',
  none: '样本不足',
}

/** 安全裕度按每个品自己的消耗波动定（ADR 0110）—— 师傅会问「怎么比以前少做了」。 */
export const SAFETY_TIER_HINT = '裕度按每个品最近的消耗波动定：波动小的备得少、波动大的备得多。'

/** 目标窗口整段落在歇业日里（`prep_closed_days`）时的那块提示。
 *  没有它的话页面上只有一份空清单，看起来像「今天不用做」，实际是「今天不做生意」。 */
export const CLOSED_DAY_TITLE = '这段时间不做生意'
export const CLOSED_DAY_FALLBACK = '这个窗口整段落在歇业日里。'
export const CLOSED_DAY_HINT = '歇业日在「数据管理 → 备货歇业日」里维护。'

/** 页面上那块「歇业日」维护区（管理端能改，员工只看得到）。 */
export const CLOSED_DAYS_TITLE = '歇业日'
export const CLOSED_DAYS_HINT = '这几天整天不做生意：不给建议，也不算进预测样本。'
export const CLOSED_DAYS_EMPTY = '还没录过歇业日。'
export const CLOSED_DAY_ADD_LABEL = '加一天'
export const CLOSED_DAY_REMOVE_LABEL = '取消'
export const CLOSED_DAY_NOTE_PLACEHOLDER = '说明（选填，如「春节」）'

export function confidenceLabel(level) {
  return CONFIDENCE_LABELS[level] || ''
}

/** 「怎么算的」里的裕度读数：同一个品比从前做得少时，师傅能在这里查到为什么。 */
export function safetyRatioText(ratio) {
  const value = Number(ratio)
  if (!Number.isFinite(value) || value < 0) return ''
  return `裕度 ${Math.round(value * 100)}%`
}
