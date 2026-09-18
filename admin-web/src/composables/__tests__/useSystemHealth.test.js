import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
  },
}))

const { useSystemHealth, formatUptime, formatMb } = await import('../useSystemHealth.js')

function readyPayload(overrides = {}) {
  return {
    status: 'healthy',
    ready: true,
    startup_id: 'startup-abc',
    started_at: '2026-09-19T03:00:00+08:00',
    db_connected: true,
    migrations_complete: true,
    key_tables_readable: true,
    details: ['数据层可用'],
    version: '0.6.0',
    ...overrides,
  }
}

function probePayload(overrides = {}) {
  return { status: 'ok', db: 'healthy', disk: { level: 'ok', free_mb: 2048.5 }, ...overrides }
}

function processPayload(overrides = {}) {
  return {
    status: 'running',
    uptime: 90061,
    version: '0.6.0',
    database: { orders: { count: 10 }, tables: { count: 2 }, dish_stations: { count: 3 } },
    memory: {
      pressure_level: 'normal',
      last_cleanup: null,
      current_usage: { rss_mb: 123.456 },
    },
    ...overrides,
  }
}

function scraperPayload(overrides = {}) {
  return {
    success: true,
    health: {
      biz_date: '2026-09-19',
      api_failures: 0,
      last_scrape_at: '2026-09-19T03:00:00+08:00',
      last_reconcile: { at: '2026-09-19T02:00:00+08:00', missed_qty: 0, miss_rate_pct: 0 },
      reconcile_running: false,
      ...overrides,
    },
  }
}

function reconcilePayload(overrides = {}) {
  return { success: true, progress: { running: false, stage: null, stage_label: '', current: 0, total: 0 }, ...overrides }
}

function mockRoutes(map) {
  apiGet.mockImplementation((path) => {
    const value = map[path]
    if (value === undefined) return Promise.reject(new Error(`unexpected path ${path}`))
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value)
  })
}

function happyRoutes(overrides = {}) {
  return {
    '/api/system/health': readyPayload(),
    '/api/healthz': probePayload(),
    '/api/system/status': processPayload(),
    '/api/system/scraper-health': scraperPayload(),
    '/api/admin/reconcile-status': reconcilePayload(),
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
})

describe('useSystemHealth 采集', () => {
  it('并行拉取全部只读健康接口并给出健康结论', async () => {
    mockRoutes(happyRoutes())
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(apiGet.mock.calls.map(([p]) => p).sort()).toEqual(
      [
        '/api/admin/reconcile-status',
        '/api/healthz',
        '/api/system/health',
        '/api/system/scraper-health',
        '/api/system/status',
      ],
    )
    expect(h.sysHealthLoading.value).toBe(false)
    expect(h.sysHealthError.value).toBe('')
    expect(h.sysHealthOverallLabel.value).toBe('健康')
    expect(h.sysHealthOverallPillClass.value).toBe('ok')
    expect(h.sysHealthUptimeLabel.value).toBe('1 天 1 小时 1 分')
    expect(h.sysHealthRssLabel.value).toBe('123.5 MB')
    expect(h.sysHealthDiskFreeLabel.value).toBe('2.00 GB')
    expect(h.sysHealthCounts.value).toHaveLength(3)
    expect(h.sysHealthReadyChecks.value.every((c) => c.ok)).toBe(true)
    expect(h.sysHealthReadyLabel.value).toBe('健康')
    expect(h.sysHealthProbeDbLabel.value).toBe('可读')
    expect(h.sysHealthProbeStatusLabel.value).toBe('正常')
  })

  it('单个接口失败时保留其余分块并汇总失败来源', async () => {
    mockRoutes(happyRoutes({ '/api/system/scraper-health': new Error('采集健康不可用') }))
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthScraperHealth.value).toBeNull()
    expect(h.sysHealthError.value).toContain('采集健康不可用')
    // 其余分块仍然可用
    expect(h.sysHealthReady.value.ready).toBe(true)
    expect(h.sysHealthOverallLabel.value).toBe('健康')
  })

  it('/api/healthz 返回 503 时仍读取 body 里的 db 与磁盘水位', async () => {
    const probeError = Object.assign(new Error('请求失败 (503)'), {
      status: 503,
      data: probePayload({ status: 'degraded', db: 'error: 连接失败', disk: { level: 'critical', free_mb: 12.3 } }),
    })
    mockRoutes(happyRoutes({ '/api/healthz': probeError }))
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthProbeDb.value).toBe('error: 连接失败')
    expect(h.sysHealthDiskLevel.value).toBe('critical')
    expect(h.sysHealthDiskLabel.value).toBe('严重不足')
    expect(h.sysHealthDiskFreeLabel.value).toBe('12.3 MB')
    // 503 的 body 是有效数据，不算「部分检查不可用」
    expect(h.sysHealthError.value).toBe('')
    expect(h.sysHealthOverallLabel.value).toBe('异常')
  })
})

describe('useSystemHealth 结论推导', () => {
  it('未就绪判定为异常', async () => {
    mockRoutes(happyRoutes({ '/api/system/health': readyPayload({ status: 'unhealthy', ready: false, db_connected: false }) }))
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthOverallLabel.value).toBe('异常')
    expect(h.sysHealthOverallPillClass.value).toBe('error')
    expect(h.sysHealthReadyChecks.value.find((c) => c.key === 'db_connected').ok).toBe(false)
  })

  it('内存压力偏高判定为注意', async () => {
    mockRoutes(happyRoutes({ '/api/system/status': processPayload({ memory: { pressure_level: 'high', current_usage: { rss_mb: 900 } } }) }))
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthMemoryPressureLabel.value).toBe('高')
    expect(h.sysHealthOverallLabel.value).toBe('注意')
    expect(h.sysHealthOverallPillClass.value).toBe('empty')
  })

  it('磁盘 warning 判定为注意', async () => {
    mockRoutes(happyRoutes({ '/api/healthz': probePayload({ disk: { level: 'warning', free_mb: 300 } }) }))
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthDiskLabel.value).toBe('偏低')
    expect(h.sysHealthOverallLabel.value).toBe('注意')
  })

  it('对账进行中时给出进度文案', async () => {
    mockRoutes(
      happyRoutes({
        '/api/admin/reconcile-status': reconcilePayload({
          progress: { running: true, stage: 'fetching', stage_label: '拉取订单', current: 3, total: 10 },
        }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthReconcileRunning.value).toBe(true)
    expect(h.sysHealthReconcileProgressLabel.value).toBe('拉取订单 3/10')
  })
})

describe('useSystemHealth 图表派生', () => {
  const realDisk = {
    level: 'ok',
    threshold_free_mb: 2048,
    worst: {
      path: '/srv/luyun/data',
      total_mb: 471482.1,
      used_mb: 360969.6,
      free_mb: 110512.5,
      used_pct: 76.6,
      level: 'ok',
    },
    paths: [
      { path: '/srv/luyun/data', total_mb: 471482.1, used_mb: 360969.6, free_mb: 110512.5, used_pct: 76.6, level: 'ok' },
    ],
  }

  it('磁盘明细优先取 /api/system/status，与探针的 free_mb 合并成一张容量图', async () => {
    mockRoutes(
      happyRoutes({
        '/api/healthz': probePayload({ disk: { level: 'ok', free_mb: 110512.5 } }),
        '/api/system/status': processPayload({ disk: realDisk }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthDisk.value).toMatchObject({
      level: 'ok',
      total_mb: 471482.1,
      used_mb: 360969.6,
      free_mb: 110512.5,
      used_pct: 76.6,
      threshold_free_mb: 2048,
      path: '/srv/luyun/data',
    })
    expect(h.sysHealthDiskFreeLabel.value).toBe('107.92 GB')
    expect(h.sysHealthDiskGauge.value.mode).toBe('capacity')
    expect(h.sysHealthDiskGauge.value.pct).toBeCloseTo(76.6, 3)
    expect(h.sysHealthDiskGauge.value.thresholdText).toBe('空闲门槛 2.00 GB')
    expect(h.sysHealthDiskGauge.value.ariaLabel).toContain('空闲门槛 2.00 GB')
  })

  it('worst 缺 used_pct 时用 used/total 推算，探针没有总量时退化为空闲量表', async () => {
    mockRoutes(
      happyRoutes({
        '/api/system/status': processPayload({
          disk: { level: 'warning', threshold_free_mb: 2048, worst: { total_mb: 1000, used_mb: 750, free_mb: 250 } },
        }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthDisk.value.used_pct).toBeCloseTo(75, 3)
    expect(h.sysHealthDiskGauge.value.mode).toBe('capacity')

    // 只有探针（无总量）时退化为空闲量表
    mockRoutes(happyRoutes({ '/api/healthz': probePayload({ disk: { level: 'ok', free_mb: 4096 } }) }))
    const probeOnly = useSystemHealth({ clearAlert: vi.fn() })
    await probeOnly.loadSysHealth()

    expect(probeOnly.sysHealthDiskGauge.value.mode).toBe('free')
    expect(probeOnly.sysHealthDiskGauge.value.valueText).toBe('空闲 4.00 GB')
  })

  it('内存量表带三档阈值，数据量条按最大值等比并给千分位数字', async () => {
    mockRoutes(
      happyRoutes({
        '/api/system/status': processPayload({
          database: { orders: { count: 182345 }, tables: { count: 21 }, dish_stations: { count: 7 } },
          memory: {
            pressure_level: 'normal',
            peak_memory_mb: 780.1,
            cleanups: 2,
            current_usage: { rss_mb: 412.3 },
            thresholds: { warning_mb: 1536, cleanup_mb: 2048, critical_mb: 2560 },
          },
        }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthMemoryGauge.value.zones).toHaveLength(4)
    expect(h.sysHealthMemoryGauge.value.peak.label).toBe('峰值 780.1 MB')
    expect(h.sysHealthMemoryGauge.value.thresholdText).toContain('2.50 GB')
    expect(h.sysHealthCounts.value.map((c) => c.display)).toEqual(['182,345', '21', '7'])
    expect(h.sysHealthCounts.value[0].pct).toBe(100)
    expect(h.sysHealthCounts.value[1].pct).toBeLessThan(0.02)
  })

  it('失败量表在缺门槛时给出数量与「未知」，就绪检查带符号', async () => {
    mockRoutes(
      happyRoutes({
        '/api/system/scraper-health': scraperPayload({ api_failures: 3 }),
        '/api/system/health': readyPayload({ db_connected: false }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthFailureGauge.value.valueText).toBe('当前 3 次')
    expect(h.sysHealthFailureGauge.value.level).toBe('warning')
    expect(h.sysHealthFailureGauge.value.thresholdText).toBe('告警门槛未知')
    expect(h.sysHealthReadyChecks.value.find((c) => c.key === 'db_connected')).toMatchObject({
      symbol: '✗',
      statusText: '未通过',
    })
    expect(h.sysHealthReadyHasFailure.value).toBe(true)
  })

  it('对账接口不可用但 scraper-health 说在跑时，按运行中（总量未知）显示', async () => {
    mockRoutes(
      happyRoutes({
        '/api/admin/reconcile-status': new Error('对账状态不可用'),
        '/api/system/scraper-health': scraperPayload({ reconcile_running: true }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthReconcileRunning.value).toBe(true)
    expect(h.sysHealthReconcileProgress.value.running).toBe(true)
    expect(h.sysHealthReconcileProgress.value.indeterminate).toBe(true)
  })

  it('对账接口明确说空闲时，不因 scraper-health 的旧标记而误报在跑', async () => {
    mockRoutes(
      happyRoutes({ '/api/system/scraper-health': scraperPayload({ reconcile_running: true }) }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    expect(h.sysHealthReconcileRunning.value).toBe(false)
    expect(h.sysHealthReconcileProgress.value.message).toBe('当前没有对账任务在跑')
  })

  it('原始指标分组保留峰值内存、GC、清理时间、分区与采集时间', async () => {
    mockRoutes(
      happyRoutes({
        '/api/system/status': processPayload({
          disk: realDisk,
          memory: {
            pressure_level: 'normal',
            peak_memory_mb: 780.1,
            cleanup_count: 4,
            gc_collections: 12,
            last_cleanup: '2026-09-19T04:00:00+08:00',
            current_usage: { rss_mb: 412.3 },
          },
        }),
        '/api/system/scraper-health': scraperPayload({
          delivery_bills_pending: 2,
          updated_at: '2026-09-19T05:00:00+08:00',
        }),
      }),
    )
    const h = useSystemHealth({ clearAlert: vi.fn() })

    await h.loadSysHealth()

    const groups = Object.fromEntries(h.sysHealthRawFacts.value.map((g) => [g.key, g.items]))
    expect(groups.process).toEqual(
      expect.arrayContaining([
        { k: '峰值内存', v: '780.1 MB' },
        { k: 'GC 次数', v: '12' },
        { k: '内存清理次数', v: '4' },
        { k: '上次内存清理', v: '2026-09-19 04:00:00' },
      ]),
    )
    expect(groups['disk-paths'][0].k).toBe('/srv/luyun/data')
    expect(groups['disk-paths'][0].v).toContain('空闲 107.92 GB')
    expect(groups.scraper).toEqual(
      expect.arrayContaining([
        { k: '待结配送单', v: '2' },
        { k: '状态更新时间', v: '2026-09-19 05:00:00' },
      ]),
    )
    expect(groups.counts).toEqual(
      expect.arrayContaining([
        { k: '订单', v: '10' },
        { k: '桌台', v: '2' },
      ]),
    )
  })
})

describe('useSystemHealth 格式化', () => {
  it('formatUptime 覆盖秒/分/时/天与非法值', () => {
    expect(formatUptime(45)).toBe('45 秒')
    expect(formatUptime(125)).toBe('2 分 5 秒')
    expect(formatUptime(3660)).toBe('1 小时 1 分')
    expect(formatUptime(90061)).toBe('1 天 1 小时 1 分')
    expect(formatUptime(null)).toBe('—')
    expect(formatUptime(-1)).toBe('—')
  })

  it('formatMb 在超过 1024MB 时换算为 GB', () => {
    expect(formatMb(512.5)).toBe('512.5 MB')
    expect(formatMb(2048)).toBe('2.00 GB')
    expect(formatMb(undefined)).toBe('—')
  })
})
