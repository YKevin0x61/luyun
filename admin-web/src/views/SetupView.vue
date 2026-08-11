<script setup>
import { onMounted, reactive, ref } from 'vue'
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
  { id: 'pos', label: 'POS 凭据', icon: 'settings' },
  { id: 'runtime', label: '运行配置', icon: 'settings' },
  { id: 'backup', label: '备份中心', icon: 'settings' },
  { id: 'update', label: '系统更新', icon: 'settings' },
  { id: 'account', label: '账号与 API Token', icon: 'settings' },
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
  onExportBackup,
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
  importPreviewItems,
  onImportFileChange,
  onPreviewImport,
  onApplyImport,
  importSuccessModal,
  confirmImportSuccessRedirect,
  snapshots,
  snapshotsLoading,
  snapshotsError,
  rollingBackTs,
  formatBytes,
  formatTs,
  loadSnapshots,
  onRollbackSnapshot,
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
  jobPolling,
  jobStageLabel,
  jobInProgress,
  loadJobStatus,
} = useSystemUpdate({ showAlert, clearAlert })

function switchSection(id) {
  activeSection.value = id
  if (id === 'account') loadTokenList()
  if (id === 'runtime') loadRuntimeSettings()
  if (id === 'backup') loadSnapshots()
  if (id === 'update') {
    loadGithubConfig()
    loadVersionCheck()
    loadJobStatus()
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
      <h1>
        系统配置
        <span class="status-pill" :class="configured ? 'ok' : 'empty'">{{ configured ? '凭据已配置' : '凭据未配置' }}</span>
      </h1>
      <p class="subtitle">
        POS 敏感信息加密保存在本机 <code>data/credentials.enc</code>；营业时段 / 轮询间隔等运行配置存于数据库并可在线热更新。
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
          >{{ s.label }}</button>
        </nav>

        <div class="setup-content">
          <div v-if="alert.show" class="alert show" :class="alert.type">{{ alert.message }}</div>

          <div v-show="activeSection === 'pos'" class="section-panel">
        <div v-if="metaItems" style="margin-bottom: 18px;">
          <div class="meta-grid">
            <div v-for="[k, v] in metaItems" :key="k"><span class="k">{{ k }}</span><span class="v">{{ v }}</span></div>
          </div>
        </div>

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
                <div class="hint">龙管家 <strong>2.0 App</strong> 的手机号与密码（不是 cy7mm 网页 1.0 账号）。密码 Fernet 加密保存，编辑时不回显。</div>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>门店信息</legend>
            <div class="grid">
              <div class="full">
                <label>从龙管家 2.0 账号拉取（推荐）</label>
                <div class="url-row">
                  <button type="button" class="btn btn-sm" :disabled="discoveringShops" @click="onDiscoverShops">{{ discoverBtnLabel }}</button>
                </div>
                <div class="hint">填写上方手机号与密码后点击，自动获取 <code>shop_id</code> / <code>company_id</code> / 店名，无需浏览器登录 cy7mm。</div>
                <div v-if="discoveredShops.length > 1" class="hint" style="margin-top:8px;">
                  <label for="discoveredShopPick">多个门店时选择：</label>
                  <select id="discoveredShopPick" class="input" @change="onPickDiscoveredShop">
                    <option v-for="(shop, idx) in discoveredShops" :key="`${shop.shop_id}-${shop.company_id}`" :value="idx">
                      {{ shop.shop_name || shop.shop_id }} — shop_id={{ shop.shop_id }}, company_id={{ shop.company_id }}
                    </option>
                  </select>
                </div>
              </div>
              <div class="full">
                <label for="targetUrl">从 App WebView URL 解析（可选）</label>
                <div class="url-row">
                  <input
                    class="input"
                    id="targetUrl"
                    v-model="credForm.targetUrl"
                    type="url"
                    placeholder="https://cy7mm.wuuxiang.com/home/tableList/1/100001/200002?shopName=..."
                  >
                  <button type="button" class="btn btn-sm" @click="onParseUrl">解析</button>
                </div>
                <div class="hint">若已在 App 内打开「报表 → 实时桌态 → 占用桌台」，可复制 WebView 地址栏完整 URL 粘贴解析；勿使用带 <code>{shopId}</code> 的占位模板。</div>
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

          <div class="actions">
            <button type="button" class="btn" @click="fetchCurrent">刷新</button>
            <button type="button" class="btn" :disabled="verifying" @click="onVerify">{{ verifyBtnLabel }}</button>
            <button v-if="configured" type="button" class="btn btn-danger" @click="onClearCredentials">清空凭据</button>
            <button type="submit" class="btn btn-primary" :disabled="saving">{{ saveBtnLabel }}</button>
          </div>
        </form>
          </div>

          <div v-show="activeSection === 'runtime'" class="section-panel">
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
                  <label>浏览器无头模式</label>
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
              <div class="hint">
                改动后保存即热生效，无需重启服务。上次更新：{{ runtimeUpdatedAt ? String(runtimeUpdatedAt).replace('T', ' ').slice(0, 19) : '（尚未自定义，使用默认值）' }}
              </div>
            </fieldset>

            <div class="actions">
              <button type="button" class="btn" :disabled="runtimeLoading" @click="loadRuntimeSettings">刷新</button>
              <button type="button" class="btn" @click="resetRuntimeDefaults">恢复默认</button>
              <button type="button" class="btn btn-primary" :disabled="runtimeSaving" @click="saveRuntimeSettings">{{ runtimeSaveLabel }}</button>
            </div>
          </div>

          <div v-show="activeSection === 'backup'" class="section-panel">
            <fieldset>
              <legend>导出备份</legend>
              <p class="hint" style="margin-bottom:12px;">
                导出为口令加密的 <code>.luyunbak</code> 文件，可打包 POS 凭据、运行配置与业务数据。口令遗失将无法解密恢复，请牢记。
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
                    <LuyunCheckbox v-model="exportForm.include_app_db" />
                    <span>同时打包业务数据库（订单、档口映射等）</span>
                  </label>
                </div>
                <div class="full">
                  <label class="luyun-check-row">
                    <LuyunCheckbox v-model="exportForm.include_recipes" />
                    <span>同时打包配方数据</span>
                  </label>
                </div>
              </div>
              <div v-if="exportHasLargePayload" class="hint" style="margin-top:8px;">
                已勾选业务数据或配方，备份文件可能较大，导出耗时会更长。
              </div>
              <div class="actions">
                <button type="button" class="btn btn-primary" :disabled="exporting" @click="onExportBackup">{{ exportBtnLabel }}</button>
              </div>
            </fieldset>

            <fieldset>
              <legend>导入 / 恢复</legend>
              <p class="hint" style="margin-bottom:12px;">
                选择 <code>.luyunbak</code> 备份文件并输入导出时的口令，先预览核对再确认导入（备份文件只需上传一次）。
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
              <div class="actions" style="justify-content:flex-start;">
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

              <div v-if="importPreview" class="meta-grid" style="margin-top:12px;">
                <div v-for="[k, v] in importPreviewItems" :key="k"><span class="k">{{ k }}</span><span class="v">{{ v }}</span></div>
              </div>

              <div v-if="importPreview" style="margin-top:14px;">
                <div class="hint" style="margin-bottom:8px;">恢复模式</div>
                <LuyunRadioGroup
                  v-model="importState.mode"
                  :options="[
                    { value: 'merge', label: '合并去重追加' },
                    { value: 'overwrite', label: '覆盖恢复 · 整库替换' },
                  ]"
                />
              </div>

              <div v-if="importPreview" style="margin-top:14px;">
                <div class="hint" style="margin-bottom:8px;">应用项</div>
                <label class="luyun-check-row" style="display:flex;margin-top:0;">
                  <LuyunCheckbox v-model="importState.apply_credentials" />
                  <span>POS 凭据</span>
                </label>
                <label class="luyun-check-row" style="display:flex;margin-top:6px;">
                  <LuyunCheckbox v-model="importState.apply_runtime" :disabled="!importIncludes.runtime" />
                  <span>运行配置</span>
                </label>
                <label class="luyun-check-row" style="display:flex;margin-top:6px;">
                  <LuyunCheckbox v-model="importState.apply_app_db" :disabled="!importIncludes.app_db" />
                  <span>业务数据库</span>
                </label>
                <label class="luyun-check-row" style="display:flex;margin-top:6px;">
                  <LuyunCheckbox v-model="importState.apply_recipes" :disabled="!importIncludes.recipes_db" />
                  <span>配方数据</span>
                </label>
              </div>

              <div v-if="importPreview" class="actions">
                <button type="button" class="btn btn-danger" :disabled="importing" @click="onApplyImport">{{ importApplyLabel }}</button>
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
            </fieldset>

            <fieldset>
              <legend>回滚快照</legend>
              <p class="hint" style="margin-bottom:12px;">
                覆盖导入前系统会自动创建回滚点；也可在此手动回滚到历史快照。
              </p>
              <div v-if="snapshotsLoading" class="hint">加载中…</div>
              <div v-else-if="snapshotsError" class="hint" style="color:var(--red);">加载失败：{{ snapshotsError }}</div>
              <div v-else-if="!snapshots.length" class="hint">暂无回滚点</div>
              <table v-else class="token-list">
                <thead>
                  <tr><th>时间戳</th><th>创建时间</th><th>大小</th><th>文件</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-for="s in snapshots" :key="s.ts">
                    <td class="mono">{{ s.ts }}</td>
                    <td>{{ formatTs(s.created_at) }}</td>
                    <td>{{ formatBytes(s.size_bytes) }}</td>
                    <td class="mono">{{ (s.files || []).join(', ') || '—' }}</td>
                    <td>
                      <button
                        type="button"
                        class="btn btn-sm btn-danger"
                        :disabled="rollingBackTs === s.ts"
                        @click="onRollbackSnapshot(s.ts)"
                      >{{ rollingBackTs === s.ts ? '回滚中…' : '回滚' }}</button>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div class="actions" style="justify-content:flex-start;">
                <button type="button" class="btn" :disabled="snapshotsLoading" @click="loadSnapshots">刷新列表</button>
              </div>
            </fieldset>
          </div>

          <div v-show="activeSection === 'update'" class="section-panel">
            <fieldset>
              <legend>GitHub 连接</legend>
              <p class="hint" style="margin-bottom:12px;">
                仓库为公开仓，已固定在代码内配置；版本检测与系统更新默认匿名访问 Releases，无需 PAT。
                若遇 API 限流，可在此选填只读 Token；保存后立即生效，无需重启。留空「新 Token」表示保持现有值不变。
              </p>
              <div v-if="githubLoading" class="hint">加载配置中…</div>
              <template v-else>
                <div class="meta-grid" style="margin-bottom:12px;">
                  <div>
                    <span class="k">仓库</span>
                    <span class="v"><code>{{ githubConfig?.repo || '—' }}</code></span>
                  </div>
                  <div>
                    <span class="k">Token 状态</span>
                    <span class="v">
                      <span class="status-pill" :class="githubConfig?.token_configured ? 'ok' : 'empty'">
                        {{ githubConfig?.token_configured ? '已配置（可选）' : '未配置（公开仓可用）' }}
                      </span>
                    </span>
                  </div>
                  <div>
                    <span class="k">上次保存</span>
                    <span class="v">{{ githubConfig?.updated_at ? String(githubConfig.updated_at).replace('T', ' ').slice(0, 19) : '（尚未在页面保存）' }}</span>
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
                <div class="actions" style="justify-content:flex-start;margin-top:12px;">
                  <button type="button" class="btn" :disabled="githubLoading" @click="loadGithubConfig">刷新</button>
                  <button type="button" class="btn btn-primary" :disabled="githubSaving" @click="saveGithubConfig">
                    {{ githubSaving ? '保存中…' : '保存连接' }}
                  </button>
                </div>
              </template>
            </fieldset>

            <fieldset>
              <legend>版本检测</legend>
              <p class="hint" style="margin-bottom:12px;">
                对照本机版本清单与 GitHub 正式 Release。应用更新由独立作业执行，不会在网页进程内改代码。
              </p>
              <div v-if="versionLoading" class="hint">检测中…</div>
              <template v-else-if="versionCheck">
                <div class="meta-grid" style="margin-bottom:12px;">
                  <div>
                    <span class="k">当前安装</span>
                    <span class="v mono">{{ versionCheck.installed_tag || '（无版本清单）' }}</span>
                  </div>
                  <div>
                    <span class="k">APP_VERSION</span>
                    <span class="v mono">{{ versionCheck.app_version || '—' }}</span>
                  </div>
                  <div>
                    <span class="k">最新正式版</span>
                    <span class="v mono">{{ versionCheck.latest_tag || '（无）' }}</span>
                  </div>
                  <div>
                    <span class="k">部署模式</span>
                    <span class="v mono">
                      {{ versionCheck.deploy_mode || '—' }}
                      <template v-if="versionCheck.deploy_mode === 'docker'">
                        <span class="dim">（{{ versionCheck.docker_container || '未设 LUYUN_DOCKER_CONTAINER' }}）</span>
                      </template>
                    </span>
                  </div>
                  <div>
                    <span class="k">状态</span>
                    <span class="v">
                      <span
                        class="status-pill"
                        :class="versionCheck.degraded ? 'empty' : (updateAvailable ? 'empty' : 'ok')"
                      >{{ statusSummary }}</span>
                    </span>
                  </div>
                </div>
                <div
                  v-if="versionCheck.degraded"
                  class="alert show"
                  style="margin-bottom:12px;"
                >
                  本机已装身份异常：{{ degradedReasonLabel(versionCheck.degraded_reason) || '未知原因' }}。
                  版本对比可能不准确；若本机已是运行实例且自检通过，可通过下方「应用此版本」切到正式发行包。
                </div>
                <div v-else-if="updateAvailable" class="hint" style="margin-bottom:12px;color:var(--accent);">
                  发现更新：{{ versionCheck.installed_tag }} → {{ versionCheck.latest_tag }}
                </div>
                <div v-else class="hint" style="margin-bottom:12px;">
                  当前已对齐最新正式发行版。仍可选择较旧 tag 回滚。
                </div>

                <div v-if="preflightChecks.length" style="margin-top:8px;">
                  <div class="k" style="margin-bottom:8px;">更新环境自检</div>
                  <ul class="hint" style="margin:0;padding-left:18px;list-style:none;">
                    <li
                      v-for="c in preflightChecks"
                      :key="c.code"
                      style="margin-bottom:6px;display:flex;gap:8px;align-items:flex-start;"
                    >
                      <span
                        class="status-pill"
                        :class="c.ok ? 'ok' : 'empty'"
                        style="flex-shrink:0;"
                      >{{ c.ok ? '通过' : '未通过' }}</span>
                      <span>{{ c.message }}</span>
                    </li>
                  </ul>
                  <div
                    v-if="!healthyRuntime"
                    class="alert show"
                    style="margin-top:12px;"
                  >
                    当前不是健康的运行实例，已禁用「应用更新」。请先修好上方红灯（重启能力、GitHub Releases 可达性等）后再试。
                  </div>
                  <div
                    v-else-if="discardLocalChangesAllowed"
                    class="alert show"
                    style="margin-top:12px;"
                  >
                    部署目录有本地改动，默认禁止更新。若确认丢弃这些改动，请在确认对话框中勾选后再继续。
                  </div>
                </div>
              </template>
              <div v-else class="hint">尚未检测，点击下方按钮开始。</div>
              <div class="actions" style="justify-content:flex-start;">
                <button type="button" class="btn btn-primary" :disabled="versionLoading" @click="refreshVersionCheck">
                  {{ versionLoading ? '检测中…' : '检测版本' }}
                </button>
              </div>
            </fieldset>

            <fieldset>
              <legend>正式发行版目录</legend>
              <p class="hint" style="margin-bottom:12px;">
                默认排除预发布（prerelease）。选择较旧 tag 即回滚到该发行版。
              </p>
              <div v-if="versionLoading" class="hint">加载中…</div>
              <div v-else-if="!versionCheck" class="hint">请先执行版本检测</div>
              <div v-else-if="!(versionCheck.releases || []).length" class="hint">暂无正式发行版</div>
              <table v-else class="token-list">
                <thead>
                  <tr>
                    <th>Tag</th>
                    <th>名称</th>
                    <th>发布时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in versionCheck.releases" :key="r.tag">
                    <td class="mono">{{ r.tag }}</td>
                    <td>{{ r.name || '—' }}</td>
                    <td>{{ r.published_at ? String(r.published_at).replace('T', ' ').slice(0, 19) : '—' }}</td>
                    <td>
                      <span
                        v-if="r.tag === versionCheck.installed_tag && !versionCheck.degraded"
                        class="status-pill ok"
                      >当前</span>
                      <template v-else>
                        <span
                          v-if="r.tag === versionCheck.latest_tag"
                          class="status-pill empty"
                          style="margin-right:8px;"
                        >最新</span>
                        <button
                          v-if="canShowApply"
                          type="button"
                          class="btn btn-primary"
                          style="padding:4px 10px;font-size:12px;"
                          :disabled="applying || jobInProgress"
                          @click="openApplyConfirm(r.tag)"
                        >应用此版本</button>
                        <span v-else class="dim">自检未通过</span>
                      </template>
                    </td>
                  </tr>
                </tbody>
              </table>
            </fieldset>

            <fieldset v-if="confirmOpen">
              <legend>确认应用更新</legend>
              <p class="hint" style="margin-bottom:12px;">
                将把运行实例切换到 <span class="mono">{{ selectedTag }}</span>。
                作业会先强制备份，再下载发行包、切换应用目录、按需同步依赖并重启主服务。
              </p>
              <div v-if="discardLocalChangesAllowed" class="alert show" style="margin-bottom:12px;">
                部署目录有本地改动。继续将丢弃这些改动并以目标发行包覆盖。
              </div>
              <label
                v-if="discardLocalChangesAllowed"
                class="check"
                style="margin-bottom:12px;"
              >
                <LuyunCheckbox v-model="discardLocalChanges" />
                <span>我确认丢弃部署目录中的本地改动</span>
              </label>
              <div v-if="needsPeakOverride" class="alert show" style="margin-bottom:12px;">
                当前处于营业高峰时段。若仍要继续，请勾选下方覆盖项后再次确认。
              </div>
              <label class="check" style="margin-bottom:12px;">
                <LuyunCheckbox v-model="peakOverride" />
                <span>我已知晓营业高峰风险，仍然执行更新</span>
              </label>
              <div class="actions" style="justify-content:flex-start;">
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
              <p class="hint" style="margin-bottom:12px;">
                进度保存在本机状态文件，主服务短暂重启后仍可继续查看。
                <span v-if="jobPolling">正在轮询…</span>
              </p>
              <div v-if="!job || job.stage === 'idle'" class="hint">暂无进行中的更新作业。</div>
              <template v-else>
                <div class="meta-grid" style="margin-bottom:12px;">
                  <div>
                    <span class="k">阶段</span>
                    <span class="v">
                      <span
                        class="status-pill"
                        :class="job.stage === 'succeeded' ? 'ok' : (job.stage === 'failed' ? 'empty' : 'empty')"
                      >{{ jobStageLabel }}</span>
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
                </div>
                <div class="hint" style="margin-bottom:8px;">{{ job.message || '' }}</div>
                <div v-if="job.error" class="alert show">{{ job.error }}</div>
                <div v-if="job.rollback_attempted" class="hint" style="margin-top:8px;">
                  已尝试恢复更新前的应用目录：{{ job.rollback_ok ? '恢复成功' : '恢复未完全成功，请查看日志或 SSH 排查' }}
                </div>
              </template>
            </fieldset>
          </div>

          <div v-show="activeSection === 'account'" class="section-panel">
        <fieldset>
          <legend>当前登录</legend>
          <p class="hint" style="margin-bottom:12px;">{{ sessionUserHint }}</p>
          <div class="actions" style="justify-content:flex-start;">
            <button type="button" class="btn btn-danger" @click="handleLogout">退出登录</button>
          </div>
        </fieldset>

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
                <div class="hint">至少 8 位字符</div>
              </div>
            </div>
            <div class="actions">
              <button type="submit" class="btn btn-primary" :disabled="changingPwd">{{ changePwdBtnLabel }}</button>
            </div>
          </form>
        </fieldset>

        <fieldset>
          <legend>API Token（KDS 等设备）</legend>
          <p class="hint" style="margin-bottom:12px;">Token 用于 KDS 等客户端调用写接口，生成后仅显示一次，请妥善保存。</p>
          <div class="grid">
            <div class="full">
              <label for="tokenLabel">备注标签（可选）</label>
              <input class="input" id="tokenLabel" v-model="tokenLabel" type="text" placeholder="例如 厨房平板-1">
            </div>
          </div>
          <div class="actions" style="justify-content:flex-start;">
            <button type="button" class="btn btn-primary" :disabled="generatingToken" @click="genToken">{{ genTokenBtnLabel }}</button>
            <button type="button" class="btn" @click="loadTokenList">刷新列表</button>
          </div>
          <div>
            <div v-if="tokensLoading" class="hint">加载中…</div>
            <div v-else-if="tokensError" class="hint" style="color:var(--red);">加载失败：{{ tokensError }}</div>
            <div v-else-if="!tokens.length" class="hint">暂无 API Token</div>
            <table v-else class="token-list">
              <thead>
                <tr><th>前缀</th><th>标签</th><th>创建时间</th><th>过期时间</th><th>状态</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="t in tokens" :key="t.token_hash_prefix" :class="{ revoked: !!t.revoked_at }">
                  <td class="mono">{{ t.token_hash_prefix }}…</td>
                  <td>{{ t.label || '—' }}</td>
                  <td>{{ formatTs(t.created_at) }}</td>
                  <td>{{ formatTs(t.expires_at) || '—' }}</td>
                  <td>{{ t.revoked_at ? '已撤销' : '有效' }}</td>
                  <td>
                    <button v-if="!t.revoked_at" type="button" class="btn btn-sm btn-danger" @click="revokeToken(t.token_hash_prefix)">撤销</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </fieldset>
          </div>
        </div>
      </div>
    </div>

    <div v-if="importSuccessModal.show" class="modal-overlay show" role="dialog" aria-modal="true">
      <div class="modal-box">
        <h3>导入成功</h3>
        <p>{{ importSuccessModal.message }}</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" @click="confirmImportSuccessRedirect">确认</button>
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
.dim { color: var(--text-dim); font-weight: normal; opacity: 0.85; }
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; }

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
  text-align: left; padding: 10px 14px; background: transparent;
  border: 1px solid transparent; border-radius: 8px;
  color: var(--text-dim); font-size: 13px; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.15s;
}
.setup-nav .nav-item:hover { color: var(--text); background: var(--card2); }
.setup-nav .nav-item.active {
  color: var(--accent); background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.35);
}
.setup-content { flex: 1 1 auto; min-width: 0; }

.switch-row { display: flex; align-items: center; gap: 8px; font-weight: normal; color: var(--text); font-size: 13px; margin-top: 2px; }

.token-list { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
.token-list th, .token-list td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
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
}
</style>
