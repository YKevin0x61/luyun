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
            { item_name: '酥皮', unit: '份', recommended_qty: 20 },
            { item_name: '蛋挞液', unit: '份', recommended_qty: 0 },
          ],
        },
        {
          stationLabel: '肠粉档',
          items: [
            { item_name: '虾饺馅', unit: '份', recommended_qty: 12 },
          ],
        },
      ],
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

  it('行上只有名字与建议量 —— 可用 / 临期 / 已做那三个数不再出现', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            // 后端已经不再返回 available_* / produced_qty；这里连传都不传，
            // 文本里若还冒出这几个词就是前端自己在编库存。
            { item_name: '酥皮', unit: '份', recommended_qty: 20 },
            { item_name: '蛋挞液', unit: '份', recommended_qty: 0 },
          ],
        },
      ],
      missingRules: [],
    })
    const pastryLine = text.split('\n').find((line) => line.includes('酥皮'))
    expect(pastryLine).toBe('酥皮  建议 20份')
    expect(text).not.toContain('可用')
    expect(text).not.toContain('临期')
    expect(text).not.toContain('已做')
    expect(text).not.toContain('剩余')
    expect(text).not.toContain('undefined')
  })

  it('页末有缺规则', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [{ item_name: '酥皮', unit: '份', recommended_qty: 20 }],
        },
      ],
      missingRules: [{ dish_name: '示例菜', reason: '没有配置半成品换算规则，无法换算为备货品' }],
    })
    expect(text).toContain('【缺规则】')
    expect(text).toContain('示例菜')
    expect(text).toContain('没有配置半成品换算规则，无法换算为备货品')
    // 临期那一段随临期批次一起删了（响应顶层也没有 `expiring`）。
    expect(text).not.toContain('【临期】')
  })

  it('文案不含英文枚举', () => {
    const text = buildPrepPlanText({
      ...WINDOW,
      stations: [
        {
          stationLabel: '西饼档',
          items: [
            { item_name: '酥皮', unit: '份', recommended_qty: 20, confidence: 'low' },
          ],
        },
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
      missingRules: [],
    })
    expect(text).toContain('暂无建议')
  })
})
