// @vitest-environment node
import { describe, expect, it } from 'vitest'

import {
  mergeStationSpeedSeries,
  summarizeMergedSpeed,
  topMergedSpeedSlots,
} from '../stationSpeed'

// 两个档口 + 一条昨日对比线：对比线若被算进合计，明细会明显变大（9 vs 3），一眼能看出。
const RESULT = {
  slots: ['07:00', '07:05', '07:10', '07:15'],
  stations: ['a', 'b'],
  series: [
    { station_id: 'a', type: 'today', data: [2, 0, 3, 1] },
    { station_id: 'b', type: 'today', data: [1, 0, 4, 0] },
    { station_id: 'a', type: 'yesterday', data: [9, 9, 9, 9] },
  ],
}

describe('合计速率：逐槽位合并', () => {
  it('所有 today 系列逐点相加，昨日/上周/上月对比线不参与', () => {
    expect(mergeStationSpeedSeries(RESULT).data).toEqual([3, 0, 7, 1])
  })

  it('槽位标签原样带出（图与摘要共用同一组时间）', () => {
    expect(mergeStationSpeedSeries(RESULT).slots).toEqual(RESULT.slots)
  })

  it('边界：没有数据时返回空结构，不造出 169 个 0', () => {
    expect(mergeStationSpeedSeries(null)).toEqual({ slots: [], data: [] })
    expect(mergeStationSpeedSeries({ slots: [], series: [] })).toEqual({ slots: [], data: [] })
  })

  it('边界：某档口 data 比 slots 短，缺的槽位按 0 补齐', () => {
    const short = {
      slots: ['07:00', '07:05', '07:10'],
      series: [{ type: 'today', data: [4] }],
    }
    expect(mergeStationSpeedSeries(short).data).toEqual([4, 0, 0])
  })

  it('边界：只有对比线（无 today）时合计全 0', () => {
    const onlyCompare = {
      slots: ['07:00', '07:05'],
      series: [{ type: 'yesterday', data: [5, 6] }],
    }
    expect(mergeStationSpeedSeries(onlyCompare).data).toEqual([0, 0])
  })
})

describe('合计速率：对比日合并', () => {
  // 三个口径混在一起：按 type 精确取，串了就会一眼看出（yt 是 5 不是 3 也不是 7）。
  const COMPARE = {
    slots: ['07:00', '07:05', '07:10'],
    compare_dates: { yesterday: '2026-11-18', last_week: '2026-11-12' },
    series: [
      { station_id: 'a', type: 'today', data: [1, 1, 1] },
      { station_id: 'a', type: 'yesterday', data: [4, 0, 2] },
      { station_id: 'b', type: 'yesterday', data: [1, 3, 0] },
      { station_id: 'a', type: 'last_week', data: [7, 7, 7] },
    ],
  }

  it('按指定口径合并：yesterday 只加昨日系列，不混入 today / 上周', () => {
    expect(mergeStationSpeedSeries(COMPARE, 'yesterday').data).toEqual([5, 3, 2])
    expect(mergeStationSpeedSeries(COMPARE, 'last_week').data).toEqual([7, 7, 7])
  })

  it('默认口径仍是 today，摘要与峰值时段不受新增参数影响', () => {
    expect(mergeStationSpeedSeries(COMPARE).data).toEqual([1, 1, 1])
    expect(summarizeMergedSpeed(COMPARE).total).toBe(3)
  })

  it('边界：该口径没有任何系列时返回全 0（调用方据此跳过这条对比线）', () => {
    expect(mergeStationSpeedSeries(COMPARE, 'last_month').data).toEqual([0, 0, 0])
  })

  it('边界：对比日 data 比 slots 短时同样按 0 补齐', () => {
    const short = { slots: ['07:00', '07:05'], series: [{ type: 'yesterday', data: [6] }] }
    expect(mergeStationSpeedSeries(short, 'yesterday').data).toEqual([6, 0])
  })
})

describe('合计速率：摘要', () => {
  it('总单量、峰值、峰值时段、活跃时段均值', () => {
    const summary = summarizeMergedSpeed(RESULT)
    expect(summary.total).toBe(11)
    expect(summary.peak).toBe(7)
    expect(summary.peakTime).toBe('07:10')
    expect(summary.activeSlots).toBe(3)
    expect(summary.avgActive).toBeCloseTo(11 / 3, 5)
  })

  it('边界：全 0 时峰值时段不拿首槽位（07:00）冒充', () => {
    const zero = { slots: ['07:00', '07:05'], series: [{ type: 'today', data: [0, 0] }] }
    expect(summarizeMergedSpeed(zero)).toMatchObject({
      total: 0, peak: 0, peakTime: '—', avgActive: 0,
    })
  })

  it('边界：空数据不会算出 NaN', () => {
    const summary = summarizeMergedSpeed(null)
    expect(summary).toMatchObject({ total: 0, peak: 0, peakTime: '—', avgActive: 0 })
  })
})

describe('合计速率：峰值时段', () => {
  it('按单量降序，0 单的槽位不占位', () => {
    const rows = topMergedSpeedSlots(RESULT, 4)
    expect(rows.map((row) => [row.time, row.value])).toEqual([
      ['07:10', 7],
      ['07:00', 3],
      ['07:15', 1],
    ])
  })

  it('边界：同单量按时间先后排，不依赖 sort 的稳定性', () => {
    const tie = {
      slots: ['07:00', '07:05', '07:10'],
      series: [{ type: 'today', data: [2, 2, 2] }],
    }
    expect(topMergedSpeedSlots(tie, 2).map((row) => row.time)).toEqual(['07:00', '07:05'])
  })

  it('边界：limit 收口，空数据返回空数组', () => {
    expect(topMergedSpeedSlots(RESULT, 1)).toHaveLength(1)
    expect(topMergedSpeedSlots(null)).toEqual([])
  })
})
