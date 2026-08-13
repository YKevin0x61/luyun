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

describe('ScreenSettingsManager orderGapMinutes', () => {
  beforeEach(() => {
    memory.clear()
  })

  it('defaults to 0 when unset', () => {
    expect(ScreenSettingsManager.getOrderGapMinutes()).toBe(0)
    expect(ScreenSettingsManager.getSettings().orderGapMinutes).toBe(0)
  })

  it('persists a valid 下单间隔 across get after save', () => {
    expect(ScreenSettingsManager.setOrderGapMinutes(10)).toBe(true)
    expect(ScreenSettingsManager.getOrderGapMinutes()).toBe(10)
    expect(ScreenSettingsManager.getSettings().orderGapMinutes).toBe(10)
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
    ScreenSettingsManager.setOrderGapMinutes(input)
    expect(ScreenSettingsManager.getOrderGapMinutes()).toBe(expected)
  })

  it('preserves other screen settings when saving 下单间隔', () => {
    ScreenSettingsManager.setWatchedStations(['changfen'])
    ScreenSettingsManager.setDensity('compact')
    ScreenSettingsManager.setDishCardQuantityCap(5)
    ScreenSettingsManager.setOrderGapMinutes(15)

    const settings = ScreenSettingsManager.getSettings()
    expect(settings.watchedStations).toEqual(['changfen'])
    expect(settings.density).toBe('compact')
    expect(settings.dishCardQuantityCap).toBe(5)
    expect(settings.orderGapMinutes).toBe(15)
  })

  it('preserves 下单间隔 when saving 菜卡份数上限', () => {
    ScreenSettingsManager.setOrderGapMinutes(15)
    ScreenSettingsManager.setDishCardQuantityCap(5)
    expect(ScreenSettingsManager.getOrderGapMinutes()).toBe(15)
    expect(ScreenSettingsManager.getDishCardQuantityCap()).toBe(5)
  })
})
