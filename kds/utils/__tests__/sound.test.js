import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  isSoundUnlocked,
  playCancelAlert,
  playDisconnectAlert,
  playNewOrderDing,
  playOvertimeAlarm
} from '../sound.js'

describe('sound engine', () => {
  afterEach(() => {
    delete globalThis.plus
    delete globalThis.uni
    delete globalThis.window
  })

  it('play* is a no-op on H5 when locked', () => {
    const AudioContext = vi.fn()
    globalThis.window = { AudioContext }
    expect(isSoundUnlocked()).toBe(false)

    playNewOrderDing(2)
    playOvertimeAlarm()
    playCancelAlert()
    playDisconnectAlert()

    expect(isSoundUnlocked()).toBe(false)
    expect(AudioContext).not.toHaveBeenCalled()
  })

  it('play* does not call system beep or vibrate when APP device APIs are present', () => {
    const beep = vi.fn()
    const vibrateShort = vi.fn()
    const vibrateLong = vi.fn()
    globalThis.plus = { device: { beep } }
    globalThis.uni = { vibrateShort, vibrateLong }

    playNewOrderDing(2)
    playOvertimeAlarm()
    playCancelAlert()
    playDisconnectAlert()

    expect(beep).not.toHaveBeenCalled()
    expect(vibrateShort).not.toHaveBeenCalled()
    expect(vibrateLong).not.toHaveBeenCalled()
  })
})
