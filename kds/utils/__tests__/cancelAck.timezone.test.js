import { afterAll, describe, expect, it } from 'vitest'

/**
 * 退菜已确认按营业日分桶，营业日是中国日历日——不能跟设备本地日期走，
 * 否则设备时区设错时，确认记录会在营业中途（而不是营业日交界）清空。
 */

const AMBIENT_TZ = process.env.TZ
process.env.TZ = 'UTC'

afterAll(() => {
  if (AMBIENT_TZ === undefined) delete process.env.TZ
  else process.env.TZ = AMBIENT_TZ
})

const { businessDateKey } = await import('../cancelAck.js')

describe('cancelAck 营业日键', () => {
  it('按中国日历日分桶，不按设备日期', () => {
    // 中国时间 2026-09-23 02:00：设备（UTC）日期还是 09-22
    expect(new Date('2026-09-22T18:00:00.000Z').getDate()).toBe(22)
    expect(businessDateKey(new Date('2026-09-22T18:00:00.000Z'))).toBe('2026-09-23')

    // 中国日交界两侧
    expect(businessDateKey(Date.parse('2026-09-22T15:59:59.999Z'))).toBe('2026-09-22')
    expect(businessDateKey(Date.parse('2026-09-22T16:00:00.000Z'))).toBe('2026-09-23')
  })
})
