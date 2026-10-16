/**
 * 考勤表的说法（考勤表导出票 01）。
 *
 * 三种说法都不许在这份文件以外再写一遍：服务端下发的是 `work` / `rest` / `todo` /
 * `null` 这四种**状态码**，翻成人话只在这一处；卡片上那两项（是否新入职、净时长）
 * 同理 —— 「算不出来」与「是否否」长得一样是这一页最容易犯的错。
 */
import { describe, expect, it } from 'vitest'

import { dayStateText, formatNetHours, newHireText } from '../attendanceSheet.js'

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
