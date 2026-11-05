import { describe, expect, it } from 'vitest'
import { buildPrepPlanText } from '../prepPlanText.js'

describe('复制清单', () => {
  it('按档口列出品名与数量', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-22T21:30:00+08:00',
      stations: [
        {
          stationLabel: '熟笼',
          items: [
            { item_name: '虾饺馅', final_qty: 80, unit: '份' },
            { item_name: '河粉', final_qty: 20, unit: '斤' },
          ],
        },
      ],
      closed: false,
    })

    expect(text).toContain('【备货计划】现在 → 21:30')
    expect(text).toContain('【熟笼】')
    expect(text).toContain('虾饺馅  80份')
    expect(text).toContain('河粉  20斤')
  })

  it('数量为 0 的写「不用做」', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-22T21:30:00+08:00',
      stations: [
        { stationLabel: '熟笼', items: [{ item_name: '虾饺馅', final_qty: 0, unit: '份' }] },
      ],
      closed: false,
    })
    expect(text).toContain('虾饺馅  不用做')
  })

  it('长跨度带上终点日期，否则群里分不清是哪天的 13:59', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-29T13:59:00+08:00',
      horizonHours: 168,
      stations: [{ stationLabel: '熟笼', items: [{ item_name: '虾饺馅', final_qty: 560, unit: '份' }] }],
      closed: false,
    })

    expect(text.split('\n')[0]).toBe('【备货计划】未来 7 天 · 现在 → 10/29 13:59')
    expect(text).toContain('虾饺馅  560份')
  })

  it('48 小时也是同样的写法', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-24T13:59:00+08:00',
      horizonHours: 48,
      stations: [],
      closed: false,
    })
    expect(text.split('\n')[0]).toBe('【备货计划】未来 48 小时 · 现在 → 10/24 13:59')
  })

  it('半成品写整份，原料写小数', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-22T21:30:00+08:00',
      stations: [
        {
          stationLabel: '熟笼',
          items: [
            { item_name: '烧卖', kind: 'made', final_qty: 58.7, unit: '份' },
            { item_name: '河粉', kind: 'bought', final_qty: 58.74, unit: '斤' },
          ],
        },
      ],
      closed: false,
    })

    expect(text).toContain('烧卖  59份')
    expect(text).toContain('河粉  58.7斤')
  })

  it('打烊后只给一句话', () => {
    const text = buildPrepPlanText({ windowEnd: '', stations: [], closed: true })
    expect(text).toContain('今天已打烊')
  })

  it('没有品时说今天不用做', () => {
    const text = buildPrepPlanText({
      windowEnd: '2026-10-22T21:30:00+08:00',
      stations: [],
      closed: false,
    })
    expect(text).toContain('今天不用做')
  })
})
