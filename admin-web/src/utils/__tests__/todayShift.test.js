import { describe, expect, it } from 'vitest'
import {
  MONTH_HEADS,
  dayBeyondWindow,
  dayLabel,
  monthCell,
  monthLabel,
  nextTwoLine,
  shiftMonth,
  shiftText,
  shiftTone,
  stepMonth,
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
// 票 08：批过的请假。结果行跟「本来就休」长得一样（都没有班次），只有 `leave` 分得出。
const LEAVE = {
  business_date: '2026-09-25',
  scheduled: true,
  shift_id: null,
  shift_name: null,
  leave: true,
}

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
    // 「已调整」有自己的语气（琥珀），不能跟「休」共用最弱的那一档 ——
    // 那天的班次没了，不等于那天不上班。
    expect(todayTone(ORPHAN)).toBe('moved')
    expect(shiftTone(ORPHAN)).toBe('moved')
    expect(shiftTone(ORPHAN)).not.toBe(shiftTone(REST))
  })

  it('一天都没有（接口给了空数组）也不炸', () => {
    expect(shiftText(null)).toBe('还没排')
    expect(todayHeadline(undefined)).toBe('今天没有你的班')
    expect(todaySubline(null)).toBe('店长还没排到你')
    expect(todayTone(null)).toBe('none')
  })
})

describe('请假跟「休」是两件事（票 08）', () => {
  it('批过的假说请假，不许说成休', () => {
    expect(shiftText(LEAVE)).toBe('请假')
    expect(todayHeadline(LEAVE)).toBe('请假')
    expect(todaySubline(LEAVE)).toBe('今天请假')
    expect(shiftTone(LEAVE)).toBe('leave')
    expect(todayTone(LEAVE)).toBe('leave')
    // 都是「那天没有班次」，但一个是自己提的、店长批的，一个是排班给的。
    expect(shiftText(LEAVE)).not.toBe(shiftText(REST))
    expect(todayHeadline(LEAVE)).not.toBe(todayHeadline(REST))
    expect(shiftTone(LEAVE)).not.toBe(shiftTone(REST))
    expect(todayTone(LEAVE)).not.toBe(todayTone(REST))
  })

  it('整月格子里写「请假」，跟「休」不同字不同色', () => {
    expect(monthCell(LEAVE)).toEqual({ text: '请假', tone: 'leave' })
    expect(monthCell(LEAVE).text).not.toBe(monthCell(REST).text)
    expect(monthCell(LEAVE).tone).not.toBe(monthCell(REST).tone)
  })

  it('往后三天那三格也认得出是假不是休', () => {
    expect(nextTwoLine([LEAVE, MORNING])).toBe('明天 请假 · 后天 白班')
  })

  it('没有 leave 这一位的老数据还是老样子', () => {
    // 服务端一定会给这个字段（`/me`、`/me/month` 都给了）；万一没有，
    // 不许把「休」猜成「请假」。
    expect(shiftText(REST)).toBe('休')
    expect(shiftText({ ...REST, leave: false })).toBe('休')
    expect(shiftTone({ ...REST, leave: false })).toBe('rest')
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
      monthLabel('2026-09', '2026-09-24'),
      monthCell(MORNING).text,
      monthCell(ORPHAN).text,
    ].join(' ')
    expect(all).not.toMatch(/\d{1,2}:\d{2}/)
  })
})

describe('员工端「整月」（票 06）', () => {
  it('表头周日开头，七格', () => {
    // 服务端给的 `lead = isoweekday() % 7` 就是这个开头（周日第 0 格）。
    expect(MONTH_HEADS).toEqual(['日', '一', '二', '三', '四', '五', '六'])
    expect(MONTH_HEADS).toHaveLength(7)
  })

  it('月份写成「9月」，跨年了才带年份', () => {
    expect(monthLabel('2026-09', '2026-09-24')).toBe('9月')
    expect(monthLabel('2026-12', '2026-09-24')).toBe('12月')
    expect(monthLabel('2027-01', '2026-09-24')).toBe('2027年1月')
    expect(monthLabel('', '2026-09-24')).toBe('')
    expect(monthLabel('九月', '2026-09-24')).toBe('')
  })

  it('翻月份会跨年，也不会翻出 13 月', () => {
    expect(shiftMonth('2026-09', 1)).toBe('2026-10')
    expect(shiftMonth('2026-09', -1)).toBe('2026-08')
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2027-01', -1)).toBe('2026-12')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(shiftMonth('', 1)).toBe('')
    expect(shiftMonth('九月', 1)).toBe('')
  })

  it('格子里四态各写各的，休不被写成空、已调整不跟休同色', () => {
    expect(monthCell(MORNING)).toEqual({ text: '白班', tone: 'shift' })
    expect(monthCell(REST)).toEqual({ text: '休', tone: 'rest' })
    expect(monthCell(ORPHAN)).toEqual({ text: '已调整', tone: 'moved' })
    expect(monthCell(NOT_ROSTERED)).toEqual({ text: '', tone: 'none' })
    expect(monthCell(null)).toEqual({ text: '', tone: 'none' })
    // 空格子（还没排）跟「休」不许长得一样。
    expect(monthCell(NOT_ROSTERED).tone).not.toBe(monthCell(REST).tone)
    expect(monthCell(NOT_ROSTERED).text).not.toBe(monthCell(REST).text)
  })

  it('格子里的语气就是「今天」页那张表的语气，不是另抄一份', () => {
    // 五态只在 `shiftTone` 里判一次：这条钉住 `monthCell` 是它的翻译，不是第二张表。
    for (const day of [MORNING, REST, LEAVE, ORPHAN, NOT_ROSTERED, null]) {
      expect(monthCell(day).tone).toBe(shiftTone(day))
    }
  })

  it('往后翻到展开窗口末日就停，往前不设底', () => {
    // 验收 2：能翻上个月、下个月；窗口末 (`window_end`) 那一步之后不再往后。
    expect(stepMonth('2026-09', -1, '2026-12-22')).toBe('2026-08')
    expect(stepMonth('2026-12', -1, '2026-12-22')).toBe('2026-11')
    expect(stepMonth('2026-09', 1, '2026-12-22')).toBe('2026-10')
    expect(stepMonth('2026-12', 1, '2026-12-22')).toBeNull() // 末日所在的月：再往后就是空月
    expect(stepMonth('2027-01', 1, '2026-12-22')).toBeNull()
    // 往前没有底（早于装机日期的月份本来就是空的）。
    expect(stepMonth('2026-01', -1, '2026-12-22')).toBe('2025-12')
    // 服务端没说窗口末（老版本/坏数据）时不设限，别把翻月整条堵死。
    expect(stepMonth('2030-01', 1, '')).toBe('2030-02')
    expect(stepMonth('', 1, '2026-12-22')).toBeNull()
  })

  it('认得出窗口外面的那些格子（淡掉，不是「那天没排」）', () => {
    expect(dayBeyondWindow({ business_date: '2026-12-23' }, '2026-12-22')).toBe(true)
    expect(dayBeyondWindow({ business_date: '2026-12-22' }, '2026-12-22')).toBe(false)
    expect(dayBeyondWindow({ business_date: '2026-12-01' }, '2026-12-22')).toBe(false)
    expect(dayBeyondWindow({ business_date: '2026-12-23' }, '')).toBe(false)
    expect(dayBeyondWindow(null, '2026-12-22')).toBe(false)
  })
})
