import { describe, expect, it } from 'vitest'
import { addDays, dayDiff, formatDate, parseLocalDate } from '../dateRange.js'

describe('formatDate / parseLocalDate', () => {
  it('往返转换后日期保持一致', () => {
    const date = parseLocalDate('2026-07-18')
    expect(formatDate(date)).toBe('2026-07-18')
  })
})

describe('dayDiff', () => {
  it('计算包含首尾两端的自然日天数', () => {
    expect(dayDiff('2026-07-01', '2026-07-07')).toBe(7)
  })
})

describe('addDays', () => {
  it('跨月时正确进位到下一个月', () => {
    const result = addDays(parseLocalDate('2026-07-30'), 3)
    expect(formatDate(result)).toBe('2026-08-02')
  })
})
