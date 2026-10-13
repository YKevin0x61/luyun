import { describe, expect, it } from 'vitest'
import * as copy from '../prepPlanCopy.js'
import {
  ALL_STATIONS_LABEL,
  CONFIDENCE_LABELS,
  CONFIDENCE_FIELD_LABEL,
  CUSTOM_TIME_LABEL,
  EMPTY_LIST_TEXT,
  EXPORT_HINT,
  EXPORT_LABEL,
  EXPORT_TITLE,
  FORECAST_LABEL,
  FORMULA_LABEL,
  PREP_PLAN_TITLE,
  PRESET_LABELS,
  READ_ONLY_HINT,
  RECOMMENDED_LABEL,
  REFRESH_LABEL,
  REFRESHING_LABEL,
  SAFETY_LABEL,
  SAFETY_TIER_HINT,
  TODO_COUNT_LABEL,
  UNCATEGORIZED_STATION,
  confidenceLabel,
  safetyRatioText,
} from '../prepPlanCopy.js'

describe('prep plan chrome copy', () => {
  it('给人看的字是中文，不含英文枚举', () => {
    const chrome = [
      PREP_PLAN_TITLE,
      REFRESH_LABEL,
      REFRESHING_LABEL,
      CUSTOM_TIME_LABEL,
      RECOMMENDED_LABEL,
      TODO_COUNT_LABEL,
      ALL_STATIONS_LABEL,
      UNCATEGORIZED_STATION,
      FORECAST_LABEL,
      SAFETY_LABEL,
      CONFIDENCE_FIELD_LABEL,
      FORMULA_LABEL,
      SAFETY_TIER_HINT,
      READ_ONLY_HINT,
      EXPORT_LABEL,
      EXPORT_TITLE,
      EXPORT_HINT,
      EMPTY_LIST_TEXT,
      ...Object.values(PRESET_LABELS),
      ...Object.values(CONFIDENCE_LABELS),
    ].join(' ')
    expect(chrome).not.toMatch(/high|medium|normal|waste_risk|run_id/i)
    expect(PREP_PLAN_TITLE).toBe('备货计划')
    expect(REFRESH_LABEL).toBe('刷新建议')
    expect(CUSTOM_TIME_LABEL).toBe('自定义时间')
    expect(PRESET_LABELS.future24).toBe('未来 24 小时')
    expect(PRESET_LABELS.morning).toBe('早班')
    expect(PRESET_LABELS.afternoon).toBe('午后补货')
    expect(ALL_STATIONS_LABEL).toBe('全部后厨')
    expect(UNCATEGORIZED_STATION).toBe('未分类')
    expect(TODO_COUNT_LABEL).toBe('还要做')
    expect(CONFIDENCE_LABELS.low).toBe('样本少')
    expect(FORECAST_LABEL).toBe('预测')
    expect(SAFETY_LABEL).toBe('安全库存')
    expect(CONFIDENCE_FIELD_LABEL).toBe('信心')
    expect(FORMULA_LABEL).toBe('怎么算的')
    expect(EXPORT_LABEL).toBe('复制清单')
    expect(EXPORT_TITLE).toBe('复制清单 — 预览')
    expect(EXPORT_HINT).toBe('预览后复制，不推企业微信。')
    expect(EMPTY_LIST_TEXT).toBe('暂无建议')
  })

  it('只读那句不提已经不存在的功能', () => {
    // 备货数量不登记、每次计算都按 0 库存：这一页两种身份都没有写控件，
    // 所以这句话不能说「登记 / 报废由管理端操作」—— 那两件事已经没有了。
    expect(READ_ONLY_HINT).toContain('只读')
    expect(READ_ONLY_HINT).not.toContain('登记')
    expect(READ_ONLY_HINT).not.toContain('报废')
  })

  it('只服务写控件的文案不再导出（删掉的东西不许回来）', () => {
    const removed = [
      'RECORD_LABEL',
      'RECORD_QTY_LABEL',
      'UNDO_LABEL',
      'DISCARD_LABEL',
      'SKIP_LABEL',
      'EXTRA_RECORD_LABEL',
      'BATCHES_LABEL',
      'FRESH_AVAILABLE_LABEL',
      'NEAR_AVAILABLE_LABEL',
      'NEAR_EXPIRY_TITLE',
      'REMAINING_LABEL',
      'EXPIRES_AT_LABEL',
      'NO_MASTER_REASON',
      'PRODUCED_LABEL',
      'RISK_LABELS',
      'riskLabel',
      'discardConfirmCopy',
    ]
    for (const name of removed) {
      expect(copy[name], `${name} 还在 prepPlanCopy 里`).toBeUndefined()
    }
  })
})

describe('confidenceLabel / safetyRatioText', () => {
  it('信心给中文，未知档不硬猜', () => {
    expect(confidenceLabel('high')).toBe('把握高')
    expect(confidenceLabel('none')).toBe('样本不足')
    expect(confidenceLabel('unknown')).toBe('')
  })

  it('裕度读数是百分数', () => {
    expect(safetyRatioText(0.15)).toBe('裕度 15%')
    expect(safetyRatioText(0)).toBe('裕度 0%')
    // 缺值 / 负数不出读数（宁可不显示，也不要一个假的裕度）。
    expect(safetyRatioText(undefined)).toBe('')
    expect(safetyRatioText(-0.1)).toBe('')
  })
})
