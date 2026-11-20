import { describe, expect, it } from 'vitest'

import { formatHygieneShortStamp, formatHygieneStamp } from '../hygieneTime.js'

describe('formatHygieneStamp', () => {
  it('renders ISO with a T separator', () => {
    expect(formatHygieneStamp('2026-09-13T15:00:00+08:00')).toBe('2026-09-13 15:00')
  })

  it('renders space-separated timestamps instead of echoing the raw string', () => {
    // 历史数据 / 手写 SQL 补的行是空格分隔的。原来的四处实现只认 T，会把整串
    // 原样吐到水印、看板和整改单上。
    expect(formatHygieneStamp('2026-09-13 15:00:00')).toBe('2026-09-13 15:00')
    expect(formatHygieneStamp('2026-09-13 15:00:00+08:00')).toBe('2026-09-13 15:00')
  })

  it('truncates seconds and fractional seconds', () => {
    expect(formatHygieneStamp('2026-09-13T15:00:59.123456+08:00')).toBe('2026-09-13 15:00')
  })

  it('echoes unparseable input rather than inventing a date', () => {
    expect(formatHygieneStamp('')).toBe('')
    expect(formatHygieneStamp(null)).toBe('')
    expect(formatHygieneStamp(undefined)).toBe('')
    expect(formatHygieneStamp('不是时间')).toBe('不是时间')
    expect(formatHygieneStamp('2026-09-13')).toBe('2026-09-13')
  })

  it('does not confuse a date-only value with a timestamp', () => {
    // week_start 之类只到日；不能补出 00:00 这种没发生过的时刻。
    expect(formatHygieneStamp('2026-09-14')).toBe('2026-09-14')
  })
})

describe('formatHygieneShortStamp', () => {
  it('drops the year', () => {
    expect(formatHygieneShortStamp('2026-09-13T15:00:00+08:00')).toBe('09-13 15:00')
    expect(formatHygieneShortStamp('2026-09-13 15:00:00')).toBe('09-13 15:00')
  })

  it('returns an empty string when it cannot parse', () => {
    expect(formatHygieneShortStamp('')).toBe('')
    expect(formatHygieneShortStamp('nonsense')).toBe('')
  })
})
