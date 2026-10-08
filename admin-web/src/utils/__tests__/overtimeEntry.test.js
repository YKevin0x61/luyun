import { describe, expect, it } from 'vitest'
import {
  HALF_HOURS_MAX,
  entryDayOptions,
  entryStatusText,
  entryStatusTone,
  formatHalfHours,
  stepHalfHours,
} from '../overtimeEntry.js'

// 加班与补钟（票 01）的界面口径：**1 格 = 0.5 小时**，库里存的就是「半小时数」
// （`half_hours`）—— 所以前端这一层只跟整数打交道，不做小时与分钟的换算，
// 也就不存在浮点误差（`docs/adr/0100`）。
//
// 界面上的两个按钮是 `+` / `−`，没有「选加班还是补钟」那一栏：符号就是类型，
// 从 `+1` 一直按 `−` 会走到 `-1`（**跳过 0**）—— 0 不是一笔登记，服务端会拒它。

describe('加班与补钟的长度与符号（票 01）', () => {
  it('每次加减 1 格（0.5 小时），跳过 0', () => {
    expect(stepHalfHours(1, 1)).toBe(2)
    expect(stepHalfHours(1, -1)).toBe(-1) // 不减到 0：0 不是一笔登记
    expect(stepHalfHours(-1, 1)).toBe(1)
    expect(stepHalfHours(-4, -1)).toBe(-5)
  })

  it('上下限是 ±12 小时（±24 格），到顶不再动', () => {
    expect(HALF_HOURS_MAX).toBe(24)
    expect(stepHalfHours(HALF_HOURS_MAX, 1)).toBe(HALF_HOURS_MAX)
    expect(stepHalfHours(-HALF_HOURS_MAX, -1)).toBe(-HALF_HOURS_MAX)
    expect(stepHalfHours(HALF_HOURS_MAX - 1, 1)).toBe(HALF_HOURS_MAX)
  })

  it('带符号显示：加班带 +、补钟带 −，整点不带小数', () => {
    expect(formatHalfHours(13)).toBe('+6.5')
    expect(formatHalfHours(1)).toBe('+0.5')
    expect(formatHalfHours(4)).toBe('+2')
    expect(formatHalfHours(-4)).toBe('−2')
    expect(formatHalfHours(-3)).toBe('−1.5')
  })

  it('日期只能是今天与昨天（自然日，服务端说了算，这里只是把那两颗按钮摆出来）', () => {
    expect(entryDayOptions({ today: '2026-09-24', yesterday: '2026-09-23' })).toEqual([
      { value: '2026-09-24', label: '今天' },
      { value: '2026-09-23', label: '昨天' },
    ])
  })

  it('五种状态各有中文与色调，认不出的状态不装懂', () => {
    expect(entryStatusText('pending')).toBe('待审批')
    expect(entryStatusText('approved')).toBe('已批准')
    expect(entryStatusText('rejected')).toBe('已驳回')
    expect(entryStatusText('cancelled')).toBe('已撤回')
    expect(entryStatusText('voided')).toBe('已作废')
    expect(entryStatusTone('pending')).toBe('wait')
    expect(entryStatusTone('approved')).toBe('ok')
    expect(entryStatusTone('rejected')).toBe('bad')
    expect(entryStatusText('whatever')).toBe('whatever')
  })
})
