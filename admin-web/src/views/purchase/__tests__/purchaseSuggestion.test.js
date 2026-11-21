import { describe, expect, it } from 'vitest'
import { suggestedQuantity } from '../purchaseUi.js'

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
