import { computed, ref } from 'vue'
import { api } from '../api/client'
import { formatTs } from '../utils/backupProgress'
import { formatCount, formatMb, formatUptime } from '../utils/systemHealthFormat'
import {
  buildCountBars,
  buildDiskGauge,
  buildFailureGauge,
  buildMemoryGauge,
  buildReadinessItems,
  buildReconcileProgress,
} from '../utils/systemHealthCharts'

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

function num(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function text(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

// 格式化实现搬到了 utils/systemHealthFormat.js（图表派生也要用），此处保留同名导出避免调用方改动。
export { formatCount, formatMb, formatUptime }

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
  // 磁盘明细优先取 /api/system/status（有总量与门槛），探针只给 {level, free_mb} 时退化为空闲量表。
  const sysHealthDisk = computed(() => {
    const proc = sysHealthProcess.value?.disk
    const probe = sysHealthProbe.value?.disk
    if (!proc && !probe) return null
    const worst = proc?.worst || {}
    const free = num(worst.free_mb) ?? num(probe?.free_mb)
    const total = num(worst.total_mb)
    const used = num(worst.used_mb)
    let usedPct = num(worst.used_pct)
    if (usedPct === null && total !== null && total > 0 && used !== null) usedPct = (used / total) * 100
    return {
      level: worst.level || proc?.level || probe?.level || 'unknown',
      free_mb: free,
      total_mb: total,
      used_mb: used,
      used_pct: usedPct,
      threshold_free_mb: num(proc?.threshold_free_mb),
      path: worst.path || '',
      paths: Array.isArray(proc?.paths) ? proc.paths : [],
    }
  })
  const sysHealthDiskLevel = computed(() => sysHealthDisk.value?.level || 'unknown')
  const sysHealthDiskFreeLabel = computed(() => formatMb(sysHealthDisk.value?.free_mb))
  const sysHealthDiskGauge = computed(() => buildDiskGauge(sysHealthDisk.value))

  const sysHealthReadyChecks = computed(() => buildReadinessItems(sysHealthReady.value))
  const sysHealthReadyDetails = computed(() => sysHealthReady.value?.details || [])
  // 有未通过项时把 details 原文摊开展示，全通过时折进「全部检查详情」。
  const sysHealthReadyHasFailure = computed(() => sysHealthReadyChecks.value.some((c) => !c.ok))

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

  const sysHealthMemoryGauge = computed(() => buildMemoryGauge(sysHealthMemory.value))

  const sysHealthCounts = computed(() => {
    const db = sysHealthProcess.value?.database
    if (!db || db.error) return []
    return buildCountBars([
      { key: 'orders', label: '订单', value: num(db.orders?.count) },
      { key: 'tables', label: '桌台', value: num(db.tables?.count) },
      { key: 'dish_stations', label: '菜品档口映射', value: num(db.dish_stations?.count) },
    ])
  })

  const sysHealthScraperHealth = computed(() => sysHealthScraper.value?.health || null)
  const sysHealthFailureGauge = computed(() => buildFailureGauge(sysHealthScraperHealth.value))
  const sysHealthReconcileRunning = computed(
    () => !!(sysHealthReconcile.value?.progress?.running ?? sysHealthScraperHealth.value?.reconcile_running),
  )
  const sysHealthReconcileProgress = computed(() =>
    buildReconcileProgress(sysHealthReconcile.value?.progress, sysHealthReconcileRunning.value),
  )
  const sysHealthReconcileProgressLabel = computed(() =>
    sysHealthReconcileProgress.value.running ? sysHealthReconcileProgress.value.stageText : '',
  )
  const sysHealthDiskLabel = computed(
    () => DISK_LEVEL_LABELS[sysHealthDiskLevel.value] || sysHealthDiskLevel.value,
  )

  // 「详情」折叠里的原始指标：图表只保留结论，明细（峰值/GC/清理次数/时间戳/全部分区）在这里一条不丢。
  const sysHealthRawFacts = computed(() => {
    const proc = sysHealthProcess.value
    const mem = sysHealthMemory.value
    const thresholds = mem?.thresholds || {}
    const ready = sysHealthReady.value
    const probe = sysHealthProbe.value
    const scraper = sysHealthScraperHealth.value
    const reconcile = sysHealthScraperHealth.value?.last_reconcile
    const groups = []

    if (ready) {
      groups.push({
        key: 'ready',
        title: '运行时就绪',
        items: [
          { k: '状态', v: sysHealthReadyLabel.value },
          { k: '版本', v: text(ready.version) },
          { k: '启动时间', v: formatTs(ready.started_at) || '—' },
          { k: '启动标识', v: text(ready.startup_id) },
          { k: '时间戳', v: formatTs(ready.timestamp) || '—' },
        ],
      })
    }
    if (probe) {
      groups.push({
        key: 'probe',
        title: '访问探针',
        items: [
          { k: '探针结论', v: sysHealthProbeStatusLabel.value },
          { k: '数据库', v: sysHealthProbeDbLabel.value },
          { k: '磁盘水位', v: sysHealthDiskLabel.value },
          { k: '剩余空间', v: formatMb(probe.disk?.free_mb) },
        ],
      })
    }
    if (proc || mem) {
      groups.push({
        key: 'process',
        title: '进程与资源',
        items: [
          { k: '运行时长', v: sysHealthUptimeLabel.value },
          { k: '当前内存', v: sysHealthRssLabel.value },
          { k: '峰值内存', v: formatMb(mem?.peak_memory_mb) },
          { k: '内存压力', v: sysHealthMemoryPressureLabel.value },
          { k: '清理阈值', v: `${formatMb(thresholds.warning_mb)} / ${formatMb(thresholds.cleanup_mb)} / ${formatMb(thresholds.critical_mb)}` },
          { k: '上次内存清理', v: formatTs(mem?.last_cleanup) || '—' },
          { k: '内存清理次数', v: formatCount(num(mem?.cleanup_count)) },
          { k: 'GC 次数', v: formatCount(num(mem?.gc_collections)) },
        ],
      })
    }
    if (sysHealthCounts.value.length) {
      groups.push({
        key: 'counts',
        title: '数据量',
        items: sysHealthCounts.value.map((c) => ({ k: c.label, v: c.display })),
      })
    }
    if (scraper) {
      groups.push({
        key: 'scraper',
        title: '采集与对账',
        items: [
          { k: '营业日', v: text(scraper.biz_date) },
          { k: 'API 失败数', v: formatCount(num(scraper.api_failures)) },
          { k: '失败告警门槛', v: formatCount(num(scraper.api_failures_threshold)) },
          { k: '待结配送单', v: formatCount(num(scraper.delivery_bills_pending)) },
          { k: '最后采集', v: formatTs(scraper.last_scrape_at) || '—' },
          { k: '最后对账', v: formatTs(reconcile?.at) || '—' },
          { k: '对账状态', v: sysHealthReconcileRunning.value ? '进行中' : '空闲' },
          { k: '漏单数量', v: formatCount(num(reconcile?.missed_qty)) },
          { k: '漏单率', v: typeof reconcile?.miss_rate_pct === 'number' ? `${reconcile.miss_rate_pct}%` : '—' },
          { k: '状态更新时间', v: formatTs(scraper.updated_at) || '—' },
        ],
      })
    }
    if (sysHealthDisk.value?.paths.length) {
      groups.push({
        key: 'disk-paths',
        title: '磁盘分区',
        items: sysHealthDisk.value.paths.map((p) => ({
          k: text(p.path),
          v: `已用 ${typeof p.used_pct === 'number' ? `${p.used_pct}%` : '—'} · 空闲 ${formatMb(p.free_mb)} / 共 ${formatMb(p.total_mb)} · ${DISK_LEVEL_LABELS[p.level] || p.level || '未知'}`,
        })),
      })
    }
    if (scraper?.last_reconcile?.report_md) {
      groups.push({
        key: 'reconcile-report',
        title: '对账报告',
        items: [{ k: '报告文件', v: String(scraper.last_reconcile.report_md) }],
      })
    }
    return groups
  })

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
    sysHealthDisk,
    sysHealthDiskLevel,
    sysHealthDiskLabel,
    sysHealthDiskFreeLabel,
    sysHealthDiskGauge,
    sysHealthReadyChecks,
    sysHealthReadyDetails,
    sysHealthReadyHasFailure,
    sysHealthOverall,
    sysHealthOverallLabel,
    sysHealthOverallPillClass,
    sysHealthUptimeLabel,
    sysHealthVersion,
    sysHealthMemory,
    sysHealthMemoryGauge,
    sysHealthRssLabel,
    sysHealthMemoryPressureLabel,
    sysHealthLastCleanup,
    sysHealthCounts,
    sysHealthScraperHealth,
    sysHealthFailureGauge,
    sysHealthReconcileProgress,
    sysHealthReconcileRunning,
    sysHealthReconcileProgressLabel,
    sysHealthRawFacts,
  }
}
