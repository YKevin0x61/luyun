import { describe, expect, it } from 'vitest'
import {
  READINESS_CHECKS,
  RECONCILE_IDLE_TEXT,
  buildCountBars,
  buildDiskGauge,
  buildFailureGauge,
  buildMemoryGauge,
  buildReadinessItems,
  buildReconcileProgress,
  clampPct,
  pctOf,
} from '../systemHealthCharts.js'

// 本机实测样例，保证图表口径贴合真实数据量级。
const realDisk = {
  level: 'ok',
  threshold_free_mb: 2048,
  total_mb: 471482.1,
  used_mb: 360969.6,
  free_mb: 110512.5,
  used_pct: 76.6,
  path: '/srv/luyun/data',
}
const realMemory = {
  current_usage: { rss_mb: 412.3 },
  peak_memory_mb: 780.1,
  pressure_level: 'normal',
  thresholds: { warning_mb: 1536, cleanup_mb: 2048, critical_mb: 2560 },
}

describe('systemHealthCharts 基础换算', () => {
  it('clampPct / pctOf 处理非法值与越界', () => {
    expect(clampPct(-10)).toBe(0)
    expect(clampPct(120)).toBe(100)
    expect(clampPct('x')).toBe(0)
    expect(pctOf(50, 200)).toBe(25)
    expect(pctOf(50, 0)).toBe(0)
    expect(pctOf(null, 10)).toBe(0)
  })
})

describe('buildDiskGauge', () => {
  it('有总量时按容量条绘制，警戒线落在空闲刚好等于门槛处', () => {
    const g = buildDiskGauge(realDisk)

    expect(g.missing).toBe(false)
    expect(g.mode).toBe('capacity')
    expect(g.level).toBe('ok')
    expect(g.levelLabel).toBe('充足')
    expect(g.pct).toBeCloseTo(76.6, 3)
    // (471482.1 - 2048) / 471482.1 ≈ 99.57%，贴边时钳到 99.5 但文字仍给精确值
    expect(g.markerPct).toBe(99.5)
    expect(g.zones).toHaveLength(1)
    expect(g.thresholdText).toBe('空闲门槛 2.00 GB')
    expect(g.caption).toContain('已用 76.6%')
    expect(g.caption).toContain('门槛 2.00 GB')
    expect(g.caption).toContain('空闲 107.92 GB')
    // 阈值与数值都在 aria 文案里，不依赖颜色
    expect(g.ariaLabel).toContain('空闲门槛 2.00 GB')
    expect(g.ariaLabel).toContain('已用 76.6%')
  })

  it('只有 free_mb 时退化为空闲量表，门槛线可见', () => {
    const g = buildDiskGauge({ level: 'warning', free_mb: 2048.5, threshold_free_mb: 2048 })

    expect(g.mode).toBe('free')
    expect(g.levelLabel).toBe('偏低')
    expect(g.pct).toBeGreaterThan(20)
    expect(g.markerPct).toBeCloseTo(25, 3)
    expect(g.valueText).toBe('空闲 2.00 GB')
    expect(g.caption).toContain('总量未上报')
    expect(g.ariaLabel).toContain('本次未取到磁盘总量')
  })

  it('进度条缺字段时不报错也不假装有数据', () => {
    const g = buildDiskGauge({ level: 'ok', free_mb: 1024 })

    expect(g.markerPct).toBeNull()
    expect(g.thresholdText).toBe('门槛未知')
    expect(g.zones).toEqual([])
  })

  it('完全没有数据时标记 missing', () => {
    const g = buildDiskGauge(null)

    expect(g.missing).toBe(true)
    expect(g.pct).toBe(0)
    expect(g.valueText).toBe('—')
    expect(g.ariaLabel).toBe('磁盘水位：数据未获取')
  })
})

describe('buildMemoryGauge', () => {
  it('画出三档阈值区间带并标出峰值位置', () => {
    const g = buildMemoryGauge(realMemory)

    expect(g.missing).toBe(false)
    expect(g.level).toBe('ok')
    expect(g.levelLabel).toBe('正常')
    expect(g.pct).toBeCloseTo((412.3 / 2816) * 100, 2)
    expect(g.peak.pct).toBeCloseTo((780.1 / 2816) * 100, 2)
    expect(g.peak.label).toBe('峰值 780.1 MB')
    expect(g.zones).toHaveLength(4)
    expect(g.zones.map((z) => z.label)).toEqual(['正常', '偏高', '高', '严重'])
    expect(g.zones[1].left).toBeCloseTo((1536 / 2816) * 100, 2)
    expect(g.zones[3].left).toBeCloseTo((2560 / 2816) * 100, 2)
    expect(g.thresholdText).toContain('1.50 GB')
    expect(g.thresholdText).toContain('2.50 GB')
    expect(g.ariaLabel).toContain('历史峰值 780.1 MB')
  })

  it('压力等级 critical 直接判严重', () => {
    const g = buildMemoryGauge({ ...realMemory, pressure_level: 'critical' })

    expect(g.level).toBe('critical')
    expect(g.levelLabel).toBe('严重')
  })

  it('缺阈值时用未知区间而不是画成正常', () => {
    const g = buildMemoryGauge({ current_usage: { rss_mb: 100 }, pressure_level: 'unknown' })

    expect(g.zones).toEqual([{ left: 0, width: 100, level: 'unknown', label: '阈值未上报' }])
    expect(g.thresholdText).toBe('阈值未上报')
    expect(g.level).toBe('unknown')
  })

  it('完全没有数据时标记 missing', () => {
    expect(buildMemoryGauge(null).missing).toBe(true)
  })
})

describe('buildFailureGauge', () => {
  it('零失败判正常，达到门槛判超阈值', () => {
    const ok = buildFailureGauge({ api_failures: 0, api_failures_threshold: 5 })
    expect(ok.level).toBe('ok')
    expect(ok.pct).toBe(0)
    expect(ok.markerPct).toBe(80)
    expect(ok.valueText).toBe('当前 0 次')
    expect(ok.thresholdText).toBe('告警门槛 5 次')

    const over = buildFailureGauge({ api_failures: 6, api_failures_threshold: 5 })
    expect(over.level).toBe('critical')
    expect(over.levelLabel).toBe('超阈值')
    expect(over.caption).toContain('已超门槛')
  })

  it('有失败但未超门槛判注意（文字给出等级，不靠颜色）', () => {
    const warn = buildFailureGauge({ api_failures: 2, api_failures_threshold: 5 })

    expect(warn.level).toBe('warning')
    expect(warn.levelLabel).toBe('有失败')
    expect(warn.zones).toHaveLength(2)
  })

  it('缺门槛时仍给出失败数量', () => {
    const g = buildFailureGauge({ api_failures: 3 })

    expect(g.markerPct).toBeNull()
    expect(g.thresholdText).toBe('告警门槛未知')
    expect(g.valueText).toBe('当前 3 次')
  })

  it('数据缺失时标记 missing', () => {
    const g = buildFailureGauge(null)

    expect(g.missing).toBe(true)
    expect(g.level).toBe('unknown')
    expect(g.ariaLabel).toBe('采集 API 失败次数：数据未获取')
  })
})

describe('buildCountBars', () => {
  it('按最大值等比，同时给出千分位真实数字', () => {
    const bars = buildCountBars([
      { key: 'orders', label: '订单', value: 182345 },
      { key: 'tables', label: '桌台', value: 21 },
      { key: 'dish_stations', label: '菜品档口映射', value: 7 },
    ])

    expect(bars[0].pct).toBe(100)
    expect(bars[0].display).toBe('182,345')
    expect(bars[0].max).toBe(182345)
    // 小值条宽接近 0，但数字文字兜底，aria 也读得出
    expect(bars[1].pct).toBeLessThan(0.02)
    expect(bars[1].display).toBe('21')
    expect(bars[2].ariaLabel).toContain('7')
    expect(bars[2].ariaLabel).toContain('182,345')
  })

  it('数量缺失时给 0 宽与占位文字', () => {
    const bars = buildCountBars([{ key: 'orders', label: '订单', value: null }])

    expect(bars[0].pct).toBe(0)
    expect(bars[0].display).toBe('—')
    expect(bars[0].ariaLabel).toBe('订单：数量未获取')
  })
})

describe('buildReadinessItems', () => {
  it('通过给 ✓/通过，未通过给 ✗/未通过', () => {
    const items = buildReadinessItems({
      ready: true,
      db_connected: false,
      migrations_complete: true,
      key_tables_readable: true,
    })

    expect(items.map((i) => i.key)).toEqual(READINESS_CHECKS.map((c) => c.key))
    expect(items[0]).toMatchObject({ symbol: '✓', statusText: '通过', level: 'ok' })
    expect(items[1]).toMatchObject({ symbol: '✗', statusText: '未通过', level: 'critical' })
  })

  it('没取到数据时用 ! 表示未知，而不是假装通过', () => {
    const items = buildReadinessItems(null)

    expect(items).toHaveLength(4)
    expect(items.every((i) => i.symbol === '!' && i.level === 'unknown' && i.statusText === '未获取')).toBe(true)
  })
})

describe('buildReconcileProgress', () => {
  it('运行中给 stage 与 current/total 百分比', () => {
    const p = buildReconcileProgress({ running: true, stage_label: '拉取订单', current: 3, total: 10 })

    expect(p.running).toBe(true)
    expect(p.indeterminate).toBe(false)
    expect(p.pct).toBe(30)
    expect(p.stageText).toBe('拉取订单 3/10')
    expect(p.message).toBe('拉取订单 3/10（30%）')
    expect(p.ariaLabel).toContain('30%')
  })

  it('运行中但总量未知时不画成 0%', () => {
    const p = buildReconcileProgress({ running: true, stage_label: '初始化', current: 0, total: 0 })

    expect(p.indeterminate).toBe(true)
    expect(p.message).toBe('初始化（总量未知）')
    expect(p.pct).toBe(0)
  })

  it('未运行时明确说明没有任务在跑', () => {
    const p = buildReconcileProgress({ running: false })

    expect(p.running).toBe(false)
    expect(p.message).toBe(RECONCILE_IDLE_TEXT)
    expect(p.stageText).toBe('')
  })

  it('scraper-health 说在跑时按运行中处理（即使进度接口没数据）', () => {
    const p = buildReconcileProgress({ running: false }, true)

    expect(p.running).toBe(true)
    expect(p.indeterminate).toBe(true)
  })
})
