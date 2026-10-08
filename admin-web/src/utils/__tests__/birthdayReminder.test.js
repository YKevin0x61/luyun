import { describe, expect, it } from 'vitest'
import { birthdayStateText, birthdayText, leapNoteText } from '../birthdayReminder.js'

// 「人事提醒」页生日那一块（票 06）的展示口径。
// 生日、这一年的生效日子、今天 / 已过 / 未到**全是服务端算好的**
//（`services/identity/profile.py` 的 `birthday_from_id_card` / `birthday_in_year`），
// 这里只把 `{birthday, on, day, state}` 翻成人话 —— 前端不自己解身份证、也不自己比日期。

describe('生日那一块的展示口径', () => {
  it('MM-DD 翻成「9 月 20 日」，坏值回空串', () => {
    expect(birthdayText('09-20')).toBe('9 月 20 日')
    expect(birthdayText('12-01')).toBe('12 月 1 日')
    expect(birthdayText('')).toBe('')
    expect(birthdayText(null)).toBe('')
    expect(birthdayText('2026-09-20')).toBe('')
  })

  it('今天 / 已过 / 未到说人话', () => {
    expect(birthdayStateText({ state: 'today' })).toBe('就是今天')
    expect(birthdayStateText({ state: 'past' })).toBe('已过')
    expect(birthdayStateText({ state: 'upcoming' })).toBe('还没到')
  })

  it('闰日在平年折到 28 日时说一句，别让人以为系统记错了', () => {
    expect(leapNoteText({ birthday: '02-29', on: '02-28' })).toBe('闰日，平年按 28 日提醒')
    expect(leapNoteText({ birthday: '02-29', on: '02-29' })).toBe('')
    expect(leapNoteText({ birthday: '03-07', on: '03-07' })).toBe('')
    expect(leapNoteText(null)).toBe('')
  })
})
