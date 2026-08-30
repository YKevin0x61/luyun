import { describe, expect, it } from 'vitest'
import {
  ALL_STATIONS_LABEL,
  BATCHES_LABEL,
  CONFIDENCE_LABELS,
  CUSTOM_TIME_LABEL,
  DISCARD_LABEL,
  EMPTY_LIST_TEXT,
  EXPORT_HINT,
  EXPORT_LABEL,
  EXPORT_TITLE,
  EXTRA_RECORD_LABEL,
  EXPIRES_AT_LABEL,
  FORECAST_LABEL,
  FORMULA_LABEL,
  FRESH_AVAILABLE_LABEL,
  NEAR_AVAILABLE_LABEL,
  NEAR_EXPIRY_TITLE,
  NO_MASTER_REASON,
  PREP_PLAN_TITLE,
  PRESET_LABELS,
  PRODUCED_LABEL,
  RECOMMENDED_LABEL,
  RECORD_LABEL,
  REFRESH_LABEL,
  REFRESHING_LABEL,
  REMAINING_LABEL,
  RISK_LABELS,
  SAFETY_LABEL,
  SKIP_LABEL,
  UNCATEGORIZED_STATION,
  UNDO_LABEL,
  discardConfirmCopy,
} from '../prepPlanCopy.js'

describe('prep plan chrome copy', () => {
  it('给人看的字是中文，不含英文枚举', () => {
    const chrome = [
      PREP_PLAN_TITLE,
      REFRESH_LABEL,
      REFRESHING_LABEL,
      SKIP_LABEL,
      CUSTOM_TIME_LABEL,
      RECOMMENDED_LABEL,
      PRODUCED_LABEL,
      RECORD_LABEL,
      UNDO_LABEL,
      DISCARD_LABEL,
      EXTRA_RECORD_LABEL,
      NO_MASTER_REASON,
      FRESH_AVAILABLE_LABEL,
      NEAR_AVAILABLE_LABEL,
      NEAR_EXPIRY_TITLE,
      REMAINING_LABEL,
      EXPIRES_AT_LABEL,
      ALL_STATIONS_LABEL,
      UNCATEGORIZED_STATION,
      FORECAST_LABEL,
      SAFETY_LABEL,
      FORMULA_LABEL,
      BATCHES_LABEL,
      EXPORT_LABEL,
      EXPORT_TITLE,
      EXPORT_HINT,
      EMPTY_LIST_TEXT,
      ...Object.values(PRESET_LABELS),
      ...Object.values(RISK_LABELS),
      ...Object.values(CONFIDENCE_LABELS),
    ].join(' ')
    expect(chrome).not.toMatch(/high|medium|normal|waste_risk|run_id/i)
    expect(PREP_PLAN_TITLE).toBe('备货计划')
    expect(REFRESH_LABEL).toBe('刷新建议')
    expect(CUSTOM_TIME_LABEL).toBe('自定义时间')
    expect(PRESET_LABELS.future24).toBe('未来 24 小时')
    expect(PRESET_LABELS.morning).toBe('早班')
    expect(PRESET_LABELS.afternoon).toBe('午后补货')
    expect(SKIP_LABEL).toBe('不用做')
    expect(PRODUCED_LABEL).toBe('已做')
    expect(RECORD_LABEL).toBe('登记')
    expect(UNDO_LABEL).toBe('撤销')
    expect(UNDO_LABEL).not.toContain('报废')
    expect(DISCARD_LABEL).toBe('报废')
    expect(NEAR_EXPIRY_TITLE).toBe('临期批次')
    expect(ALL_STATIONS_LABEL).toBe('全部后厨')
    expect(UNCATEGORIZED_STATION).toBe('未分类')
    expect(REMAINING_LABEL).toBe('剩余')
    expect(EXPIRES_AT_LABEL).toBe('过期时间')
    expect(EXTRA_RECORD_LABEL).toBe('多做一笔')
    expect(NO_MASTER_REASON).toBe('没有备货品主数据，不能登记')
    expect(CONFIDENCE_LABELS.low).toBe('样本少')
    expect(RISK_LABELS.high).toBe('缺货高')
    expect(FORECAST_LABEL).toBe('预测')
    expect(SAFETY_LABEL).toBe('安全库存')
    expect(FORMULA_LABEL).toBe('怎么算的')
    expect(BATCHES_LABEL).toBe('本品批次')
    expect(EXPORT_LABEL).toBe('复制清单')
    expect(EXPORT_TITLE).toBe('复制清单 — 预览')
    expect(EXPORT_HINT).toBe('预览后复制，不推企业微信。')
    expect(EMPTY_LIST_TEXT).toBe('暂无建议')
  })
})

describe('discardConfirmCopy', () => {
  it('报废确认点名剩余和过期时间', () => {
    const copy = discardConfirmCopy({
      item_name: '虾饺馅',
      remaining_qty: 30,
      unit: '份',
      expires_at: '今天 15:00',
    })
    expect(copy.title).toBe('报废「虾饺馅」？')
    expect(copy.body).toContain('剩余')
    expect(copy.body).toContain('30')
    expect(copy.body).toContain('过期时间')
    expect(copy.body).toContain('今天 15:00')
    expect(copy.confirmLabel).toBe('报废')
  })
})
