import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

const playDisconnectAlert = vi.fn()
const getAlertParams = vi.fn(() => ({
  disconnectTone: '清脆',
  alertVolume: 0.6
}))

vi.mock('../../utils/sound.js', () => ({
  playDisconnectAlert: (...args) => playDisconnectAlert(...args)
}))

vi.mock('../../utils/storage.js', () => ({
  ScreenSettingsManager: {
    getAlertParams: (...args) => getAlertParams(...args)
  }
}))

const { useDisconnectAlert } = await import('../useDisconnectAlert.js')

describe('useDisconnectAlert', () => {
  beforeEach(() => {
    playDisconnectAlert.mockReset()
    getAlertParams.mockReset()
    getAlertParams.mockReturnValue({
      disconnectTone: '清脆',
      alertVolume: 0.6
    })
  })

  afterEach(() => {
    delete globalThis.plus
    delete globalThis.uni
  })

  it('plays disconnect alert only on a connected → not-connected edge', async () => {
    const status = ref('connected')
    const { start, stop } = useDisconnectAlert(() => status.value)
    start()

    status.value = 'disconnected'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenCalledTimes(1)

    status.value = 'reconnecting'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenCalledTimes(1)

    status.value = 'connected'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenCalledTimes(1)

    stop()
  })

  it('does not call plus.device.beep or uni.vibrate for 断连告警', async () => {
    const beep = vi.fn()
    const vibrateShort = vi.fn()
    const vibrateLong = vi.fn()
    globalThis.plus = { device: { beep } }
    globalThis.uni = { vibrateShort, vibrateLong }

    const status = ref('connected')
    const { start, stop } = useDisconnectAlert(() => status.value)
    start()
    status.value = 'disconnected'
    await nextTick()

    expect(playDisconnectAlert).toHaveBeenCalledTimes(1)
    expect(beep).not.toHaveBeenCalled()
    expect(vibrateShort).not.toHaveBeenCalled()
    expect(vibrateLong).not.toHaveBeenCalled()
    stop()
  })

  it('skips 断连告警 when a higher kind claimed this moment', async () => {
    const status = ref('connected')
    const { start, stop } = useDisconnectAlert(() => status.value, {
      higherKindClaimed: () => true
    })
    start()

    status.value = 'disconnected'
    await nextTick()
    expect(playDisconnectAlert).not.toHaveBeenCalled()
    stop()
  })

  it('plays 断连告警 when higherKindClaimed is false', async () => {
    const status = ref('connected')
    const { start, stop } = useDisconnectAlert(() => status.value, {
      higherKindClaimed: () => false
    })
    start()

    status.value = 'disconnected'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenCalledTimes(1)
    stop()
  })

  it('plays the snapshotted 款 until reloadConfig', async () => {
    const status = ref('connected')
    const { start, stop, reloadConfig } = useDisconnectAlert(() => status.value)
    start()
    getAlertParams.mockReturnValue({
      disconnectTone: '穿透',
      alertVolume: 0.8
    })

    status.value = 'disconnected'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenCalledWith({ tone: '清脆', volume: 0.6 })

    reloadConfig()
    status.value = 'connected'
    await nextTick()
    status.value = 'disconnected'
    await nextTick()
    expect(playDisconnectAlert).toHaveBeenLastCalledWith({ tone: '穿透', volume: 0.8 })
    stop()
  })
})
