import { describe, expect, it } from 'vitest'

import {
  MAX_HOLIDAY_NAME,
  WEEKDAYS,
  holidayMap,
  monthLabel,
  nameError,
  yearGrid,
} from '../holidayCalendar'

/** 一年一个网格（票 02）：12 个月 × 周一到周日 7 列。期望值来自 `cal`（系统日历）
 *  的手工核对，不是按同一套算式再算一遍。 */
describe('节假日日历的年份网格', () => {
  const days = (months) => months.flatMap((month) => month.weeks.flat()).filter(Boolean)

  it('一年 12 个月、每月 7 列，日子首尾正好是 1 月 1 日与 12 月 31 日', () => {
    const months = yearGrid(2026)

    expect(months.map((month) => month.month)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    ])
    for (const month of months) {
      for (const week of month.weeks) expect(week).toHaveLength(7)
    }
    const all = days(months)
    expect(all).toHaveLength(365) // 2026 不是闰年
    expect(all[0]).toBe('2026-01-01')
    expect(all[all.length - 1]).toBe('2026-12-31')
  })

  it('闰年 2 月是 29 天（`cal 2 2024` 核过）', () => {
    const february = yearGrid(2024).find((month) => month.month === 2)
    expect(days([february])).toHaveLength(29)
    expect(days([february]).at(-1)).toBe('2024-02-29')
  })

  it('列从周一开始：2026-10-01（周四）落在第 4 格，前面三格是上月', () => {
    expect(WEEKDAYS).toEqual(['一', '二', '三', '四', '五', '六', '日'])

    const october = yearGrid(2026).find((month) => month.month === 10)
    expect(october.weeks[0]).toEqual([
      null,
      null,
      null,
      '2026-10-01',
      '2026-10-02',
      '2026-10-03',
      '2026-10-04',
    ])
    // 31 天 + 前面 3 格空 = 34 格 = 5 周（最后一格收在周六）。
    expect(october.weeks).toHaveLength(5)
    expect(october.weeks.at(-1)).toEqual([
      '2026-10-26',
      '2026-10-27',
      '2026-10-28',
      '2026-10-29',
      '2026-10-30',
      '2026-10-31',
      null,
    ])
  })

  it('2027-01-01（周五）落在第 5 格 —— 换一年也不会整体错位', () => {
    const january = yearGrid(2027).find((month) => month.month === 1)
    expect(january.weeks[0]).toEqual([
      null,
      null,
      null,
      null,
      '2027-01-01',
      '2027-01-02',
      '2027-01-03',
    ])
  })

  it('月份标签：1 月 … 12 月', () => {
    expect(yearGrid(2026).map((month) => month.label)).toEqual([
      '1 月', '2 月', '3 月', '4 月', '5 月', '6 月',
      '7 月', '8 月', '9 月', '10 月', '11 月', '12 月',
    ])
    expect(monthLabel(10)).toBe('10 月')
  })
})

describe('名称校验（与后端同一口径：1~20 字）', () => {
  it('空与纯空白被拒', () => {
    expect(nameError('')).not.toBe('')
    expect(nameError('   ')).not.toBe('')
    expect(nameError(null)).not.toBe('')
  })

  it('正好 20 个字放行，超一个字被拒', () => {
    expect(nameError('国'.repeat(MAX_HOLIDAY_NAME))).toBe('')
    expect(nameError('国'.repeat(MAX_HOLIDAY_NAME + 1))).not.toBe('')
  })

  it('两端的空白不算字数（20 个字加空格仍然放行）', () => {
    expect(nameError(`  ${'国'.repeat(MAX_HOLIDAY_NAME)} `)).toBe('')
    expect(nameError('  国庆 ')).toBe('')
  })
})

describe('接口返回 → 格子上的名称', () => {
  it('按日期索引，空列表就是空表（没有兜底的年度表）', () => {
    expect(holidayMap([])).toEqual({})
    expect(
      holidayMap([
        { day: '2026-10-01', name: '国庆' },
        { day: '2027-01-01', name: '元旦' },
      ]),
    ).toEqual({ '2026-10-01': '国庆', '2027-01-01': '元旦' })
  })
})
