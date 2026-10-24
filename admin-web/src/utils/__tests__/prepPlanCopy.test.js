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
    expect(copy.MADE_LABEL).toBe('厨房做的')
    expect(copy.BOUGHT_LABEL).toBe('要买的')
  })
})
