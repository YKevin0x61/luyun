import { describe, expect, it } from 'vitest'
import {
  canFire,
  canHold,
  canRush,
  compareFloorTableNumber,
  decorateFloorTables,
  defaultSelectedOrderIds,
  dropSuccessfulOrderIds,
  floorConflictsToastTitle,
  floorLineNotes,
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
  it('pre-checks 待出餐 / 待上笼 / 等叫 and leaves 在蒸 / 已制作待上菜 / 已取消 unchecked', () => {
    const lines = [
      { order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'basket-1', dish_name: '虾饺', phase: '待上笼' },
      { order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' },
      { order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'cancel-1', dish_name: '虾饺', phase: '已取消' },
      { order_id: 'ready-1', dish_name: '虾饺', phase: '已制作待上菜' }
    ]
    expect(defaultSelectedOrderIds(lines)).toEqual(['pending-1', 'basket-1', 'hold-1'])
  })

  it('「全选」 is that default set, so a hand-checked 在蒸 is not in it', () => {
    const lines = [
      { order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' }
    ]
    const selectAllIds = defaultSelectedOrderIds(lines)
    expect(selectAllIds).toEqual(['pending-1', 'hold-1'])
    expect(selectAllIds).not.toContain('steam-1')
  })
})

describe('floorConsole seen-group keep', () => {
  it('keeps peeled checks, does not auto-check new portions, drops became-在蒸, keeps still-在蒸 if selected', () => {
    const previousLines = [
      { order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'pending-2', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'swap-1', dish_name: '虾饺', phase: '待上笼' },
      { order_id: 'ready-1', dish_name: '虾饺', phase: '待出餐' }
    ]
    const nextLines = [
      { order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'pending-2', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'new-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'swap-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'ready-1', dish_name: '虾饺', phase: '已制作待上菜' }
    ]
    expect(nextSelectedOrderIds(
      ['pending-1', 'steam-1', 'swap-1', 'ready-1'],
      nextLines,
      { groupSeen: true, previousLines }
    )).toEqual(['pending-1', 'steam-1'])
  })

  it('uses the default set for a first-seen group and keeps an explicit empty selection empty', () => {
    const lines = [
      { order_id: 'pending-1', dish_name: '虾饺', phase: '待出餐' },
      { order_id: 'steam-1', dish_name: '虾饺', phase: '在蒸' },
      { order_id: 'hold-1', dish_name: '虾饺', phase: '等叫' }
    ]
    expect(nextSelectedOrderIds(undefined, lines, { groupSeen: false })).toEqual(['pending-1', 'hold-1'])
    expect(nextSelectedOrderIds([], lines, { groupSeen: true, previousLines: lines })).toEqual([])
  })
})

describe('floorConsole drop after action', () => {
  it('drops submitted ids minus conflict order_ids and leaves conflict checks; 409 is not this helper\'s job', () => {
    expect(dropSuccessfulOrderIds(
      ['hold-1', 'steam-1', 'pending-1', 'other-1'],
      ['hold-1', 'steam-1', 'pending-1'],
      [{ order_id: 'steam-1', reason: '在蒸且无替补' }]
    )).toEqual(['steam-1', 'other-1'])
    expect(dropSuccessfulOrderIds(
      ['hold-1', 'pending-1'],
      ['hold-1', 'pending-1'],
      []
    )).toEqual([])
  })
})

describe('floorConsole row and conflict copy', () => {
  it('prints 进入待出餐工作时刻, then 叫起时刻, then 下单时间, as 「阶段 HH:mm」 / 「·加急」', () => {
    expect(floorLineRowText({
      phase: '等叫',
      work_enter_time: '2026-08-18T12:40:00+08:00',
      fired_at: '2026-08-18T12:35:00+08:00',
      order_time: '2026-08-18T12:30:00+08:00'
    })).toBe('等叫 12:40')
    expect(floorLineRowText({
      phase: '待出餐',
      fired_at: '2026-08-18T09:10:00+08:00',
      order_time: '2026-08-18T09:05:00+08:00',
      is_rushed: true
    })).toBe('待出餐 09:10·加急')
    expect(floorLineRowText({
      phase: '待上笼',
      order_time: '2026-08-18T08:15:00+08:00'
    })).toBe('待上笼 08:15')
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

  it('keeps 免葱 and plain 艇仔粥 in one group; notes stay on each line, not the title', () => {
    const groups = groupLinesByDishName([
      { order_id: 'onion', dish_name: '艇仔粥', notes: '免葱', phase: '待出餐' },
      { order_id: 'plain', dish_name: '艇仔粥', notes: '', phase: '待出餐' }
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].dishName).toBe('艇仔粥')
    expect(groups[0].dishName).not.toContain('免葱')
    expect(groups[0].lines.map((line) => line.order_id)).toEqual(['onion', 'plain'])
    expect(floorLineNotes(groups[0].lines[0])).toBe('免葱')
    expect(floorLineNotes(groups[0].lines[1])).toBe('')
  })

  it('does not treat 外卖平台: notes as visible 备注, and does not stuff notes into the phase row', () => {
    expect(floorLineNotes({ notes: '外卖平台:美团|来源:美团1' })).toBe('')
    expect(floorLineNotes({ notes: '  免葱  ' })).toBe('免葱')
    expect(floorLineNotes({ notes: '' })).toBe('')
    expect(floorLineNotes({})).toBe('')
    expect(floorLineRowText({
      phase: '待出餐',
      notes: '免葱',
      order_time: '2026-08-18T08:15:00+08:00'
    })).toBe('待出餐 08:15')
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
  it('puts groups with an actionable portion first, then remaining dishes by 菜名', () => {
    const groups = sortFloorDishGroups([
      { dishName: '叉烧包', lines: [{ phase: '已制作待上菜' }] },
      { dishName: '虾饺', lines: [{ phase: '待出餐' }] },
      { dishName: '烧卖', lines: [{ phase: '等叫' }] },
      { dishName: '凤爪', lines: [{ phase: '在蒸' }] }
    ])
    expect(groups.map((group) => group.dishName)).toEqual(['凤爪', '烧卖', '虾饺', '叉烧包'])
  })

  it('orders lines as actionable-not-在蒸, then 在蒸, then read-only, each by 进入待出餐工作时刻', () => {
    const lines = sortFloorGroupLines([
      { order_id: 'ready-late', phase: '已制作待上菜', work_enter_time: '2026-08-18T10:00:00+08:00' },
      { order_id: 'steam-late', phase: '在蒸', work_enter_time: '2026-08-18T09:45:00+08:00' },
      { order_id: 'hold-late', phase: '等叫', work_enter_time: '2026-08-18T09:30:00+08:00' },
      { order_id: 'pending-early', phase: '待出餐', work_enter_time: '2026-08-18T09:00:00+08:00' },
      { order_id: 'steam-early', phase: '在蒸', work_enter_time: '2026-08-18T08:30:00+08:00' },
      { order_id: 'cancel', phase: '已取消', work_enter_time: '2026-08-18T08:00:00+08:00' }
    ])
    expect(lines.map((line) => line.order_id)).toEqual([
      'pending-early',
      'hold-late',
      'steam-early',
      'steam-late',
      'cancel',
      'ready-late'
    ])
  })
})
