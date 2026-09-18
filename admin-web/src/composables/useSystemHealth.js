import { computed, ref } from 'vue'
import { api } from '../api/client'

// 磁盘 / 内存水位到中文结论的映射，供状态 pill 与总体结论复用。
const DISK_LEVEL_LABELS = { ok: '充足', warning: '偏低', critical: '严重不足' }
const PRESSURE_LABELS = { normal: '正常', warning: '偏高', high: '高', critical: '严重', unknown: '未知' }
const OVERALL_LABELS = { ok: '健康', warning: '注意', critical: '异常', unknown: '未知' }
// level -> status-pill 的类名（ok 绿 / empty 黄 / error 红）。
const OVERALL_PILL_CLASSES = { ok: 'ok', warning: 'empty', critical: 'error', unknown: 'empty' }

const LEVEL_ORDER = { ok: 0, warning: 1, critical: 2 }

function worse(a, b) {
  return (LEVEL_ORDER[b] ?? 0) > (LEVEL_ORDER[a] ?? 0) ? b : a
}

export function formatUptime(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d} 天 ${h} 小时 ${m} 分`
  if (h > 0) return `${h} 小时 ${m} 分`
  if (m > 0) return `${m} 分 ${s} 秒`
  return `${s} 秒`
}

export function formatMb(mb) {
  if (typeof mb !== 'number' || !Number.isFinite(mb)) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`
}

/** /api/healthz 未就绪时返回 503（状态码非 2xx），但 db/disk 只在 body 里。 */
function readResult(settled, { acceptErrorBody = false } = {}) {
  if (settled.status === 'fulfilled') return { value: settled.value, error: '' }
  const err = settled.reason
  if (acceptErrorBody && err?.data && typeof err.data === 'object') {
    return { value: err.data, error: '' }
  }
  return { value: null, error: err?.message || '加载失败' }
}

/**
 * Setup page —「系统健康状态」面板：只读聚合运行时就绪、访问探针、进程资源与采集健康。
 *
 * 数据源全部是既有只读接口；单个接口失败不影响其余分块展示，
 * 只把失败来源汇总成一行提示。
 */
export function useSystemHealth({ clearAlert } = {}) {
  const sysHealthLoading = ref(false)
  const sysHealthError = ref('')
  const sysHealthReady = ref(null) // /api/system/health（就绪）
  const sysHealthProbe = ref(null) // /api/healthz（db + 磁盘）
  const sysHealthProcess = ref(null) // /api/system/status（uptime / 内存 / 数据量）
  const sysHealthScraper = ref(null) // /api/system/scraper-health
  const sysHealthReconcile = ref(null) // /api/admin/reconcile-status

  async function loadSysHealth() {
    clearAlert?.()
    sysHealthLoading.value = true
    sysHealthError.value = ''
    const [readyRes, probeRes, processRes, scraperRes, reconcileRes] = await Promise.allSettled([
      api.get('/api/system/health'),
      api.get('/api/healthz'),
      api.get('/api/system/status'),
      api.get('/api/system/scraper-health'),
      api.get('/api/admin/reconcile-status'),
    ])

    const failed = []
    const ready = readResult(readyRes)
    // 探针：503 也带 payload，视为可用数据。
    const probe = readResult(probeRes, { acceptErrorBody: true })
    const proc = readResult(processRes)
    const scraper = readResult(scraperRes)
    const reconcile = readResult(reconcileRes)

    sysHealthReady.value = ready.value
    sysHealthProbe.value = probe.value
    sysHealthProcess.value = proc.value
    sysHealthScraper.value = scraper.value
    sysHealthReconcile.value = reconcile.value

    if (ready.error) failed.push(`运行时就绪（${ready.error}）`)
    if (probe.error) failed.push(`访问探针（${probe.error}）`)
    if (proc.error) failed.push(`进程状态（${proc.error}）`)
    if (scraper.error) failed.push(`采集健康（${scraper.error}）`)
    if (reconcile.error) failed.push(`对账状态（${reconcile.error}）`)
    sysHealthError.value = failed.join('；')

    sysHealthLoading.value = false
  }

  const sysHealthProbeDb = computed(() => sysHealthProbe.value?.db || '—')
  const sysHealthProbeDbLabel = computed(() => {
    const db = sysHealthProbe.value?.db
    if (db === 'healthy') return '可读'
    if (db === 'uninitialized') return '未初始化'
    return db || '—'
  })
  const sysHealthProbeStatusLabel = computed(
    () => ({ ok: '正常', degraded: '降级' }[sysHealthProbe.value?.status] || sysHealthProbe.value?.status || '—'),
  )
  const sysHealthReadyLabel = computed(
    () => ({ healthy: '健康', unhealthy: '未就绪' }[sysHealthReady.value?.status] || sysHealthReady.value?.status || '—'),
  )
  const sysHealthDiskLevel = computed(() => sysHealthProbe.value?.disk?.level || 'unknown')
  const sysHealthDiskFreeLabel = computed(() => {
    const free = sysHealthProbe.value?.disk?.free_mb
    return typeof free === 'number' ? formatMb(free) : '—'
  })

  const sysHealthReadyChecks = computed(() => {
    const r = sysHealthReady.value
    if (!r) return []
    return [
      { key: 'ready', label: '整体就绪', ok: !!r.ready },
      { key: 'db_connected', label: '数据库连接', ok: !!r.db_connected },
      { key: 'migrations_complete', label: '数据库迁移', ok: !!r.migrations_complete },
      { key: 'key_tables_readable', label: '关键表可读', ok: !!r.key_tables_readable },
    ]
  })
  const sysHealthReadyDetails = computed(() => sysHealthReady.value?.details || [])

  // 总体结论：就绪 -> 探针 -> 磁盘 -> 内存，取最坏的一项。
  const sysHealthOverall = computed(() => {
    if (!sysHealthReady.value) return 'unknown'
    let level = sysHealthReady.value.ready ? 'ok' : 'critical'
    if (sysHealthProbe.value && sysHealthProbe.value.status !== 'ok') {
      level = worse(level, sysHealthProbe.value.status === 'degraded' ? 'warning' : 'critical')
    }
    const disk = sysHealthProbe.value?.disk?.level
    if (disk === 'critical') level = worse(level, 'critical')
    else if (disk && disk !== 'ok') level = worse(level, 'warning')
    const pressure = sysHealthProcess.value?.memory?.pressure_level
    if (pressure === 'critical') level = worse(level, 'critical')
    else if (pressure && pressure !== 'normal' && pressure !== 'unknown') level = worse(level, 'warning')
    return level
  })
  const sysHealthOverallLabel = computed(() => OVERALL_LABELS[sysHealthOverall.value] || '未知')
  const sysHealthOverallPillClass = computed(() => OVERALL_PILL_CLASSES[sysHealthOverall.value] || 'empty')

  const sysHealthUptimeLabel = computed(() => formatUptime(sysHealthProcess.value?.uptime))
  const sysHealthVersion = computed(() => sysHealthProcess.value?.version || sysHealthReady.value?.version || '—')

  const sysHealthMemory = computed(() => sysHealthProcess.value?.memory || null)
  const sysHealthRssLabel = computed(() => {
    const usage = sysHealthMemory.value?.current_usage
    return typeof usage?.rss_mb === 'number' ? formatMb(usage.rss_mb) : '—'
  })
  const sysHealthMemoryPressure = computed(() => sysHealthMemory.value?.pressure_level || 'unknown')
  const sysHealthMemoryPressureLabel = computed(
    () => PRESSURE_LABELS[sysHealthMemoryPressure.value] || sysHealthMemoryPressure.value,
  )
  const sysHealthLastCleanup = computed(() => sysHealthMemory.value?.last_cleanup || null)

  const sysHealthCounts = computed(() => {
    const db = sysHealthProcess.value?.database
    if (!db || db.error) return []
    const toValue = (v) => (typeof v === 'number' ? v : '—')
    return [
      { key: 'orders', label: '订单', value: toValue(db.orders?.count) },
      { key: 'tables', label: '桌台', value: toValue(db.tables?.count) },
      { key: 'dish_stations', label: '菜品档口映射', value: toValue(db.dish_stations?.count) },
    ]
  })

  const sysHealthScraperHealth = computed(() => sysHealthScraper.value?.health || null)
  const sysHealthReconcileRunning = computed(
    () => !!(sysHealthReconcile.value?.progress?.running ?? sysHealthScraperHealth.value?.reconcile_running),
  )
  const sysHealthReconcileProgressLabel = computed(() => {
    const p = sysHealthReconcile.value?.progress
    if (!p?.running) return ''
    const stage = p.stage_label || p.stage || '进行中'
    return p.total > 0 ? `${stage} ${p.current || 0}/${p.total}` : stage
  })
  const sysHealthDiskLabel = computed(
    () => DISK_LEVEL_LABELS[sysHealthDiskLevel.value] || sysHealthDiskLevel.value,
  )

  return {
    sysHealthLoading,
    sysHealthError,
    sysHealthReady,
    sysHealthProbe,
    sysHealthProcess,
    sysHealthScraper,
    sysHealthReconcile,
    loadSysHealth,
    sysHealthProbeDb,
    sysHealthProbeDbLabel,
    sysHealthProbeStatusLabel,
    sysHealthReadyLabel,
    sysHealthDiskLevel,
    sysHealthDiskLabel,
    sysHealthDiskFreeLabel,
    sysHealthReadyChecks,
    sysHealthReadyDetails,
    sysHealthOverall,
    sysHealthOverallLabel,
    sysHealthOverallPillClass,
    sysHealthUptimeLabel,
    sysHealthVersion,
    sysHealthMemory,
    sysHealthRssLabel,
    sysHealthMemoryPressureLabel,
    sysHealthLastCleanup,
    sysHealthCounts,
    sysHealthScraperHealth,
    sysHealthReconcileRunning,
    sysHealthReconcileProgressLabel,
  }
}
