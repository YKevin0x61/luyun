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

describe('ScreenSettingsManager watched station lock', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('defaults to unlocked', () => {
    expect(ScreenSettingsManager.getWatchedStations()).toEqual([])
    expect(ScreenSettingsManager.getLockedStationId()).toBeNull()
    expect(ScreenSettingsManager.isStationWatched('changfen')).toBe(false)
  })

  it('locks exactly one station', () => {
    expect(ScreenSettingsManager.setWatchedStations(['changfen'])).toBe(true)
    expect(ScreenSettingsManager.getWatchedStations()).toEqual(['changfen'])
    expect(ScreenSettingsManager.getLockedStationId()).toBe('changfen')
    expect(ScreenSettingsManager.isStationWatched('changfen')).toBe(true)
    expect(ScreenSettingsManager.isStationWatched('xibing')).toBe(false)
  })

  it('treats legacy multi-select as unlocked', () => {
    expect(ScreenSettingsManager.setWatchedStations(['changfen', 'xibing'])).toBe(true)
    expect(ScreenSettingsManager.getWatchedStations()).toEqual([])
    expect(ScreenSettingsManager.getLockedStationId()).toBeNull()
  })

  it('treats raw multi-select on disk as unlocked without rewriting until save', () => {
    memory.set(
      'kds_screen_settings',
      JSON.stringify({
        watchedStations: ['changfen', 'shulong'],
        density: 'standard'
      })
    )
    expect(ScreenSettingsManager.getWatchedStations()).toEqual([])
    expect(ScreenSettingsManager.getLockedStationId()).toBeNull()
  })
})
