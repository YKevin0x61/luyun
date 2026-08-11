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
  restarting: '正在重启服务',
  succeeded: '已成功',
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

const DEFAULT_POLL_INTERVAL_MS = 2000

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
 * @param {{ showAlert: Function, clearAlert: Function, pollIntervalMs?: number }} opts
 */
export function useSystemUpdate({ showAlert, clearAlert, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS }) {
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
  const jobPolling = ref(false)
  let pollTimer = null

  const updateAvailable = computed(() => !!versionCheck.value?.update_available)
  const jobStageLabel = computed(() => stageLabel(job.value?.stage))
  const jobInProgress = computed(() => IN_PROGRESS.has(job.value?.stage))

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

  async function pollJobOnce() {
    try {
      const data = await api.get('/api/release-update/job', null, null, 'no-store')
      job.value = data.job || null
      const stage = job.value?.stage
      if (stage === 'succeeded') {
        stopPolling()
        showAlert('success', `应用更新成功：${job.value.target_tag || ''}`)
        return
      }
      if (stage === 'failed') {
        stopPolling()
        const reason = job.value?.error || '未知错误'
        const log = job.value?.log_path ? `（日志：${job.value.log_path}）` : ''
        showAlert('error', `应用更新失败：${reason}${log}`)
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
      job.value = data.job || null
      if (IN_PROGRESS.has(job.value?.stage)) {
        startPolling()
      }
    } catch (_) {
      // ignore — section may open while service is restarting
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
    jobPolling,
    jobStageLabel,
    jobInProgress,
    loadJobStatus,
    stageLabel,
    STAGE_LABELS,
  }
}
