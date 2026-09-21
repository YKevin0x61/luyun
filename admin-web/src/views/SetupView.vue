<script setup>
import { onMounted, reactive, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import LuyunRadioGroup from '../components/ui/LuyunRadioGroup.vue'
import LuyunTimePicker from '../components/ui/LuyunTimePicker.vue'
import LuyunFileDropzone from '../components/ui/LuyunFileDropzone.vue'
import LuyunNumberInput from '../components/ui/LuyunNumberInput.vue'
import { usePosCredentials } from '../composables/usePosCredentials'
import { useRuntimeSettings } from '../composables/useRuntimeSettings'
import { useBackupCenter } from '../composables/useBackupCenter'
import { useAccountSettings } from '../composables/useAccountSettings'
import { useSystemUpdate } from '../composables/useSystemUpdate'
import { useDbCredentials } from '../composables/useDbCredentials'
import { useSystemHealth } from '../composables/useSystemHealth'
import { formatCount, formatMb } from '../utils/systemHealthFormat'
import SvgIcon from '../components/SvgIcon.vue'
import HealthSummary from '../components/system/HealthSummary.vue'
import BulletGauge from '../components/system/BulletGauge.vue'
import UsageBar from '../components/system/UsageBar.vue'
import ReadinessGrid from '../components/system/ReadinessGrid.vue'
import ReconcileProgress from '../components/system/ReconcileProgress.vue'
import StatusPill from '../components/ui/StatusPill.vue'
import CheckList from '../components/ui/CheckList.vue'
import PanelHeader from '../components/ui/PanelHeader.vue'
import BackupOverview from '../components/backup/BackupOverview.vue'
import BackupPointList from '../components/backup/BackupPointList.vue'
import UpdateOverview from '../components/update/UpdateOverview.vue'
import DatabaseMigrations from '../components/update/DatabaseMigrations.vue'
import UpdateStageProgress from '../components/update/UpdateStageProgress.vue'
import ReleaseRow from '../components/update/ReleaseRow.vue'

const route = useRoute()
const router = useRouter()

// 配置页是独立全屏页（无主导航壳），返回按钮固定回到主页（仪表盘）。
function goBack() {
  router.push('/')
}

// 迁移自 public/setup.html：POS 凭据配置 + 账号/API Token 管理。
// 写接口一律走 api/client.js（自带 credentials:'include' 与 401 处理），
// client.js 已针对 /login、/setup 关闭 401 自动跳转，避免在本页造成重定向死循环。

const SECTIONS = [
  { id: 'pos', label: 'POS 凭据', icon: 'store' },
  { id: 'runtime', label: '运行配置', icon: 'timer' },
  { id: 'health', label: '系统健康状态', icon: 'check-circle' },
  { id: 'backup', label: '备份中心', icon: 'package' },
  { id: 'update', label: '系统更新', icon: 'refresh-cw' },
  { id: 'database', label: '数据库凭据', icon: 'database' },
  { id: 'account', label: '账号与 API Token', icon: 'key' },
]
const activeSection = ref('pos')

const alert = reactive({ show: false, type: 'info', message: '' })
let alertTimer = null

function showAlert(type, message) {
  if (alertTimer) {
    clearTimeout(alertTimer)
    alertTimer = null
  }
  alert.show = true
  alert.type = type
  alert.message = message
  if (type === 'success') {
    alertTimer = setTimeout(() => {
      alert.show = false
    }, 3500)
  }
}

function clearAlert() {
  alert.show = false
}

const {
  configured,
  credForm,
  phonePlaceholder,
  showPassword,
  verifying,
  discoveringShops,
  discoveredShops,
  saving,
  togglePwdLabel,
  verifyBtnLabel,
  discoverBtnLabel,
  saveBtnLabel,
  metaItems,
  resetVerifiedSignature,
  fetchCurrent,
  onParseUrl,
  onDiscoverShops,
  onPickDiscoveredShop,
  togglePasswordVisibility,
  onVerify,
  onSubmitCred,
  onClearCredentials,
} = usePosCredentials({ showAlert, clearAlert })

const {
  runtimeForm,
  runtimeLoading,
  runtimeSaving,
  runtimeUpdatedAt,
  runtimeSaveLabel,
  loadRuntimeSettings,
  saveRuntimeSettings,
  resetRuntimeDefaults,
} = useRuntimeSettings({ showAlert, clearAlert })

const {
  exportForm,
  exporting,
  exportBtnLabel,
  exportHasLargePayload,
  // 后端能力位：业务数据能否打进包（两种后端都能，形态不同），以及形态本身
  exportAppDbSupported,
  appDbExportFormat,
  onExportBackup,
  // 备份健康（总览条）
  healthView,
  healthLoading,
  healthError,
  loadHealth,
  refreshHealth,
  // 备份点列表（分组渲染在 BackupPointList 内）
  points,
  pointsLoading,
  pointsError,
  loadPoints,
  notBackedUp,
  coldPoints,
  mediumLabels,
  mediumPurposes,
  validatingId,
  validateResults,
  validatePoint,
  // 两步确认（备份恢复 / 数据回滚 / 清理 / 以及本页的危险动作共用同一弹窗）
  confirmState,
  confirmChecked,
  confirmReady,
  requestConfirm,
  closeConfirm,
  onConfirmClick,
  // 导入 / 恢复
  importState,
  importPreview,
  previewing,
  importing,
  previewBtnLabel,
  importApplyLabel,
  previewProgress,
  importProgress,
  progressLabel,
  importIncludes,
  importHasPgAppDb,
  importModeOptions,
  importPreviewItems,
  importPhotoItems,
  importMissing,
  importErrors,
  importHasErrors,
  restoreAllowed,
  requiresForce,
  forceRequired,
  forceContinue,
  importRecheckedMissing,
  importInvalidReason,
  canApplyImport,
  onImportFileChange,
  onPreviewImport,
  onApplyImport,
  importSuccessModal,
  confirmImportSuccessRedirect,
  // 本机回滚快照：列表已在 BackupPointList 的「本机回滚快照」分组里渲染
  rollingBackTs,
  onRollbackSnapshot,
  // 保留与清理
  retention,
  retentionLimits,
  retentionForm,
  retentionLoading,
  retentionSaving,
  cleanupPreview,
  cleanupPreviewLoading,
  cleaningUp,
  retentionPreviewSummary,
  loadRetention,
  saveRetention,
  runCleanup,
  formatBytes,
  formatTs,
} = useBackupCenter({
  showAlert,
  clearAlert,
  onAfterRollback: async () => {
    await fetchCurrent()
    await loadRuntimeSettings()
  },
})

const {
  sessionUserHint,
  sessionUsername,
  loadSessionInfo,
  handleLogout,
  changePwdForm,
  changingPwd,
  changePwdBtnLabel,
  onChangePassword,
  tokenLabel,
  tokens,
  tokensLoading,
  tokensError,
  generatingToken,
  genTokenBtnLabel,
  loadTokenList,
  tokenModal,
  copyLabel,
  genToken,
  revokeToken,
  closeTokenModal,
  copyToken,
} = useAccountSettings({ showAlert, clearAlert })

const {
  versionCheck,
  versionLoading,
  updateAvailable,
  statusSummary,
  visibleReleases,
  hiddenReleases,
  hiddenReleasesHasInstalled,
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
  // 版本回滚入口（回退点展示直接用 job.previous_ref）
  previousTag,
  canRollbackToPrevious,
  rollbackToPrevious,
  // 更新历史
  historyEntries,
  historyLoading,
  historyError,
  loadHistory,
  lastSuccessEntry,
  failureCount,
} = useSystemUpdate({ showAlert, clearAlert })

/** 作业阶段胶囊：失败与已切换但未健康是错误态，成功是 ok，其余（含进行中）用提醒色。 */
const jobPillTone = computed(() => {
  const stage = job.value?.stage
  if (stage === 'succeeded') return 'ok'
  if (stage === 'failed' || stage === 'succeeded_but_unhealthy') return 'error'
  return 'warn'
})

const {
  dbCred,
  dbCredLoading,
  dbCredError,
  loadDbCred,
  dbIsPostgres,
  dbEnvOverride,
  dbPasswordLengthLabel,
  dbEnvFileWritable,
  dbPasswordWarning,
  dbPasswordWarningType,
  dbResetDisabled,
  dbResetDisabledReason,
  dbResetConfirm,
  dbResetShowPassword,
  dbResetToggleLabel,
  dbResetting,
  dbResetBtnLabel,
  openDbReset,
  closeDbReset,
  dbResetSubmit,
  dbResetResult,
} = useDbCredentials({ showAlert, clearAlert })

const {
  sysHealthLoading,
  sysHealthError,
  sysHealthReady,
  sysHealthProbe,
  sysHealthProcess,
  loadSysHealth,
  sysHealthProbeDbLabel,
  sysHealthProbeStatusLabel,
  sysHealthReadyChecks,
  sysHealthReadyDetails,
  sysHealthReadyHasFailure,
  sysHealthOverallLabel,
  sysHealthOverallPillClass,
  sysHealthUptimeLabel,
  sysHealthVersion,
  sysHealthDisk,
  sysHealthDiskGauge,
  sysHealthMemoryGauge,
  sysHealthFailureGauge,
  sysHealthCounts,
  sysHealthScraperHealth,
  sysHealthReconcileProgress,
  sysHealthRawFacts,
} = useSystemHealth({ clearAlert })

// ===== 各分节头部的关键事实（facts 用 props 传，样式留在 PanelHeader 内）=====

const posPill = computed(() => (configured.value
  ? { tone: 'ok', label: '凭据已配置' }
  : { tone: 'warn', label: '凭据未配置' }))
const posFacts = computed(() => (metaItems.value || []).map(([k, v]) => ({ k, v })))

const runtimePill = computed(() => (runtimeUpdatedAt.value
  ? { tone: 'info', label: '已自定义' }
  : { tone: 'neutral', label: '使用默认值' }))
const runtimeFacts = computed(() => [
  { k: '营业时段', v: `${runtimeForm.work_start || '—'} – ${runtimeForm.work_end || '—'}` },
  { k: '轮询间隔', v: `${runtimeForm.interval_min}–${runtimeForm.interval_max} 秒` },
  { k: '浏览器模式', v: runtimeForm.headless ? '无头（后台运行）' : '显示窗口（调试）' },
  { k: '上次保存', v: formatTs(runtimeUpdatedAt.value) || '尚未自定义' },
])

const dbFacts = computed(() => {
  const cred = dbCred.value
  if (!cred) return []
  return [
    { k: '后端', v: cred.backend || '—' },
    { k: '用户', v: cred.user || '—' },
    { k: '主机', v: cred.host || '—' },
    { k: '端口', v: cred.port || '—' },
    { k: '数据库', v: cred.database || '—' },
    { k: '密码长度', v: dbPasswordLengthLabel.value || '—' },
    { k: 'DSN（脱敏）', v: cred.dsn || '—', wide: true },
    {
      k: 'env 文件',
      v: `${cred.env_file || '—'}${dbEnvFileWritable.value ? '' : '（不可写）'}`,
      wide: true,
    },
  ]
})

const accountFacts = computed(() => {
  const list = tokens.value || []
  const active = list.filter((token) => !token.revoked_at)
  const latest = list.map((token) => token.created_at).filter(Boolean).sort().pop()
  return [
    { k: '登录账号', v: sessionUsername.value || '—' },
    { k: '有效 Token', v: `${active.length} 个` },
    { k: '已撤销', v: `${list.length - active.length} 个` },
    { k: '最近创建', v: formatTs(latest) || '—' },
  ]
})

// ===== 危险动作：统一走站内两步确认，不用浏览器原生 confirm =====

/** 删凭据：清空后爬虫待机，属于不可逆动作，先确认再执行。 */
function onClearCredentialsConfirm() {
  requestConfirm(
    {
      title: '确认清空 POS 凭据',
      message: '清空后爬虫进入待机，直到下次保存新凭据；已采集的数据不受影响。',
      details: ['本机加密凭据文件会被删除', '需要重新填写手机号与密码才能恢复采集'],
      danger: true,
      confirmLabel: '清空凭据',
      checkboxes: [{ key: 'clear', label: '我确认清空当前 POS 凭据', required: true }],
    },
    onClearCredentials,
  )
}

function onLogoutConfirm() {
  requestConfirm(
    {
      title: '确认退出登录',
      message: '退出后需要重新输入管理员密码才能回到管理页面。',
      danger: true,
      confirmLabel: '退出登录',
    },
    handleLogout,
  )
}

function onRevokeTokenConfirm(prefix) {
  requestConfirm(
    {
      title: '确认撤销 API Token',
      message: `撤销后该 Token（${prefix}…）立即失效，对应设备需要重新生成并配置。`,
      danger: true,
      confirmLabel: '撤销 Token',
      checkboxes: [{ key: 'revoke', label: '我确认撤销该 Token', required: true }],
    },
    () => revokeToken(prefix),
  )
}

function switchSection(id) {
  activeSection.value = id
  if (id === 'account') loadTokenList()
  if (id === 'runtime') loadRuntimeSettings()
  if (id === 'health') loadSysHealth()
  if (id === 'database') loadDbCred()
  if (id === 'backup') {
    loadPoints()
    loadHealth()
    loadRetention()
  }
  if (id === 'update') {
    loadGithubConfig()
    loadVersionCheck()
    loadJobStatus()
    loadHistory()
  }
}

onMounted(() => {
  const section = route.query.section
  if (typeof section === 'string' && SECTIONS.some((s) => s.id === section)) {
    switchSection(section)
  }
  fetchCurrent()
  loadSessionInfo()
})
</script>

<template>
  <div class="setup-page">
    <div class="container">
      <button type="button" class="back-btn" @click="goBack">← 返回主页</button>
      <h1>系统配置</h1>
      <p class="subtitle">
        七个分节各自独立生效：POS 凭据加密保存在本机 <code>data/credentials.enc</code>；运行配置存于数据库并可在线热更新；备份、更新、健康状态都在本页完成。
      </p>

      <div class="setup-body">
        <nav class="setup-nav">
          <button
            v-for="s in SECTIONS"
            :key="s.id"
            type="button"
            class="nav-item"
            :class="{ active: activeSection === s.id }"
            @click="switchSection(s.id)"
          >
            <SvgIcon :name="s.icon" :size="14" />
            <span>{{ s.label }}</span>
          </button>
        </nav>

        <div class="setup-content">
          <div v-if="alert.show" class="alert show" :class="alert.type">{{ alert.message }}</div>

          <div v-show="activeSection === 'pos'" class="section-panel">
            <PanelHeader
              icon="store"
              title="POS 凭据"
              :tone="posPill.tone"
              :pill-label="posPill.label"
              description="龙管家 2.0 App 的手机号与密码，Fernet 加密保存在本机 data/credentials.enc，编辑时不回显。"
              :facts="posFacts"
              note="「验证登录」只做一次真实登录探测、不写入凭据；「保存并启用」才会把当前填写内容落盘。"
            >
              <template #actions>
                <button type="button" class="btn" :disabled="verifying" @click="onVerify">{{ verifyBtnLabel }}</button>
              </template>
            </PanelHeader>

            <form autocomplete="off" @submit.prevent="onSubmitCred">
              <fieldset>
                <legend>账号信息</legend>
                <div class="grid">
                  <div>
                    <label for="phone">登录手机号</label>
                    <input
                      class="input"
                      id="phone"
                      v-model="credForm.phone"
                      type="text"
                      inputmode="numeric"
                      autocomplete="off"
                      :placeholder="phonePlaceholder"
                      @input="resetVerifiedSignature"
                    >
                  </div>
                  <div>
                    <label for="password">登录密码</label>
                    <div class="password-row">
                      <input
                        class="input"
                        id="password"
                        v-model="credForm.password"
                        :type="showPassword ? 'text' : 'password'"
                        autocomplete="new-password"
                        placeholder="若无变化可留空，将沿用原密码"
                        @input="resetVerifiedSignature"
                      >
                      <button type="button" class="toggle" @click="togglePasswordVisibility">{{ togglePwdLabel }}</button>
                    </div>
                    <div class="hint">龙管家 <strong>2.0 App</strong> 的手机号与密码（不是 cy7mm 网页 1.0 账号）。</div>
                  </div>
                </div>
              </fieldset>

              <fieldset>
                <legend>门店信息</legend>
                <div class="grid">
                  <div class="full">
                    <div class="field-block">
                      <div class="field-block__head">
                        <span class="field-block__title">从龙管家 2.0 账号拉取（推荐）</span>
                        <button
                          type="button"
                          class="btn btn-sm"
                          :disabled="discoveringShops"
                          @click="onDiscoverShops"
                        >{{ discoverBtnLabel }}</button>
                      </div>
                      <p class="hint">
                        填写上方手机号与密码后点击，自动获取 <code>shop_id</code> / <code>company_id</code> / 店名，无需浏览器登录 cy7mm。
                      </p>
                      <div v-if="discoveredShops.length > 1" class="field-block__pick">
                        <label for="discoveredShopPick">多个门店时选择</label>
                        <select id="discoveredShopPick" class="input" @change="onPickDiscoveredShop">
                          <option
                            v-for="(shop, idx) in discoveredShops"
                            :key="`${shop.shop_id}-${shop.company_id}`"
                            :value="idx"
                          >
                            {{ shop.shop_name || shop.shop_id }} — shop_id={{ shop.shop_id }}, company_id={{ shop.company_id }}
                          </option>
                        </select>
                      </div>
                    </div>
                  </div>
                  <div class="full">
                    <div class="field-block">
                      <div class="field-block__head">
                        <span class="field-block__title">从 App WebView URL 解析（可选）</span>
                        <button type="button" class="btn btn-sm" @click="onParseUrl">解析</button>
                      </div>
                      <input
                        class="input"
                        id="targetUrl"
                        v-model="credForm.targetUrl"
                        type="url"
                        placeholder="https://cy7mm.wuuxiang.com/home/tableList/1/100001/200002?shopName=..."
                      >
                      <p class="hint">
                        若已在 App 内打开「报表 → 实时桌态 → 占用桌台」，可复制 WebView 地址栏完整 URL 粘贴解析；勿使用带 <code>{shopId}</code> 的占位模板。
                      </p>
                    </div>
                  </div>
                  <div>
                    <label for="shopId">shop_id <span class="dim">(URL 第 1 段，centerId)</span></label>
                    <input class="input" id="shopId" v-model="credForm.shopId" type="text" inputmode="numeric" placeholder="例如 100001" @input="resetVerifiedSignature">
                  </div>
                  <div>
                    <label for="companyId">company_id <span class="dim">(URL 第 2 段)</span></label>
                    <input class="input" id="companyId" v-model="credForm.companyId" type="text" inputmode="numeric" placeholder="例如 200002" @input="resetVerifiedSignature">
                  </div>
                  <div class="full">
                    <label for="shopName">门店名称（shopName 参数）</label>
                    <input class="input" id="shopName" v-model="credForm.shopName" type="text" placeholder="例如 LuckIn" @input="resetVerifiedSignature">
                  </div>
                  <div>
                    <label for="deliveryShopId">delivery_shop_id <span class="dim">(已结账单接口)</span></label>
                    <input class="input" id="deliveryShopId" v-model="credForm.deliveryShopId" type="text" inputmode="numeric" placeholder="留空则与 company_id 相同" @input="resetVerifiedSignature">
                    <div class="hint">已结账单 / 外卖订单接口里 <code>shopId</code> 与 <code>shops</code> 字段使用的 ID，通常等于 company_id。</div>
                  </div>
                </div>
              </fieldset>

              <div class="actions is-start">
                <button type="button" class="btn" @click="fetchCurrent">刷新</button>
                <button type="submit" class="btn btn-primary" :disabled="saving">{{ saveBtnLabel }}</button>
                <!-- 破坏性操作放最后，离主操作远一点 -->
                <button
                  v-if="configured"
                  type="button"
                  class="btn btn-danger"
                  @click="onClearCredentialsConfirm"
                >清空凭据</button>
              </div>
            </form>
          </div>
          <div v-show="activeSection === 'runtime'" class="section-panel">
            <PanelHeader
              icon="timer"
              title="运行配置"
              :tone="runtimePill.tone"
              :pill-label="runtimePill.label"
              description="营业时段、轮询间隔等存在数据库里，保存即热生效，无需重启服务；非营业时段爬虫自动暂停采集。"
              :facts="runtimeFacts"
            />

            <fieldset>
              <legend>营业时段</legend>
              <div class="grid">
                <div>
                  <label for="workStart">营业开始时间</label>
                  <LuyunTimePicker id="workStart" v-model="runtimeForm.work_start" />
                </div>
                <div>
                  <label for="workEnd">营业结束时间</label>
                  <LuyunTimePicker id="workEnd" v-model="runtimeForm.work_end" />
                </div>
              </div>
              <div class="hint">仅支持同日时段（开始须早于结束），暂不支持跨零点通宵营业。非营业时段爬虫自动暂停采集。</div>
            </fieldset>

            <fieldset>
              <legend>采集频率</legend>
              <div class="grid">
                <div>
                  <label for="intervalMin">轮询间隔下限（秒）</label>
                  <LuyunNumberInput id="intervalMin" v-model="runtimeForm.interval_min" :min="1" :max="3600" />
                </div>
                <div>
                  <label for="intervalMax">轮询间隔上限（秒）</label>
                  <LuyunNumberInput id="intervalMax" v-model="runtimeForm.interval_max" :min="1" :max="3600" />
                </div>
              </div>
              <div class="hint">每轮采集后在上下限之间随机等待，降低对 POS 站点的规律性压力。</div>
            </fieldset>

            <fieldset>
              <legend>高级选项</legend>
              <div class="grid">
                <div>
                  <span class="field-label">浏览器无头模式</span>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="runtimeForm.headless" />
                    <span>{{ runtimeForm.headless ? '开启（后台运行）' : '关闭（显示窗口，调试用）' }}</span>
                  </label>
                </div>
                <div>
                  <label for="retryCount">失败重试次数</label>
                  <LuyunNumberInput id="retryCount" v-model="runtimeForm.retry_count" :min="0" :max="10" />
                </div>
                <div>
                  <label for="timeoutMs">超时（毫秒）</label>
                  <LuyunNumberInput id="timeoutMs" v-model="runtimeForm.timeout_ms" :min="1000" :max="300000" :step="1000" />
                </div>
                <div>
                  <label for="deliveryCancelMiss">外卖取消判定次数</label>
                  <LuyunNumberInput id="deliveryCancelMiss" v-model="runtimeForm.delivery_cancel_miss_threshold" :min="1" :max="20" />
                  <div class="hint">外卖单连续缺席多少次采集后判为取消（防误杀，越大越保守）。默认 3。</div>
                </div>
              </div>
              <div class="hint">改动后保存即热生效，无需重启服务。</div>
            </fieldset>

            <div class="actions is-start">
              <button type="button" class="btn" :disabled="runtimeLoading" @click="loadRuntimeSettings">刷新</button>
              <button type="button" class="btn" @click="resetRuntimeDefaults">恢复默认</button>
              <button type="button" class="btn btn-primary" :disabled="runtimeSaving" @click="saveRuntimeSettings">{{ runtimeSaveLabel }}</button>
            </div>
          </div>

          <div v-show="activeSection === 'health'" class="section-panel">
            <HealthSummary
              :pill-class="sysHealthOverallPillClass"
              :overall-label="sysHealthOverallLabel"
              :version="sysHealthVersion"
              :uptime-label="sysHealthUptimeLabel"
              :loading="sysHealthLoading"
              :error="sysHealthError"
              @refresh="loadSysHealth"
            />
            <p class="hint health-panel__note">
              只读聚合运行时就绪、访问探针、进程资源与采集健康；本页不触发任何写操作，图表为当前值对照阈值的量表。
            </p>

            <div class="health-cards">
              <section class="health-card" aria-labelledby="health-card-disk">
                <h3 id="health-card-disk" class="health-card__title">
                  <SvgIcon name="database" :size="14" />磁盘水位
                </h3>
                <BulletGauge
                  title="磁盘空闲"
                  :level="sysHealthDiskGauge.level"
                  :level-label="sysHealthDiskGauge.levelLabel"
                  :pct="sysHealthDiskGauge.pct"
                  :zones="sysHealthDiskGauge.zones"
                  :marker-pct="sysHealthDiskGauge.markerPct"
                  :value-text="sysHealthDiskGauge.valueText"
                  :threshold-text="sysHealthDiskGauge.thresholdText"
                  :caption="sysHealthDiskGauge.caption"
                  :aria-label="sysHealthDiskGauge.ariaLabel"
                />
                <dl v-if="sysHealthDisk" class="health-facts">
                  <div><dt>挂载点</dt><dd>{{ sysHealthDisk.path || '—' }}</dd></div>
                  <div><dt>空闲</dt><dd>{{ formatMb(sysHealthDisk.free_mb) }}</dd></div>
                </dl>
                <p v-else class="hint">未获取到磁盘明细。</p>
                <p v-if="sysHealthDiskGauge.level === 'critical'" class="health-warn is-critical">
                  磁盘剩余空间严重不足，采集与备份可能失败，请尽快在宿主机清理。
                </p>
                <p v-else-if="sysHealthDiskGauge.level === 'warning'" class="health-warn is-warning">
                  磁盘剩余空间偏低，建议尽快清理或扩容。
                </p>
              </section>

              <section class="health-card" aria-labelledby="health-card-memory">
                <h3 id="health-card-memory" class="health-card__title">
                  <SvgIcon name="bar-chart" :size="14" />内存用量
                </h3>
                <BulletGauge
                  title="进程内存（RSS）"
                  :level="sysHealthMemoryGauge.level"
                  :level-label="sysHealthMemoryGauge.levelLabel"
                  :pct="sysHealthMemoryGauge.pct"
                  :zones="sysHealthMemoryGauge.zones"
                  :marker-pct="sysHealthMemoryGauge.markerPct"
                  :value-text="sysHealthMemoryGauge.valueText"
                  :threshold-text="sysHealthMemoryGauge.thresholdText"
                  :caption="sysHealthMemoryGauge.caption"
                  :peak="sysHealthMemoryGauge.peak"
                  :aria-label="sysHealthMemoryGauge.ariaLabel"
                />
                <dl v-if="sysHealthProcess?.memory" class="health-facts">
                  <div><dt>峰值内存</dt><dd>{{ formatMb(sysHealthProcess.memory.peak_memory_mb) }}</dd></div>
                  <div><dt>上次内存清理</dt><dd>{{ formatTs(sysHealthProcess.memory.last_cleanup) || '—' }}</dd></div>
                  <div><dt>内存清理次数</dt><dd>{{ formatCount(sysHealthProcess.memory.cleanup_count) }}</dd></div>
                  <div><dt>GC 次数</dt><dd>{{ formatCount(sysHealthProcess.memory.gc_collections) }}</dd></div>
                </dl>
                <p v-else class="hint">未获取到进程内存信息。</p>
              </section>

              <section class="health-card" aria-labelledby="health-card-failures">
                <h3 id="health-card-failures" class="health-card__title">
                  <SvgIcon name="alert-triangle" :size="14" />采集失败
                </h3>
                <BulletGauge
                  title="API 失败次数"
                  :level="sysHealthFailureGauge.level"
                  :level-label="sysHealthFailureGauge.levelLabel"
                  :pct="sysHealthFailureGauge.pct"
                  :zones="sysHealthFailureGauge.zones"
                  :marker-pct="sysHealthFailureGauge.markerPct"
                  :value-text="sysHealthFailureGauge.valueText"
                  :threshold-text="sysHealthFailureGauge.thresholdText"
                  :caption="sysHealthFailureGauge.caption"
                  :aria-label="sysHealthFailureGauge.ariaLabel"
                />
                <dl v-if="sysHealthScraperHealth" class="health-facts">
                  <div><dt>营业日</dt><dd>{{ sysHealthScraperHealth.biz_date || '—' }}</dd></div>
                  <div><dt>最后采集</dt><dd>{{ formatTs(sysHealthScraperHealth.last_scrape_at) || '—' }}</dd></div>
                  <div><dt>待结配送单</dt><dd>{{ formatCount(sysHealthScraperHealth.delivery_bills_pending) }}</dd></div>
                  <div><dt>状态更新</dt><dd>{{ formatTs(sysHealthScraperHealth.updated_at) || '—' }}</dd></div>
                </dl>
                <p v-else class="hint">未获取到采集健康数据。</p>
              </section>

              <section class="health-card" aria-labelledby="health-card-counts">
                <h3 id="health-card-counts" class="health-card__title">
                  <SvgIcon name="layout-grid" :size="14" />数据量
                </h3>
                <div v-if="sysHealthCounts.length" class="health-usage">
                  <UsageBar
                    v-for="c in sysHealthCounts"
                    :key="c.key"
                    :label="c.label"
                    :display="c.display"
                    :pct="c.pct"
                    :aria-label="c.ariaLabel"
                  />
                  <p class="hint">条长按三者最大值等比，真实数量以右侧数字为准。</p>
                </div>
                <p v-else class="hint">未获取到数据量统计。</p>
              </section>

              <section class="health-card" aria-labelledby="health-card-readiness">
                <h3 id="health-card-readiness" class="health-card__title">
                  <SvgIcon name="check-circle" :size="14" />就绪检查
                </h3>
                <ReadinessGrid
                  :items="sysHealthReadyChecks"
                  :details="sysHealthReadyDetails"
                  :has-failure="sysHealthReadyHasFailure"
                />
                <dl v-if="sysHealthReady" class="health-facts">
                  <div><dt>版本</dt><dd>{{ sysHealthVersion }}</dd></div>
                  <div><dt>启动时间</dt><dd>{{ formatTs(sysHealthReady.started_at) || '—' }}</dd></div>
                  <div><dt>启动标识</dt><dd>{{ sysHealthReady.startup_id || '—' }}</dd></div>
                </dl>
                <dl v-if="sysHealthProbe" class="health-facts">
                  <div><dt>探针结论</dt><dd>{{ sysHealthProbeStatusLabel }}</dd></div>
                  <div><dt>数据库</dt><dd>{{ sysHealthProbeDbLabel }}</dd></div>
                </dl>
              </section>

              <section class="health-card" aria-labelledby="health-card-reconcile">
                <h3 id="health-card-reconcile" class="health-card__title">
                  <SvgIcon name="refresh-cw" :size="14" />对账进度
                </h3>
                <ReconcileProgress :state="sysHealthReconcileProgress" />
                <dl v-if="sysHealthScraperHealth" class="health-facts">
                  <div><dt>最后对账</dt><dd>{{ formatTs(sysHealthScraperHealth.last_reconcile?.at) || '—' }}</dd></div>
                  <div><dt>漏单数量</dt><dd>{{ formatCount(sysHealthScraperHealth.last_reconcile?.missed_qty) }}</dd></div>
                  <div>
                    <dt>漏单率</dt>
                    <dd>
                      {{ typeof sysHealthScraperHealth.last_reconcile?.miss_rate_pct === 'number'
                        ? `${sysHealthScraperHealth.last_reconcile.miss_rate_pct}%` : '—' }}
                    </dd>
                  </div>
                </dl>
                <p v-if="sysHealthScraperHealth?.last_reconcile?.report_md" class="hint">
                  报告：<code>{{ sysHealthScraperHealth.last_reconcile.report_md }}</code>
                </p>
              </section>
            </div>

            <details v-if="sysHealthRawFacts.length" class="health-raw">
              <summary>全部原始指标</summary>
              <div
                v-for="g in sysHealthRawFacts"
                :key="g.key"
                class="health-raw__group"
              >
                <h4>{{ g.title }}</h4>
                <dl class="health-facts">
                  <div v-for="item in g.items" :key="`${g.key}-${item.k}`">
                    <dt>{{ item.k }}</dt><dd>{{ item.v }}</dd>
                  </div>
                </dl>
              </div>
            </details>
          </div>

          <div v-show="activeSection === 'backup'" class="section-panel">
            <BackupOverview
              :health="healthView"
              :loading="healthLoading"
              :error="healthError"
              @refresh="refreshHealth"
            />

            <BackupPointList
              :points="points"
              :loading="pointsLoading"
              :error="pointsError"
              :medium-labels="mediumLabels"
              :medium-purposes="mediumPurposes"
              :validating-id="validatingId"
              :validate-results="validateResults"
              :rolling-back-ts="rollingBackTs"
              :not-backed-up="notBackedUp"
              @validate="validatePoint"
              @rollback="onRollbackSnapshot"
              @refresh="loadPoints"
            />

            <fieldset>
              <legend>导出备份</legend>
              <p v-if="appDbExportFormat !== 'pgdump'" class="hint section-lead">
                导出为口令加密的 <code>.luyunbak</code> 文件，可打包 POS 凭据、运行配置、业务数据与两类卫生照片。口令遗失将无法解密恢复，请牢记。
              </p>
              <p v-else class="hint section-lead">
                导出为口令加密的 <code>.luyunbak</code> 文件，可打包 POS 凭据、运行配置、业务数据（PostgreSQL 整库快照）、配方数据与两类卫生照片。口令遗失将无法解密恢复，请牢记。
              </p>
              <div class="grid">
                <div>
                  <label for="exportPass">加密口令</label>
                  <input class="input" id="exportPass" v-model="exportForm.passphrase" type="password" autocomplete="new-password" placeholder="至少 6 位">
                </div>
                <div>
                  <label for="exportPass2">确认口令</label>
                  <input class="input" id="exportPass2" v-model="exportForm.passphrase2" type="password" autocomplete="new-password" placeholder="再次输入">
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_runtime" />
                    <span>同时打包运行配置（营业时段 / 轮询间隔等）</span>
                  </label>
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_app_db" :disabled="!exportAppDbSupported" />
                    <span>同时打包业务数据库（订单、档口映射等）</span>
                  </label>
                  <div v-if="appDbExportFormat === 'pgdump'" class="hint">
                    PostgreSQL 门店的业务数据以整库快照（<code>pg_dump</code>）打包，恢复时是<strong>整库覆盖</strong>、不能合并导入；宿主机冷备命令见 <code>deploy/README.md</code> 第 10.4 节。
                  </div>
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_recipes" />
                    <span>同时打包配方数据</span>
                  </label>
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_standard_photos" />
                    <span>同时打包标准图（含全部历史版本）</span>
                  </label>
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_other_photos" />
                    <span>同时打包其它照片（日常实拍、专项、整改回拍、教材）</span>
                  </label>
                </div>
              </div>
              <div v-if="exportHasLargePayload" class="hint">
                已勾选业务数据或配方，备份文件可能较大，导出耗时会更长。
              </div>
              <div class="actions">
                <button type="button" class="btn btn-primary" :disabled="exporting" @click="onExportBackup">{{ exportBtnLabel }}</button>
              </div>
            </fieldset>

            <fieldset>
              <legend>恢复</legend>
              <p class="hint section-lead">
                选择 <code>.luyunbak</code> 导出备份并输入导出时的口令，先预览核对再确认恢复（备份文件只需上传一次）。
              </p>
              <div class="grid">
                <div class="full">
                  <label for="importFile">备份文件</label>
                  <LuyunFileDropzone
                    id="importFile"
                    accept=".luyunbak"
                    label="拖拽 .luyunbak 到此处，或点击选择"
                    @change="onImportFileChange"
                  />
                  <div v-if="importState.fileName" class="hint">已选择：{{ importState.fileName }}</div>
                </div>
                <div class="full">
                  <label for="importPass">解密口令</label>
                  <input class="input" id="importPass" v-model="importState.passphrase" type="password" autocomplete="new-password" placeholder="导出时设置的口令">
                </div>
              </div>
              <div class="actions is-start">
                <button type="button" class="btn" :disabled="previewing" @click="onPreviewImport">{{ previewBtnLabel }}</button>
              </div>
              <div v-if="previewProgress.active" class="upload-progress" :class="'is-' + previewProgress.phase">
                <div class="upload-progress__track">
                  <div
                    class="upload-progress__bar"
                    :class="{ indeterminate: previewProgress.phase === 'processing' }"
                    :style="{ width: previewProgress.percent + '%' }"
                  ></div>
                </div>
                <span class="upload-progress__label">{{ progressLabel(previewProgress) }}</span>
              </div>

              <div v-if="importPreview" class="meta-grid import-preview-grid">
                <div v-for="[k, v] in importPreviewItems" :key="k"><span class="k">{{ k }}</span><span class="v">{{ v }}</span></div>
              </div>

              <!-- 两层校验：备份自身损坏 → 阻止恢复且不可覆盖 -->
              <div v-if="importHasErrors" class="alert error show import-block">
                这份备份自身不一致，已阻止恢复（无法强制继续）：
                <ul class="bullet-list">
                  <li v-for="(msg, i) in importErrors" :key="i">{{ msg }}</li>
                </ul>
              </div>
              <!-- 跨备份点差异 → 只提示，默认不勾选受影响类别 -->
              <div v-else-if="requiresForce" class="alert info show import-block">
                这份备份缺少当前数据引用的照片{{ importRecheckedMissing.length ? `（${importRecheckedMissing.join('、')}）` : '' }}。
                这只是不匹配，不是备份损坏：受影响的照片类已默认不勾选；若仍要恢复它们，需勾选下方「我已知晓并强制继续」。
              </div>

              <div v-if="importPreview" class="import-block">
                <div class="hint">照片清单</div>
                <ul class="plain-list">
                  <li v-for="(item, i) in importPhotoItems" :key="i">{{ item }}</li>
                </ul>
                <div v-if="importMissing.length" class="hint">
                  备份中不含：{{ importMissing.map((m) => m.label).join('、') }}
                </div>
              </div>

              <div v-if="importPreview" class="import-block">
                <div class="hint">恢复模式</div>
                <LuyunRadioGroup
                  v-model="importState.mode"
                  :options="importModeOptions"
                />
                <div v-if="importHasPgAppDb" class="hint">
                  这份备份的业务数据是 PostgreSQL 整库快照，只能整库覆盖恢复，<strong>不能与现有数据合并</strong>。
                </div>
              </div>

              <div v-if="importPreview" class="import-block">
                <div class="hint">应用项</div>
                <div class="check-stack">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_credentials" />
                    <span>POS 凭据</span>
                  </label>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_runtime" :disabled="!importIncludes.runtime" />
                    <span>运行配置</span>
                  </label>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_app_db" :disabled="!importIncludes.app_db && !importIncludes.app_pg" />
                    <span>业务数据</span>
                  </label>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_recipes" :disabled="!importIncludes.recipes_db" />
                    <span>配方数据</span>
                  </label>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_standard_photos" :disabled="!importIncludes.standard_photos" />
                    <span>标准图</span>
                  </label>
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="importState.apply_other_photos" :disabled="!importIncludes.other_photos" />
                    <span>其它照片</span>
                  </label>
                </div>
              </div>

              <label v-if="importPreview && forceRequired" class="luyun-check-row import-block">
                <LuyunCheckbox v-model="forceContinue" />
                <span>我已知晓并强制继续（已勾选备份中缺失的照片类）</span>
              </label>

              <div v-if="importPreview" class="actions">
                <button
                  type="button"
                  class="btn btn-danger"
                  :disabled="!canApplyImport"
                  @click="onApplyImport"
                >{{ importApplyLabel }}</button>
              </div>
              <div v-if="importPreview && importInvalidReason" class="hint is-warn">
                {{ importInvalidReason }}
              </div>
              <div v-if="importProgress.active" class="upload-progress" :class="'is-' + importProgress.phase">
                <div class="upload-progress__track">
                  <div
                    class="upload-progress__bar"
                    :class="{ indeterminate: importProgress.phase === 'processing' }"
                    :style="{ width: importProgress.percent + '%' }"
                  ></div>
                </div>
                <span class="upload-progress__label">{{ progressLabel(importProgress) }}</span>
              </div>
              <p class="hint">
                本机回滚快照请在页面顶部的「备份点 · 本机回滚快照」分组里回滚；回滚前会自动再新建一份快照。
              </p>
            </fieldset>

            <fieldset>
              <legend>保留与清理</legend>
              <p class="hint section-lead">
                本机回滚快照、导出备份与冷备各自保留份数；超出份数会被清理，但更新作业前的本机回滚快照与最近一份永不自动删除。保存前会先展示将删除哪些备份点。
              </p>
              <div v-if="retentionLoading" class="hint">加载中…</div>
              <template v-else>
                <div class="grid">
                  <div>
                    <label for="snapshotKeep">本机回滚快照保留份数</label>
                    <LuyunNumberInput
                      id="snapshotKeep"
                      v-model="retentionForm.snapshot_keep"
                      :min="retentionLimits?.snapshot_keep_min ?? 1"
                      :max="retentionLimits?.snapshot_keep_max ?? 20"
                    />
                  </div>
                  <div>
                    <label for="exportKeep">导出备份保留份数</label>
                    <LuyunNumberInput
                      id="exportKeep"
                      v-model="retentionForm.export_keep"
                      :min="retentionLimits?.export_keep_min ?? 1"
                      :max="retentionLimits?.export_keep_max ?? 20"
                    />
                  </div>
                  <div>
                    <label for="coldKeep">冷备保留份数</label>
                    <LuyunNumberInput
                      id="coldKeep"
                      v-model="retentionForm.cold_keep"
                      :min="retentionLimits?.cold_keep_min ?? 1"
                      :max="retentionLimits?.cold_keep_max ?? 90"
                    />
                  </div>
                </div>
                <div class="hint">
                  默认值：本机回滚快照 {{ retentionLimits?.snapshot_keep_default ?? '—' }} 份、
                  导出备份 {{ retentionLimits?.export_keep_default ?? '—' }} 份、
                  冷备 {{ retentionLimits?.cold_keep_default ?? '—' }} 份。
                  导出备份以 .luyunbak 下载到浏览器，本机另留一份副本用于列表展示与直接恢复。
                </div>

                <div v-if="cleanupPreview" class="cleanup-preview">
                  <div class="hint">
                    当前配置的清理预览：将删除本机回滚快照 {{ retentionPreviewSummary.snapshotDelete }} 份、
                    导出备份 {{ retentionPreviewSummary.exportDelete }} 份、
                    冷备 {{ retentionPreviewSummary.coldDelete }} 份；受保护 {{ retentionPreviewSummary.protected }} 份。
                  </div>
                  <ul class="cleanup-list">
                    <li v-for="entry in cleanupPreview.snapshot.protected" :key="'sp-' + entry.id">
                      <StatusPill tone="ok" label="受保护" />
                      <span>{{ entry.ts }} · {{ entry.provenance_label }} · {{ entry.reason }}</span>
                    </li>
                    <li v-for="entry in cleanupPreview.export.protected" :key="'ep-' + entry.id">
                      <StatusPill tone="ok" label="受保护" />
                      <span>{{ entry.name }} · {{ entry.reason }}</span>
                    </li>
                    <li v-for="entry in cleanupPreview.cold.protected" :key="'cp-' + entry.id">
                      <StatusPill tone="ok" label="受保护" />
                      <span>{{ entry.ts }} · {{ entry.reason }}</span>
                    </li>
                    <li v-for="entry in cleanupPreview.snapshot.delete" :key="'sd-' + entry.id">
                      <StatusPill tone="warn" label="将删除" />
                      <span>{{ entry.ts }} · {{ entry.provenance_label }} · {{ formatBytes(entry.size_bytes) }}</span>
                    </li>
                    <li v-for="entry in cleanupPreview.export.delete" :key="'ed-' + entry.id">
                      <StatusPill tone="warn" label="将删除" />
                      <span>{{ entry.name }} · {{ formatBytes(entry.size_bytes) }}</span>
                    </li>
                    <li v-for="entry in cleanupPreview.cold.delete" :key="'cd-' + entry.id">
                      <StatusPill tone="warn" label="将删除" />
                      <span>{{ entry.ts }} · {{ formatBytes(entry.size_bytes) }}</span>
                    </li>
                  </ul>
                </div>

                <div class="actions is-start">
                  <button type="button" class="btn" :disabled="retentionLoading" @click="loadRetention">刷新</button>
                  <button type="button" class="btn btn-primary" :disabled="retentionSaving" @click="saveRetention">
                    {{ retentionSaving ? '保存中…' : '保存保留配置' }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-danger"
                    :disabled="cleaningUp || cleanupPreviewLoading"
                    @click="runCleanup"
                  >{{ cleaningUp ? '清理中…' : '立即清理' }}</button>
                </div>
                <div v-if="!retention && !cleanupPreview" class="hint">
                  尚未加载保留配置。
                </div>
              </template>
            </fieldset>
          </div>
          <div v-show="activeSection === 'update'" class="section-panel">
            <UpdateOverview
              :version-check="versionCheck"
              :loading="versionLoading"
              :update-available="updateAvailable"
              :status-summary="statusSummary"
              :degraded-reason="degradedReasonLabel(versionCheck?.degraded_reason)"
              @refresh="refreshVersionCheck"
            />

            <fieldset v-if="preflightChecks.length">
              <legend>更新环境自检</legend>
              <p class="hint section-lead">
                应用更新由独立作业执行，不会在网页进程内改代码；下面每一项都必须通过才能更新。
              </p>
              <CheckList :items="preflightChecks" />
              <div v-if="!healthyRuntime" class="alert show import-block">
                当前不是健康的运行实例，已禁用「应用更新」。请先修好上方未通过项（重启能力、GitHub Releases 可达性等）后再试。
              </div>
              <div v-else-if="discardLocalChangesAllowed" class="alert show import-block">
                部署目录有本地改动，默认禁止更新。若确认丢弃这些改动，请在确认对话框中勾选后再继续。
              </div>
            </fieldset>

            <DatabaseMigrations />

            <fieldset>
              <legend>正式发行版目录</legend>
              <p class="hint section-lead">
                默认排除预发布（prerelease）。选择较旧 tag 即回滚到该发行版。
              </p>
              <div v-if="versionLoading" class="hint">加载中…</div>
              <div v-else-if="!versionCheck" class="hint">请先执行版本检测</div>
              <div
                v-else-if="versionCheck.catalogue_ok === false && !(versionCheck.releases || []).length"
                class="hint is-warn"
              >
                发行目录不可用：没能读到 GitHub Releases，无法列出可选版本；请检查网络或 API 限流后重新检测。
              </div>
              <div v-else-if="!(versionCheck.releases || []).length" class="hint">暂无正式发行版</div>
              <template v-else>
                <p v-if="!canShowApply" class="hint is-warn release-blocked">
                  更新环境自检未通过，「应用此版本」入口已全部禁用；先修好上方自检的未通过项（重启能力、GitHub Releases 可达性等）再回来。
                </p>
                <table class="token-list release-list">
                  <thead>
                    <tr>
                      <th>Tag</th>
                      <th>名称</th>
                      <th>发布时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <ReleaseRow
                      v-for="r in visibleReleases"
                      :key="r.tag"
                      :release="r"
                      :installed-tag="versionCheck.installed_tag"
                      :latest-tag="versionCheck.latest_tag"
                      :degraded="!!versionCheck.degraded"
                      :can-apply="canShowApply"
                      :busy="applying || jobInProgress"
                      @apply="openApplyConfirm"
                    />
                  </tbody>
                </table>
                <details v-if="hiddenReleases.length" class="release-more">
                  <summary>
                    <SvgIcon name="chevron-right" :size="14" class="release-more__icon" />
                    <span>
                      展开其余 {{ hiddenReleases.length }} 个版本<template
                        v-if="hiddenReleasesHasInstalled"
                      >（含当前版本 {{ versionCheck.installed_tag }}）</template>
                    </span>
                  </summary>
                  <table class="token-list release-list">
                    <thead>
                      <tr>
                        <th>Tag</th>
                        <th>名称</th>
                        <th>发布时间</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      <ReleaseRow
                        v-for="r in hiddenReleases"
                        :key="r.tag"
                        :release="r"
                        :installed-tag="versionCheck.installed_tag"
                        :latest-tag="versionCheck.latest_tag"
                        :degraded="!!versionCheck.degraded"
                        :can-apply="canShowApply"
                        :busy="applying || jobInProgress"
                        @apply="openApplyConfirm"
                      />
                    </tbody>
                  </table>
                </details>
              </template>
            </fieldset>

            <fieldset v-if="confirmOpen">
              <legend>确认应用更新</legend>
              <p class="hint section-lead">
                将把运行实例切换到 <span class="mono">{{ selectedTag }}</span>。
                作业会先强制备份，再下载发行包、切换应用目录、按需同步依赖并重启主服务。
              </p>
              <div v-if="discardLocalChangesAllowed" class="alert show import-block">
                部署目录有本地改动。继续将丢弃这些改动并以目标发行包覆盖。
              </div>
              <label v-if="discardLocalChangesAllowed" class="luyun-check-row import-block">
                <LuyunCheckbox v-model="discardLocalChanges" />
                <span>我确认丢弃部署目录中的本地改动</span>
              </label>
              <div v-if="needsPeakOverride" class="alert show import-block">
                当前处于营业高峰时段。若仍要继续，请勾选下方覆盖项后再次确认。
              </div>
              <label class="luyun-check-row import-block">
                <LuyunCheckbox v-model="peakOverride" />
                <span>我已知晓营业高峰风险，仍然执行更新</span>
              </label>
              <div class="actions is-start">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="!applyEnabled || (needsPeakOverride && !peakOverride)"
                  @click="confirmApply"
                >{{ applying ? '提交中…' : '确认开始更新' }}</button>
                <button type="button" class="btn" :disabled="applying" @click="cancelApplyConfirm">取消</button>
              </div>
            </fieldset>

            <fieldset>
              <legend>更新作业进度</legend>
              <p class="hint section-lead">
                进度保存在本机状态文件，主服务短暂重启后仍可继续查看。
                <span v-if="jobPolling">正在轮询…</span>
                <span v-else-if="healthPending && autoRecheckEnabled">
                  将于 {{ recheckIn }} 秒后自动重新检测就绪（冷却重检，不会自动重试更新或回滚）。
                </span>
              </p>
              <UpdateStageProgress :job="job" />

              <template v-if="job && job.stage !== 'idle'">
                <div class="meta-grid job-facts">
                  <div>
                    <span class="k">阶段</span>
                    <span class="v">
                      <StatusPill :tone="jobPillTone" :label="jobStageLabel" />
                    </span>
                  </div>
                  <div>
                    <span class="k">目标</span>
                    <span class="v mono">{{ job.target_tag || '—' }}</span>
                  </div>
                  <div>
                    <span class="k">回退点</span>
                    <span class="v mono">{{ job.previous_ref || '—' }}</span>
                  </div>
                  <div>
                    <span class="k">日志</span>
                    <span class="v mono">{{ job.log_path || '—' }}</span>
                  </div>
                  <div v-if="job.restart_requested_at">
                    <span class="k">已请求重启</span>
                    <span class="v">{{ formatTs(job.restart_requested_at) }}</span>
                  </div>
                  <div v-if="job.health_confirmed_at">
                    <span class="k">健康确认</span>
                    <span class="v">{{ formatTs(job.health_confirmed_at) }}</span>
                  </div>
                </div>
                <div v-if="job.message" class="hint">{{ job.message }}</div>
                <div v-if="job.error" class="alert show">{{ job.error }}</div>
                <div v-if="job.rollback_attempted" class="hint">
                  已尝试恢复更新前的应用目录：{{ job.rollback_ok ? '恢复成功' : '恢复未完全成功，请查看日志或 SSH 排查' }}
                </div>
                <div class="actions is-start">
                  <button
                    v-if="jobInProgress"
                    type="button"
                    class="btn btn-danger"
                    :disabled="!canCancelJob"
                    @click="cancelJob"
                  >{{ cancelling || job.cancel_requested ? '正在终止…' : '终止更新' }}</button>
                  <button
                    v-else-if="canRollbackToPrevious"
                    type="button"
                    class="btn btn-danger"
                    :disabled="applying"
                    @click="rollbackToPrevious"
                  >回到上一版本（{{ previousTag }}）</button>
                </div>
                <div v-if="jobLogTail" class="job-log">
                  <div class="hint">最近日志</div>
                  <pre class="mono">{{ jobLogTail }}</pre>
                </div>
              </template>
            </fieldset>

            <!-- 健康确认结果：succeeded_but_unhealthy 明确显示 + 三条出路 -->
            <fieldset v-if="unhealthy || (healthPending && job && job.stage !== 'idle')">
              <legend>健康确认结果</legend>
              <div v-if="unhealthy" class="alert error show import-block">
                已切换到 <span class="mono">{{ healthDetailView?.targetTag || '—' }}</span>，但重启后健康确认未通过。
                页面不会自动重试，也不会自动回滚；请查看日志或回到上一版本。
              </div>
              <div v-else class="hint section-lead">
                主服务已切换、正在重启，等待健康确认（数据库已连接、迁移完成、关键表可读）。
              </div>
              <div class="meta-grid job-facts">
                <div>
                  <span class="k">阶段</span>
                  <span class="v">{{ healthDetailView?.stageLabel || '—' }}</span>
                </div>
                <div>
                  <span class="k">健康结论</span>
                  <span class="v">{{ healthDetailView?.healthDetail || '（尚未确认）' }}</span>
                </div>
                <div>
                  <span class="k">回退点</span>
                  <span class="v mono">{{ healthDetailView?.previousRef || '—' }}</span>
                </div>
                <div>
                  <span class="k">日志</span>
                  <span class="v mono">{{ healthDetailView?.logPath || '—' }}</span>
                </div>
              </div>
              <div v-if="healthCheckError" class="alert show import-block">
                重新检测失败：{{ healthCheckError }}
              </div>
              <div class="actions is-start">
                <button
                  type="button"
                  class="btn"
                  :disabled="healthChecking"
                  @click="recheckHealth"
                >{{ healthChecking ? '检测中…' : '重新检测' }}</button>
                <button
                  v-if="canRollbackToPrevious"
                  type="button"
                  class="btn btn-danger"
                  :disabled="applying"
                  @click="rollbackToPrevious"
                >回到上一版本（{{ previousTag }}）</button>
                <a
                  v-if="healthDetailView?.logPath"
                  class="btn"
                  href="/logs"
                  target="_blank"
                  rel="noopener"
                >查看日志（{{ healthDetailView.logPath }}）</a>
              </div>
              <div v-if="!autoRecheckEnabled" class="hint">
                自动重检已关闭，请手动点击「重新检测」。
              </div>
            </fieldset>

            <fieldset>
              <legend>更新历史</legend>
              <p class="hint section-lead">
                最近一次成功：{{ lastSuccessEntry ? `${lastSuccessEntry.target_tag}（${formatTs(lastSuccessEntry.finished_at)}）` : '（暂无成功记录）' }}；
                之后失败或取消 {{ failureCount }} 次。
              </p>
              <div v-if="historyLoading" class="hint">加载中…</div>
              <div v-else-if="historyError" class="hint is-error">
                加载失败：{{ historyError }}
                <button type="button" class="btn btn-sm" @click="loadHistory">重试</button>
              </div>
              <div v-else-if="!historyEntries.length" class="hint">暂无更新历史。</div>
              <table v-else class="token-list history-list">
                <thead>
                  <tr>
                    <th>目标版本</th>
                    <th>更新前版本</th>
                    <th>结果</th>
                    <th>耗时</th>
                    <th>结束时间</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(entry, i) in historyEntries" :key="`${entry.target_tag}-${entry.finished_at}-${i}`">
                    <td class="mono">{{ entry.target_tag || '—' }}</td>
                    <td class="mono">{{ entry.previous_tag || '—' }}</td>
                    <td>
                      <StatusPill
                        :tone="entry.result === 'succeeded' ? 'ok' : 'error'"
                        :label="entry.result_label || entry.result"
                      />
                      <div v-if="entry.rolled_back" class="hint">
                        已回滚：{{ entry.rollback_ok ? '恢复成功' : '恢复未完全成功' }}
                      </div>
                      <div v-if="entry.error" class="hint">{{ entry.error }}</div>
                    </td>
                    <td>{{ entry.duration_seconds != null ? `${entry.duration_seconds} 秒` : '—' }}</td>
                    <td>{{ formatTs(entry.finished_at) || '—' }}</td>
                  </tr>
                </tbody>
              </table>
              <div class="actions is-start">
                <button type="button" class="btn" :disabled="historyLoading" @click="loadHistory">刷新历史</button>
              </div>
            </fieldset>

            <!-- 公开仓无需 PAT，属于低频配置：折叠起来，避免占掉首屏。 -->
            <details class="github-panel">
              <summary>GitHub 连接（公开仓通常无需配置）</summary>
              <div class="github-panel__body">
                <p class="hint section-lead">
                  仓库为公开仓，已固定在代码内配置；版本检测与系统更新默认匿名访问 Releases，无需 PAT。
                  若遇 API 限流，可在此选填只读 Token；保存后立即生效，无需重启。留空「新 Token」表示保持现有值不变。
                </p>
                <div v-if="githubLoading" class="hint">加载配置中…</div>
                <template v-else>
                  <div class="meta-grid job-facts">
                    <div>
                      <span class="k">仓库</span>
                      <span class="v"><code>{{ githubConfig?.repo || '—' }}</code></span>
                    </div>
                    <div>
                      <span class="k">Token 状态</span>
                      <span class="v">
                        <StatusPill
                          :tone="githubConfig?.token_configured ? 'ok' : 'neutral'"
                          :label="githubConfig?.token_configured ? '已配置（可选）' : '未配置（公开仓可用）'"
                        />
                      </span>
                    </div>
                    <div>
                      <span class="k">上次保存</span>
                      <span class="v">{{ formatTs(githubConfig?.updated_at) || '（尚未在页面保存）' }}</span>
                    </div>
                  </div>
                  <div class="grid">
                    <div class="full">
                      <label for="githubToken">新 Token（可选）</label>
                      <input
                        class="input"
                        id="githubToken"
                        v-model="githubForm.token"
                        type="password"
                        autocomplete="new-password"
                        placeholder="公开仓可留空；仅在限流时需要"
                      >
                    </div>
                    <div class="full">
                      <label class="luyun-check-row">
                        <LuyunCheckbox v-model="githubForm.clear_token" />
                        <span>清除已保存的 Token（回退到环境变量；公开仓无 Token 也可继续版本检测）</span>
                      </label>
                    </div>
                  </div>
                  <div class="actions is-start">
                    <button type="button" class="btn" :disabled="githubLoading" @click="loadGithubConfig">刷新</button>
                    <button type="button" class="btn btn-primary" :disabled="githubSaving" @click="saveGithubConfig">
                      {{ githubSaving ? '保存中…' : '保存连接' }}
                    </button>
                  </div>
                </template>
              </div>
            </details>
          </div>
          <div v-show="activeSection === 'database'" class="section-panel">
            <PanelHeader
              icon="database"
              title="数据库凭据"
              :tone="dbIsPostgres ? 'info' : 'neutral'"
              :pill-label="dbIsPostgres ? 'PostgreSQL 后端' : 'SQLite 后端'"
              description="仅展示连接信息与脱敏 DSN；数据库密码不会在页面显示或索取，重置后的新密码只写入 env 文件。"
              :facts="dbFacts"
              :note="dbCredError ? `加载失败：${dbCredError}` : ''"
              :note-tone="dbCredError ? 'error' : ''"
            >
              <template #actions>
                <button type="button" class="btn" :disabled="dbCredLoading" @click="loadDbCred">
                  {{ dbCredLoading ? '刷新中…' : '刷新连接信息' }}
                </button>
              </template>
            </PanelHeader>

            <div v-if="dbCredLoading && !dbCred" class="hint">加载中…</div>
            <div v-else-if="!dbCred && !dbCredError" class="hint">尚未读取到连接信息，点右上角「刷新连接信息」重试。</div>

            <fieldset v-if="dbCred">
              <legend>重置数据库密码</legend>
              <p class="hint section-lead">
                重置会生成 32 位随机密码并写入 env 文件，需要二次确认当前后台管理员密码；页面不会显示新密码明文。
              </p>

              <div
                v-if="dbPasswordWarning && dbIsPostgres"
                class="alert show import-block"
                :class="dbPasswordWarningType"
              >
                {{ dbPasswordWarning }}
              </div>
              <div v-if="dbEnvOverride" class="alert error show import-block">
                检测到环境变量 <code>LUYUN_POSTGRES_DSN</code> 已被显式设置，其优先级高于 env 文件
                <code>{{ dbCred.env_file || 'env 文件' }}</code>，重置写文件不会生效。<template v-if="dbCred.env_override_target">当前生效的连接来自 <code>{{ dbCred.env_override_target }}</code>。</template>
                请先移除该环境变量并重启应用，再回来重置。
              </div>
              <div v-else-if="!dbIsPostgres" class="alert info show import-block">
                当前为 SQLite 后端，无需数据库密码。
              </div>
              <div v-else-if="!dbEnvFileWritable" class="alert info show import-block">
                env 文件当前不可写，重置可能失败；请先修好文件权限（{{ dbCred.env_file || 'env 文件' }}）。
              </div>

              <div class="actions is-start">
                <button
                  type="button"
                  class="btn btn-danger"
                  :disabled="dbResetDisabled || dbResetting"
                  @click="openDbReset"
                >重置密码</button>
              </div>
              <div v-if="dbResetDisabledReason" class="hint">{{ dbResetDisabledReason }}</div>

              <div
                v-if="dbResetResult.show"
                class="alert show import-block"
                :class="dbResetResult.restartError ? 'error' : 'success'"
              >
                <div>
                  新密码已生成并写入 <code>{{ dbResetResult.envFile || '—' }}</code><template v-if="dbResetResult.passwordLength">（{{ dbResetResult.passwordLength }} 位）</template>；页面不会显示密码明文。
                </div>
                <div v-if="dbResetResult.restartTriggered">应用正在重启，页面稍后会自动重连。</div>
                <div v-if="dbResetResult.restartError">
                  自动重启未成功：{{ dbResetResult.restartError }}，请手动重启应用使新密码生效。
                </div>
              </div>
            </fieldset>
          </div>
          <div v-show="activeSection === 'account'" class="section-panel">
            <PanelHeader
              icon="key"
              title="账号与会话"
              :tone="sessionUsername ? 'ok' : 'warn'"
              :pill-label="sessionUsername ? '已登录' : '未登录'"
              :description="sessionUserHint"
              :facts="accountFacts"
            >
              <template #actions>
                <button type="button" class="btn btn-danger" @click="onLogoutConfirm">退出登录</button>
              </template>
            </PanelHeader>

            <fieldset>
              <legend>修改登录密码</legend>
              <form autocomplete="off" @submit.prevent="onChangePassword">
                <div class="grid">
                  <div>
                    <label for="oldPassword">当前密码</label>
                    <input class="input" id="oldPassword" v-model="changePwdForm.oldPassword" type="password" required autocomplete="current-password">
                  </div>
                  <div>
                    <label for="newPassword">新密码</label>
                    <input class="input" id="newPassword" v-model="changePwdForm.newPassword" type="password" required autocomplete="new-password" minlength="8">
                    <div class="hint">至少 8 位字符。</div>
                  </div>
                </div>
                <div class="actions is-start">
                  <button type="submit" class="btn btn-primary" :disabled="changingPwd">{{ changePwdBtnLabel }}</button>
                </div>
              </form>
            </fieldset>

            <fieldset>
              <legend>API Token（KDS 等设备）</legend>
              <p class="hint section-lead">
                生成后仅显示一次，请立即复制保存；撤销后调用方立即失效。
              </p>
              <div class="grid">
                <div class="full">
                  <label for="tokenLabel">备注标签（可选）</label>
                  <input class="input" id="tokenLabel" v-model="tokenLabel" type="text" placeholder="例如 厨房平板-1">
                </div>
              </div>
              <div class="actions is-start">
                <button type="button" class="btn btn-primary" :disabled="generatingToken" @click="genToken">{{ genTokenBtnLabel }}</button>
                <button type="button" class="btn" :disabled="tokensLoading" @click="loadTokenList">刷新列表</button>
              </div>

              <div v-if="tokensLoading" class="hint">加载中…</div>
              <div v-else-if="tokensError" class="hint is-error">加载失败：{{ tokensError }}</div>
              <div v-else-if="!tokens.length" class="hint">暂无 API Token。</div>
              <table v-else class="token-list token-table">
                <thead>
                  <tr>
                    <th>前缀</th>
                    <th>标签</th>
                    <th>创建时间</th>
                    <th>过期时间</th>
                    <th>状态</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="t in tokens" :key="t.token_hash_prefix" :class="{ revoked: !!t.revoked_at }">
                    <td class="mono">{{ t.token_hash_prefix }}…</td>
                    <td>{{ t.label || '—' }}</td>
                    <td>{{ formatTs(t.created_at) || '—' }}</td>
                    <td>{{ formatTs(t.expires_at) || '永不过期' }}</td>
                    <td>
                      <StatusPill
                        :tone="t.revoked_at ? 'neutral' : 'ok'"
                        :label="t.revoked_at ? '已撤销' : '有效'"
                      />
                    </td>
                    <td class="token-table__actions">
                      <button
                        v-if="!t.revoked_at"
                        type="button"
                        class="btn btn-sm btn-danger"
                        @click="onRevokeTokenConfirm(t.token_hash_prefix)"
                      >撤销</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </fieldset>
          </div>
        </div>
      </div>
    </div>

    <div v-if="importSuccessModal.show" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>恢复完成</h3>
        <p>{{ importSuccessModal.message }}</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" @click="confirmImportSuccessRedirect">
            {{ importSuccessModal.sessionInvalidated ? '重新登录' : '确认' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 两步确认：覆盖导入 / 数据回滚 / 清理 / 保存保留配置都走这里，不使用浏览器原生 confirm -->
    <div v-if="confirmState.open" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>{{ confirmState.title }}</h3>
        <p>{{ confirmState.message }}</p>
        <ul v-if="confirmState.details.length" class="confirm-details">
          <li v-for="(item, i) in confirmState.details" :key="i">{{ item }}</li>
        </ul>
        <label
          v-for="box in confirmState.checkboxes"
          :key="box.key"
          class="luyun-check-row confirm-check"
        >
          <LuyunCheckbox v-model="confirmChecked[box.key]" />
          <span>{{ box.label }}</span>
        </label>
        <div class="actions">
          <button type="button" class="btn" @click="closeConfirm">取消</button>
          <button
            type="button"
            :class="['btn', confirmState.danger ? 'btn-danger' : 'btn-primary']"
            :disabled="!confirmReady"
            @click="onConfirmClick"
          >{{ confirmState.confirmLabel }}</button>
        </div>
      </div>
    </div>

    <!-- 重置数据库密码：二次确认必须输入当前后台管理员密码，不使用浏览器原生 prompt -->
    <div v-if="dbResetConfirm.open" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>重置数据库密码</h3>
        <p>
          将为 <code>{{ dbCred?.user || '当前数据库角色' }}</code> 生成 32 位随机密码并写入
          <code>{{ dbCred?.env_file || 'env 文件' }}</code>。页面不会显示新密码明文，写入成功后会自动重启应用使其生效。
          请输入当前后台管理员密码以确认本次重置。
        </p>
        <div class="grid">
          <div class="full">
            <label for="dbResetPassword">当前后台管理员密码</label>
            <div class="password-row">
              <input
                class="input"
                id="dbResetPassword"
                v-model="dbResetConfirm.password"
                :type="dbResetShowPassword ? 'text' : 'password'"
                autocomplete="current-password"
                placeholder="输入当前登录后台的密码"
                @keyup.enter="dbResetSubmit"
              >
              <button type="button" class="toggle" @click="dbResetShowPassword = !dbResetShowPassword">{{ dbResetToggleLabel }}</button>
            </div>
          </div>
        </div>
        <div v-if="dbResetConfirm.error" class="alert error show import-block">{{ dbResetConfirm.error }}</div>
        <div class="actions">
          <button type="button" class="btn" :disabled="dbResetting" @click="closeDbReset">取消</button>
          <button type="button" class="btn btn-danger" :disabled="dbResetting" @click="dbResetSubmit">{{ dbResetBtnLabel }}</button>
        </div>
      </div>
    </div>

    <div v-if="tokenModal.show" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>API Token 已生成</h3>
        <p>请立即复制并保存，关闭后将无法再次查看完整 Token。</p>
        <div class="token-display">{{ tokenModal.plaintext }}</div>
        <div class="actions">
          <button type="button" class="btn" @click="copyToken">{{ copyLabel }}</button>
          <button type="button" class="btn btn-primary" @click="closeTokenModal">我已保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-page {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(circle at 12% 0%, rgba(99, 102, 241, 0.14), transparent 30%),
    radial-gradient(circle at 88% 10%, rgba(6, 182, 212, 0.08), transparent 28%),
    var(--bg);
  color: var(--text);
  min-height: 100vh;
}

.container {
  max-width: 920px;
  margin: 32px auto 80px;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px 32px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
}

.back-btn {
  display: inline-flex; align-items: center; gap: 6px;
  margin-bottom: 14px; padding: 7px 14px;
  background: var(--card2); border: 1px solid var(--border); border-radius: 7px;
  color: var(--text-dim); font-size: 13px; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.15s;
}
.back-btn:hover { color: var(--text); border-color: var(--accent); }

h1 { font-size: 20px; margin-bottom: 6px; display: flex; align-items: center; gap: 10px; }
.subtitle { color: var(--text-dim); font-size: 13px; margin-bottom: 18px; line-height: 1.6; }

.status-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
}
.status-pill.ok { background: rgba(34, 197, 94, 0.15); color: var(--green); }
.status-pill.empty { background: rgba(245, 158, 11, 0.15); color: var(--yellow); }
.status-pill.error { background: rgba(239, 68, 68, 0.15); color: var(--red); }

.alert {
  padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px;
}
.alert.success { background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }
.alert.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert.info { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.25); color: #93c5fd; }

fieldset {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px 18px; margin-bottom: 16px;
  background: rgba(10, 13, 22, 0.5);
}
legend { font-size: 12px; font-weight: 700; color: var(--text-dim); padding: 0 8px; letter-spacing: 0.5px; }

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.grid .full { grid-column: 1 / -1; }
.grid .input { width: 100%; }
label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; font-weight: 600; }
/* 与 label 同款，但用在不能嵌套 <label> 的场景（如「标签 + 复选行」）。 */
.field-label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; font-weight: 600; }
.dim { color: var(--text-dim); font-weight: normal; opacity: 0.85; }
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; }
.hint.is-warn { color: var(--yellow); opacity: 1; }
.hint.is-error { color: #fca5a5; opacity: 1; }
/* fieldset 开头的说明段：与下方控件拉开，避免和 .hint 的 4px 顶距混在一起。 */
.section-lead { margin: 0 0 12px; }

.upload-progress {
  display: flex; align-items: center; gap: 10px;
  margin-top: 10px;
}
.upload-progress__track {
  flex: 1; height: 6px; border-radius: 999px;
  background: rgba(255, 255, 255, 0.08); overflow: hidden;
}
.upload-progress__bar {
  height: 100%; border-radius: 999px;
  background: var(--accent);
  transition: width 0.2s ease, background 0.2s ease;
}
.upload-progress__bar.indeterminate {
  width: 40% !important;
  animation: upload-progress-pulse 1.1s ease-in-out infinite;
}
.upload-progress.is-success .upload-progress__bar { background: var(--green); }
.upload-progress.is-error .upload-progress__bar { background: var(--red); }
.upload-progress__label {
  font-size: 11px; color: var(--text-dim); white-space: nowrap; min-width: 72px;
}
.upload-progress.is-success .upload-progress__label { color: var(--green); }
.upload-progress.is-error .upload-progress__label { color: var(--red); }
@keyframes upload-progress-pulse {
  0% { transform: translateX(-120%); }
  100% { transform: translateX(320%); }
}

.password-row { position: relative; }
.password-row .input { padding-right: 64px; }
.password-row .toggle {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: transparent; border: none; color: var(--text-dim);
  font-size: 11px; cursor: pointer; padding: 4px 8px;
}
.password-row .toggle:hover { color: var(--text); }

.url-row { display: flex; gap: 8px; align-items: stretch; }
.url-row .input { flex: 1; min-width: 0; }

.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
/* 备份 / 更新面板里的操作区一律左对齐，和上方表单对齐。 */
.actions.is-start { justify-content: flex-start; }

/* ===== 各面板通用的细节类 ===== */
.import-block { margin-top: 12px; }
/* grid 单元格里的复选行撑满一行，点击区域跟着变大。 */
.grid .luyun-check-row { display: flex; }
.confirm-check { margin-top: 10px; }
.plain-list, .bullet-list {
  margin: 4px 0 0; padding-left: 18px;
  font-size: 11px; color: var(--text-dim); line-height: 1.6;
}
.bullet-list { margin-top: 6px; }
.check-stack { display: grid; gap: 6px; margin-top: 2px; }
.import-preview-grid { margin-top: 12px; }

.cleanup-preview { margin-top: 14px; }
.cleanup-list { display: grid; gap: 4px; margin: 8px 0 0; padding: 0; list-style: none; }
.cleanup-list li {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 11px; line-height: 1.6; color: var(--text-dim);
}

/* 发行版目录：Tag / 发布时间收紧成内容宽，操作列右对齐，
   免得「最新」胶囊贴到发布时间上、名称列被挤成一条缝。 */
.release-list th:first-child, .release-list td:first-child,
.release-list th:nth-child(3), .release-list td:nth-child(3) {
  width: 1%; white-space: nowrap;
}
.release-list th:last-child, .release-list td:last-child {
  width: 1%; text-align: right; white-space: nowrap;
}
.release-list tbody tr:hover { background: rgba(255, 255, 255, 0.03); }
.release-blocked { margin: 0 0 4px; }

.release-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }

/* 正式发行版目录默认只列最新几个，其余折进这里展开。 */
.release-more { margin-top: 10px; }
.release-more > summary {
  display: flex; align-items: center; gap: 6px; min-height: 36px;
  cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text-dim);
  list-style: none;
}
.release-more > summary::-webkit-details-marker { display: none; }
.release-more > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.release-more__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.release-more[open] > summary,
.release-more[open] .release-more__icon { color: var(--text); }
.release-more[open] .release-more__icon { transform: rotate(90deg); }
.release-more .token-list { margin-top: 4px; }
@media (prefers-reduced-motion: reduce) {
  .release-more__icon { transition: none; }
}

.job-facts { margin-top: 12px; }
.job-log { margin-top: 12px; }
.job-log pre {
  max-height: 220px; overflow: auto; margin: 6px 0 0; padding: 10px;
  white-space: pre-wrap; word-break: break-word;
  background: rgba(0, 0, 0, 0.25); border-radius: 8px; font-size: 12px;
}

.github-panel {
  border: 1px solid var(--border); border-radius: 10px;
  background: rgba(10, 13, 22, 0.5); margin-bottom: 16px;
}
.github-panel > summary {
  display: flex; align-items: center; min-height: 44px;
  padding: 0 18px; cursor: pointer;
  font-size: 12px; font-weight: 700; color: var(--text-dim); letter-spacing: 0.5px;
}
.github-panel > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.github-panel__body { padding: 0 18px 18px; }

.meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px 18px;
  background: rgba(10, 13, 22, 0.6); border-radius: 8px; padding: 10px 14px;
  font-size: 12px;
}
.meta-grid div { display: flex; justify-content: space-between; gap: 10px; }
.meta-grid .k { color: var(--text-dim); opacity: 0.85; }
.meta-grid .v { color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.setup-body { display: flex; gap: 24px; align-items: flex-start; }
.setup-nav {
  flex: 0 0 180px; display: flex; flex-direction: column; gap: 4px;
  position: sticky; top: 32px;
}
.setup-nav .nav-item {
  display: inline-flex; align-items: center; gap: 8px;
  text-align: left; padding: 10px 14px; background: transparent;
  border: 1px solid transparent; border-radius: 8px;
  color: var(--text-dim); font-size: 13px; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.15s;
}
.setup-nav .nav-item :deep(svg) { opacity: 0.8; }
.setup-nav .nav-item:hover { color: var(--text); background: var(--card2); }
.setup-nav .nav-item.active {
  color: var(--accent); background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.35);
}
.setup-nav .nav-item.active :deep(svg) { opacity: 1; }
.setup-content { flex: 1 1 auto; min-width: 0; }

/* 表单里的辅助块：标题 + 行内操作按钮 + 说明 +（可选）选择器。 */
.field-block {
  padding: 10px 12px; border-radius: 8px;
  background: rgba(10, 13, 22, 0.5); border: 1px solid var(--border);
}
.field-block__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.field-block__title { font-size: 12px; font-weight: 600; color: var(--text); }
.field-block__head .btn { margin-left: auto; }
.field-block .hint { margin-top: 6px; }
.field-block .input { margin-top: 2px; }
.field-block__pick { margin-top: 8px; }
.field-block__pick label { margin-bottom: 4px; }

.switch-row { display: flex; align-items: center; gap: 8px; font-weight: normal; color: var(--text); font-size: 13px; margin-top: 2px; }

.token-list { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
.token-list th, .token-list td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
/* Token 表格：撤销列贴右，状态列不折行。 */
.token-table th:last-child, .token-table td:last-child { width: 1%; text-align: right; white-space: nowrap; }
.token-table th:nth-child(5), .token-table td:nth-child(5) { width: 1%; white-space: nowrap; }
.token-list th { color: var(--text-dim); font-weight: 600; }
.token-list .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.token-list tr.revoked { color: var(--text-dim); text-decoration: line-through; opacity: 0.7; }

.modal-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.65);
  z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal-box {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 24px; max-width: 520px; width: 100%;
}
.modal-box h3 { font-size: 16px; margin-bottom: 10px; }
.modal-box p { font-size: 13px; color: var(--text-dim); margin-bottom: 14px; line-height: 1.6; }
.token-display {
  background: var(--card2); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px; word-break: break-all; margin-bottom: 16px; user-select: all;
}

.confirm-details {
  margin: 0 0 8px; padding-left: 18px;
  font-size: 12px; color: var(--text-dim); line-height: 1.7;
}
.confirm-details li { margin-bottom: 2px; }

/* ===== 系统健康状态（图表化运维面板）===== */
.health-panel__note { margin: 0 0 12px; }

.health-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.health-card {
  display: flex; flex-direction: column; gap: 8px;
  min-width: 0;
  padding: 12px 14px 14px;
  background: rgba(10, 13, 22, 0.5);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.health-card__title {
  display: flex; align-items: center; gap: 6px;
  margin: 0; font-size: 12px; font-weight: 700; letter-spacing: 0.4px; color: var(--text-dim);
}

/* 明细一律「标签 + 等宽数字」，长路径换行而不是撑出横向滚动。 */
.health-facts { display: grid; gap: 4px; margin: 0; font-size: 11px; }
.health-facts > div { display: flex; justify-content: space-between; gap: 10px; min-width: 0; }
.health-facts dt { color: var(--text-dim); white-space: nowrap; }
.health-facts dd {
  margin: 0; min-width: 0; text-align: right; color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}

.health-warn { margin: 0; padding: 8px 10px; border-radius: 8px; font-size: 11px; line-height: 1.5; }
.health-warn.is-critical { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.health-warn.is-warning { background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.3); color: #fde68a; }

.health-usage { display: flex; flex-direction: column; gap: 8px; }

.health-raw {
  padding: 10px 14px;
  background: rgba(10, 13, 22, 0.5);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 12px;
}
.health-raw > summary {
  display: flex; align-items: center; min-height: 36px;
  cursor: pointer; font-weight: 600; color: var(--text-dim);
}
.health-raw > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.health-raw__group { margin-top: 10px; }
.health-raw__group h4 { margin: 0 0 6px; font-size: 11px; letter-spacing: 0.4px; color: var(--text-dim); }

@media (max-width: 700px) {
  .container { padding: 20px 18px; }
  .grid { grid-template-columns: 1fr; }
  .meta-grid { grid-template-columns: 1fr; }
  .setup-body { flex-direction: column; gap: 12px; }
  .setup-nav {
    flex: none; width: 100%; flex-direction: row; overflow-x: auto;
    position: static; border-bottom: 1px solid var(--border); padding-bottom: 8px;
  }
  .setup-nav .nav-item { white-space: nowrap; }
  .health-cards { grid-template-columns: minmax(0, 1fr); }
  /* 窄屏只保留版本对照的关键列：名称 / 更新前版本 / 耗时 先让位，避免横向滚动。 */
  .release-list th:nth-child(2), .release-list td:nth-child(2),
  .history-list th:nth-child(2), .history-list td:nth-child(2),
  .history-list th:nth-child(4), .history-list td:nth-child(4) { display: none; }
  .release-actions { width: 100%; }
  /* 裁列后按钮列变窄，别把「应用此版本」折成两行。 */
  .release-actions .btn-sm { white-space: nowrap; }
  /* Token 表格同理：窄屏只留前缀 / 标签 / 状态 / 撤销，并收紧内边距给标签留宽度。 */
  .token-table th, .token-table td { padding: 8px 6px; }
  .token-table th:nth-child(3), .token-table td:nth-child(3),
  .token-table th:nth-child(4), .token-table td:nth-child(4) { display: none; }
  .token-table__actions .btn { white-space: nowrap; }
}
</style>

