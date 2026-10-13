import { describe, expect, it } from 'vitest'
import {
  PRESET_AFTERNOON,
  PRESET_CUSTOM,
  PRESET_FUTURE_24H,
  PRESET_MORNING,
  itemKey,
  resolvePrepWindow,
} from '../prepPlanWindow.js'

describe('resolvePrepWindow', () => {
  it('未来 24 小时从现在起算', () => {
    const now = new Date(2026, 7, 30, 16, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_FUTURE_24H, now)
    expect(end.getTime() - start.getTime()).toBe(24 * 3600 * 1000)
    expect(start.getTime()).toBe(now.getTime())
  })

  it('下午四点点早班，起点不早于现在，终点明天 07:30', () => {
    const now = new Date(2026, 7, 30, 16, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_MORNING, now)
    expect(start.getTime()).toBe(now.getTime())
    expect(end.getHours()).toBe(7)
    expect(end.getMinutes()).toBe(30)
    expect(end.getDate()).toBe(31)
  })

  it('上午十点点早班，从现在起', () => {
    const now = new Date(2026, 7, 30, 10, 0, 0)
    const { start } = resolvePrepWindow(PRESET_MORNING, now)
    expect(start.getTime()).toBe(now.getTime())
  })

  it('六点点早班，等到今天 07:30', () => {
    const now = new Date(2026, 7, 30, 6, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_MORNING, now)
    expect(start.getHours()).toBe(7)
    expect(start.getMinutes()).toBe(30)
    expect(start.getDate()).toBe(30)
    expect(end.getDate()).toBe(31)
  })

  it('午后补货终点是明天 13:00 —— 撑到午市的三分之二', () => {
    const now = new Date(2026, 7, 30, 16, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_AFTERNOON, now)
    expect(start.getTime()).toBe(now.getTime())
    expect(end.getHours()).toBe(13)
    expect(end.getMinutes()).toBe(0)
    expect(end.getDate()).toBe(31)
    // 终点必须落在午市时段（11:00–14:00）**里面**：停在 11:00 或 12:00 都等于午市没盖住
    expect(end.getHours()).toBeGreaterThan(11)
    expect(end.getHours()).toBeLessThan(14)
  })

  it('上午十点点午后补货，从今天 14:00 起', () => {
    const now = new Date(2026, 7, 30, 10, 0, 0)
    const { start } = resolvePrepWindow(PRESET_AFTERNOON, now)
    expect(start.getHours()).toBe(14)
    expect(start.getMinutes()).toBe(0)
    expect(start.getDate()).toBe(30)
  })

  it('自定义结束不晚于开始时，结束补到次日同一钟点', () => {
    const now = new Date(2026, 7, 30, 16, 0, 0)
    const customStart = new Date(2026, 7, 30, 16, 0, 0)
    const customEnd = new Date(2026, 7, 30, 10, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_CUSTOM, now, customStart, customEnd)
    expect(start.getTime()).toBe(customStart.getTime())
    expect(end.getFullYear()).toBe(2026)
    expect(end.getMonth()).toBe(7)
    expect(end.getDate()).toBe(31)
    expect(end.getHours()).toBe(16)
    expect(end.getMinutes()).toBe(0)
  })

  it('自定义结束等于开始时，结束补到次日同一钟点', () => {
    const now = new Date(2026, 7, 30, 9, 0, 0)
    const customStart = new Date(2026, 7, 30, 14, 0, 0)
    const { start, end } = resolvePrepWindow(PRESET_CUSTOM, now, customStart, customStart)
    expect(start.getTime()).toBe(customStart.getTime())
    expect(end.getFullYear()).toBe(2026)
    expect(end.getMonth()).toBe(7)
    expect(end.getDate()).toBe(31)
    expect(end.getHours()).toBe(14)
    expect(end.getMinutes()).toBe(0)
  })
})

describe('itemKey', () => {
  it('空档口和未分类用同一把钥匙', () => {
    const emptyStation = itemKey({ station: '', item_name: '虾饺馅', unit: '份' })
    const missingStation = itemKey({ item_name: '虾饺馅', unit: '份' })
    const uncategorized = itemKey({ station: '未分类', item_name: '虾饺馅', unit: '份' })
    expect(emptyStation).toBe('未分类|虾饺馅|份')
    expect(missingStation).toBe(emptyStation)
    expect(uncategorized).toBe(emptyStation)
  })
})
