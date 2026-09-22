import { afterAll, describe, expect, it } from 'vitest'

/**
 * 营业日日界是纯函数：只吃一个时间戳，不读设备时区。
 * 这里的期望值全部是手算字面量（中国时间 2026-09-23 00:00 = 2026-09-22T16:00:00Z），
 * 不复用被测代码的算法。
 */

const AMBIENT_TZ = process.env.TZ

afterAll(() => {
  if (AMBIENT_TZ === undefined) delete process.env.TZ
  else process.env.TZ = AMBIENT_TZ
})

// 中国时间 2026-09-23 02:00，设备时区设成 UTC 时设备本地日期还是 09-22
const CHINA_0230 = Date.parse('2026-09-22T18:00:00.000Z')
const CHINA_DAY_START = '2026-09-22T16:00:00.000Z'
const CHINA_DAY_END = '2026-09-23T15:59:59.999Z'

const {
  CST_OFFSET_MS,
  chinaDateKey,
  chinaDayRange,
  isChinaToday,
  startOfChinaDay
} = await import('../businessDay.js')

describe('businessDay', () => {
  it('anchors the day window to CST regardless of device timezone', () => {
    const deviceDates = []
    for (const tz of ['Asia/Shanghai', 'UTC', 'America/New_York', 'Pacific/Kiritimati']) {
      process.env.TZ = tz
      deviceDates.push(new Date(CHINA_0230).getDate())
      expect(chinaDayRange(CHINA_0230)).toEqual({ start: CHINA_DAY_START, end: CHINA_DAY_END })
    }
    // 正对照：上面这个时刻在不同设备时区里确实是不同的"设备日期"，
    // 说明 TZ 真的生效了，而窗口没有跟着变。
    expect([...new Set(deviceDates)].length).toBeGreaterThan(1)
    expect(deviceDates[1]).toBe(22) // UTC 设备：09-22
    expect(deviceDates[0]).toBe(23) // 东八区设备：09-23
  })

  it('accepts a Date or a millisecond timestamp', () => {
    expect(startOfChinaDay(new Date(CHINA_0230))).toBe(Date.parse(CHINA_DAY_START))
    expect(startOfChinaDay(CHINA_0230)).toBe(Date.parse(CHINA_DAY_START))
    expect(chinaDayRange(new Date(CHINA_0230))).toEqual({
      start: CHINA_DAY_START,
      end: CHINA_DAY_END
    })
  })

  it('ends the window at 23:59:59.999 CST, one millisecond before the next CST day', () => {
    const range = chinaDayRange(CHINA_0230)
    expect(Date.parse(range.end) - Date.parse(range.start)).toBe(24 * 60 * 60 * 1000 - 1)
    expect(startOfChinaDay(Date.parse(CHINA_DAY_END))).toBe(Date.parse(CHINA_DAY_START))
    expect(startOfChinaDay(Date.parse(CHINA_DAY_END) + 1)).toBe(Date.parse(CHINA_DAY_END) + 1)
  })

  it('derives the CST calendar date', () => {
    process.env.TZ = 'UTC'
    expect(chinaDateKey(CHINA_0230)).toBe('2026-09-23')
    expect(chinaDateKey(new Date(CHINA_0230))).toBe('2026-09-23')
    // CST 日界两侧
    expect(chinaDateKey(Date.parse('2026-09-22T15:59:59.999Z'))).toBe('2026-09-22')
    expect(chinaDateKey(Date.parse(CHINA_DAY_START))).toBe('2026-09-23')
  })

  it('isChinaToday judges by the CST date, not the device date', () => {
    process.env.TZ = 'UTC'
    expect(isChinaToday('2026-09-23T01:30:00+08:00', CHINA_0230)).toBe(true)
    // 设备（UTC）日期是 09-22，但这条单的中国日期是 09-22 = 中国时间的昨天
    expect(new Date('2026-09-22T12:00:00+08:00').getDate()).toBe(22)
    expect(isChinaToday('2026-09-22T12:00:00+08:00', CHINA_0230)).toBe(false)
    // CST 日界两侧
    expect(isChinaToday('2026-09-22T23:59:59.999+08:00', CHINA_0230)).toBe(false)
    expect(isChinaToday('2026-09-23T00:00:00.000+08:00', CHINA_0230)).toBe(true)
    expect(isChinaToday('2026-09-23T23:59:59.999+08:00', CHINA_0230)).toBe(true)
    expect(isChinaToday('2026-09-24T00:00:00.000+08:00', CHINA_0230)).toBe(false)
    expect(isChinaToday('not-a-time', CHINA_0230)).toBe(false)
  })

  it('exposes the fixed CST offset used for the anchor', () => {
    expect(CST_OFFSET_MS).toBe(8 * 60 * 60 * 1000)
    expect(CST_OFFSET_MS).toBe(28800000)
  })
})
