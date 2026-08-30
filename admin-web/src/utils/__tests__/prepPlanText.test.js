import { describe, expect, it } from 'vitest'
import { buildPrepPlanText } from '../prepPlanText.js'

const WINDOW = {
  windowStart: '2026-08-30T16:00:00+08:00',
  windowEnd: '2026-08-31T16:00:00+08:00',
}

describe('buildPrepPlanText', () => {
  it('按档口分组，建议行和不用做是两行，不是建议 0', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            {
              item_name: '酥皮',
              unit: '份',
              recommended_qty: 20,
              available_fresh_qty: 4,
              available_near_expiry_qty: 2,
              produced_qty: 0,
            },
            {
              item_name: '蛋挞液',
              unit: '份',
              recommended_qty: 0,
              available_fresh_qty: 10,
              available_near_expiry_qty: 0,
              produced_qty: 8,
            },
          ],
        },
        {
          stationLabel: '肠粉档',
          items: [
            {
              item_name: '虾饺馅',
              unit: '份',
              recommended_qty: 12,
              available_fresh_qty: 1,
              available_near_expiry_qty: 0,
              produced_qty: 3,
            },
          ],
        },
      ],
      expiring: [],
      missingRules: [],
    })
    expect(text).toContain('【备货计划】')
    expect(text).toContain('【西饼档】')
    expect(text).toContain('【肠粉档】')
    const pastryLine = text.split('\n').find((line) => line.includes('酥皮'))
    const custardLine = text.split('\n').find((line) => line.includes('蛋挞液'))
    expect(pastryLine).toContain('建议 20份')
    expect(custardLine).toContain('不用做')
    expect(custardLine).not.toContain('建议')
    expect(pastryLine).not.toBe(custardLine)
    expect(text).not.toContain('建议 0')
  })

  it('行上写出可用、临期、已做的数字', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            {
              item_name: '酥皮',
              unit: '份',
              recommended_qty: 20,
              available_fresh_qty: 4,
              available_near_expiry_qty: 2,
              produced_qty: 0,
            },
            {
              item_name: '蛋挞液',
              unit: '份',
              recommended_qty: 0,
              available_fresh_qty: 10,
              available_near_expiry_qty: 0,
              produced_qty: 8,
            },
          ],
        },
      ],
      expiring: [],
      missingRules: [],
    })
    expect(text).toContain('可用 4份')
    expect(text).toContain('临期 2份')
    expect(text).toContain('已做 0份')
    expect(text).toContain('可用 10份')
    expect(text).toContain('临期 0份')
    expect(text).toContain('已做 8份')
  })

  it('页末有临期和缺规则', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            {
              item_name: '酥皮',
              unit: '份',
              recommended_qty: 20,
              available_fresh_qty: 4,
              available_near_expiry_qty: 2,
              produced_qty: 0,
            },
          ],
        },
      ],
      expiring: [
        { item_name: '虾饺馅', unit: '份', remaining_qty: 30, expires_at: '2026-08-30T18:00:00+08:00' },
      ],
      missingRules: [{ dish_name: '示例菜', reason: '没有配置半成品换算规则，无法换算为备货品' }],
    })
    expect(text).toContain('【临期】')
    expect(text).toContain('虾饺馅')
    expect(text).toContain('剩余 30份')
    expect(text).toContain('【缺规则】')
    expect(text).toContain('示例菜')
    expect(text).toContain('没有配置半成品换算规则，无法换算为备货品')
  })

  it('文案不含英文枚举', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            {
              item_name: '酥皮',
              unit: '份',
              recommended_qty: 20,
              available_fresh_qty: 4,
              available_near_expiry_qty: 2,
              produced_qty: 0,
              risk_level: 'high',
              confidence: 'low',
            },
          ],
        },
      ],
      expiring: [
        { item_name: '虾饺馅', unit: '份', remaining_qty: 30, expires_at: '2026-08-30T18:00:00+08:00' },
      ],
      missingRules: [{ dish_name: '示例菜', reason: '没有配置半成品换算规则，无法换算为备货品' }],
    })
    expect(text).not.toMatch(/\bhigh\b/)
    expect(text).not.toMatch(/\bmedium\b/)
    expect(text).not.toMatch(/\bwaste_risk\b/)
    expect(text).not.toMatch(/\bexpiry_risk\b/)
    expect(text).not.toMatch(/\brun_id\b/)
    expect(text).not.toMatch(/\bnormal\b/)
  })

  it('空清单写暂无建议', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [],
      expiring: [],
      missingRules: [],
    })
    expect(text).toContain('暂无建议')
  })
})
