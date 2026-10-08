<script setup>
// ============================================================================
// 系统节（原「系统更新」：版本 / 自检 / 迁移 / 发行目录 / 作业 / 历史 / GitHub）
//
// 契约（ADR 0099；唯一一份接口定义在 ./sectionContract.js）
//   props  active: Boolean
//   翻成 true 时本节自取数据（版本检测 / 作业进度 / 更新历史 / GitHub 连接）。
//   emits  无 —— 本节不与壳交换状态。
//   inject 无：本节没有手写弹窗（数据库迁移的重启确认走 components/update/DatabaseMigrations.vue 自带的 ConfirmDialog）。
//
// 反馈就地：composable 只给一个 showAlert/clearAlert 出口，这里按「这条消息是谁的」分流到
// 各自贴着触发点的那条提示上（顶部那条只留给读操作的失败），不再有一条飘在分节顶上管全场——
//   - 保存 GitHub 连接 → 折叠面板内、紧贴「保存连接」；
//   - 应用更新 / 作业（提交被拦下、已开始、成功失败、终止）→ 确认框内按钮上方
//     （提交被 409 拦下时框还开着，运维的下一步就在那几个按钮上），其余落在作业进度下面
//     （结论本来就跟着进度走；那时确认框已经关了）；
//   - 读操作失败（版本检测、加载 GitHub 配置）→ 分节顶部，紧贴版本状态卡。
//
// 文案（ADR 0099「默认态只留操作必需与风险提示，机制解释收进折叠」）：
//   - 机制解释一律进 <details class="section-help">；默认态可见的说明性文字只留在带
//     data-default-note 的块里（test 会把两边的总量卡在 200 字内）；
//   - 未通过自检的指引、「部署目录有本地改动」的勾选说明各只保留一处：前者留在发行目录
//     面板（「应用此版本」按钮就在那儿），后者留在确认框里那个必勾项旁边；
//   - 更新历史的结果列在展示层映射成中文标签，接口字段（result / result_label）与返回值不动。
//
// 内容归属：本节只搬自原 SetupView.vue 的 update 一节。
// ============================================================================
import { computed, reactive, watch } from 'vue'
import SvgIcon from '../../components/SvgIcon.vue'
import LuyunCheckbox from '../../components/ui/LuyunCheckbox.vue'
import StatusPill from '../../components/ui/StatusPill.vue'
import CheckList from '../../components/ui/CheckList.vue'
import UpdateOverview from '../../components/update/UpdateOverview.vue'
import DatabaseMigrations from '../../components/update/DatabaseMigrations.vue'
import UpdateStageProgress from '../../components/update/UpdateStageProgress.vue'
import ReleaseRow from '../../components/update/ReleaseRow.vue'
import { useSystemUpdate } from '../../composables/useSystemUpdate'
import { formatTs } from '../../utils/backupProgress'

const props = defineProps({
  /** 本分节是否为当前分节（壳给）；翻成 true 时自取数据。 */
  active: { type: Boolean, default: false },
})

/**
 * 三条就地提示条。`alert` 是分节顶部那条（只留给版本检测），另两条跟着自己的触发按钮走。
 * 提示条只自动收起成功态：失败要留在屏幕上，直到下一次操作把它清掉。
 */
const alert = reactive({ show: false, type: 'info', message: '' })
const confirmFeedback = reactive({ show: false, type: 'info', message: '' })
const jobFeedback = reactive({ show: false, type: 'info', message: '' })
const githubFeedback = reactive({ show: false, type: 'info', message: '' })

const FEEDBACK_TTL_MS = 3500
let alertTimer = null

function fillFeedback(box, type, message) {
  box.show = true
  box.type = type
  box.message = message
}

function clearAlert() {
  if (alertTimer) {
    clearTimeout(alertTimer)
    alertTimer = null
  }
  for (const box of [alert, confirmFeedback, jobFeedback, githubFeedback]) {
    box.show = false
    box.message = ''
  }
}

/**
 * composable 的提示出口 → 就地提示条。分流按「这条消息是谁的、触发它的东西在哪」：
 *   - 保存 GitHub 连接（写）→ 折叠面板里、贴着那个按钮；
 *   - 读操作的失败（版本检测、加载 GitHub 配置）→ 分节顶部那条 —— 折叠面板收起时，
 *     落在面板里的失败连个影都没有；
 *   - 其余是应用更新 / 作业的消息：提交被 409 拦下时确认框还开着（下一步就是框里那两个
 *     勾选项），所以落在框内；成功与轮询结论跟着作业进度走（那时框已经关了）。
 */
function feedbackBox(message) {
  if (/已保存|保存 GitHub 配置失败/.test(message)) return githubFeedback
  if (/版本检测|加载 GitHub/.test(message)) return alert
  return confirmOpen.value ? confirmFeedback : jobFeedback
}

function showAlert(type, message) {
  if (alertTimer) {
    clearTimeout(alertTimer)
    alertTimer = null
  }
  const box = feedbackBox(message)
  fillFeedback(box, type, message)
  if (type === 'success') {
    alertTimer = setTimeout(() => {
      box.show = false
    }, FEEDBACK_TTL_MS)
  }
}

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
  unhealthyNextSteps,
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

/**
 * 更新历史的结果码 → 中文标签（展示层兜底）。
 *
 * 历史是旁路 JSON，接口一直给 `result_label`，但老记录（或更早版本写下的行）可能只有英文的
 * `result`。接口字段与返回值不动，页面这一层负责不再把英文原文直接端到运维面前。
 */
const HISTORY_RESULT_LABELS = {
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  succeeded_but_unhealthy: '已切换但未健康',
}

function historyResultLabel(entry) {
  const label = entry?.result_label
  if (typeof label === 'string' && /[\u4e00-\u9fff]/.test(label)) return label
  return HISTORY_RESULT_LABELS[entry?.result] || '未知结果'
}

/** 失败原因首行摘要的预算（含省略号：渲染出来不超过 60 字）。 */
const HISTORY_ERROR_SUMMARY_LIMIT = 60

/**
 * 更新历史里的失败原因 `error` 是 systemd / 作业的英文输出：可能几十到几千字符、可能多行。
 * 直接铺在结果单元格里店主看不懂，长串还会把表格撑爆，所以默认只给**首行摘要**，
 * 原文一字不改地折进下面那个 <details>（运维要复制、要贴工单时展开就有）。
 * 接口字段与返回值不动 —— 这里只换展示（F-05：t5 只改了 result_label，error 还裸着）。
 */
function historyErrorSummary(error) {
  const text = String(error ?? '').trim()
  if (!text) return '（无输出）'
  const firstLine = text.split('\n').map((line) => line.trim()).find((line) => line) || '（无输出）'
  if (firstLine.length <= HISTORY_ERROR_SUMMARY_LIMIT) return firstLine
  return `${firstLine.slice(0, HISTORY_ERROR_SUMMARY_LIMIT - 1)}…`
}

/** 最近一次成功（折进「历史怎么看」里，不再占默认首屏）。 */
const lastSuccessLabel = computed(() => (
  lastSuccessEntry.value
    ? `${lastSuccessEntry.value.target_tag || '—'}（${formatTs(lastSuccessEntry.value.finished_at)}）`
    : '暂无成功记录'
))

/**
 * 轮询 / 重检刚报过的失败已经以提示条呈现（还带日志路径），那句原因就不再在下面重复一遍；
 * 判据是**提示条里确实带着这句原因**（而不是"只要有个错误提示就藏起来"）——终止更新失败那类
 * 与作业原因无关的提示不该把 job.error 一起吞掉。
 * 重开页面看到的「上次就失败了」没有提示条，照旧由 job.error 呈现。
 */
const jobErrorDuplicated = computed(() => {
  const reason = job.value?.error
  if (!jobFeedback.show || !reason) return false
  return jobFeedback.message.includes(reason)
})

// 自取数据：原 switchSection 的 update 一支。
watch(
  () => props.active,
  (on) => {
    if (!on) return
    loadGithubConfig()
    loadVersionCheck()
    loadJobStatus()
    loadHistory()
  },
  { immediate: true },
)
</script>

<template>
  <div class="settings-section">
    <div v-if="alert.show" class="alert show" :class="alert.type">{{ alert.message }}</div>

    <UpdateOverview
      :version-check="versionCheck"
      :loading="versionLoading"
      :update-available="updateAvailable"
      :status-summary="statusSummary"
      :degraded-reason="degradedReasonLabel(versionCheck?.degraded_reason)"
      @refresh="refreshVersionCheck"
    />

    <!-- data-default-note：默认态就摆在这儿的说明性文字（ADR 0099 的 ≤200 字预算）。
         机制解释一律进 <details class="section-help">，这里只留操作必需与风险提示。 -->
    <fieldset v-if="preflightChecks.length">
      <legend>更新环境自检</legend>
      <p class="hint section-lead" data-default-note>自检全部通过才能应用更新。</p>
      <CheckList :items="preflightChecks" />
      <details class="section-help">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
          <span>自检在查什么</span>
        </summary>
        <!-- 只解释「这些勾是谁在探」，不重复发行目录那句「先修好哪些项」的指引，也不再讲一遍
             更新会做什么（那是确认框里的「更新后果」）。 -->
        <p class="hint">
          每一项都在服务端现场探测：能不能重启主服务、能不能读到 GitHub Releases、
          部署目录是否干净——探测结果就是上面这份清单，未通过项各自带修法。
        </p>
      </details>
    </fieldset>

    <DatabaseMigrations />

    <fieldset>
      <legend>正式发行版目录</legend>
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
        <!-- 自检未通过的指引只在这里留一处：被禁用的入口就是这个表格里的按钮。 -->
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
      <details class="section-help">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
          <span>怎么选版本</span>
        </summary>
        <p class="hint">目录默认排除预发布（prerelease）；选较旧 tag 即回滚到该发行版。</p>
      </details>
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
      <!-- 提交被拦下（高峰 / 本地改动 / 自检）就落在这儿：下一步要动的是下面那两个勾选项。 -->
      <div
        v-if="confirmFeedback.show"
        class="alert show feedback"
        :class="confirmFeedback.type"
      >{{ confirmFeedback.message }}</div>
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
      <p v-if="jobPolling || (healthPending && autoRecheckEnabled)" class="hint section-lead">
        <span v-if="jobPolling">正在轮询…</span>
        <span v-else>
          将于 {{ recheckIn }} 秒后自动重新检测就绪（冷却重检，不会自动重试更新或回滚）。
        </span>
      </p>
      <UpdateStageProgress :job="job" />
      <div
        v-if="jobFeedback.show"
        class="alert show feedback"
        :class="jobFeedback.type"
      >{{ jobFeedback.message }}</div>

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
        <div v-if="job.error && !jobErrorDuplicated" class="alert show">{{ job.error }}</div>
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
      <details class="section-help">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
          <span>作业进度怎么看</span>
        </summary>
        <p class="hint">
          进度写在本机状态文件里，主服务短暂重启后仍可继续查看；作业只负责换代码并发出重启，
          健康确认由本页面完成。
        </p>
      </details>
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
      <!-- 未健康时把「下一步」写全：本页打不开（主服务没起来）是这类故障的常见形态，
           操作者那时看不到这个面板，所以宿主机路径必须在这里也留下。 -->
      <ul v-if="unhealthy" class="hint unhealthy-next-steps">
        <li v-for="step in unhealthyNextSteps" :key="step">{{ step }}</li>
      </ul>
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
      <!-- 失败条数只在真出现过失败时出现（风险提示），默认态不占位置。 -->
      <p v-if="failureCount" class="hint is-warn" role="status">
        最近一次成功之后失败或取消 {{ failureCount }} 次。
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
            <td class="history-result">
              <StatusPill
                :tone="entry.result === 'succeeded' ? 'ok' : 'error'"
                :label="historyResultLabel(entry)"
              />
              <div v-if="entry.rolled_back" class="hint">
                已回滚：{{ entry.rollback_ok ? '恢复成功' : '恢复未完全成功' }}
              </div>
              <!-- 失败原因：默认只给中文标签 + 首行摘要（≤60 字），完整英文原文折在下面。
                   过去的失败是给人看结论的，原文只有排查时才需要（F-05）。 -->
              <div v-if="entry.error" class="hint history-error">
                <span class="history-error__label">失败原因：</span>
                <span class="history-error__summary mono">{{ historyErrorSummary(entry.error) }}</span>
                <details class="history-error__raw">
                  <summary>
                    <SvgIcon name="chevron-right" :size="14" class="history-error__raw__icon" />
                    <span>完整原文</span>
                  </summary>
                  <pre class="mono">{{ entry.error }}</pre>
                </details>
              </div>
            </td>
            <td>{{ entry.duration_seconds != null ? `${entry.duration_seconds} 秒` : '—' }}</td>
            <td>{{ formatTs(entry.finished_at) || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="actions is-start">
        <button type="button" class="btn" :disabled="historyLoading" @click="loadHistory">刷新历史</button>
      </div>
      <details class="section-help">
        <summary>
          <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
          <span>历史怎么看</span>
        </summary>
        <p class="hint">
          保留最近 30 条、时间倒序；最近一次成功：{{ lastSuccessLabel }}。
          结果取自作业终态（成功 / 失败 / 已取消 / 已切换但未健康）。
        </p>
      </details>
    </fieldset>

    <!-- 公开仓无需 PAT，属于低频配置：折叠起来，避免占掉首屏。 -->
    <details class="github-panel">
      <summary>
        <SvgIcon name="chevron-right" :size="14" class="github-panel__icon" />
        <span>GitHub 连接（公开仓通常无需配置）</span>
      </summary>
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
          <!-- 加载 / 保存的结果就落在这儿：紧贴下面那两个按钮。 -->
          <div
            v-if="githubFeedback.show"
            class="alert show feedback"
            :class="githubFeedback.type"
          >{{ githubFeedback.message }}</div>
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
</template>

<style scoped>
.alert {
  padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px;
}
.alert.success { background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.3); color: #86efac; }
.alert.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.alert.info { background: rgba(59, 130, 246, 0.10); border: 1px solid rgba(59, 130, 246, 0.25); color: #93c5fd; }
/* 就地反馈条：贴着触发它的那块（确认框按钮上、作业进度下、GitHub 保存连接旁），
   间距比分节顶部那条紧。 */
.alert.feedback { margin: 10px 0 0; }
fieldset {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px 18px; margin-bottom: 16px;
  background: rgba(10, 13, 22, 0.5);
}
legend { font-size: 12px; font-weight: 700; color: var(--text-dim); padding: 0 8px; letter-spacing: 0.5px; }
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; }
.hint.is-warn { color: var(--yellow); opacity: 1; }
.hint.is-error { color: #fca5a5; opacity: 1; }
.section-lead { margin: 0 0 12px; }
.unhealthy-next-steps { margin: 8px 0 0; padding-left: 18px; }
.unhealthy-next-steps li + li { margin-top: 4px; }

.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
/* 备份 / 更新面板里的操作区一律左对齐，和上方表单对齐。 */
.actions.is-start { justify-content: flex-start; }
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

/* 机制解释的折叠块（自检 / 怎么选版本 / 作业 / 历史）：默认收起，展开是一段 11px 说明。
   皮与 .release-more 同一套，逐项对齐。 */
.section-help { margin-top: 10px; }
.section-help > summary {
  display: flex; align-items: center; gap: 6px; min-height: 32px;
  cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text-dim);
  list-style: none;
}
.section-help > summary::-webkit-details-marker { display: none; }
.section-help > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.section-help__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.section-help[open] > summary,
.section-help[open] .section-help__icon { color: var(--text); }
.section-help[open] .section-help__icon { transform: rotate(90deg); }
.section-help > .hint { margin: 0; max-width: 76ch; }

@media (prefers-reduced-motion: reduce) {
  .release-more__icon,
  .section-help__icon,
  .history-error__raw__icon,
  .github-panel__icon { transition: none; }
}
.job-facts { margin-top: 12px; }
.job-log { margin-top: 12px; }
.job-log pre {
  max-height: 220px; overflow: auto; margin: 6px 0 0; padding: 10px;
  white-space: pre-wrap; word-break: break-word;
  background: rgba(0, 0, 0, 0.25); border-radius: 8px; font-size: 12px;
}

/* 更新历史的结果单元格：失败原因可能是几千字符的 systemd 输出（多行、单行 500+ 字符），
   所以只有**这个单元格**允许在任意字符处换行。不给整张表：`overflow-wrap: anywhere`
   会连最小内容宽度一起改掉 —— 每个单元格都能缩到一个字符时，版本 / 时间这些短列会被挤成
   竖排（390px 实测过）。也不反过来把短列 nowrap 钉住：那样结束时间会占掉结果列的宽度
   （实测 113px vs 192px），摘要就要多折好几行。 */
.history-list .history-result { overflow-wrap: anywhere; }
.history-error__label { color: var(--text-dim); opacity: 0.85; }
.history-error__summary { color: var(--text-dim); }
/* 开合标记：与 .section-help / .release-more 同一套皮（chevron + 展开转 90°）。
   原来 summary 是 display:flex / inline-flex，Chromium 不渲染原生三角，这两个折叠
   在页面上看不出能点开（t15 的 F15-03）。 */
.history-error__raw__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.history-error__raw[open] > summary,
.history-error__raw[open] .history-error__raw__icon { color: var(--text); }
.history-error__raw[open] .history-error__raw__icon { transform: rotate(90deg); }

.history-error__raw { margin-top: 2px; }
.history-error__raw > summary {
  display: inline-flex; align-items: center; gap: 4px; min-height: 22px;
  cursor: pointer; font-size: 11px; color: var(--text-dim); list-style: none;
}
.history-error__raw > summary::-webkit-details-marker { display: none; }
.history-error__raw > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.history-error__raw[open] > summary { color: var(--text); }
/* 原文原样可复制：等宽、限高可滚，长行在任意字符处折。 */
.history-error__raw pre {
  max-height: 220px; overflow: auto; margin: 4px 0 0; padding: 8px;
  white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
  background: rgba(0, 0, 0, 0.25); border-radius: 6px; font-size: 11px;
}
.github-panel {
  border: 1px solid var(--border); border-radius: 10px;
  background: rgba(10, 13, 22, 0.5); margin-bottom: 16px;
}
.github-panel > summary {
  display: flex; align-items: center; gap: 6px; min-height: 44px;
  padding: 0 18px; cursor: pointer;
  font-size: 12px; font-weight: 700; color: var(--text-dim); letter-spacing: 0.5px;
}
.github-panel__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.github-panel[open] > summary,
.github-panel[open] .github-panel__icon { color: var(--text); }
.github-panel[open] .github-panel__icon { transform: rotate(90deg); }
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

.token-list { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
.token-list th, .token-list td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
/* Token 表格：撤销列贴右，状态列不折行。 */
.token-table th:last-child, .token-table td:last-child { width: 1%; text-align: right; white-space: nowrap; }
.token-table th:nth-child(5), .token-table td:nth-child(5) { width: 1%; white-space: nowrap; }
.token-list th { color: var(--text-dim); font-weight: 600; }
.token-list .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.token-list tr.revoked { color: var(--text-dim); text-decoration: line-through; opacity: 0.7; }

/* 窄屏（含 390px）：日志路径、回退点这类等宽长串不许把整节撑宽。
   flex 子项默认 min-width:auto，不显式压到 0 就只肯按最长单词撑开 —— 页面横滑就是这么来的。 */
.meta-grid > div,
.meta-grid .v { min-width: 0; }
.meta-grid .v { overflow-wrap: anywhere; }
.actions .btn { max-width: 100%; overflow-wrap: anywhere; }

/* 窄屏触控尺寸：本节有四处折叠把手（.section-help ×4 / .release-more / 历史失败原文）
 * 只有 22–36px 高，改由这一档统一抬到 44px；迁移面板（子组件）的两个折叠头用
 * :deep() 且带上自己的作用域类名才压得住它的 scoped 规则。桌面那一档维持原尺寸。 */
@media (max-width: 430px) {
  .section-help > summary,
  .release-more > summary,
  .history-error__raw > summary { min-height: 44px; }
  .settings-section :deep(.migration-applied > summary),
  .settings-section :deep(.migration-bootstrap > summary) { min-height: 44px; }
}

@media (max-width: 700px) {
  .meta-grid { grid-template-columns: 1fr; }
  /* 窄屏只保留版本对照的关键列：名称 / 更新前版本 / 耗时 先让位，避免横向滚动。 */
  .release-list th:nth-child(2), .release-list td:nth-child(2),
  .history-list th:nth-child(2), .history-list td:nth-child(2),
  .history-list th:nth-child(4), .history-list td:nth-child(4) { display: none; }
  .release-actions { width: 100%; }
  /* 裁列后按钮列变窄，别把「应用此版本」折成两行。 */
  .release-actions .btn-sm { white-space: nowrap; }
}
</style>
