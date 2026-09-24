import { describe, expect, it } from 'vitest'
import {
  dayLabel,
  nextTwoLine,
  shiftText,
  todayHeadline,
  todaySubline,
  todayTone,
} from '../todayShift'

// 服务端 `GET /api/scheduling/me` 一天的三个字段合起来是四件事，四个样本各占一件。
const MORNING = {
  business_date: '2026-09-25',
  scheduled: true,
  shift_id: 3,
  shift_name: '白班',
  zone_name: '案板',
}
const REST = { business_date: '2026-09-25', scheduled: true, shift_id: null, shift_name: null }
const NOT_ROSTERED = {
  business_date: '2026-09-25',
  scheduled: false,
  shift_id: null,
  shift_name: null,
}
// 行上写着 shift_id、但那条班次已经被删（票 11 管增删）：名字取不到。
const ORPHAN = { business_date: '2026-09-25', scheduled: true, shift_id: 9, shift_name: null }

describe('营业日写成「9/25 周五」', () => {
  it('按 UTC 算星期几，手机时区挪不动它', () => {
    expect(dayLabel('2026-09-24')).toBe('9/24 周四')
    expect(dayLabel('2026-09-25')).toBe('9/25 周五')
    expect(dayLabel('2026-09-27')).toBe('9/27 周日')
  })

  it('坏数据给空串，不抛也不编一个日期', () => {
    expect(dayLabel('')).toBe('')
    expect(dayLabel(null)).toBe('')
    expect(dayLabel(undefined)).toBe('')
    expect(dayLabel('昨天')).toBe('')
    expect(dayLabel('2026-09')).toBe('')
  })
})

describe('「休」和「还没排」是两件事（验收 3）', () => {
  it('没铺到那一天说还没排', () => {
    expect(shiftText(NOT_ROSTERED)).toBe('还没排')
    expect(todayHeadline(NOT_ROSTERED)).toBe('今天没有你的班')
    expect(todayTone(NOT_ROSTERED)).toBe('none')
    expect(todaySubline(NOT_ROSTERED)).toBe('店长还没排到你')
  })

  it('行上写着休的那天说休', () => {
    expect(shiftText(REST)).toBe('休')
    expect(todayHeadline(REST)).toBe('休')
    expect(todayTone(REST)).toBe('rest')
    expect(todaySubline(REST)).toBe('今天休息')
  })

  it('两种状态不许翻成同一句话', () => {
    expect(shiftText(REST)).not.toBe(shiftText(NOT_ROSTERED))
    expect(todayHeadline(REST)).not.toBe(todayHeadline(NOT_ROSTERED))
    expect(todayTone(REST)).not.toBe(todayTone(NOT_ROSTERED))
  })

  it('班次名与责任区都从行上取，页面不写死白班夜班', () => {
    expect(shiftText(MORNING)).toBe('白班')
    expect(todayHeadline(MORNING)).toBe('白班')
    expect(todayTone(MORNING)).toBe('')
    expect(todaySubline(MORNING)).toBe('今天上班 · 案板')
    expect(todaySubline({ ...MORNING, zone_name: null })).toBe('今天上班')
    // 换个店配的班次名，翻出来的就是那个名字。
    expect(shiftText({ ...MORNING, shift_name: '早班', zone_name: '蒸柜' })).toBe('早班')
    expect(todaySubline({ ...MORNING, shift_name: '早班', zone_name: '蒸柜' })).toBe(
      '今天上班 · 蒸柜',
    )
  })

  it('班次被删了说「已调整」，不许假装那天是休', () => {
    expect(shiftText(ORPHAN)).toBe('班次已调整')
    expect(todayHeadline(ORPHAN)).toBe('班次已调整')
    expect(shiftText(ORPHAN)).not.toBe('休')
    expect(todayTone(ORPHAN)).toBe('none')
  })

  it('一天都没有（接口给了空数组）也不炸', () => {
    expect(shiftText(null)).toBe('还没排')
    expect(todayHeadline(undefined)).toBe('今天没有你的班')
    expect(todaySubline(null)).toBe('店长还没排到你')
    expect(todayTone(null)).toBe('none')
  })
})

describe('卡顶那句「明天 X · 后天 Y」（验收 3）', () => {
  it('两句一顿点，第三格留给「往后三天」', () => {
    expect(nextTwoLine([REST, MORNING, NOT_ROSTERED])).toBe('明天 休 · 后天 白班')
  })

  it('一天少一天就不补', () => {
    expect(nextTwoLine([MORNING])).toBe('明天 白班')
    expect(nextTwoLine([])).toBe('')
    expect(nextTwoLine(null)).toBe('')
  })
})

describe('整屏没有钟点（验收 5）', () => {
  it('这些文案里拼不出 hh:mm', () => {
    const all = [
      dayLabel('2026-09-25'),
      shiftText(MORNING),
      todayHeadline(MORNING),
      todaySubline(MORNING),
      nextTwoLine([REST, MORNING]),
    ].join(' ')
    expect(all).not.toMatch(/\d{1,2}:\d{2}/)
  })
})
