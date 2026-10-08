<script setup>
// ============================================================================
// 数据节（原「备份中心」+「数据库凭据」两节合并）
//
// 契约（ADR 0099；唯一一份接口定义在 ./sectionContract.js）
//   props  active: Boolean
//   翻成 true 时本节自取数据（备份健康 / 备份点 / 保留配置 / 数据库连接信息）。
//   emits  'data-restored'(无载荷) —— 整库恢复完成后抛给壳；采集节的凭据与运行配置已被
//          恢复覆盖，要重取（壳把它翻成 CollectSection 的 refreshKey）。
//   inject SETTINGS_MODAL_HOST：注册 'confirm'（两步确认：覆盖恢复 / 数据回滚 / 清理 /
//          保存保留配置都走这条共用通道，采集节与账号节的危险动作也用它）、'importSuccess'
//          （恢复完成）、'dbReset'（重置数据库密码）三个弹窗。四个弹窗的「框 + Esc」在壳里，
//          状态与文案在这里注册。
//
// 文案规则（ADR 0099）：默认态只留操作必需与风险/后果提示；机制解释（包里装了什么、
// 保留与清理怎么算）一律进默认折叠的 <details>；"重置写了哪个文件、要不要重启"由两步确认
// 弹窗在确认那一刻交代（弹窗文案在本节注册，见下面的 dbReset）。已知重复只留一处：
//   · `.luyunbak` 口令提示 → 只剩导出面板那一行（口令遗失 = 无法解密恢复）；
//   · 「整库覆盖不能合并」→ 只剩恢复面板的 PG 快照提示；导出面板只讲打包形态（pg_dump），
//     不再复述恢复语义；
//   · 「回滚前会自动再建一份快照」→ 只在 BackupPointList 的「本机回滚快照」分组提示里，
//     本节不再复述（那条提示在组件里，见 components/backup/BackupPointList.vue）；
//   · 清理预览的份数摘要 → 只在两步确认弹窗里；本节只渲染 cleanupPreview 里的条目清单
//     （删哪些 / 保护哪些），弹窗明细与它同一份数据，不再把份数写第二遍；
//   · 重置密码的禁用原因 → 只在按钮上方那块 alert（composable 另有一条 dbResetDisabledReason
//     几乎是同一句话，本节不渲染，见下面的 useDbCredentials 注释）。
//
// 反馈归属：本节有六个动作区，各有一条就地提示条（见下面的 runSpotted）。composable 只吐
// 一条 showAlert(type, message)，分不出是哪个按钮触发的；触发它的调用点知道。
//
// 内容归属：本节只搬自原 SetupView.vue 的 backup / database 两节。
// ============================================================================
import { computed, inject, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import SvgIcon from '../../components/SvgIcon.vue'
import PanelHeader from '../../components/ui/PanelHeader.vue'
import LuyunCheckbox from '../../components/ui/LuyunCheckbox.vue'
import LuyunFileDropzone from '../../components/ui/LuyunFileDropzone.vue'
import LuyunNumberInput from '../../components/ui/LuyunNumberInput.vue'
import LuyunRadioGroup from '../../components/ui/LuyunRadioGroup.vue'
import StatusPill from '../../components/ui/StatusPill.vue'
import BackupOverview from '../../components/backup/BackupOverview.vue'
import BackupPointList from '../../components/backup/BackupPointList.vue'
import { useBackupCenter } from '../../composables/useBackupCenter'
import { useDbCredentials } from '../../composables/useDbCredentials'
import { SETTINGS_MODAL_HOST } from './sectionContract'

const props = defineProps({
  /** 本分节是否为当前分节（壳给）；翻成 true 时自取数据。 */
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['data-restored'])

// composable 不引 router 单例（单测要换内存路由），所以仍是页面这一层给它。
const router = useRouter()
const modalHost = inject(SETTINGS_MODAL_HOST, null)

// ===== 就地反馈条：一个动作区一条 =====
const AUTO_HIDE_MS = 3500

/** 一条提示条的初始状态。 */
function createAlertBar() {
  return { show: false, type: 'info', message: '' }
}

const alert = reactive(createAlertBar()) // 备份状态 / 备份点：重跑健康、校验、回滚
const exportAlert = reactive(createAlertBar()) // 导出备份
const previewAlert = reactive(createAlertBar()) // 恢复：预览（解密）
const restoreAlert = reactive(createAlertBar()) // 恢复：确认恢复
const retentionAlert = reactive(createAlertBar()) // 保留与清理：刷新 / 保存 / 立即清理
const dbAlert = reactive(createAlertBar()) // 数据库凭据：重置密码

const ALERT_BARS = {
  backup: alert,
  export: exportAlert,
  preview: previewAlert,
  restore: restoreAlert,
  retention: retentionAlert,
  db: dbAlert,
}
const alertTimers = new Map()

/** 当前反馈归属；初值是本节第一块动作区。 */
let activeSpot = 'backup'

/**
 * 记下"接下来这条反馈归哪个动作区"，然后执行动作。**写完不还原**：两步确认的落地动作
 * （覆盖恢复 / 数据回滚 / 立即清理 / 保存保留配置）是用户在弹窗里点「确认」时才跑的，
 * 那时发起处的调用早就返回了；归属一直留到下一个动作把它改掉，提示才会落在触发它的
 * 那颗按钮旁边，而不是别处或页面顶部。
 */
function runSpotted(spot, action) {
  if (ALERT_BARS[spot]) activeSpot = spot
  return action()
}

/** 往某个动作区的提示条里写一条反馈；成功提示 3.5s 后自动收起。 */
function writeAlert(name, type, message) {
  const bar = ALERT_BARS[name]
  const timer = alertTimers.get(name)
  if (timer) clearTimeout(timer)
  bar.type = type
  bar.message = message
  bar.show = true
  if (type === 'success') {
    alertTimers.set(name, setTimeout(() => { bar.show = false }, AUTO_HIDE_MS))
  } else {
    alertTimers.delete(name)
  }
}

function showAlert(type, message) {
  writeAlert(ALERT_BARS[activeSpot] ? activeSpot : 'backup', type, message)
}

/** composable 在每个动作开始时调它：收起各动作区上一轮的提示（成功提示自己也会 3.5s 后收）。 */
function clearAlert() {
  for (const name of Object.keys(ALERT_BARS)) {
    ALERT_BARS[name].show = false
    const timer = alertTimers.get(name)
    if (timer) {
      clearTimeout(timer)
      alertTimers.delete(name)
    }
  }
}

const {
  exportForm,
  exporting,
  exportBtnLabel,
  exportStageText,
  exportPercent,
  exportIndeterminate,
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
  loadRetention,
  saveRetention,
  runCleanup,
  formatBytes,
  formatTs,
} = useBackupCenter({
  showAlert,
  clearAlert,
  router,
  // 恢复完业务库之后：不在本节内直接摸采集节的 composable（那会跨节）。
  // 抛给壳，由壳去通知采集节重取被覆盖的凭据与运行配置。
  onAfterRollback: async () => {
    emit('data-restored')
  },
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
// 注意：`dbResetDisabledReason` 没有取。它在三种"按钮禁用"的状态下说的正是上面那一块
// alert（无密码 / 非 PG / 环境变量优先）已经说过的话（原文几乎逐字相同，见 useDbCredentials），
// 渲染出来就是同一句话讲两遍；禁用原因由那块 alert 承载，这里只留按钮的 disabled。

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

// 备份点列表里的校验 / 回滚：校验结论还会就地显示在该行里，提示条归备份点那一块。
function onValidatePoint(id) {
  return runSpotted('backup', () => validatePoint(id))
}

function onRollbackPoint(point) {
  return runSpotted('backup', () => onRollbackSnapshot(point))
}

// ===== 三个弹窗注册给壳：状态与文案留在这里，壳只渲染「框」并接 Esc =====
modalHost?.register('confirm', {
  isOpen: () => confirmState.open,
  close: closeConfirm,
  state: confirmState,
  checked: confirmChecked,
  ready: () => confirmReady.value,
  submit: onConfirmClick,
  request: requestConfirm,
})

modalHost?.register('importSuccess', {
  isOpen: () => importSuccessModal.show,
  close: confirmImportSuccessRedirect,
  state: importSuccessModal,
  copy: () => ({
    title: '恢复完成',
    confirmLabel: importSuccessModal.sessionInvalidated ? '重新登录' : '确认',
  }),
})

modalHost?.register('dbReset', {
  isOpen: () => dbResetConfirm.open,
  close: closeDbReset,
  state: dbResetConfirm,
  submit: () => runSpotted('db', dbResetSubmit),
  busy: () => dbResetting.value,
  isPasswordVisible: () => dbResetShowPassword.value,
  togglePassword: () => {
    dbResetShowPassword.value = !dbResetShowPassword.value
  },
  copy: () => ({
    title: '重置数据库密码',
    message:
      `将为 ${dbCred.value?.user || '当前数据库角色'} 生成 32 位随机密码并写入 ` +
      `${dbCred.value?.env_file || 'env 文件'}。页面不会显示新密码明文，写入成功后会自动重启` +
      '应用使其生效。请输入当前后台超级管理员密码以确认本次重置。',
    fieldLabel: '当前后台超级管理员密码',
    placeholder: '输入当前登录后台的密码',
    toggleLabel: dbResetToggleLabel.value,
    confirmLabel: dbResetBtnLabel.value,
  }),
})

// 自取数据：原 switchSection 的 backup / database 两支。
// 四个请求并发跑，失败提示的归属各不相同：健康 / 备份点 / 连接信息三处失败都由各自的
// 组件就地报（BackupOverview 的 error、BackupPointList 的提示行、PanelHeader 的 note），
// 只有"保留配置"没有就地错误位，所以那一个把归属记到保留与清理这一块。
watch(
  () => props.active,
  (on) => {
    if (!on) return
    loadHealth()
    loadPoints()
    runSpotted('retention', loadRetention)
    loadDbCred()
  },
  { immediate: true },
)
</script>

<template>
  <div class="settings-section">
    <BackupOverview
      :health="healthView"
      :loading="healthLoading"
      :error="healthError"
      @refresh="runSpotted('backup', refreshHealth)"
    />

    <!-- 备份状态 / 备份点的就地反馈：重跑备份健康与列表里的校验、回滚都落在这一条上 -->
    <div v-if="alert.show" class="alert show" :class="alert.type">{{ alert.message }}</div>

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
      @validate="onValidatePoint"
      @rollback="onRollbackPoint"
      @refresh="loadPoints"
    />

    <!-- ===== 导出备份：口令 → 勾选项 → 生成下载；反馈与进度都贴在这颗按钮下面 ===== -->
    <fieldset>
      <legend>导出备份</legend>
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
      <!-- 风险提示：口令遗失不可解密，是本节唯一一处 .luyunbak 口令说明 -->
      <p class="hint is-warn">导出的 <code>.luyunbak</code> 由口令加密，口令遗失将无法解密恢复，请牢记。</p>
      <p v-if="exportHasLargePayload" class="hint">已勾选业务数据或配方，备份文件可能较大，导出耗时会更长。</p>
      <div class="actions is-start">
        <button type="button" class="btn btn-primary" :disabled="exporting" @click="runSpotted('export', onExportBackup)">{{ exportBtnLabel }}</button>
      </div>
      <div v-if="exportAlert.show" class="alert show" :class="exportAlert.type">{{ exportAlert.message }}</div>
      <!-- 导出是服务端后台任务：按 stage 显示阶段文案，照片阶段还有 done/total -->
      <div v-if="exporting" class="upload-progress">
        <div class="upload-progress__track">
          <div
            class="upload-progress__bar"
            :class="{ indeterminate: exportIndeterminate }"
            :style="{ width: exportPercent + '%' }"
          ></div>
        </div>
        <span class="upload-progress__label">{{ exportStageText }}</span>
      </div>
      <!-- 机制解释：包里装了什么、业务数据是什么形态 —— 默认折叠 -->
      <details class="more">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="more__icon" />
          <span>这个包里装了什么</span>
        </summary>
        <p v-if="appDbExportFormat !== 'pgdump'" class="hint">
          可打包 POS 凭据、运行配置、业务数据与两类卫生照片。
        </p>
        <p v-else class="hint">
          可打包 POS 凭据、运行配置、业务数据（PostgreSQL 整库快照）、配方数据与两类卫生照片。
        </p>
        <p v-if="appDbExportFormat === 'pgdump'" class="hint">
          PostgreSQL 门店的业务数据以整库快照（<code>pg_dump</code>）打包；宿主机冷备命令见 <code>deploy/README.md</code> 第 10.4 节。恢复时的行为见下面「恢复」一节。
        </p>
      </details>
    </fieldset>

    <!-- ===== 恢复：先预览核对，再确认恢复（预览结果与两颗按钮的反馈都在本面板内） ===== -->
    <fieldset>
      <legend>恢复</legend>
      <p class="hint section-lead">先预览核对，再确认恢复。</p>
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
        <button type="button" class="btn" :disabled="previewing" @click="runSpotted('preview', onPreviewImport)">{{ previewBtnLabel }}</button>
      </div>
      <div v-if="previewAlert.show" class="alert show" :class="previewAlert.type">{{ previewAlert.message }}</div>
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
        <!-- 整库覆盖不能合并：本节唯一一处（导出面板只讲打包形态，不复述恢复语义） -->
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
          @click="runSpotted('restore', onApplyImport)"
        >{{ importApplyLabel }}</button>
      </div>
      <div v-if="restoreAlert.show" class="alert show" :class="restoreAlert.type">{{ restoreAlert.message }}</div>
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
      <!-- 本机回滚快照的入口在备份点列表里（它的分组提示已经写明回滚前会自动再建一份，这里不重复） -->
      <p class="hint">本机回滚快照请在页面顶部的「备份点」分组里回滚。</p>
    </fieldset>

    <!-- ===== 保留与清理：按钮在上、预览在下（点完新长出来的清单不会把按钮顶走） ===== -->
    <fieldset>
      <legend>保留与清理</legend>
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
        <p class="hint is-warn">保存后立即清理，超出份数的备份点会被删除。</p>

        <div class="actions is-start">
          <button type="button" class="btn" :disabled="retentionLoading" @click="runSpotted('retention', loadRetention)">刷新</button>
          <button type="button" class="btn btn-primary" :disabled="retentionSaving" @click="runSpotted('retention', saveRetention)">
            {{ retentionSaving ? '保存中…' : '保存保留配置' }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="cleaningUp || cleanupPreviewLoading"
            @click="runSpotted('retention', runCleanup)"
          >{{ cleaningUp ? '清理中…' : '立即清理' }}</button>
        </div>
        <div v-if="retentionAlert.show" class="alert show" :class="retentionAlert.type">{{ retentionAlert.message }}</div>

        <!-- 预览在按钮下方：保存 / 清理后重算出来的清单往下长，按钮不再被顶走。
             份数摘要只在两步确认弹窗里，这里只列"删哪些 / 保护哪些"（同一份 cleanupPreview）。 -->
        <div v-if="cleanupPreview" class="cleanup-preview">
          <div class="cleanup-preview__title">清理预览</div>
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
        <p v-if="!retention && !cleanupPreview" class="hint">尚未加载保留配置。</p>

        <!-- 机制解释：保留口径与清理时机 —— 默认折叠 -->
        <details class="more">
          <summary>
            <SvgIcon name="chevron-right" :size="14" class="more__icon" />
            <span>保留与清理怎么算</span>
          </summary>
          <p class="hint">
            本机回滚快照、导出备份与冷备各自保留份数。默认值：本机回滚快照
            {{ retentionLimits?.snapshot_keep_default ?? '—' }} 份、导出备份
            {{ retentionLimits?.export_keep_default ?? '—' }} 份、冷备
            {{ retentionLimits?.cold_keep_default ?? '—' }} 份。
          </p>
          <p class="hint">
            更新作业前的本机回滚快照与最近一份永不自动删除；保存保留配置或立即清理前，
            都会先弹出将删除的份数与条目（与上面的清理预览同一份数据），确认后才执行。
          </p>
        </details>
      </template>
    </fieldset>

    <!-- ===== 数据库凭据：连接信息只读；密码重置需要当前后台超级管理员密码 ===== -->
    <PanelHeader
      icon="database"
      title="数据库凭据"
      :tone="dbIsPostgres ? 'info' : 'neutral'"
      :pill-label="dbIsPostgres ? 'PostgreSQL 后端' : 'SQLite 后端'"
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

      <p class="hint">重置后新密码只写入 env 文件，页面不显示明文。</p>
      <div class="actions is-start">
        <button
          type="button"
          class="btn btn-danger"
          :disabled="dbResetDisabled || dbResetting"
          @click="runSpotted('db', openDbReset)"
        >重置密码</button>
      </div>
      <div v-if="dbAlert.show" class="alert show" :class="dbAlert.type">{{ dbAlert.message }}</div>
      <!-- 禁用原因不在这里复述：上面那块 alert 就是同一件事（见 useDbCredentials 的注释） -->

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
</template>

<style scoped>
.alert {
  padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px;
}
.alert.success { background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }
.alert.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert.info { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.25); color: #93c5fd; }
/* 就地提示条紧跟触发它的按钮：上面一行操作区已经有 8px，这里只留一点距离。 */
.actions + .alert { margin-top: 10px; }
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
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; }
.hint.is-warn { color: var(--yellow); opacity: 1; }
.hint.is-error { color: #fca5a5; opacity: 1; }
.section-lead { margin: 0 0 12px; }

/* 机制解释的折叠区（默认收起）：与采集节 / 状态节同一视觉。 */
.more {
  margin-top: 14px; padding: 10px 14px;
  background: rgba(10, 13, 22, 0.5);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 12px;
}
.more > summary {
  display: flex; align-items: center; gap: 6px; min-height: 36px;
  cursor: pointer; font-weight: 600; color: var(--text-dim);
}
.more > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* 开合标记：与采集节 / 状态节 / 系统节的 .section-help__icon 同一套皮（chevron + 展开转 90°）。
   原来 summary 是 display:flex，Chromium 连原生三角都不渲染，于是这两个折叠看起来像普通小标题。 */
.more__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.more[open] > summary,
.more[open] .more__icon { color: var(--text); }
.more[open] .more__icon { transform: rotate(90deg); }
.more .hint { margin-top: 6px; }

/* 备份点列表（子组件 components/backup/BackupPointList.vue）的两个分组折叠头也是 display:flex，
   同样没有开合标记；它的高度与样式写在子组件的 scoped 里，这里只能从父级用 :deep() 补：
   改回 display:list-item 让原生三角回来（t15 的建议之一），min-height 与 t13 那档不受影响。 */
.settings-section :deep(.points__shared > summary),
.settings-section :deep(.points__excluded > summary) {
  display: list-item; padding-top: 11px;
  transition: color 0.15s ease;
}
.settings-section :deep(.points__shared[open] > summary),
.settings-section :deep(.points__excluded[open] > summary) { color: var(--text); }

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
.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
/* 备份 / 更新面板里的操作区一律左对齐，和上方表单对齐。 */
.actions.is-start { justify-content: flex-start; }
.import-block { margin-top: 12px; }
.grid .luyun-check-row { display: flex; }
.plain-list, .bullet-list {
  margin: 4px 0 0; padding-left: 18px;
  font-size: 11px; color: var(--text-dim); line-height: 1.6;
}
.bullet-list { margin-top: 6px; }
.check-stack { display: grid; gap: 6px; margin-top: 2px; }
.import-preview-grid { margin-top: 12px; }

/* 清理预览：在操作区下方（结果往下长，按钮不被顶走） */
.cleanup-preview { margin-top: 14px; }
.cleanup-preview__title { font-size: 12px; font-weight: 600; color: var(--text-dim); }
.cleanup-list { display: grid; gap: 4px; margin: 8px 0 0; padding: 0; list-style: none; }
.cleanup-list li {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 11px; line-height: 1.6; color: var(--text-dim);
}
/* 导出备份的文件名（`luyun_backup_20260921_220039.luyunbak`）是一整串不可断行的
   标识符：它把整行的 min-content 顶到 294px，320px 上卡片内只有 262px，于是行连同
   卡片一起被裁（t9 实测 4 个元素越界 6px）。`anywhere` 只在这一行放不下时才断，
   顺带把 min-content 也算小 —— 宽屏一个字都不会变。 */
.cleanup-list li > span { overflow-wrap: anywhere; }
.meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px 18px;
  background: rgba(10, 13, 22, 0.6); border-radius: 8px; padding: 10px 14px;
  font-size: 12px;
}
.meta-grid div { display: flex; justify-content: space-between; gap: 10px; }
.meta-grid .k { color: var(--text-dim); opacity: 0.85; }
.meta-grid .v { color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

@media (max-width: 700px) {
  .grid { grid-template-columns: 1fr; }
  .meta-grid { grid-template-columns: 1fr; }
}

/* 窄屏触控尺寸：折叠区的开合把手与密码框的显隐开关都只有 32/36 与 22/24px 高，
 * 手指点不中。只在 ≤430px 抬到 44 / 40px —— 桌面那一档是鼠标点，密度维持原样。
 * （`.btn` / `.btn-sm` / 勾选行的下限在 styles/theme.css 里，这里只补分节自己的。） */
@media (prefers-reduced-motion: reduce) {
  .more__icon { transition: none; }
  .settings-section :deep(.points__shared > summary),
  .settings-section :deep(.points__excluded > summary) { transition: none; }
}

@media (max-width: 430px) {
  .more > summary { min-height: 44px; }
  /* 备份点列表（子组件）里两个分组折叠头也在这条线上：它的高度写在子组件的 scoped 里，
     父级要用 :deep() 且带上自己的作用域类名才压得住。 */
  .settings-section :deep(.points__shared > summary),
  .settings-section :deep(.points__excluded > summary) { min-height: 44px; }
}
</style>
