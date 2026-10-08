<script setup>
// ============================================================================
// 账号节（原「账号与 API Token」）
//
// 契约（ADR 0099；唯一一份接口定义在 ./sectionContract.js）
//   props  active: Boolean
//   翻成 true 时本节自取数据（登录状态 + Token 列表）。
//   emits  无 —— 本节不与壳交换状态。
//   inject SETTINGS_MODAL_HOST：注册 'token'（Token 已生成）弹窗；「退出登录」与「撤销 Token」的两个步确认走 host.requestConfirm(...)。
//
// 命名（ADR 0099）：导航与本节标题统一叫「账号」——原来一处「账号与 API Token」、
// 一处「账号与会话」，同一块东西两个名字。
//
// 反馈就地：一个动作区一条提示条（页头「退出登录」/ 登录密码 / API Token / 被撤的那一行），
// composable 那条 showAlert 只有类型和信息、分不出是谁触发的，所以由**触发它的调用点**认领
// （runSpotted，见下面）。页头那行只说结论（登录了谁）：退出后的后果写在两步确认里 ——
// 那儿才是动作发生的地方，不在页头再重复一句。
//
// 内容归属：本节只搬自原 SetupView.vue 的 account 一节；Token 弹窗的「框」在壳里，
// 文案与动作在这里注册（那边没有第二处能放"复制失败"的位置，所以它认领到 API Token 面板）。
// ============================================================================
import { computed, inject, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import PanelHeader from '../../components/ui/PanelHeader.vue'
import StatusPill from '../../components/ui/StatusPill.vue'
import { useAccountSettings } from '../../composables/useAccountSettings'
import { SETTINGS_MODAL_HOST } from './sectionContract'

const props = defineProps({
  /** 本分节是否为当前分节（壳给）；翻成 true 时自取数据。 */
  active: { type: Boolean, default: false },
})

const router = useRouter()
const modalHost = inject(SETTINGS_MODAL_HOST, null)

// ===== 就地反馈条：一个动作区一条 =====
const AUTO_HIDE_MS = 3500

/** 一条提示条的初始状态。 */
function createAlertBar() {
  return { show: false, type: 'info', message: '' }
}

const alert = reactive(createAlertBar()) // 登录会话：退出登录（按钮在页头右侧）
const passwordAlert = reactive(createAlertBar()) // 登录密码：更新
const tokenAlert = reactive(createAlertBar()) // API Token：生成 / 复制
const revokeAlert = reactive(createAlertBar()) // 撤销：落在被撤的那一行

/** 撤销的反馈要认领到**哪一行**：撤销按钮在行尾，撤完按钮位就换成这条结论。 */
const revokePrefix = ref('')

const ALERT_BARS = {
  logout: alert,
  password: passwordAlert,
  token: tokenAlert,
  revoke: revokeAlert,
}
const alertTimers = new Map()

/** 当前反馈归属；初值是本节第一个动作区。 */
let activeSpot = 'password'

/**
 * 记下"接下来这条反馈归哪个动作区"，然后执行动作。**写完不还原**：两步确认的落地动作
 * （退出登录 / 撤销 Token）是用户在弹窗里点「确认」时才跑的，那时发起处的调用早就返回了；
 * 归属一直留到下一个动作把它改掉，反馈才会落在触发它的那颗按钮旁边。
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
  writeAlert(ALERT_BARS[activeSpot] ? activeSpot : 'password', type, message)
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
  formatTs,
} = useAccountSettings({ showAlert, clearAlert, router })

/**
 * 页头那行只留结论：登录了谁。退出后的后果在两步确认里说（那儿才有按钮），
 * 这里不再重复；「未登录 / 取不到状态」时原样透出 composable 的提示。
 */
const accountSummary = computed(() => {
  if (!sessionUsername.value) return sessionUserHint.value
  return `已登录为 ${sessionUsername.value}。`
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

// ===== 触发点认领：composable 吐消息时才知道该写哪一条 =====

/** 登录密码：更新（按钮在表单里，反馈落在它上面那条）。 */
function submitPassword() {
  return runSpotted('password', () => onChangePassword())
}

/** API Token：生成新 Token（生成成功由 Token 弹窗本身反馈，失败落在这条）。 */
function generateToken() {
  return runSpotted('token', () => genToken())
}

/** 撤销某一行的 Token：反馈认领到那一行。 */
function revokeRow(prefix) {
  revokePrefix.value = prefix
  return runSpotted('revoke', () => revokeToken(prefix))
}

const loggingOut = ref(false)

/**
 * 退出登录：本来就没有"留在这页"的结果 —— 成功即离开（客户端路由到 /login）。
 * 所以这里的反馈是"正在退出"，失败（理论上不该发生，登出实现自己吞掉了请求失败）
 * 才留一条错误；按钮同时变成「退出中…」且不可再点。
 */
async function runLogout() {
  loggingOut.value = true
  writeAlert('logout', 'info', '正在退出登录…')
  try {
    await handleLogout()
  } catch (err) {
    writeAlert('logout', 'error', `退出登录失败：${err?.message || '未知错误'}`)
  } finally {
    loggingOut.value = false
  }
}

/** 危险动作：确认弹窗由壳的两步确认通道负责（不用浏览器原生 confirm）。 */
function onLogoutConfirm() {
  modalHost?.requestConfirm(
    {
      title: '确认退出登录',
      message: '退出后需要重新输入超级管理员密码才能回到管理页面。',
      danger: true,
      confirmLabel: '退出登录',
    },
    runLogout,
  )
}

function onRevokeTokenConfirm(prefix) {
  modalHost?.requestConfirm(
    {
      title: '确认撤销 API Token',
      message: `撤销后该 Token（${prefix}…）立即失效，对应设备需要重新生成并配置。`,
      danger: true,
      confirmLabel: '撤销 Token',
      checkboxes: [{ key: 'revoke', label: '我确认撤销该 Token', required: true }],
    },
    () => revokeRow(prefix),
  )
}

// ===== Token 弹窗注册给壳：状态与文案留在这里，壳渲染「框」并接 Esc =====
modalHost?.register('token', {
  isOpen: () => tokenModal.show,
  // Esc 与「我已保存」走同一条关闭路径（弹窗没有"取消"语义）。
  close: closeTokenModal,
  state: tokenModal,
  // 「复制」那颗按钮在壳的弹窗里，但它失败时的反馈属于本节的 API Token 面板：
  // 关掉弹窗就在那儿看得到。
  copyToken: () => runSpotted('token', copyToken),
  copy: () => ({
    title: 'API Token 已生成',
    message: '请立即复制并保存，关闭后将无法再次查看完整 Token。',
    copyLabel: copyLabel.value,
    confirmLabel: '我已保存',
  }),
})

// 自取数据：原 onMounted 的 loadSessionInfo + switchSection 的 account 一支。
watch(
  () => props.active,
  (on) => {
    if (!on) return
    loadSessionInfo()
    loadTokenList()
  },
  { immediate: true },
)
</script>

<template>
  <div class="settings-section">
    <PanelHeader
      icon="key"
      title="账号"
      :tone="sessionUsername ? 'ok' : 'warn'"
      :pill-label="sessionUsername ? '已登录' : '未登录'"
      :description="accountSummary"
      :facts="accountFacts"
    >
      <template #actions>
        <button
          type="button"
          class="btn btn-danger"
          :disabled="loggingOut"
          @click="onLogoutConfirm"
        >{{ loggingOut ? '退出中…' : '退出登录' }}</button>
      </template>
    </PanelHeader>

    <!-- 退出登录的反馈：触发它的按钮在上面那条页头的右侧，所以这条就紧跟页头渲染。 -->
    <div v-if="alert.show" class="alert show feedback" :class="alert.type">{{ alert.message }}</div>

    <fieldset>
      <legend>登录密码</legend>
      <form autocomplete="off" @submit.prevent="submitPassword">
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
        <!-- 更新的结果贴着提交按钮（它就这两颗字段转出来的动作）。 -->
        <div v-if="passwordAlert.show" class="alert show feedback" :class="passwordAlert.type">{{ passwordAlert.message }}</div>
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
      <!-- 生成 / 复制失败贴着「生成新 Token」；生成成功由 Token 弹窗本身反馈。 -->
      <div v-if="tokenAlert.show" class="alert show feedback" :class="tokenAlert.type">{{ tokenAlert.message }}</div>
      <div class="actions is-start">
        <button type="button" class="btn btn-primary" :disabled="generatingToken" @click="generateToken">{{ genTokenBtnLabel }}</button>
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
          <template v-for="t in tokens" :key="t.token_hash_prefix">
            <tr :class="{ revoked: !!t.revoked_at }">
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
            <!-- 撤销的结果就写在这一行下面：按钮在那行的行尾，撤完按钮位就是这条结论。 -->
            <tr
              v-if="revokeAlert.show && revokePrefix === t.token_hash_prefix"
              class="token-feedback-row"
            >
              <td colspan="6">
                <div class="alert show feedback" :class="revokeAlert.type">{{ revokeAlert.message }}</div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
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
/* 就地反馈条：贴着触发它的那一块（页头下、提交按钮上、生成按钮上、被撤的那一行）。 */
.alert.feedback { margin: 10px 0 0; }
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

.actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap; }
/* 备份 / 更新面板里的操作区一律左对齐，和上方表单对齐。 */
.actions.is-start { justify-content: flex-start; }
.token-list { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
.token-list th, .token-list td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
/* Token 表格：撤销列贴右，状态列不折行。 */
.token-table th:last-child, .token-table td:last-child { width: 1%; text-align: right; white-space: nowrap; }
.token-table th:nth-child(5), .token-table td:nth-child(5) { width: 1%; white-space: nowrap; }
.token-list th { color: var(--text-dim); font-weight: 600; }
.token-list .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.token-list tr.revoked { color: var(--text-dim); text-decoration: line-through; opacity: 0.7; }
/* 撤销反馈那一行不是数据行：不吃表格线，内边距交给提示条自己。 */
.token-feedback-row td { padding: 0; border-bottom: none; }
.token-feedback-row .alert { margin: 0 0 8px; }

@media (max-width: 700px) {
  .grid { grid-template-columns: 1fr; }
  /* 窄屏只裁信息列（创建 / 过期时间），**前缀 / 标签 / 状态 / 撤销一律留着**：
     撤销入口是这一节的操作，绝不能因为裁列消失。 */
  .token-table th:nth-child(3), .token-table td:nth-child(3),
  .token-table th:nth-child(4), .token-table td:nth-child(4) { display: none; }
  /* 收紧内边距给标签留宽度；标签可能是长设备名，换行而不是把表格撑出横向滚动。 */
  .token-table th, .token-table td { padding: 8px 6px; }
  .token-table td { overflow-wrap: anywhere; }
  .token-table__actions .btn { white-space: nowrap; }
}
</style>
