import { describe, expect, it } from 'vitest'
import { getSteamTimeThresholdsMs, getTimeThresholdsMs } from '../timeThresholds.js'

describe('getTimeThresholdsMs', () => {
  it('uses provided alert params', () => {
    expect(getTimeThresholdsMs({ warnMin: 10, urgentMin: 25 })).toEqual({
      warning: 10 * 60 * 1000,
      urgent: 25 * 60 * 1000
    })
  })

  it('falls back to 15/20 when params missing fields', () => {
    expect(getTimeThresholdsMs({})).toEqual({
      warning: 15 * 60 * 1000,
      urgent: 20 * 60 * 1000
    })
  })
})

describe('getSteamTimeThresholdsMs', () => {
  it('reads steamWarnMin / steamUrgentMin independently of wait mins', () => {
    expect(
      getSteamTimeThresholdsMs({
        warnMin: 1,
        urgentMin: 2,
        steamWarnMin: 8,
        steamUrgentMin: 12
      })
    ).toEqual({
      warning: 8 * 60 * 1000,
      urgent: 12 * 60 * 1000
    })
  })

  it('does not change when only wait mins change', () => {
    const steam = { steamWarnMin: 8, steamUrgentMin: 12 }
    expect(getSteamTimeThresholdsMs({ ...steam, warnMin: 1, urgentMin: 2 })).toEqual({
      warning: 8 * 60 * 1000,
      urgent: 12 * 60 * 1000
    })
    expect(getSteamTimeThresholdsMs({ ...steam, warnMin: 40, urgentMin: 50 })).toEqual({
      warning: 8 * 60 * 1000,
      urgent: 12 * 60 * 1000
    })
    expect(getTimeThresholdsMs({ ...steam, warnMin: 40, urgentMin: 50 })).toEqual({
      warning: 40 * 60 * 1000,
      urgent: 50 * 60 * 1000
    })
  })

  it('falls back to 15/20 when steam fields are missing', () => {
    expect(getSteamTimeThresholdsMs({ warnMin: 1, urgentMin: 2 })).toEqual({
      warning: 15 * 60 * 1000,
      urgent: 20 * 60 * 1000
    })
    expect(getSteamTimeThresholdsMs({})).toEqual({
      warning: 15 * 60 * 1000,
      urgent: 20 * 60 * 1000
    })
  })
})
