import { beforeEach, describe, expect, it, vi } from 'vitest'

const memory = new Map()

vi.stubGlobal('uni', {
  getStorageSync(key) {
    return memory.has(key) ? memory.get(key) : ''
  },
  setStorageSync(key, value) {
    memory.set(key, value)
  },
  removeStorageSync(key) {
    memory.delete(key)
  }
})

const { ScreenSettingsManager } = await import('../storage.js')

describe('ScreenSettingsManager 提示音款 and 本屏告警音量', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('defaults all four 提示音款 to 清脆 and 本屏告警音量 to 0.6', () => {
    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe('清脆')
    expect(alert.overtimeTone).toBe('清脆')
    expect(alert.cancelTone).toBe('清脆')
    expect(alert.disconnectTone).toBe('清脆')
    expect(alert.alertVolume).toBe(0.6)
  })

  it('persists each 提示音款 and 本屏告警音量 across get after save', () => {
    expect(
      ScreenSettingsManager.setAlertParams({
        newOrderTone: '穿透',
        overtimeTone: '圆润',
        cancelTone: '低沉',
        disconnectTone: '厚实',
        alertVolume: 0.8
      })
    ).toBe(true)

    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe('穿透')
    expect(alert.overtimeTone).toBe('圆润')
    expect(alert.cancelTone).toBe('低沉')
    expect(alert.disconnectTone).toBe('厚实')
    expect(alert.alertVolume).toBe(0.8)
  })

  it.each([
    [undefined, '清脆'],
    [null, '清脆'],
    ['', '清脆'],
    ['crisp', '清脆'],
    ['清脆 ', '清脆'],
    ['sixth', '清脆']
  ])('normalizes stored 款 %j to 清脆', (input, expected) => {
    ScreenSettingsManager.setAlertParams({
      newOrderTone: input,
      overtimeTone: input,
      cancelTone: input,
      disconnectTone: input
    })
    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe(expected)
    expect(alert.overtimeTone).toBe(expected)
    expect(alert.cancelTone).toBe(expected)
    expect(alert.disconnectTone).toBe(expected)
  })

  it.each([
    [undefined, 0.6],
    [null, 0.6],
    ['', 0.6],
    [Number.NaN, 0.6],
    [0, 0.2],
    [0.1, 0.2],
    [-0.3, 0.2],
    [0.2, 0.2],
    [0.6, 0.6],
    [1, 1],
    [1.4, 1],
    ['0.8', 0.8]
  ])('normalizes stored volume %j to %j', (input, expected) => {
    ScreenSettingsManager.setAlertParams({ alertVolume: input })
    expect(ScreenSettingsManager.getAlertParams().alertVolume).toBe(expected)
  })

  it('resetToDefault returns 清脆 and volume 0.6', () => {
    ScreenSettingsManager.setAlertParams({
      newOrderTone: '穿透',
      overtimeTone: '圆润',
      cancelTone: '低沉',
      disconnectTone: '厚实',
      alertVolume: 1
    })
    expect(ScreenSettingsManager.resetToDefault()).toBe(true)

    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe('清脆')
    expect(alert.overtimeTone).toBe('清脆')
    expect(alert.cancelTone).toBe('清脆')
    expect(alert.disconnectTone).toBe('清脆')
    expect(alert.alertVolume).toBe(0.6)
  })

  it('saving 提示音款 does not clobber steam or wait thresholds', () => {
    ScreenSettingsManager.setAlertParams({
      warnMin: 9,
      urgentMin: 11,
      steamWarnMin: 8,
      steamUrgentMin: 12
    })
    ScreenSettingsManager.setAlertParams({
      newOrderTone: '穿透',
      alertVolume: 0.8
    })

    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe('穿透')
    expect(alert.alertVolume).toBe(0.8)
    expect(alert.warnMin).toBe(9)
    expect(alert.urgentMin).toBe(11)
    expect(alert.steamWarnMin).toBe(8)
    expect(alert.steamUrgentMin).toBe(12)
  })

  it('saving steam thresholds does not clobber 提示音款 or 本屏告警音量', () => {
    ScreenSettingsManager.setAlertParams({
      newOrderTone: '厚实',
      overtimeTone: '低沉',
      cancelTone: '圆润',
      disconnectTone: '穿透',
      alertVolume: 0.9
    })
    ScreenSettingsManager.setAlertParams({
      steamWarnMin: 7,
      steamUrgentMin: 13
    })

    const alert = ScreenSettingsManager.getAlertParams()
    expect(alert.newOrderTone).toBe('厚实')
    expect(alert.overtimeTone).toBe('低沉')
    expect(alert.cancelTone).toBe('圆润')
    expect(alert.disconnectTone).toBe('穿透')
    expect(alert.alertVolume).toBe(0.9)
    expect(alert.steamWarnMin).toBe(7)
    expect(alert.steamUrgentMin).toBe(13)
  })
})
