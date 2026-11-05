import { describe, expect, it } from 'vitest'
import * as copy from '../prepPlanCopy.js'

/**
 * 备货计划文案表（ADR 0117）：时间窗、信心、裕度、歇业日那一批常量随功能一起退场，
 * 这里盯住的是「页面还会用到的那些名字在、退场的那些名字不再回来」。
 */

const KEPT = [
  'PREP_PLAN_TITLE',
  'MADE_LABEL',
  'BOUGHT_LABEL',
  'DONE_LABEL',
  'COPY_LABEL',
  'EMPTY_LIST_TEXT',
  'CLOSED_TEXT',
  'PICK_STATION_TITLE',
  'PICK_STATION_HINT',
  'BASIS_HINT',
  'MISSING_RULE_TEXT',
  'NO_HISTORY_TEXT',
  'RECIPE_FIX_LINK',
]

const RETIRED = [
  'REFRESH_LABEL',
  'EXPORT_LABEL',
  'EXPORT_TITLE',
  'PRESET_LABELS',
  'CONFIDENCE_LABELS',
  'SAFETY_LABEL',
  'SAFETY_TIER_HINT',
  'CLOSED_DAYS_TITLE',
  'CLOSED_DAY_TITLE',
  'ALL_STATIONS_LABEL',
  'TODO_COUNT_LABEL',
  'RECOMMENDED_LABEL',
]

describe('prep-plan copy', () => {
  it('页面还会用的常量都在', () => {
    for (const name of KEPT) {
      expect(copy[name], `${name} 丢了`).toBeTruthy()
    }
  })

  it('退场的常量没有再挂回来', () => {
    for (const name of RETIRED) {
      expect(copy[name], `${name} 还在文案表里`).toBeUndefined()
    }
  })

  it('标题与两类归属是中文，不把英文枚举漏到界面上', () => {
    expect(copy.PREP_PLAN_TITLE).toBe('备货计划')
    expect(copy.MADE_LABEL).toBe('半成品')
    expect(copy.BOUGHT_LABEL).toBe('原料')
  })
})


describe('时间跨度文案', () => {
  it('四个选项按 24 / 48 / 72 / 168 排，长跨度用「7 天」而不是「168 小时」', () => {
    expect(copy.HORIZON_OPTIONS.map((row) => [row.hours, row.label])).toEqual([
      [24, '24 小时'],
      [48, '48 小时'],
      [72, '72 小时'],
      [168, '7 天'],
    ])
  })

  it('标题 24 小时说「今天」，其余直说跨度', () => {
    expect(copy.horizonTitle(24)).toBe('今天')
    expect(copy.horizonTitle(48)).toBe('未来 48 小时')
    expect(copy.horizonTitle(168)).toBe('未来 7 天')
  })

  it('页脚口径跟着跨度走，24 小时那句与老文案一字不差', () => {
    expect(copy.basisHint(24)).toBe(copy.BASIS_HINT)
    expect(copy.BASIS_HINT).toContain('未来 24 小时要卖多少')
    expect(copy.basisHint(168)).toContain('未来 7 天要卖多少')
    // 算法口径不变：分位 × 整店水位、再减实物库存
    expect(copy.basisHint(168)).toContain('近 8 周同星期几的分位量 × 整店水位')
  })
})
