import { formatCount, formatMb, formatPct } from './systemHealthFormat.js'

// 「系统健康状态」页的图表派生：把接口字段换算成 0-100 的百分比位置、区间带与无障碍文案。
// 组件只负责把这里给出的数字画出来，口径与文案全部集中在此，便于在 node 环境直接单测。

const DISK_LEVEL_LABELS = { ok: '充足', warning: '偏低', critical: '严重不足', unknown: '未知' }
const DISK_LEVEL_HINTS = { ok: '正常', warning: '偏低', critical: '严重不足', unknown: '未获取' }
const PRESSURE_LABELS = { normal: '正常', warning: '偏高', high: '高', critical: '严重', unknown: '未知' }
const PRESSURE_LEVELS = { normal: 'ok', warning: 'warning', high: 'warning', critical: 'critical', unknown: 'unknown' }
const FAILURE_LEVEL_LABELS = { ok: '正常', warning: '有失败', critical: '超阈值', unknown: '未获取' }

// 就绪检查固定四项，顺序与后端字段一致。
export const READINESS_CHECKS = [
  { key: 'ready', label: '整体就绪' },
  { key: 'db_connected', label: '数据库连接' },
  { key: 'migrations_complete', label: '数据库迁移' },
  { key: 'key_tables_readable', label: '关键表可读' },
]

export const RECONCILE_IDLE_TEXT = '当前没有对账任务在跑'

function num(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function clampPct(value) {
  const v = num(value)
  if (v === null) return 0
  return Math.min(100, Math.max(0, v))
}

/** value 占 max 的百分比（夹在 0-100）；max 非法或 <=0 时返回 0。 */
export function pctOf(value, max) {
  const v = num(value)
  const m = num(max)
  if (v === null || m === null || m <= 0) return 0
  return clampPct((v / m) * 100)
}

// 阈值刻度线贴边时渲染不出来，留 0.5% 余量；精确值始终由文字给出。
function markerPosition(pct) {
  return Math.min(99.5, Math.max(0.5, clampPct(pct)))
}

function zone(left, right, level, label) {
  const l = clampPct(left)
  const r = clampPct(right)
  return { left: l, width: Math.max(0, r - l), level, label }
}

function joinParts(parts) {
  return parts.filter(Boolean).join(' · ')
}

/**
 * 磁盘水位量表。
 *
 * /api/healthz 只给 {level, free_mb}，/api/system/status 才带总量与门槛，
 * 因此两种形态都支持：
 * - 有总量：按「容量条」画，填充=已用比例，警戒线落在「空闲刚好等于门槛」的位置；
 * - 只有空闲：退化成「空闲 vs 门槛」量表，填充=空闲，门槛是下限线。
 */
export function buildDiskGauge(disk) {
  const d = disk || {}
  const free = num(d.free_mb)
  const total = num(d.total_mb)
  const used = num(d.used_mb)
  const threshold = num(d.threshold_free_mb)
  const level = DISK_LEVEL_LABELS[d.level] ? d.level : 'unknown'
  const levelLabel = DISK_LEVEL_LABELS[level]
  const capacityMode = total !== null && total > 0

  let usedPct = num(d.used_pct)
  if (usedPct === null && capacityMode && used !== null) usedPct = (used / total) * 100

  if (!capacityMode && free === null) {
    return {
      missing: true,
      mode: 'free',
      level: 'unknown',
      levelLabel: DISK_LEVEL_LABELS.unknown,
      pct: 0,
      markerPct: null,
      zones: [],
      valueText: '—',
      thresholdText: '数据未获取',
      caption: '',
      ariaLabel: '磁盘水位：数据未获取',
    }
  }

  if (capacityMode) {
    // 渲染位置贴边时钳位，aria 里仍给未钳位的真实百分比。
    const markerTrue = threshold === null ? null : pctOf(total - threshold, total)
    const markerPct = markerTrue === null ? null : markerPosition(markerTrue)
    const zones = markerPct === null ? [] : [zone(markerPct, 100, 'critical', '空闲低于门槛')]
    const aria =
      `磁盘水位：已用 ${formatPct(usedPct)}（${formatMb(used)} / ${formatMb(total)}），` +
      `空闲 ${formatMb(free)}，空闲门槛 ${formatMb(threshold)}，判定${DISK_LEVEL_HINTS[level]}` +
      (markerTrue === null ? '' : `；警戒线在用量 ${formatPct(markerTrue)} 处，越过即空闲低于门槛`)
    return {
      missing: false,
      mode: 'capacity',
      level,
      levelLabel,
      pct: usedPct === null ? 0 : clampPct(usedPct),
      markerPct,
      zones,
      valueText: `已用 ${formatPct(usedPct)}`,
      thresholdText: threshold === null ? '空闲门槛未知' : `空闲门槛 ${formatMb(threshold)}`,
      caption: joinParts([
        `已用 ${formatPct(usedPct)}`,
        `${formatMb(used)} / ${formatMb(total)}`,
        free === null ? '' : `空闲 ${formatMb(free)}`,
        threshold === null ? '' : `门槛 ${formatMb(threshold)}`,
      ]),
      ariaLabel: aria,
    }
  }

  // 只有 free_mb：以「空闲」为量的量表，刻度取空闲的 1.2 倍与门槛的 4 倍中较大者，保证门槛线可见。
  const scaleMax = Math.max(free * 1.2, (threshold ?? 0) * 4, 1)
  const markerPct = threshold === null ? null : markerPosition(pctOf(threshold, scaleMax))
  const zones = markerPct === null ? [] : [zone(0, markerPct, 'critical', '空闲低于门槛')]
  return {
    missing: false,
    mode: 'free',
    level,
    levelLabel,
    pct: pctOf(free, scaleMax),
    markerPct,
    zones,
    valueText: `空闲 ${formatMb(free)}`,
    thresholdText: threshold === null ? '门槛未知' : `门槛 ${formatMb(threshold)}`,
    caption: joinParts([
      `空闲 ${formatMb(free)}`,
      threshold === null ? '' : `门槛 ${formatMb(threshold)}`,
      '总量未上报',
    ]),
    ariaLabel:
      `磁盘空闲：${formatMb(free)}，门槛 ${formatMb(threshold)}，判定${DISK_LEVEL_HINTS[level]}` +
      `；本次未取到磁盘总量，量表按空闲量相对刻度绘制`,
  }
}

/** 内存用量量表：当前 RSS vs 三档阈值，并标出峰值位置。 */
export function buildMemoryGauge(memory) {
  const m = memory || {}
  const rss = num(m.current_usage?.rss_mb)
  const peak = num(m.peak_memory_mb)
  const th = m.thresholds || {}
  const warning = num(th.warning_mb)
  const cleanup = num(th.cleanup_mb)
  const critical = num(th.critical_mb)
  const pressure = PRESSURE_LABELS[m.pressure_level] ? m.pressure_level : 'unknown'
  const pressureLabel = PRESSURE_LABELS[pressure]
  const hasThreshold = warning !== null || cleanup !== null || critical !== null
  const scaleMax = Math.max(warning ?? 0, cleanup ?? 0, critical ?? 0, rss ?? 0, peak ?? 0) * 1.1 || 1

  const zones = hasThreshold
    ? [
        zone(0, pctOf(warning ?? 0, scaleMax), 'ok', '正常'),
        zone(pctOf(warning ?? 0, scaleMax), pctOf(cleanup ?? scaleMax, scaleMax), 'warning', '偏高'),
        zone(pctOf(cleanup ?? scaleMax, scaleMax), pctOf(critical ?? scaleMax, scaleMax), 'warning', '高'),
        zone(pctOf(critical ?? scaleMax, scaleMax), 100, 'critical', '严重'),
      ]
    : [zone(0, 100, 'unknown', '阈值未上报')]

  let level = PRESSURE_LEVELS[pressure] || 'unknown'
  if (level === 'unknown' && rss !== null && hasThreshold) {
    if (critical !== null && rss >= critical) level = 'critical'
    else if (cleanup !== null && rss >= cleanup) level = 'warning'
    else if (warning !== null && rss >= warning) level = 'warning'
    else level = 'ok'
  }

  return {
    missing: rss === null && !hasThreshold,
    level,
    levelLabel: pressureLabel,
    pct: pctOf(rss, scaleMax),
    markerPct: null,
    zones,
    peak: peak === null ? null : { pct: pctOf(peak, scaleMax), label: `峰值 ${formatMb(peak)}` },
    valueText: `当前 ${formatMb(rss)}`,
    thresholdText: hasThreshold
      ? `阈值 偏高 ${formatMb(warning)} / 清理 ${formatMb(cleanup)} / 严重 ${formatMb(critical)}`
      : '阈值未上报',
    caption: joinParts([`压力 ${pressureLabel}`, hasThreshold ? '' : '阈值未上报']),
    ariaLabel:
      `内存用量：当前 ${formatMb(rss)}，历史峰值 ${formatMb(peak)}，压力判定${pressureLabel}；` +
      (hasThreshold
        ? `三档阈值：偏高起点 ${formatMb(warning)}、触发清理 ${formatMb(cleanup)}、严重 ${formatMb(critical)}`
        : '后端未上报内存阈值'),
  }
}

/** 采集 API 失败次数量表：次数 vs 告警门槛。 */
export function buildFailureGauge(health) {
  const h = health || {}
  const failures = num(h.api_failures)
  const threshold = num(h.api_failures_threshold)
  const scaleMax = Math.max(failures ?? 0, threshold ?? 0, 1) * 1.25
  const thresholdPct = threshold === null ? null : pctOf(threshold, scaleMax)

  let level = 'unknown'
  if (failures !== null) {
    if (threshold !== null && failures >= threshold) level = 'critical'
    else if (failures > 0) level = 'warning'
    else level = 'ok'
  }

  const zones =
    thresholdPct === null
      ? []
      : [
          zone(0, thresholdPct, 'ok', '门槛内'),
          zone(thresholdPct, 100, 'critical', '超门槛'),
        ]

  return {
    missing: failures === null,
    level,
    levelLabel: FAILURE_LEVEL_LABELS[level],
    pct: pctOf(failures, scaleMax),
    markerPct: thresholdPct === null ? null : markerPosition(thresholdPct),
    zones,
    valueText: failures === null ? '当前 —' : `当前 ${formatCount(failures)} 次`,
    thresholdText: threshold === null ? '告警门槛未知' : `告警门槛 ${formatCount(threshold)} 次`,
    caption:
      failures === null
        ? '失败次数未上报'
        : failures >= (threshold ?? Number.POSITIVE_INFINITY)
          ? '已超门槛，请检查采集日志'
          : '',
    ariaLabel:
      failures === null
        ? '采集 API 失败次数：数据未获取'
        : `采集 API 失败次数：当前 ${formatCount(failures)} 次，告警门槛 ${formatCount(threshold)} 次，` +
          `判定${FAILURE_LEVEL_LABELS[level]}`,
  }
}

/** 数据量横向条：按最大值等比，同时给出真实数字（小值靠文字兜底，不靠条宽）。 */
export function buildCountBars(counts) {
  const list = Array.isArray(counts) ? counts : []
  const values = list.map((c) => num(c.value)).filter((v) => v !== null)
  const max = values.length ? Math.max(...values, 1) : 1
  return list.map((c) => {
    const value = num(c.value)
    const pct = value === null ? 0 : pctOf(value, max)
    return {
      key: c.key,
      label: c.label,
      value,
      display: value === null ? '—' : formatCount(value),
      pct,
      max,
      ariaLabel:
        value === null
          ? `${c.label}：数量未获取`
          : `${c.label}：${formatCount(value)}，条长按最大值 ${formatCount(max)} 等比，占 ${formatPct(pct, pct < 1 ? 2 : 1)}`,
    }
  })
}

/** 就绪检查四项：✓ / ! / ✗ + 文字，未取到数据时用 ! 而不是假装通过。 */
export function buildReadinessItems(ready) {
  if (!ready) {
    return READINESS_CHECKS.map((c) => ({
      ...c,
      ok: false,
      level: 'unknown',
      symbol: '!',
      statusText: '未获取',
    }))
  }
  return READINESS_CHECKS.map((c) => {
    const ok = !!ready[c.key]
    return {
      ...c,
      ok,
      level: ok ? 'ok' : 'critical',
      symbol: ok ? '✓' : '✗',
      statusText: ok ? '通过' : '未通过',
    }
  })
}

/** 对账进度：运行中给 stage + current/total 百分比，空闲时明确说明没有任务在跑。 */
export function buildReconcileProgress(progress, runningOverride) {
  const p = progress || {}
  const running = typeof runningOverride === 'boolean' ? runningOverride : !!p.running
  const total = num(p.total) ?? 0
  const current = num(p.current) ?? 0
  const stage = p.stage_label || p.stage || ''

  if (!running) {
    return {
      running: false,
      indeterminate: false,
      pct: 0,
      current,
      total,
      stageLabel: '',
      stageText: '',
      message: RECONCILE_IDLE_TEXT,
      ariaLabel: `对账进度：${RECONCILE_IDLE_TEXT}`,
    }
  }

  if (total <= 0) {
    const stageLabel = stage || '进行中'
    return {
      running: true,
      indeterminate: true,
      pct: 0,
      current,
      total,
      stageLabel,
      stageText: stageLabel,
      message: `${stageLabel}（总量未知）`,
      ariaLabel: `对账进行中：${stageLabel}，总量未知`,
    }
  }

  const pct = clampPct((current / total) * 100)
  const stageLabel = stage || '进行中'
  return {
    running: true,
    indeterminate: false,
    pct,
    current,
    total,
    stageLabel,
    stageText: `${stage ? `${stage} ` : ''}${current}/${total}`,
    message: `${stageLabel} ${current}/${total}（${Math.round(pct)}%）`,
    ariaLabel: `对账进度：${stageLabel}，已完成 ${current} / ${total}，${Math.round(pct)}%`,
  }
}
