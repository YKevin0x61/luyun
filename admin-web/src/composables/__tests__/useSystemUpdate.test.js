import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPost = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
  },
}))

const { useSystemUpdate, degradedReasonLabel, STAGE_LABELS } = await import('../useSystemUpdate.js')

describe('useSystemUpdate', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
    vi.useRealTimers()
  })

  it('maps Version Check payload into current / available / degraded state', async () => {
    apiGet.mockResolvedValue({
      success: true,
      installed_tag: 'v0.1.0',
      degraded: false,
      degraded_reason: null,
      app_version: '0.1.0',
      latest_tag: 'v0.2.0',
      update_available: true,
      releases: [
        { tag: 'v0.2.0', name: '0.2.0', published_at: '2026-08-01T00:00:00Z', prerelease: false },
        { tag: 'v0.1.0', name: '0.1.0', published_at: '2026-07-01T00:00:00Z', prerelease: false },
      ],
    })

    const showAlert = vi.fn()
    const clearAlert = vi.fn()
    const {
      versionCheck,
      versionLoading,
      updateAvailable,
      statusSummary,
      loadVersionCheck,
    } = useSystemUpdate({ showAlert, clearAlert })

    await loadVersionCheck()

    expect(apiGet).toHaveBeenCalledWith(
      '/api/release-update/version-check',
      null,
      null,
      'no-store',
    )
    expect(versionLoading.value).toBe(false)
    expect(versionCheck.value.installed_tag).toBe('v0.1.0')
    expect(versionCheck.value.latest_tag).toBe('v0.2.0')
    expect(updateAvailable.value).toBe(true)
    expect(statusSummary.value).toContain('有可用更新')
    expect(showAlert).not.toHaveBeenCalled()
  })

  it('surfaces degraded identity clearly', async () => {
    apiGet.mockResolvedValue({
      success: true,
      installed_tag: null,
      degraded: true,
      degraded_reason: 'missing_manifest',
      app_version: '0.1.0',
      latest_tag: 'v0.2.0',
      update_available: false,
      releases: [{ tag: 'v0.2.0', name: '0.2.0', published_at: '2026-08-01T00:00:00Z', prerelease: false }],
    })

    const { versionCheck, statusSummary, loadVersionCheck } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
    })
    await loadVersionCheck()

    expect(versionCheck.value.degraded).toBe(true)
    expect(statusSummary.value).toContain('已装身份异常')
    expect(degradedReasonLabel('missing_manifest')).toBe('本机缺少版本清单（非发行包安装）')
  })

  it('labels manifest inconsistent and legacy dirty reasons', () => {
    expect(degradedReasonLabel('inconsistent')).toBe(
      '本机版本清单与远端同 tag 发行版不一致',
    )
    expect(degradedReasonLabel('dirty')).toBe('工作区有未提交改动')
  })

  it('alerts when Version Check fails', async () => {
    apiGet.mockRejectedValue(new Error('网络错误'))
    const showAlert = vi.fn()
    const { loadVersionCheck, versionCheck } = useSystemUpdate({
      showAlert,
      clearAlert: vi.fn(),
    })
    await loadVersionCheck()
    expect(versionCheck.value).toBeNull()
    expect(showAlert).toHaveBeenCalledWith('error', expect.stringContaining('网络错误'))
  })

  it('selectTarget and confirm gate Apply Update', async () => {
    const { selectedTag, confirmOpen, openApplyConfirm, cancelApplyConfirm } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
    })
    expect(confirmOpen.value).toBe(false)
    openApplyConfirm('v0.2.0')
    expect(selectedTag.value).toBe('v0.2.0')
    expect(confirmOpen.value).toBe(true)
    cancelApplyConfirm()
    expect(confirmOpen.value).toBe(false)
  })

  it('surfaces peak_hours and retries with override', async () => {
    const peakErr = new Error('当前处于营业高峰时段')
    peakErr.status = 409
    peakErr.detail = { reason: 'peak_hours', message: '当前处于营业高峰时段' }
    apiPost.mockRejectedValueOnce(peakErr).mockResolvedValueOnce({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.2.0', message: 'queued' },
    })
    apiGet.mockResolvedValue({
      success: true,
      job: { stage: 'queued', target_tag: 'v0.2.0', message: 'queued' },
    })

    const showAlert = vi.fn()
    const {
      openApplyConfirm,
      confirmApply,
      needsPeakOverride,
      peakOverride,
      applying,
      job,
    } = useSystemUpdate({
      showAlert,
      clearAlert: vi.fn(),
      pollIntervalMs: 60_000,
    })

    openApplyConfirm('v0.2.0')
    await confirmApply()
    expect(needsPeakOverride.value).toBe(true)
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/apply', {
      target_tag: 'v0.2.0',
      peak_override: false,
      discard_local_changes: false,
    })

    peakOverride.value = true
    await confirmApply()
    expect(apiPost).toHaveBeenLastCalledWith('/api/release-update/apply', {
      target_tag: 'v0.2.0',
      peak_override: true,
      discard_local_changes: false,
    })
    expect(applying.value).toBe(false)
    expect(job.value.stage).toBe('queued')
    expect(needsPeakOverride.value).toBe(false)
  })

  it('disables Apply when preflight forbids healthy runtime', async () => {
    apiGet.mockResolvedValue({
      success: true,
      installed_tag: 'v0.1.0',
      degraded: false,
      degraded_reason: null,
      app_version: '0.1.0',
      latest_tag: 'v0.2.0',
      update_available: true,
      releases: [{ tag: 'v0.2.0', name: '0.2.0', published_at: '2026-08-01T00:00:00Z', prerelease: false }],
      preflight: {
        healthy_runtime: false,
        apply_allowed: false,
        dirty_tree: false,
        discard_local_changes_allowed: false,
        checks: [
          { code: 'restart', ok: false, message: '当前部署模式缺少重启能力' },
          { code: 'credentials', ok: true, message: '可访问 GitHub Releases（公开仓无需 PAT）' },
          { code: 'job_idle', ok: true, message: '当前没有进行中的更新作业' },
          { code: 'tree_clean', ok: true, message: '部署目录干净' },
        ],
      },
    })

    const {
      loadVersionCheck,
      applyEnabled,
      canShowApply,
      preflightChecks,
      discardLocalChangesAllowed,
    } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
    })
    await loadVersionCheck()

    expect(canShowApply.value).toBe(false)
    expect(applyEnabled.value).toBe(false)
    expect(discardLocalChangesAllowed.value).toBe(false)
    expect(preflightChecks.value).toHaveLength(4)
    expect(preflightChecks.value[0].ok).toBe(false)
  })

  it('keeps Apply visible for dirty tree and sends discard_local_changes', async () => {
    apiGet.mockResolvedValue({
      success: true,
      installed_tag: 'v0.1.0',
      degraded: false,
      degraded_reason: null,
      app_version: '0.1.0',
      latest_tag: 'v0.2.0',
      update_available: true,
      releases: [{ tag: 'v0.2.0', name: '0.2.0', published_at: '2026-08-01T00:00:00Z', prerelease: false }],
      preflight: {
        healthy_runtime: true,
        apply_allowed: false,
        dirty_tree: true,
        discard_local_changes_allowed: true,
        checks: [
          { code: 'restart', ok: true, message: '重启能力可用' },
          { code: 'credentials', ok: true, message: '可访问 GitHub Releases（公开仓无需 PAT）' },
          { code: 'job_idle', ok: true, message: '当前没有进行中的更新作业' },
          { code: 'tree_clean', ok: false, message: '部署目录有本地改动' },
        ],
      },
    })
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.2.0', message: 'queued' },
    })

    const {
      loadVersionCheck,
      canShowApply,
      applyEnabled,
      discardLocalChangesAllowed,
      discardLocalChanges,
      openApplyConfirm,
      confirmApply,
    } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
      pollIntervalMs: 60_000,
    })
    await loadVersionCheck()

    expect(canShowApply.value).toBe(true)
    expect(discardLocalChangesAllowed.value).toBe(true)
    expect(applyEnabled.value).toBe(false)

    openApplyConfirm('v0.2.0')
    // Confirm stays blocked until discard is checked (same pattern as peak override).
    expect(applyEnabled.value).toBe(false)
    discardLocalChanges.value = true
    expect(applyEnabled.value).toBe(true)

    await confirmApply()
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/apply', {
      target_tag: 'v0.2.0',
      peak_override: false,
      discard_local_changes: true,
    })
  })

  it('polls job status across brief disconnect then succeeds', async () => {
    vi.useFakeTimers()
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.2.0', message: 'queued' },
    })
    apiGet
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'fetching_bundle',
          target_tag: 'v0.2.0',
          message: 'downloading',
          log_path: 'data/update_job.log',
        },
      })
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'installing',
          target_tag: 'v0.2.0',
          message: 'atomic switch',
          log_path: 'data/update_job.log',
        },
      })
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'succeeded',
          target_tag: 'v0.2.0',
          message: 'done',
          log_path: 'data/update_job.log',
        },
      })

    const showAlert = vi.fn()
    const { openApplyConfirm, confirmApply, job, jobPolling, jobStageLabel } = useSystemUpdate({
      showAlert,
      clearAlert: vi.fn(),
      pollIntervalMs: 1000,
    })

    openApplyConfirm('v0.2.0')
    await confirmApply()
    expect(jobPolling.value).toBe(true)
    await Promise.resolve()
    await Promise.resolve()
    expect(job.value.stage).toBe('fetching_bundle')
    expect(jobStageLabel.value).toBe('正在下载发行包')

    // Brief main-service restart: keep polling.
    await vi.advanceTimersByTimeAsync(1000)
    expect(jobPolling.value).toBe(true)

    await vi.advanceTimersByTimeAsync(1000)
    expect(job.value.stage).toBe('installing')
    expect(jobStageLabel.value).toBe('正在安装发行包')

    await vi.advanceTimersByTimeAsync(1000)
    expect(job.value.stage).toBe('succeeded')
    expect(jobPolling.value).toBe(false)
    expect(STAGE_LABELS.succeeded).toBeTruthy()
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('成功'))
  })

  it('labels bundle Update Job stages for Admin polling', () => {
    expect(STAGE_LABELS.fetching_bundle).toBe('正在下载发行包')
    expect(STAGE_LABELS.installing).toBe('正在安装发行包')
    expect(STAGE_LABELS.syncing_deps).toBe('正在同步依赖')
    // restarting 不是成功：主服务已切换、正在重启，仍需健康确认。
    expect(STAGE_LABELS.restarting).toBe('已切换、重启中')
    expect(STAGE_LABELS.succeeded_but_unhealthy).toBe('已切换但未健康')
  })

  it('stops on succeeded_but_unhealthy and exposes the three ways out', async () => {
    vi.useFakeTimers()
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.2.0', previous_ref: 'v0.1.0' },
    })
    apiGet
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'restarting',
          target_tag: 'v0.2.0',
          previous_ref: 'v0.1.0',
          message: 'restart requested',
          log_path: 'data/update_job.log',
        },
      })
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'succeeded_but_unhealthy',
          target_tag: 'v0.2.0',
          previous_ref: 'v0.1.0',
          health_detail: '数据库未连接',
          log_path: 'data/update_job.log',
        },
        log_tail: '[update] health check failed\n',
      })

    const showAlert = vi.fn()
    const {
      openApplyConfirm,
      confirmApply,
      job,
      jobPolling,
      jobStageLabel,
      unhealthy,
      healthPending,
      healthDetailView,
      previousRef,
      canRollbackToPrevious,
      rollbackToPrevious,
      selectedTag,
      confirmOpen,
    } = useSystemUpdate({ showAlert, clearAlert: vi.fn(), pollIntervalMs: 1000 })

    openApplyConfirm('v0.2.0')
    await confirmApply()
    await Promise.resolve()
    await Promise.resolve()
    expect(job.value.stage).toBe('restarting')
    expect(jobStageLabel.value).toBe('已切换、重启中')
    expect(jobPolling.value).toBe(true)
    // restarting 期间不能当成成功（只有「已开始应用更新」那条提示）。
    expect(showAlert).not.toHaveBeenCalledWith('success', expect.stringContaining('成功'))

    await vi.advanceTimersByTimeAsync(1000)
    expect(job.value.stage).toBe('succeeded_but_unhealthy')
    expect(jobPolling.value).toBe(false)
    expect(unhealthy.value).toBe(true)
    // 已切换但未健康是终态：宽限期已过、结论已定，不再做冷却自动重检。
    expect(healthPending.value).toBe(false)
    expect(healthDetailView.value.healthDetail).toBe('数据库未连接')
    expect(healthDetailView.value.logPath).toBe('data/update_job.log')
    // 未健康不弹成功提示。
    expect(showAlert).not.toHaveBeenCalledWith('success', expect.stringContaining('成功'))

    // 三条出路之一是「回到上一版本」：复用正常 Apply 流程。
    expect(previousRef.value).toBe('v0.1.0')
    expect(canRollbackToPrevious.value).toBe(true)
    rollbackToPrevious()
    expect(selectedTag.value).toBe('v0.1.0')
    expect(confirmOpen.value).toBe(true)
  })

  it('auto-rechecks readiness on a cooldown while restarting and stops after success', async () => {
    vi.useFakeTimers()
    apiGet.mockResolvedValue({
      success: true,
      job: {
        stage: 'restarting',
        target_tag: 'v0.2.0',
        previous_ref: 'v0.1.0',
        log_path: 'data/update_job.log',
      },
    })
    apiPost.mockResolvedValueOnce({
      success: true,
      job: {
        stage: 'succeeded',
        target_tag: 'v0.2.0',
        previous_ref: 'v0.1.0',
        log_path: 'data/update_job.log',
      },
      log_tail: '[update] ready\n',
    })

    const showAlert = vi.fn()
    const { loadJobStatus, recheckIn, healthChecking, autoRecheckActive, job, jobLogTail } =
      useSystemUpdate({
        showAlert,
        clearAlert: vi.fn(),
        pollIntervalMs: 60_000,
        recheckIntervalMs: 10_000,
      })

    await loadJobStatus()
    expect(autoRecheckActive.value).toBe(true)
    expect(recheckIn.value).toBe(10)

    await vi.advanceTimersByTimeAsync(5_000)
    expect(recheckIn.value).toBe(5)
    expect(apiPost).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(5_000)
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/job/health-check', {})
    expect(healthChecking.value).toBe(false)
    expect(job.value.stage).toBe('succeeded')
    expect(jobLogTail.value).toContain('ready')
    expect(autoRecheckActive.value).toBe(false)
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('v0.2.0'))
  })

  it('never auto-retries the update and never auto-rolls-back on unhealthy', async () => {
    vi.useFakeTimers()
    apiGet.mockResolvedValue({
      success: true,
      job: {
        stage: 'restarting',
        target_tag: 'v0.2.0',
        previous_ref: 'v0.1.0',
        log_path: 'data/update_job.log',
      },
    })
    apiPost.mockResolvedValue({
      success: true,
      job: {
        stage: 'succeeded_but_unhealthy',
        target_tag: 'v0.2.0',
        previous_ref: 'v0.1.0',
        health_detail: '就绪检查未通过',
        log_path: 'data/update_job.log',
      },
    })

    const showAlert = vi.fn()
    const { loadJobStatus, recheckHealth, healthChecking, healthCheckError, job, autoRecheckEnabled } =
      useSystemUpdate({
        showAlert,
        clearAlert: vi.fn(),
        pollIntervalMs: 60_000,
        recheckIntervalMs: 10_000,
      })

    await loadJobStatus()
    const result = await recheckHealth()
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/job/health-check', {})
    expect(result).toBe('succeeded_but_unhealthy')
    expect(job.value.stage).toBe('succeeded_but_unhealthy')
    expect(healthCheckError.value).toBe('')
    expect(healthChecking.value).toBe(false)
    expect(autoRecheckEnabled.value).toBe(true)

    // 终态未健康：不再做冷却自动重检，也就绝不会自动重试更新或自动回滚。
    await vi.advanceTimersByTimeAsync(30_000)
    expect(apiPost).toHaveBeenCalledTimes(1)
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/job/health-check', {})
  })

  it('loads 更新历史 and answers the last success / failure count question', async () => {
    apiGet.mockResolvedValue({
      success: true,
      entries: [
        { target_tag: 'v0.4.0', previous_tag: 'v0.3.0', result: 'failed', result_label: '失败', rolled_back: true, rollback_ok: true, started_at: '2026-09-04T10:00:00+08:00', finished_at: '2026-09-04T10:01:00+08:00', duration_seconds: 60, log_path: 'data/update_job.log', snapshot_ts: '20260904_100000', error: 'health check failed' },
        { target_tag: 'v0.3.0', previous_tag: 'v0.2.0', result: 'cancelled', result_label: '已取消', rolled_back: false, rollback_ok: null, started_at: '2026-09-03T10:00:00+08:00', finished_at: '2026-09-03T10:01:00+08:00', duration_seconds: 60, log_path: null, snapshot_ts: null, error: null },
        { target_tag: 'v0.2.0', previous_tag: 'v0.1.0', result: 'succeeded', result_label: '成功', rolled_back: false, rollback_ok: null, started_at: '2026-09-02T10:00:00+08:00', finished_at: '2026-09-02T10:00:30+08:00', duration_seconds: 30, log_path: 'data/update_job.log', snapshot_ts: null, error: null },
      ],
    })

    const { historyEntries, historyLoading, historyError, loadHistory, lastSuccessEntry, failureCount } =
      useSystemUpdate({ showAlert: vi.fn(), clearAlert: vi.fn() })

    await loadHistory()

    expect(apiGet).toHaveBeenCalledWith('/api/release-update/history', null, null, 'no-store')
    expect(historyLoading.value).toBe(false)
    expect(historyError.value).toBe('')
    expect(historyEntries.value).toHaveLength(3)
    expect(lastSuccessEntry.value.target_tag).toBe('v0.2.0')
    expect(lastSuccessEntry.value.finished_at).toContain('2026-09-02')
    // 最近一次成功之后失败（含取消）过两次。
    expect(failureCount.value).toBe(2)
  })

  it('surfaces 更新历史 load failures without breaking the page', async () => {
    apiGet.mockRejectedValue(new Error('网络错误'))
    const { historyEntries, historyError, historyLoading, loadHistory } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
    })
    await loadHistory()
    expect(historyEntries.value).toEqual([])
    expect(historyError.value).toContain('网络错误')
    expect(historyLoading.value).toBe(false)
  })

  it('shows the 上一次更新 preflight hint without blocking Apply', async () => {
    apiGet.mockResolvedValue({
      success: true,
      installed_tag: 'v0.2.0',
      degraded: false,
      degraded_reason: null,
      app_version: '0.2.0',
      latest_tag: 'v0.3.0',
      update_available: true,
      releases: [{ tag: 'v0.3.0', name: '0.3.0', published_at: '2026-09-01T00:00:00Z', prerelease: false }],
      preflight: {
        healthy_runtime: true,
        apply_allowed: true,
        dirty_tree: false,
        discard_local_changes_allowed: false,
        checks: [
          { code: 'last_update', ok: true, message: '上一次更新（v0.2.0）结果为「失败」；仅提示，不阻塞本次更新。日志：data/update_job.log' },
          { code: 'job_idle', ok: true, message: '当前没有进行中的更新作业' },
        ],
      },
    })

    const { loadVersionCheck, preflightChecks, applyEnabled } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
    })
    await loadVersionCheck()

    expect(preflightChecks.value[0].code).toBe('last_update')
    expect(preflightChecks.value[0].ok).toBe(true)
    expect(preflightChecks.value[0].message).toContain('不阻塞本次更新')
    expect(applyEnabled.value).toBe(true)
  })

  it('applies an older formal Release through the same confirm path (rollback)', async () => {
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.1.0', previous_ref: 'v0.2.0' },
    })
    apiGet.mockResolvedValue({
      success: true,
      job: { stage: 'queued', target_tag: 'v0.1.0', previous_ref: 'v0.2.0' },
    })

    const { openApplyConfirm, confirmApply, job } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
      pollIntervalMs: 60_000,
    })

    openApplyConfirm('v0.1.0')
    await confirmApply()

    expect(apiPost).toHaveBeenCalledWith('/api/release-update/apply', {
      target_tag: 'v0.1.0',
      peak_override: false,
      discard_local_changes: false,
    })
    expect(job.value.target_tag).toBe('v0.1.0')
    expect(job.value.previous_ref).toBe('v0.2.0')
  })

  it('surfaces failure with log pointer and stops polling', async () => {
    vi.useFakeTimers()
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      job: { stage: 'queued', target_tag: 'v0.2.0' },
    })
    apiGet
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'fetching_bundle',
          target_tag: 'v0.2.0',
          message: 'downloading',
          log_path: 'data/update_job.log',
        },
      })
      .mockResolvedValueOnce({
        success: true,
        job: {
          stage: 'failed',
          target_tag: 'v0.2.0',
          error: 'checksum mismatch',
          log_path: 'data/update_job.log',
        },
      })

    const showAlert = vi.fn()
    const { openApplyConfirm, confirmApply, job, jobPolling, jobStageLabel } = useSystemUpdate({
      showAlert,
      clearAlert: vi.fn(),
      pollIntervalMs: 1000,
    })

    openApplyConfirm('v0.2.0')
    await confirmApply()
    await Promise.resolve()
    await Promise.resolve()
    expect(job.value.stage).toBe('fetching_bundle')
    expect(jobStageLabel.value).toBe('正在下载发行包')
    expect(jobPolling.value).toBe(true)

    await vi.advanceTimersByTimeAsync(1000)
    expect(job.value.stage).toBe('failed')
    expect(jobPolling.value).toBe(false)
    expect(showAlert).toHaveBeenCalledWith(
      'error',
      expect.stringMatching(/checksum mismatch.*data\/update_job\.log/),
    )
  })

  it('resumes polling when loadJobStatus sees an in-progress bundle stage', async () => {
    apiGet.mockResolvedValue({
      success: true,
      job: {
        stage: 'installing',
        target_tag: 'v0.2.0',
        message: 'atomic switch',
        log_path: 'data/update_job.log',
      },
    })

    const { loadJobStatus, jobPolling, job, jobStageLabel } = useSystemUpdate({
      showAlert: vi.fn(),
      clearAlert: vi.fn(),
      pollIntervalMs: 60_000,
    })

    await loadJobStatus()

    expect(job.value.stage).toBe('installing')
    expect(jobStageLabel.value).toBe('正在安装发行包')
    expect(jobPolling.value).toBe(true)
  })

  it('keeps log_tail while polling syncing_deps and can cancel the job', async () => {
    vi.useFakeTimers()
    apiGet.mockResolvedValue({
      success: true,
      job: {
        stage: 'syncing_deps',
        target_tag: 'v0.2.0',
        message: 'Syncing Python dependencies',
        log_path: 'data/update_job.log',
      },
      log_tail: 'Collecting fastapi\n',
    })
    apiPost.mockResolvedValue({
      success: true,
      accepted: true,
      forced: true,
      job: {
        stage: 'failed',
        target_tag: 'v0.2.0',
        error: 'cancelled by operator (forced)',
        cancel_requested: true,
        log_path: 'data/update_job.log',
      },
      log_tail: 'Collecting fastapi\n[update] cancel requested\n',
    })

    const showAlert = vi.fn()
    const {
      loadJobStatus,
      jobLogTail,
      canCancelJob,
      cancelJob,
      job,
      jobPolling,
    } = useSystemUpdate({
      showAlert,
      clearAlert: vi.fn(),
      pollIntervalMs: 1000,
    })

    await loadJobStatus()
    expect(jobLogTail.value).toContain('Collecting fastapi')
    expect(canCancelJob.value).toBe(true)
    expect(jobPolling.value).toBe(true)

    await cancelJob()
    expect(apiPost).toHaveBeenCalledWith('/api/release-update/job/cancel', {})
    expect(job.value.stage).toBe('failed')
    expect(jobPolling.value).toBe(false)
    expect(jobLogTail.value).toContain('cancel requested')
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('已终止'))
  })
})
