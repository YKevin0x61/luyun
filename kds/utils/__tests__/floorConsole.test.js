import { describe, expect, it } from 'vitest'
import {
  canFire,
  canHold,
  canRush,
  compareFloorTableNumber,
  decorateFloorTables,
  defaultSelectedOrderIds,
  floorConflictsToastTitle,
  floorLineRowText,
  groupLinesByDishName,
  isActionable,
  isFloorSplitLayout,
  matchFloorTable,
  nextSelectedOrderIds,
  nextSelectedTableNumber,
  sortFloorDishGroups,
  sortFloorGroupLines,
  tableCardEmphasis,
  tableCardStats,
  tableLeftToastTitle,
  FLOOR_JUMP_MISS_TOAST
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

describe('floorConsole row and conflict copy', () => {
  it('shows 下单时间 on the portion row so 对账 still sees when the guest ordered', () => {
    expect(floorLineRowText({
      phase: '等叫',
      order_time: '2026-08-18T12:30:00+08:00'
    })).toBe('等叫 12:30')
    expect(floorLineRowText({
      phase: '待出餐',
      is_rushed: true,
      order_time: '2026-08-18T09:05:00+08:00'
    })).toBe('待出餐 09:05·加急')
    expect(floorLineRowText({ phase: '在蒸' })).toBe('在蒸')
  })

  it('names every distinct conflict reason with the unchanged portion count', () => {
    expect(floorConflictsToastTitle([])).toBe('')
    expect(floorConflictsToastTitle([{ order_id: 'a', reason: '已出餐' }])).toBe('1 份未改：已出餐')
    expect(floorConflictsToastTitle([
      { order_id: 'a', reason: '已出餐' },
      { order_id: 'b', reason: '已被等叫' },
      { order_id: 'c', reason: '已出餐' }
    ])).toBe('3 份未改：已出餐；已被等叫')
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

describe('floorConsole 桌卡 counts', () => {
  it('counts 等叫 / 待出餐工作 / 已制作待上菜 and flags 加急, including zeros', () => {
    const stats = tableCardStats([
      { phase: '等叫' },
      { phase: '待出餐', is_rushed: true },
      { phase: '待上笼' },
      { phase: '在蒸' },
      { phase: '已制作待上菜' },
      { phase: '已制作待上菜' },
      { phase: '已取消' }
    ])
    expect(stats).toEqual({
      holdCount: 1,
      pendingWorkCount: 3,
      readyCount: 2,
      hasRush: true
    })
    expect(tableCardStats([])).toEqual({
      holdCount: 0,
      pendingWorkCount: 0,
      readyCount: 0,
      hasRush: false
    })
    expect(tableCardEmphasis({ readyCount: 2 })).toBe('ready')
    expect(tableCardEmphasis({ readyCount: 0, hasRush: true })).toBe('plain')
  })
})

describe('floorConsole split layout', () => {
  it('splits on width ≥960 or both sides ≥768, not on phone landscape', () => {
    expect(isFloorSplitLayout({ width: 390, height: 844 })).toBe(false)
    expect(isFloorSplitLayout({ width: 844, height: 390 })).toBe(false)
    expect(isFloorSplitLayout({ width: 959, height: 767 })).toBe(false)
    expect(isFloorSplitLayout({ width: 767, height: 1024 })).toBe(false)
    expect(isFloorSplitLayout({ width: 960, height: 600 })).toBe(true)
    expect(isFloorSplitLayout({ width: 834, height: 1194 })).toBe(true)
    expect(isFloorSplitLayout({ width: 1194, height: 834 })).toBe(true)
    expect(isFloorSplitLayout({ width: 768, height: 768 })).toBe(true)
  })
})

describe('floorConsole jump and selected table', () => {
  const tables = [{ table_number: '1' }, { table_number: '12' }]

  it('matches trimmed exact 桌号 only', () => {
    expect(matchFloorTable(tables, '12')).toEqual({ table_number: '12' })
    expect(matchFloorTable(tables, ' 12 ')).toEqual({ table_number: '12' })
    expect(matchFloorTable(tables, '1')).toEqual({ table_number: '1' })
    expect(matchFloorTable(tables, '10')).toBe(null)
    expect(matchFloorTable(tables, '')).toBe(null)
    expect(FLOOR_JUMP_MISS_TOAST).toBe('不在盯桌列表')
  })

  it('matches non-numeric 桌号 strings exactly', () => {
    const posNamedTables = [{ table_number: '包1' }, { table_number: '包10' }]
    expect(matchFloorTable(posNamedTables, '包1')).toEqual({ table_number: '包1' })
    expect(matchFloorTable(posNamedTables, ' 包1 ')).toEqual({ table_number: '包1' })
    expect(matchFloorTable(posNamedTables, '包2')).toBe(null)
  })

  it('keeps the open 桌 if it is still in the list, otherwise clears it', () => {
    expect(nextSelectedTableNumber('12', tables)).toBe('12')
    expect(nextSelectedTableNumber('8', tables)).toBe('')
    expect(nextSelectedTableNumber('', tables)).toBe('')
    expect(tableLeftToastTitle('8')).toBe('8桌已离台')
  })

  it('orders 桌卡 by 桌号 with numeric awareness', () => {
    expect(compareFloorTableNumber('2', '10')).toBeLessThan(0)
    expect(
      decorateFloorTables([
        { table_number: '10', lines: [] },
        { table_number: '2', lines: [] }
      ]).map((table) => table.table_number)
    ).toEqual(['2', '10'])
  })
})

describe('floorConsole 单桌面 order', () => {
  it('puts 已制作待上菜 groups first, then remaining dishes by name', () => {
    const groups = sortFloorDishGroups([
      { dishName: '虾饺', lines: [{ phase: '待出餐' }] },
      { dishName: '叉烧包', lines: [{ phase: '已制作待上菜' }] },
      { dishName: '烧卖', lines: [{ phase: '等叫' }] }
    ])
    expect(groups.map((group) => group.dishName)).toEqual(['叉烧包', '烧卖', '虾饺'])
  })

  it('puts actionable portions above read-only, then 进入待出餐工作时刻', () => {
    const lines = sortFloorGroupLines([
      { order_id: 'ready-late', phase: '已制作待上菜', work_enter_time: '2026-08-18T10:00:00+08:00' },
      { order_id: 'hold-late', phase: '等叫', work_enter_time: '2026-08-18T09:30:00+08:00' },
      { order_id: 'pending-early', phase: '待出餐', work_enter_time: '2026-08-18T09:00:00+08:00' },
      { order_id: 'cancel', phase: '已取消', work_enter_time: '2026-08-18T08:00:00+08:00' }
    ])
    expect(lines.map((line) => line.order_id)).toEqual([
      'pending-early',
      'hold-late',
      'cancel',
      'ready-late'
    ])
  })
})
