import { describe, expect, it } from 'vitest'
import { addDays, dayDiff, eachDayInRange, formatDate, parseLocalDate } from '../dateRange.js'

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

describe('eachDayInRange（月历上按天标待批角标用）', () => {
  it('两端都算，一天一天吐出来', () => {
    expect(eachDayInRange('2026-09-28', '2026-09-30')).toEqual([
      '2026-09-28',
      '2026-09-29',
      '2026-09-30',
    ])
  })

  it('只有一天时就是那一天（不填结束日的单日请假照这个走）', () => {
    expect(eachDayInRange('2026-09-28', '2026-09-28')).toEqual(['2026-09-28'])
  })

  it('跨月跨年靠 Date 自己进位', () => {
    expect(eachDayInRange('2026-12-30', '2027-01-02')).toEqual([
      '2026-12-30',
      '2026-12-31',
      '2027-01-01',
      '2027-01-02',
    ])
  })

  it('结束日缺省/早于开始日/脏数据都给空数组，调用方自己决定退路', () => {
    expect(eachDayInRange('2026-09-28', '')).toEqual([])
    expect(eachDayInRange('2026-09-28', '2026-09-27')).toEqual([])
    expect(eachDayInRange('', '2026-09-28')).toEqual([])
  })
})
