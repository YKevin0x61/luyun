import { describe, expect, it } from 'vitest'
import {
  formatClock,
  formatMonthDayChinese,
  lunarMonthDay,
  suggestedQuantity,
} from '../purchaseUi.js'

describe('suggestedQuantity', () => {
  it('参考值 × 安全系数 − 盘点库存，按步长向上取整', () => {
    expect(suggestedQuantity(5, { safetyFactor: 1.2, currentStock: 0, orderStep: 1 })).toBe(6)
    expect(suggestedQuantity(5, { safetyFactor: 1.2, currentStock: 2, orderStep: 1 })).toBe(4)
    expect(suggestedQuantity(5, { safetyFactor: 1.2, currentStock: 6, orderStep: 1 })).toBe(0)
    expect(suggestedQuantity(3, { safetyFactor: 1.2, currentStock: 1, orderStep: 5 })).toBe(5)
  })

  it('结果小于 1 保留 1 位小数，不按步长放大', () => {
    expect(suggestedQuantity(0.5, { safetyFactor: 1.2, currentStock: 0, orderStep: 5 })).toBe(0.6)
    expect(suggestedQuantity(0.5, { safetyFactor: 1.2, currentStock: 0.1, orderStep: 5 })).toBe(0.5)
  })

  it('没有参考值 / 参考值为 0 时不给下单量', () => {
    expect(suggestedQuantity(null)).toBeNull()
    expect(suggestedQuantity(0)).toBeNull()
    expect(suggestedQuantity('!')).toBeNull()
  })
})

describe('日期显示', () => {
  it('头部日期与天气卡用中文月日', () => {
    expect(formatMonthDayChinese('2026-11-22')).toBe('11月22日')
    expect(formatMonthDayChinese('2026-02-17T10:00:00+08:00')).toBe('2月17日')
    expect(formatMonthDayChinese('bad')).toBe('')
  })

  it('农历按北京时间换算，初一与廿X都正确', () => {
    expect(lunarMonthDay('2026-02-17')).toBe('正月初一')
    expect(lunarMonthDay('2026-11-22')).toBe('十月十四')
    expect(lunarMonthDay('2026-12-09')).toBe('十一月初一')
    expect(lunarMonthDay('bad')).toBe('')
  })

  it('更新时间只取 HH:mm', () => {
    expect(formatClock('2026-11-22T03:19:32+08:00')).toBe('03:19')
    expect(formatClock('')).toBe('')
  })
})
