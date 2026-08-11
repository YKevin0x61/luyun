import { describe, expect, it } from 'vitest'
import { getTimeThresholdsMs } from '../timeThresholds.js'

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
