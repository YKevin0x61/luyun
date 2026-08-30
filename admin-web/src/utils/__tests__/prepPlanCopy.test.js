import { describe, expect, it } from 'vitest'
import {
  CONFIDENCE_LABELS,
  FRESH_AVAILABLE_LABEL,
  NEAR_AVAILABLE_LABEL,
  PREP_PLAN_TITLE,
  PRESET_LABELS,
  RECOMMENDED_LABEL,
  REFRESH_LABEL,
  REFRESHING_LABEL,
  RISK_LABELS,
  SKIP_LABEL,
  discardConfirmCopy,
} from '../prepPlanCopy.js'

describe('prep plan chrome copy', () => {
  it('给人看的字是中文，不含英文枚举', () => {
    const chrome = [
      PREP_PLAN_TITLE,
      REFRESH_LABEL,
      REFRESHING_LABEL,
      SKIP_LABEL,
      RECOMMENDED_LABEL,
      FRESH_AVAILABLE_LABEL,
      NEAR_AVAILABLE_LABEL,
      ...Object.values(PRESET_LABELS),
      ...Object.values(RISK_LABELS),
      ...Object.values(CONFIDENCE_LABELS),
    ].join(' ')
    expect(chrome).not.toMatch(/high|medium|normal|waste_risk|run_id/i)
    expect(PREP_PLAN_TITLE).toBe('备货计划')
    expect(REFRESH_LABEL).toBe('刷新建议')
    expect(SKIP_LABEL).toBe('不用做')
    expect(CONFIDENCE_LABELS.low).toBe('样本少')
    expect(RISK_LABELS.high).toBe('缺货高')
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
    expect(copy.body).toContain('30')
    expect(copy.body).toContain('今天 15:00')
    expect(copy.confirmLabel).toBe('报废')
  })
})
