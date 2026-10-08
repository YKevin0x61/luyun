import { describe, expect, it } from 'vitest'
import {
  canConfirm,
  confirmPayload,
  currentText,
  gapText,
  isDue,
  missingText,
  monthText,
  reminderCounts,
  stateText,
} from '../seniorityReminder.js'

// 「人事提醒」页（票 05）的展示口径。
// 折算月 / 档位 / 差额都是**服务端算好的**（`services/identity/profile.py` 是唯一实现），
// 这里只把服务端那几个字段翻成人话 —— 所以断言对着接口形状
//（`{id, name, hire_date, current, should_be, gap, state, due_month, next_adjust_month}`）写。

const DUE = {
  id: 1,
  name: '张三',
  hire_date: '2023-09-15',
  current: null,
  should_be: 300,
  gap: 200,
  state: 'due',
  due_month: '2026-09',
  next_adjust_month: '2027-09',
}
const OVER = {
  id: 2,
  name: '李四',
  hire_date: '2025-09-15',
  current: 500,
  should_be: 100,
  gap: -400,
  state: 'over',
  due_month: '2026-09',
  next_adjust_month: '2027-09',
}

describe('人事提醒的展示口径', () => {
  it('没调过显示「未调」，不是 0 元', () => {
    expect(currentText(DUE)).toBe('未调')
    expect(currentText({ ...DUE, current: 100 })).toBe('100 元')
  })

  it('该调的才给「确认调整」，调过头的不给 —— 一键把 500 改成 100 就是自动下调', () => {
    expect(isDue(DUE)).toBe(true)
    expect(canConfirm(DUE)).toBe(true)
    expect(canConfirm(OVER)).toBe(false)
  })

  it('确认调整写入的是服务端给的应为值', () => {
    expect(confirmPayload(DUE)).toEqual({ seniority_bonus: 300 })
    expect(confirmPayload(OVER)).toEqual({ seniority_bonus: 100 })
  })

  it('差额按方向说人话，不带符号', () => {
    expect(gapText(DUE)).toBe('差 200 元')
    expect(gapText(OVER)).toBe('高出 400 元')
  })

  it('状态词只有两种', () => {
    expect(stateText(DUE)).toBe('该调')
    expect(stateText(OVER)).toBe('调过头')
  })

  it('下次调整月翻成年月，空值回空串', () => {
    expect(monthText('2027-09')).toBe('2027 年 9 月')
    expect(monthText(null)).toBe('')
    expect(monthText('')).toBe('')
  })

  it('三块条数各数各的：due 才是要处理的', () => {
    const counts = reminderCounts({
      seniority: { items: [DUE, OVER], count: 1 },
      incomplete: { items: [{ id: 3, missing: ['hire_date'] }] },
    })
    expect(counts).toEqual({ due: 1, over: 1, incomplete: 1 })
  })

  it('读不到数据时不炸', () => {
    expect(reminderCounts(null)).toEqual({ due: 0, over: 0, incomplete: 0 })
    expect(gapText(null)).toBe('')
  })

  // 票 06：待补那一块从「只缺入职日期」扩成两项，文案跟着 `missing` 走。
  it('待补缺哪几项就说哪几项，两项都缺就说两项', () => {
    expect(missingText(['hire_date'])).toBe('缺入职日期')
    expect(missingText(['id_card_no'])).toBe('缺身份证')
    expect(missingText(['hire_date', 'id_card_no'])).toBe('缺入职日期、缺身份证')
    expect(missingText([])).toBe('')
    expect(missingText(undefined)).toBe('')
  })
})
