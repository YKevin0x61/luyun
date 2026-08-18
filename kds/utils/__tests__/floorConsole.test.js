import { describe, expect, it } from 'vitest'
import {
  canFire,
  canHold,
  canRush,
  defaultSelectedOrderIds,
  groupLinesByDishName,
  isActionable,
  nextSelectedOrderIds
} from '../floorConsole.js'

describe('floorConsole actionability', () => {
  it('allows 等叫 on 待出餐 / 待上笼 / 在蒸 only', () => {
    expect(canHold({ phase: '待出餐' })).toBe(true)
    expect(canHold({ phase: '待上笼' })).toBe(true)
    expect(canHold({ phase: '在蒸' })).toBe(true)
    expect(canHold({ phase: '等叫' })).toBe(false)
    expect(canHold({ phase: '已制作待上菜' })).toBe(false)
    expect(canHold({ phase: '已取消' })).toBe(false)
  })

  it('allows 叫起 only on 等叫', () => {
    expect(canFire({ phase: '等叫' })).toBe(true)
    expect(canFire({ phase: '待出餐' })).toBe(false)
    expect(canFire({ phase: '待上笼' })).toBe(false)
    expect(canFire({ phase: '在蒸' })).toBe(false)
    expect(canFire({ phase: '已制作待上菜' })).toBe(false)
    expect(canFire({ phase: '已取消' })).toBe(false)
  })

  it('allows 加急 on 待出餐 / 待上笼 that are not already rushed', () => {
    expect(canRush({ phase: '待出餐' })).toBe(true)
    expect(canRush({ phase: '待上笼' })).toBe(true)
    expect(canRush({ phase: '待出餐', is_rushed: true })).toBe(false)
    expect(canRush({ phase: '待上笼', is_rushed: true })).toBe(false)
    expect(canRush({ phase: '在蒸' })).toBe(false)
    expect(canRush({ phase: '等叫' })).toBe(false)
    expect(canRush({ phase: '已制作待上菜' })).toBe(false)
    expect(canRush({ phase: '已取消' })).toBe(false)
  })

  it('locks 已取消 and 已制作待上菜 so they cannot 等叫 / 叫起 / 加急', () => {
    expect(isActionable({ phase: '已取消' })).toBe(false)
    expect(isActionable({ phase: '已制作待上菜' })).toBe(false)
    expect(isActionable({ phase: '待出餐' })).toBe(true)
    expect(isActionable({ phase: '等叫' })).toBe(true)
  })
})

describe('floorConsole default select', () => {
  it('selects every actionable portion of the same dish', () => {
    const lines = [
      { order_id: 'a', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'b', dish_name: '虾饺', phase: '待上笼' },
      { order_id: 'c', dish_name: '虾饺', phase: '已取消' },
      { order_id: 'd', dish_name: '虾饺', phase: '已制作待上菜' }
    ]
    expect(defaultSelectedOrderIds(lines)).toEqual(['a', 'b'])
  })

  it('keeps an explicit empty selection after refresh instead of re-defaulting', () => {
    const lines = [
      { order_id: 'a', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'b', dish_name: '虾饺', phase: '待上笼' }
    ]
    expect(nextSelectedOrderIds(undefined, lines, { groupSeen: false })).toEqual(['a', 'b'])
    expect(nextSelectedOrderIds([], lines, { groupSeen: true })).toEqual([])
    expect(nextSelectedOrderIds(['a', 'gone'], lines, { groupSeen: true })).toEqual(['a'])
  })
})

describe('floorConsole grouping', () => {
  it('groups one table into separate dishes so each can be held apart', () => {
    const lines = [
      { order_id: 'a', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'b', dish_name: '叉烧包', phase: '等叫' },
      { order_id: 'c', dish_name: '虾饺', phase: '待出餐' }
    ]
    const groups = groupLinesByDishName(lines)
    expect(groups.map((group) => group.dishName)).toEqual(['虾饺', '叉烧包'])
    expect(groups[0].lines.map((line) => line.order_id)).toEqual(['a', 'c'])
    expect(groups[1].lines.map((line) => line.order_id)).toEqual(['b'])
    expect(defaultSelectedOrderIds(groups[0].lines)).toEqual(['a', 'c'])
    expect(defaultSelectedOrderIds(groups[1].lines)).toEqual(['b'])
  })
})
