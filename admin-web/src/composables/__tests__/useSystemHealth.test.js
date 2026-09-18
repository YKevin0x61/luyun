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
