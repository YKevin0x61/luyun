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

describe('ScreenSettingsManager dishCardQuantityCap', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('defaults to 0 when unset', () => {
    expect(ScreenSettingsManager.getDishCardQuantityCap()).toBe(0)
    expect(ScreenSettingsManager.getSettings().dishCardQuantityCap).toBe(0)
  })

  it('persists a valid cap across get after save', () => {
    expect(ScreenSettingsManager.setDishCardQuantityCap(10)).toBe(true)
    expect(ScreenSettingsManager.getDishCardQuantityCap()).toBe(10)
    expect(ScreenSettingsManager.getSettings().dishCardQuantityCap).toBe(10)
  })

  it.each([
    [0, 0],
    [1, 1],
    [99, 99],
    [100, 99],
    [-3, 0],
    [12.9, 12],
    ['8', 8],
    ['', 0],
    [null, 0],
    [undefined, 0],
    [Number.NaN, 0],
    [Number.POSITIVE_INFINITY, 0]
  ])('normalizes %j to %j', (input, expected) => {
    ScreenSettingsManager.setDishCardQuantityCap(input)
    expect(ScreenSettingsManager.getDishCardQuantityCap()).toBe(expected)
  })

  it('preserves other screen settings when saving the cap', () => {
    ScreenSettingsManager.setWatchedStations(['changfen'])
    ScreenSettingsManager.setDensity('compact')
    ScreenSettingsManager.setDishCardQuantityCap(5)

    const settings = ScreenSettingsManager.getSettings()
    expect(settings.watchedStations).toEqual(['changfen'])
    expect(settings.density).toBe('compact')
    expect(settings.dishCardQuantityCap).toBe(5)
  })
})
