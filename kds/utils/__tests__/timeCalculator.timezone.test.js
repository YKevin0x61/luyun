import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 「今天」是营业日（中国时间）概念，不是设备本地日期。
 * 设备时区用 process.env.TZ 伪造（Node 会在赋值时重读时区，见本文件的正对照断言），
 * 时刻用 vi.setSystemTime 固定在中国时间凌晨 02:00。
 */

const AMBIENT_TZ = process.env.TZ
process.env.TZ = 'UTC'

afterAll(() => {
  if (AMBIENT_TZ === undefined) delete process.env.TZ
  else process.env.TZ = AMBIENT_TZ
})

// 中国时间 2026-09-23 02:00；设备时区 UTC 时设备日期还是 09-22
const CHINA_0230 = Date.parse('2026-09-22T18:00:00.000Z')
const CHINA_DAY_START = '2026-09-22T16:00:00.000Z'
const CHINA_DAY_END = '2026-09-23T15:59:59.999Z'

const { TimeCalculator } = await import('../timeCalculator.js')
const { chinaDateKey } = await import('../businessDay.js')

describe('TimeCalculator 营业日口径', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: CHINA_0230, toFake: ['Date'] })
  })

  afterEach(() => {
    vi.useRealTimers()
    process.env.TZ = 'UTC'
  })

  it('isToday 按中国日期判定：设备时区 UTC 时，中国昨天的单不算今天', () => {
    expect(chinaDateKey()).toBe('2026-09-23')
    // 正对照：设备（UTC）本地日期还停在 09-22
    expect(new Date().getDate()).toBe(22)

    expect(TimeCalculator.isToday('2026-09-23T01:30:00+08:00')).toBe(true)
    // 设备日期 = 09-22 = "今天"，但中国日期 = 09-22 = 中国时间的昨天
    expect(TimeCalculator.isToday('2026-09-22T12:00:00+08:00')).toBe(false)
    expect(TimeCalculator.isToday('2026-09-22T23:59:59.999+08:00')).toBe(false)
    expect(TimeCalculator.isToday('2026-09-23T00:00:00.000+08:00')).toBe(true)
  })

  it('getTodayRange 给出中国日 00:00–23:59:59.999 的 UTC ISO 窗口', () => {
    expect(TimeCalculator.getTodayRange()).toEqual({
      start: CHINA_DAY_START,
      end: CHINA_DAY_END
    })
  })

  it('设备时区本身就是东八区时结果不变（回归）', () => {
    process.env.TZ = 'Asia/Shanghai'
    expect(new Date().getDate()).toBe(23)

    expect(TimeCalculator.isToday('2026-09-23T01:30:00+08:00')).toBe(true)
    expect(TimeCalculator.isToday('2026-09-22T12:00:00+08:00')).toBe(false)
    expect(TimeCalculator.getTodayRange()).toEqual({
      start: CHINA_DAY_START,
      end: CHINA_DAY_END
    })
  })
})
