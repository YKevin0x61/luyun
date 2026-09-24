import { afterAll, describe, expect, it } from 'vitest'

/**
 * TEST-14：时刻文本必须按东八区呈现，不随设备时区漂移。
 *
 * KDS 跑在门店设备上，系统时区不可信（Android 盒子出厂 UTC、没配 NTP、运维设错）。
 * `formatTime` 旧实现取 `getHours()` 这类设备本地字段，在 UTC 设备上会少 8 小时，
 * 00:00–08:00 还会整段显示成昨天。这里把进程时区固定成 UTC 作为反证条件：
 * 输出必须仍然是东八区时刻（锚点与 utils/businessDay.js、后端 CHINA_TZ 同一套）。
 */
const AMBIENT_TZ = process.env.TZ
process.env.TZ = 'UTC'

// 东八区 2026-09-24 02:00:00 == UTC 2026-09-23 18:00:00
const CHINA_0200_MS = Date.parse('2026-09-23T18:00:00.000Z')
// 东八区 2026-09-24 07:30:00 == UTC 2026-09-23 23:30:00（跨 UTC 零点，东八区已是 24 日）
const CHINA_0730_MS = Date.parse('2026-09-23T23:30:00.000Z')

afterAll(() => {
  if (AMBIENT_TZ === undefined) {
    delete process.env.TZ
  } else {
    process.env.TZ = AMBIENT_TZ
  }
})

describe('TimeCalculator.formatTime 的东八区锚点', () => {
  it('renders East-8 wall clock even when the device clock is UTC', async () => {
    const { TimeCalculator } = await import('../timeCalculator.js')

    // 反证：设备本地（UTC）字段是 18 点，和下面断言的 02:00 不是一回事
    expect(new Date(CHINA_0200_MS).getHours()).toBe(18)

    expect(TimeCalculator.formatTime(CHINA_0200_MS, 'HH:mm')).toBe('02:00')
    expect(TimeCalculator.formatTime(CHINA_0200_MS, 'HH:mm:ss')).toBe('02:00:00')
    expect(TimeCalculator.formatTime(CHINA_0200_MS)).toBe('02:00:00')
    expect(TimeCalculator.formatTime(CHINA_0200_MS, 'MM-DD HH:mm')).toBe('09-24 02:00')
  })

  it('does not drift to yesterday around the UTC midnight boundary', async () => {
    const { TimeCalculator } = await import('../timeCalculator.js')

    // 设备本地日期还是 9-23，东八区已经是 9-24
    expect(new Date(CHINA_0730_MS).getDate()).toBe(23)

    expect(TimeCalculator.formatTime(CHINA_0730_MS, 'YYYY-MM-DD')).toBe('2026-09-24')
    expect(TimeCalculator.formatTime(CHINA_0730_MS, 'MM-DD HH:mm')).toBe('09-24 07:30')
    expect(TimeCalculator.formatTime(CHINA_0730_MS, 'YYYY-MM-DD HH:mm:ss')).toBe('2026-09-24 07:30:00')
  })

  it('accepts the ISO strings and Date objects callers actually pass in', async () => {
    const { TimeCalculator } = await import('../timeCalculator.js')

    // 小票打印链路传的是带 +08:00 偏移的 ISO 串
    expect(TimeCalculator.formatTime('2026-09-24T10:05:00+08:00', 'HH:mm')).toBe('10:05')
    expect(TimeCalculator.formatTime('2026-09-23T18:00:00.000Z', 'HH:mm')).toBe('02:00')
    expect(TimeCalculator.formatTime(new Date(CHINA_0200_MS), 'HH:mm')).toBe('02:00')
  })
})
