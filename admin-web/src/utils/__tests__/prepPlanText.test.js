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
