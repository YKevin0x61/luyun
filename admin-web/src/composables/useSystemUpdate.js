import { computed, getCurrentInstance, onUnmounted, ref } from 'vue'
import { api } from '../api/client'

const DEGRADED_REASON_LABELS = {
  missing_manifest: '本机缺少版本清单（非发行包安装）',
  invalid_manifest: '版本清单无效或无法解析',
  inconsistent: '本机版本清单与远端同 tag 发行版不一致',
  // Legacy git-era codes (preflight / older payloads).
  not_on_tag: '当前不在精确 git tag 上',
  dirty: '工作区有未提交改动',
  git_unavailable: '无法读取本机 git 状态',
}

export const STAGE_LABELS = {
  idle: '空闲',
  queued: '已排队',
  backing_up: '正在备份',
  fetching_bundle: '正在下载发行包',
  installing: '正在安装发行包',
  syncing_deps: '正在同步依赖',
  restarting: '已切换、重启中',
  succeeded: '已成功',
  succeeded_but_unhealthy: '已切换但未健康',
  failed: '已失败',
  // Legacy ADR 0010 stages (in-flight cutover / older payloads).
  fetching: '正在拉取代码',
  installing_assets: '正在安装前端资产',
}

const IN_PROGRESS = new Set([
  'queued',
  'backing_up',
  'fetching_bundle',
  'installing',
  'syncing_deps',
  'restarting',
  'fetching',
  'installing_assets',
])

/**
 * 需要页面持续做健康确认（带冷却自动重检）的阶段。
 *
 * 只有 `restarting`（已切换、重启中）需要自动重检：主服务还在恢复时页面应自己
 * 刷新，而不是让管理员狂点。`succeeded_but_unhealthy` 是终态——宽限期已过、
 * 结论已定，只保留手动「重新检测」与「回到上一版本」，不自动重试也不自动回滚。
 */
const HEALTH_PENDING = new Set(['restarting'])

const DEFAULT_POLL_INTERVAL_MS = 2000
const DEFAULT_RECHECK_INTERVAL_MS = 10_000
const RECHECK_TICK_MS = 1000

/** Human-readable label for Version Check degraded_reason codes. */
export function degradedReasonLabel(reason) {
  if (!reason) return ''
  return DEGRADED_REASON_LABELS[reason] || String(reason)
}

export function stageLabel(stage) {
  if (!stage) return ''
  return STAGE_LABELS[stage] || String(stage)
}

/**
 * Setup page —「系统更新」Version Check + Apply Update + job polling.
 *
 * 更新成功拆成两个事实：作业只负责「已切换发行包并发出重启」，健康确认由这里
 * 轮询 `/job` 与 `/job/health-check` 完成。`succeeded_but_unhealthy` 是终态之一，
 * 页面给出「看日志 / 重新检测 / 回到上一版本」三条出路，绝不自动重试或自动回滚。
 *
 * @param {{
 *   showAlert: Function,
 *   clearAlert: Function,
 *   pollIntervalMs?: number,
 *   recheckIntervalMs?: number,
 * }} opts
 */
export function useSystemUpdate({
  showAlert,
  clearAlert,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  recheckIntervalMs = DEFAULT_RECHECK_INTERVAL_MS,
}) {
  const versionCheck = ref(null)
  const versionLoading = ref(false)

  const githubConfig = ref(null)
  const githubLoading = ref(false)
  const githubSaving = ref(false)
  const githubForm = ref({
    token: '',
    clear_token: false,
  })

  const selectedTag = ref('')
  const confirmOpen = ref(false)
  const peakOverride = ref(false)
  const needsPeakOverride = ref(false)
  const discardLocalChanges = ref(false)
  const applying = ref(false)

  const job = ref(null)
  const jobLogTail = ref('')
  const jobPolling = ref(false)
  const cancelling = ref(false)
  let pollTimer = null

  /** 健康确认：手动重检的结果与状态。 */
  const healthChecking = ref(false)
  const healthCheckError = ref('')
  /** 冷却自动重检：只在 restarting / succeeded_but_unhealthy 时运行。 */
  const autoRecheckEnabled = ref(true)
  const recheckIn = ref(0)
  let recheckTimer = null
  let recheckCooldownUntil = 0

  /** 更新历史（旁路 JSON，默认 30 条，时间倒序）。 */
  const historyEntries = ref([])
  const historyLoading = ref(false)
  const historyError = ref('')

  const updateAvailable = computed(() => !!versionCheck.value?.update_available)
  const jobStageLabel = computed(() => stageLabel(job.value?.stage))
  const jobInProgress = computed(() => IN_PROGRESS.has(job.value?.stage))
  const canCancelJob = computed(
    () =>
      jobInProgress.value
      && !cancelling.value
      && !applying.value
      && !job.value?.cancel_requested,
  )

  /** 已切换发行包并发出重启，但健康确认还没通过。 */
  const unhealthy = computed(() => job.value?.stage === 'succeeded_but_unhealthy')
  const healthPending = computed(() => HEALTH_PENDING.has(job.value?.stage))
  const autoRecheckActive = computed(
    () => autoRecheckEnabled.value && healthPending.value,
  )
  /** 未健康时页面要一并给出「为什么」与「日志在哪」。 */
  const healthDetailView = computed(() => {
    if (!job.value) return null
    return {
      stage: job.value.stage,
      stageLabel: stageLabel(job.value.stage),
      healthDetail: job.value.health_detail || '',
      logPath: job.value.log_path || '',
      targetTag: job.value.target_tag || '',
      previousRef: job.value.previous_ref || '',
      restartRequestedAt: job.value.restart_requested_at || null,
      healthConfirmedAt: job.value.health_confirmed_at || null,
      startupId: job.value.startup_id || null,
      error: job.value.error || '',
    }
  })

  /** 版本回滚入口：回到上一版本＝对 previous_ref 再走一次应用更新。 */
  const previousRef = computed(() => job.value?.previous_ref || '')
  const canRollbackToPrevious = computed(
    () => !!previousRef.value && !applying.value && !jobInProgress.value,
  )

  function applyJobPayload(data) {
    job.value = data?.job || null
    if (typeof data?.log_tail === 'string') {
      jobLogTail.value = data.log_tail
    }
  }

  /** 上一次成功更新（历史时间倒序，第一条成功即最近一次）。 */
  const lastSuccessEntry = computed(
    () => historyEntries.value.find((entry) => entry?.result === 'succeeded') || null,
  )
  /** 最近一次成功之后失败（含健康确认失败）过几次。 */
  const failureCount = computed(() => {
    const entries = historyEntries.value
    const lastSuccessIndex = entries.findIndex((entry) => entry?.result === 'succeeded')
    const window = lastSuccessIndex < 0 ? entries : entries.slice(0, lastSuccessIndex)
    return window.filter((entry) => entry?.result !== 'succeeded').length
  })

  const preflight = computed(() => versionCheck.value?.preflight || null)
  const preflightChecks = computed(() => preflight.value?.checks || [])
  const healthyRuntime = computed(() => !!preflight.value?.healthy_runtime)
  const discardLocalChangesAllowed = computed(
    () => !!preflight.value?.discard_local_changes_allowed,
  )
  /** Hide Apply when this is not a healthy Runtime Instance. */
  const canShowApply = computed(() => {
    if (!versionCheck.value) return false
    if (!preflight.value) return true
    return !!preflight.value.healthy_runtime
  })
  /** Enable Apply when preflight allows, or dirty override is checked. */
  const applyEnabled = computed(() => {
    if (!canShowApply.value || applying.value || jobInProgress.value) return false
    const pf = preflight.value
    if (!pf) return true
    if (pf.apply_allowed) return true
    return !!pf.discard_local_changes_allowed && !!discardLocalChanges.value
  })

  const statusSummary = computed(() => {
    const vc = versionCheck.value
    if (!vc) return ''
    if (vc.degraded) {
      return '已装身份异常，版本检测结果仅供参考'
    }
    if (vc.update_available) {
      return '有可用更新'
    }
    return '已是最新正式发行版'
  })

  function stopPolling() {
    if (pollTimer != null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    jobPolling.value = false
  }

  function stopAutoRecheck() {
    if (recheckTimer != null) {
      clearInterval(recheckTimer)
      recheckTimer = null
    }
    recheckIn.value = 0
  }

  /** 冷却自动重检：只重跑就绪检查，不重试更新、不回滚。 */
  function startAutoRecheck() {
    if (recheckTimer != null) return
    recheckCooldownUntil = Date.now() + recheckIntervalMs
    recheckIn.value = Math.round(recheckIntervalMs / RECHECK_TICK_MS)
    recheckTimer = setInterval(() => {
      if (!autoRecheckEnabled.value || !healthPending.value) {
        stopAutoRecheck()
        return
      }
      const remaining = recheckCooldownUntil - Date.now()
      recheckIn.value = Math.max(0, Math.ceil(remaining / RECHECK_TICK_MS))
      if (remaining <= 0) {
        recheckCooldownUntil = Date.now() + recheckIntervalMs
        recheckIn.value = Math.round(recheckIntervalMs / RECHECK_TICK_MS)
        runHealthCheck()
      }
    }, RECHECK_TICK_MS)
  }

  /**
   * 就绪检查（POST /job/health-check）：作业会在这次调用里落成 succeeded 或
   * succeeded_but_unhealthy。unhealthy 不弹成功提示，交给页面持续展示。
   */
  async function runHealthCheck() {
    healthChecking.value = true
    healthCheckError.value = ''
    try {
      const data = await api.post('/api/release-update/job/health-check', {})
      applyJobPayload(data)
      const stage = job.value?.stage
      if (stage === 'succeeded') {
        stopPolling()
        stopAutoRecheck()
        showAlert('success', `应用更新成功：${job.value.target_tag || ''}`)
        return 'succeeded'
      }
      if (stage === 'succeeded_but_unhealthy') {
        stopPolling()
        return 'succeeded_but_unhealthy'
      }
      if (stage === 'failed') {
        stopPolling()
        stopAutoRecheck()
        const reason = job.value?.error || '未知错误'
        const log = job.value?.log_path ? `（日志：${job.value.log_path}）` : ''
        showAlert('error', `应用更新失败：${reason}${log}`)
        return 'failed'
      }
      return stage
    } catch (err) {
      healthCheckError.value = err.message || '就绪检查失败'
      return null
    } finally {
      healthChecking.value = false
      // 终态后不再需要冷却倒计时。
      if (!healthPending.value) stopAutoRecheck()
    }
  }

  /** 「重新检测」按钮：清掉旧提示后立刻做一次就绪检查。 */
  function recheckHealth() {
    clearAlert()
    return runHealthCheck()
  }

  async function pollJobOnce() {
    try {
      const data = await api.get('/api/release-update/job', null, null, 'no-store')
      applyJobPayload(data)
      const stage = job.value?.stage
      if (stage === 'succeeded') {
        stopPolling()
        stopAutoRecheck()
        showAlert('success', `应用更新成功：${job.value.target_tag || ''}`)
        return
      }
      if (stage === 'succeeded_but_unhealthy') {
        // 已切换但健康确认未通过：终态，保留日志与回退点，等操作者决定。
        stopPolling()
        stopAutoRecheck()
        return
      }
      if (stage === 'failed') {
        stopPolling()
        stopAutoRecheck()
        const reason = job.value?.error || '未知错误'
        const log = job.value?.log_path ? `（日志：${job.value.log_path}）` : ''
        showAlert('error', `应用更新失败：${reason}${log}`)
        return
      }
      // restarting 不是成功：继续轮询，并准备好冷却自动重检。
      if (stage === 'restarting' && autoRecheckActive.value) {
        startAutoRecheck()
      }
    } catch (_) {
      // Brief main-service restart: keep polling until success/failure.
    }
  }

  function startPolling() {
    stopPolling()
    jobPolling.value = true
    pollTimer = setInterval(() => {
      pollJobOnce()
    }, pollIntervalMs)
    // Kick once immediately.
    pollJobOnce()
    if (autoRecheckActive.value) startAutoRecheck()
  }

  async function loadGithubConfig() {
    githubLoading.value = true
    try {
      const data = await api.get('/api/release-update/github-config', null, null, 'no-store')
      githubConfig.value = data
      githubForm.value = {
        token: '',
        clear_token: false,
      }
    } catch (err) {
      githubConfig.value = null
      showAlert('error', '加载 GitHub 配置失败：' + err.message)
    } finally {
      githubLoading.value = false
    }
  }

  async function saveGithubConfig() {
    if (githubSaving.value) return
    clearAlert()
    githubSaving.value = true
    try {
      const body = {
        clear_token: !!githubForm.value.clear_token,
      }
      const token = (githubForm.value.token || '').trim()
      if (token) body.token = token
      const data = await api.put('/api/release-update/github-config', body)
      githubConfig.value = data
      githubForm.value.token = ''
      githubForm.value.clear_token = false
      showAlert('success', 'GitHub 配置已保存（立即生效，无需重启）')
      // Re-run Version Check with the new credentials.
      await loadVersionCheck()
    } catch (err) {
      showAlert('error', '保存 GitHub 配置失败：' + err.message)
    } finally {
      githubSaving.value = false
    }
  }

  async function loadVersionCheck() {
    versionLoading.value = true
    try {
      const data = await api.get('/api/release-update/version-check', null, null, 'no-store')
      versionCheck.value = data
      if (!data?.preflight?.discard_local_changes_allowed) {
        discardLocalChanges.value = false
      }
    } catch (err) {
      versionCheck.value = null
      discardLocalChanges.value = false
      showAlert('error', '版本检测失败：' + err.message)
    } finally {
      versionLoading.value = false
    }
  }

  function refreshVersionCheck() {
    clearAlert()
    return loadVersionCheck()
  }

  async function loadJobStatus() {
    try {
      const data = await api.get('/api/release-update/job', null, null, 'no-store')
      applyJobPayload(data)
      if (jobInProgress.value) {
        startPolling()
      } else if (healthPending.value && autoRecheckActive.value) {
        startAutoRecheck()
      }
    } catch (_) {
      // ignore — section may open while service is restarting
    }
  }

  async function loadHistory() {
    historyLoading.value = true
    historyError.value = ''
    try {
      const data = await api.get('/api/release-update/history', null, null, 'no-store')
      historyEntries.value = Array.isArray(data?.entries) ? data.entries : []
    } catch (err) {
      historyError.value = err.message || '加载失败'
      historyEntries.value = []
    } finally {
      historyLoading.value = false
    }
  }

  async function cancelJob() {
    if (!canCancelJob.value) return
    clearAlert()
    cancelling.value = true
    try {
      const data = await api.post('/api/release-update/job/cancel', {})
      job.value = data.job || null
      if (typeof data.log_tail === 'string') {
        jobLogTail.value = data.log_tail
      }
      if (IN_PROGRESS.has(job.value?.stage)) {
        startPolling()
        showAlert('info', '已请求终止更新，等待作业收尾…')
      } else {
        stopPolling()
        const forced = data.forced ? '（强制收尾；若已切树请再 Apply 回退点）' : ''
        showAlert('success', `更新作业已终止${forced}`)
      }
    } catch (err) {
      const detail = err.detail
      const msg = typeof detail === 'string'
        ? detail
        : (detail && detail.message) || err.message
      showAlert('error', '终止更新失败：' + msg)
    } finally {
      cancelling.value = false
    }
  }

  function openApplyConfirm(tag) {
    selectedTag.value = tag
    confirmOpen.value = true
    needsPeakOverride.value = false
    peakOverride.value = false
    // Keep discard choice if operator already confirmed for a dirty tree.
    if (!discardLocalChangesAllowed.value) {
      discardLocalChanges.value = false
    }
  }

  function cancelApplyConfirm() {
    confirmOpen.value = false
  }

  /**
   * 回到上一版本：复用既有「应用更新」确认与预检，不新开更新通道。
   * 目标就是作业记录里的 previous_ref（通常是切换前的 tag / commit）。
   */
  function rollbackToPrevious() {
    if (!previousRef.value) return
    openApplyConfirm(previousRef.value)
  }

  async function confirmApply() {
    if (!selectedTag.value || applying.value) return
    clearAlert()
    applying.value = true
    try {
      const data = await api.post('/api/release-update/apply', {
        target_tag: selectedTag.value,
        peak_override: !!peakOverride.value,
        discard_local_changes: !!discardLocalChanges.value,
      })
      job.value = data.job || null
      needsPeakOverride.value = false
      confirmOpen.value = false
      showAlert('success', `已开始应用更新：${selectedTag.value}`)
      startPolling()
    } catch (err) {
      const detail = err.detail
      if (err.status === 409 && detail && detail.reason === 'peak_hours') {
        needsPeakOverride.value = true
        showAlert('error', detail.message || '当前处于营业高峰时段，请确认后勾选覆盖再试')
        return
      }
      if (err.status === 409 && detail && detail.reason === 'dirty_tree') {
        showAlert('error', detail.message || '部署目录有本地改动，请确认丢弃后再试')
        return
      }
      if (err.status === 409 && detail && detail.reason === 'preflight') {
        showAlert('error', detail.message || '更新环境自检未通过，无法应用更新')
        return
      }
      const msg = typeof detail === 'string'
        ? detail
        : (detail && detail.message) || err.message
      showAlert('error', '启动应用更新失败：' + msg)
    } finally {
      applying.value = false
    }
  }

  if (getCurrentInstance()) {
    onUnmounted(() => {
      stopPolling()
      stopAutoRecheck()
    })
  }

  return {
    stopPolling,
    versionCheck,
    versionLoading,
    updateAvailable,
    statusSummary,
    loadVersionCheck,
    refreshVersionCheck,
    githubConfig,
    githubLoading,
    githubSaving,
    githubForm,
    loadGithubConfig,
    saveGithubConfig,
    degradedReasonLabel,
    selectedTag,
    confirmOpen,
    peakOverride,
    needsPeakOverride,
    discardLocalChanges,
    discardLocalChangesAllowed,
    preflight,
    preflightChecks,
    healthyRuntime,
    canShowApply,
    applyEnabled,
    applying,
    openApplyConfirm,
    cancelApplyConfirm,
    confirmApply,
    job,
    jobLogTail,
    jobPolling,
    jobStageLabel,
    jobInProgress,
    canCancelJob,
    cancelling,
    cancelJob,
    loadJobStatus,
    // 健康确认（succeeded_but_unhealthy）
    unhealthy,
    healthPending,
    healthDetailView,
    healthChecking,
    healthCheckError,
    recheckHealth,
    recheckIn,
    autoRecheckEnabled,
    autoRecheckActive,
    startAutoRecheck,
    stopAutoRecheck,
    // 版本回滚入口
    previousRef,
    canRollbackToPrevious,
    rollbackToPrevious,
    // 更新历史
    historyEntries,
    historyLoading,
    historyError,
    loadHistory,
    lastSuccessEntry,
    failureCount,
    stageLabel,
    STAGE_LABELS,
  }
}
