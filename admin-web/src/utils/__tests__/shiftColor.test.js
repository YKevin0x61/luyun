// 班次配色（0043）：色键 → class/中文名、占用规则、员工端格子拿色。
//
// 这些断言的用意：颜色是**数据**（`staff_shifts.color_key`），前端只做映射与降级。
// 一旦谁再把「按序号取色」写回来、或者把后端那 8 个键改了名而前端没跟着改，
// 这组用例会红。
import { describe, expect, it } from 'vitest'
import {
  SHIFT_COLOR_CHOICES,
  SHIFT_COLOR_NEUTRAL,
  colorOwner,
  shiftColorClass,
  shiftColorLabel,
} from '../shiftTable'
import { monthCell } from '../todayShift'

describe('班次色键', () => {
  it('8 档的顺序与服务端 SHIFT_COLORS 一致', () => {
    // 服务端 `services/scheduling/store.py` 的 SHIFT_COLORS —— 前 6 个是主色。
    expect(SHIFT_COLOR_CHOICES.map((choice) => choice.key)).toEqual([
      'moss',
      'mint',
      'tide',
      'sky',
      'pine',
      'rose',
      'grass',
      'lilac',
    ])
    expect(SHIFT_COLOR_CHOICES.filter((choice) => choice.main)).toHaveLength(6)
  })

  it('class 名是 tone-<键>，缺色回中性', () => {
    expect(shiftColorClass('pine')).toBe('tone-pine')
    expect(shiftColorClass('')).toBe('tone-neutral')
    expect(shiftColorClass(null)).toBe('tone-neutral')
    expect(shiftColorClass(undefined)).toBe('tone-neutral')
  })

  it('中文名：认识的给名字，不认识的把键原样回出来，不猜', () => {
    expect(shiftColorLabel('moss')).toBe('苔绿')
    expect(shiftColorLabel('lilac')).toBe('藕荷')
    expect(shiftColorLabel(null)).toBe('中性')
    expect(shiftColorLabel('chartreuse')).toBe('chartreuse')
  })
})

describe('占用规则', () => {
  const shifts = [
    { id: 1, name: '早班', is_active: 1, color_key: 'moss' },
    { id: 2, name: '夜班', is_active: 1, color_key: 'mint' },
    { id: 3, name: '旧的班', is_active: 0, color_key: 'tide' },
  ]

  it('启用中的班次占着它那一档', () => {
    expect(colorOwner(shifts, 'moss', 2)).toEqual(shifts[0])
    expect(colorOwner(shifts, 'mint', 1)).toEqual(shifts[1])
  })

  it('自己不算占着自己那一档（否则页面上看不出当前选的是哪个）', () => {
    expect(colorOwner(shifts, 'moss', 1)).toBeNull()
  })

  it('停用的班次不占额：它的色可以给别人', () => {
    expect(colorOwner(shifts, 'tide', 1)).toBeNull()
  })

  it('中性可以重复，永远不算被占用', () => {
    expect(colorOwner(shifts, SHIFT_COLOR_NEUTRAL, 1)).toBeNull()
    expect(colorOwner(shifts, null, 1)).toBeNull()
  })
})

describe('员工端整月格子', () => {
  const day = {
    business_date: '2026-11-02',
    scheduled: true,
    shift_id: 7,
    shift_name: '早班',
    shift_color: 'moss',
    half_rest: 0,
  }

  it('上班那天带上班次色键', () => {
    expect(monthCell(day)).toMatchObject({ text: '早班', tone: 'shift', color: 'moss' })
  })

  it('休 / 请假不带班次色（颜色只表示「上哪个班」）', () => {
    expect(monthCell({ ...day, shift_id: null, scheduled: true }).color).toBeNull()
    expect(monthCell({ ...day, leave: true }).color).toBeNull()
  })

  it('迁移没应用（没有 shift_color）时回 null，格子退回原样', () => {
    const { shift_color: _ignored, ...withoutColor } = day
    expect(monthCell(withoutColor).color).toBeNull()
  })
})
