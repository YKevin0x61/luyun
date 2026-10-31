import { describe, expect, it } from 'vitest'
import { coverSlotsFor } from '../weekCoverSlots'

// 夹具按 spec 冻结的 `/week` 形状搭：班次的 `duty_slot` 与工作区的 `day_shift` /
// `night_shift` 都是票 01 补进来的两个渲染输入，这里不等它，直接按契约造。
const SHIFTS = [
  { id: 1, name: '早班', sort_order: 10, is_active: true, duty_slot: 'day' },
  { id: 2, name: '夜班', sort_order: 20, is_active: true, duty_slot: 'night' },
  // 「中班」这种不出日常的班次：`duty_slot` 为 null。
  { id: 3, name: '中班', sort_order: 30, is_active: true, duty_slot: null },
  { id: 4, name: '老早班', sort_order: 40, is_active: false, duty_slot: 'day' },
]

const ZONES = [
  { id: 1, name: '案板', day_shift: 1, night_shift: 1 },
  // 真数据：馅档只跑夜市。
  { id: 2, name: '馅档', day_shift: 0, night_shift: 1 },
  { id: 3, name: '熟笼', day_shift: 1, night_shift: 0 },
  { id: 4, name: '西饼', day_shift: 1, night_shift: 1 },
]

const DATE = '2026-11-04'

function cell(shiftId, zoneId, extra = {}) {
  return {
    scheduled: true,
    shift_id: shiftId,
    zone_id: zoneId,
    leave: false,
    half_rest: false,
    ...extra,
  }
}

function rest(leave = false) {
  return { scheduled: true, shift_id: null, zone_id: null, leave, half_rest: false }
}

function emp(id, name, cellData, defaults = []) {
  return { id, name, cells: { [DATE]: cellData }, zone_defaults: defaults }
}

function def(shiftId, zoneId, zoneName = null) {
  return { shift_id: shiftId, zone_id: zoneId, zone_name: zoneName }
}

function slots(employees, shifts = SHIFTS, zones = ZONES) {
  return coverSlotsFor({ employees, shifts, zones, date: DATE })
}

describe('那天要顶的档：取人与空清单', () => {
  it('休息的人按固定区列一行：区 × 档 + 该档还有谁', () => {
    const rows = slots([emp(8, '张三', rest(), [def(2, 2, '馅档')])])
    expect(rows).toEqual([
      {
        employee_id: 8,
        employee_name: '张三',
        leave: false,
        shift_id: 2,
        shift_name: '夜班',
        slot: 'night',
        zone_id: 2,
        zone_name: '馅档',
        others: [],
        can_pen: true,
        blocked_reason: null,
      },
    ])
  })

  it('请假只是行上的标记，照列', () => {
    const rows = slots([emp(8, '张三', rest(true), [def(2, 2, '馅档')])])
    expect(rows.map((row) => row.leave)).toEqual([true])
  })

  it('没配固定区的人休息不产生任何行', () => {
    expect(slots([emp(9, '李四', rest())])).toEqual([])
  })

  it('那天还没铺到（没有这一格）不产生行', () => {
    const employee = { id: 9, name: '李四', cells: {}, zone_defaults: [def(2, 2, '馅档')] }
    expect(slots([employee])).toEqual([])
  })

  it('没人休 / 请假 → 空清单', () => {
    expect(slots([emp(8, '张三', cell(2, 2), [def(2, 2, '馅档')])])).toEqual([])
  })
})

describe('不列的行：判不了档，或者那个档根本不存在', () => {
  it('中班（duty_slot 为 null）不列 —— 它不出日常检查', () => {
    expect(slots([emp(8, '张三', rest(), [def(3, 1, '案板')])])).toEqual([])
    // 连着已删的区一起配也不列：判断不了档位时，连「照列 + 说原因」都不做。
    expect(slots([emp(8, '张三', rest(), [def(3, 99, null)])])).toEqual([])
  })

  it('区不跑这一档的不列：馅档只跑夜市，「馅档 × 早班」这种配置不产生行', () => {
    // 真数据里陆柏豪配的就是这一条：列出来就是让人去顶一个根本不存在的档。
    expect(slots([emp(7, '陆柏豪', rest(), [def(1, 2, '馅档')])])).toEqual([])
    // 反向同理：熟笼只跑白班。
    expect(slots([emp(8, '张三', rest(), [def(2, 3, '熟笼')])])).toEqual([])
    // 同一个人的其它配置行照列（跳过的是那一行，不是这个人）。
    const rows = slots([emp(7, '陆柏豪', rest(), [def(1, 2, '馅档'), def(2, 2, '馅档')])])
    expect(rows.map((row) => [row.shift_id, row.zone_id])).toEqual([[2, 2]])
  })

  it('班次被硬删（快照里没有这个 id）不列 —— 连档位都不知道', () => {
    expect(slots([emp(8, '张三', rest(), [def(99, 2, '馅档')])])).toEqual([])
  })

  it('没配区的固定区配置不列', () => {
    expect(slots([emp(8, '张三', rest(), [def(2, null)])])).toEqual([])
  })
})

describe('照列但不能装笔：停用班次 / 已删工作区', () => {
  it('停用班次照列，can_pen=false，理由是班次已停用', () => {
    const rows = slots([emp(8, '张三', rest(), [def(4, 1, '案板')])])
    expect(rows).toHaveLength(1)
    expect(rows[0].can_pen).toBe(false)
    expect(rows[0].blocked_reason).toBe('shift_inactive')
    expect(rows[0].shift_name).toBe('老早班')
  })

  it('已删工作区照列，can_pen=false，理由是工作区已删，区名照实为 null', () => {
    const rows = slots([emp(8, '张三', rest(), [def(2, 99, null)])])
    expect(rows).toHaveLength(1)
    expect(rows[0].can_pen).toBe(false)
    expect(rows[0].blocked_reason).toBe('zone_deleted')
    expect(rows[0].zone_name).toBeNull()
  })

  it('那天这一格只是「还没排」（scheduled=false）也不列', () => {
    const blank = { scheduled: false, shift_id: null, zone_id: null, leave: false, half_rest: false }
    expect(slots([emp(8, '张三', blank, [def(2, 2, '馅档')])])).toEqual([])
  })

  it('停用班次 × 区不跑该档：先按「档不存在」不列，而不是照列一条不能装笔的行', () => {
    expect(slots([emp(8, '张三', rest(), [def(4, 2, '馅档')])])).toEqual([])
  })

  it('停用班次 + 区还在：区名照常给（只是不能装笔）', () => {
    const rows = slots([emp(8, '张三', rest(), [def(4, 4, '西饼')])])
    expect(rows[0].zone_name).toBe('西饼')
    expect(rows[0].blocked_reason).toBe('shift_inactive')
  })
})

describe('这个档现在还有谁', () => {
  it('同区同档的人都在；半休的人也算（他那天班次还在）', () => {
    const rows = slots([
      emp(8, '张三', rest(), [def(2, 2, '馅档')]),
      emp(9, '李四', cell(2, 2)),
      // 半休那格班次还在，只是旁边多一个「半」标记。
      emp(10, '王五', cell(2, 2, { half_rest: true })),
      // 在馅档，但上的是早班 —— 不是同一个档。
      emp(11, '赵六', cell(1, 2)),
      // 也是夜市，但人在案板。
      emp(12, '孙七', cell(2, 1)),
      // 那天休的人不在「还有谁」里（他自己就是这一行）。
      emp(13, '周八', rest()),
    ])
    expect(rows[0].others).toEqual(['李四', '王五'])
  })

  it('那个档只有半休一个人，也照样算有人', () => {
    const rows = slots([
      emp(8, '张三', rest(), [def(2, 2, '馅档')]),
      emp(10, '王五', cell(2, 2, { half_rest: true })),
    ])
    expect(rows[0].others).toEqual(['王五'])
  })

  it('同档没人 → 空数组（由渲染层说「没人」）', () => {
    const rows = slots([emp(8, '张三', rest(), [def(2, 2, '馅档')])])
    expect(rows[0].others).toEqual([])
  })
})

describe('排序与一人多行', () => {
  it('按班次 sort_order、同班次按区 id', () => {
    const rows = slots([
      emp(8, '张三', rest(), [def(2, 2, '馅档'), def(1, 4, '西饼')]),
      emp(9, '李四', rest(), [def(1, 1, '案板')]),
      emp(10, '王五', rest(), [def(4, 4, '西饼')]), // 停用班次排在最后
    ])
    expect(rows.map((row) => [row.employee_name, row.shift_name, row.zone_name])).toEqual([
      ['李四', '早班', '案板'],
      ['张三', '早班', '西饼'],
      ['张三', '夜班', '馅档'],
      ['王五', '老早班', '西饼'],
    ])
  })

  it('同一个人两个班次就是两行，不保证相邻', () => {
    const rows = slots([emp(8, '张三', rest(), [def(2, 2, '馅档'), def(1, 1, '案板')])])
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => [row.shift_id, row.zone_id])).toEqual([[1, 1], [2, 2]])
    expect(rows.every((row) => row.employee_id === 8)).toBe(true)
  })
})
