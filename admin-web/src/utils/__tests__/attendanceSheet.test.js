/**
 * 考勤表的说法（考勤表导出票 01）。
 *
 * 三种说法都不许在这份文件以外再写一遍：服务端下发的是 `work` / `rest` / `todo` /
 * `null` 这四种**状态码**，翻成人话只在这一处；卡片上那两项（是否新入职、净时长）
 * 同理 —— 「算不出来」与「是否否」长得一样是这一页最容易犯的错。
 */
import { describe, expect, it } from 'vitest'

import {
  dayStateText,
  formatDays,
  formatNetHours,
  leaveFormulaText,
  newHireText,
  pendingOvertimeText,
} from '../attendanceSheet.js'

describe('逐日三态（验收 3/4）', () => {
  it('上班 / 休 / 待补各说各话', () => {
    expect(dayStateText('work')).toBe('上班')
    expect(dayStateText('rest')).toBe('休')
    expect(dayStateText('todo')).toBe('待补')
  })

  it('今天之后的日期留空 —— 不是「待补」', () => {
    // 服务端给的是 `null`：那些日子还没发生，不该被当成「要人工确认」。
    expect(dayStateText(null)).toBe('')
    expect(dayStateText(undefined)).toBe('')
  })
})

describe('卡片上那两项（验收 6/7）', () => {
  it('是否新入职：算不出来（入职日期为空）就留空，不是「否」', () => {
    expect(newHireText(true)).toBe('是')
    expect(newHireText(false)).toBe('否')
    expect(newHireText(null)).toBe('')
    expect(newHireText(undefined)).toBe('')
  })

  it('净时长带符号，可为负、可带 .5', () => {
    expect(formatNetHours(2)).toBe('+2 小时')
    expect(formatNetHours(0.5)).toBe('+0.5 小时')
    expect(formatNetHours(-1.5)).toBe('−1.5 小时')
    expect(formatNetHours(0)).toBe('0 小时')
  })
})

describe('假期台账那一块的写法（票 03）', () => {
  it('天数：正数不带符号、负数用全角减号、.5 如实', () => {
    expect(formatDays(12)).toBe('12 天')
    expect(formatDays(19.5)).toBe('19.5 天')
    expect(formatDays(0)).toBe('0 天')
    // 负数是**欠班**，不是脏数据 —— 纸面上必须与攒下的假分得开。
    expect(formatDays(-10.5)).toBe('−10.5 天')
    expect(formatDays(null)).toBe('0 天')
  })

  it('算式把六项摊开，再说清池与存假', () => {
    const leave = {
      actual_days: 12,
      monthly_rest: 2,
      holiday_makeup: 1,
      birthday_leave: 0,
      annual_leave: 3,
      previous_balance: 5.5,
      pool: 23.5,
      month_days: 30,
      balance: -6.5,
    }
    expect(leaveFormulaText(leave)).toBe(
      '实际出勤 12 天 + 月例休 2 天 + 法定节假日补休 1 天 + 生日假 0 天 + ' +
        '年假 3 天 + 上月存假 5.5 天 = 可用 23.5 天；本月 30 天，存假 −6.5 天',
    )
  })

  it('没有 leave 时算式是空的（老载荷不编数字）', () => {
    expect(leaveFormulaText(null)).toBe('')
  })

  it('未审批的加班：有待批的才报数（0 笔不留痕迹）', () => {
    expect(pendingOvertimeText(2, 1)).toBe('待批 +1 小时')
    expect(pendingOvertimeText(1, -1.5)).toBe('待批 −1.5 小时')
    expect(pendingOvertimeText(0, 0)).toBe('')
    expect(pendingOvertimeText(undefined, undefined)).toBe('')
  })
})
