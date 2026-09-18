import { describe, expect, it } from 'vitest'
import { formatCount, formatMb, formatPct, formatUptime } from '../systemHealthFormat.js'

describe('systemHealthFormat', () => {
  it('formatCount 给大整数加千分位，非法值给占位符', () => {
    expect(formatCount(182345)).toBe('182,345')
    expect(formatCount(7)).toBe('7')
    expect(formatCount(0)).toBe('0')
    expect(formatCount(1234567.4)).toBe('1,234,567')
    expect(formatCount(null)).toBe('—')
    expect(formatCount('x')).toBe('—')
  })

  it('formatPct 保留一位小数，非法值给占位符', () => {
    expect(formatPct(76.63)).toBe('76.6%')
    expect(formatPct(0)).toBe('0.0%')
    expect(formatPct(undefined)).toBe('—')
  })

  it('formatMb 超过 1024MB 换算 GB；formatUptime 覆盖各量级', () => {
    expect(formatMb(512.5)).toBe('512.5 MB')
    expect(formatMb(2048)).toBe('2.00 GB')
    expect(formatUptime(90061)).toBe('1 天 1 小时 1 分')
    expect(formatUptime(-1)).toBe('—')
  })
})
