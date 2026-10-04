<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'
import { useImageUploadQueueStore } from '../stores/imageUploadQueue'
import { clearAuthStatusCache, setAuthLoggedIn } from '../utils/authStatus'
import {
  STAFF_SESSION_PROBE_TIMEOUT_MS,
  parseApiDetail,
  staffRequest,
} from '../utils/hygieneStaff'
import {
  loadLoginPrefs,
  loadLoginTab,
  saveLoginPrefs,
  saveLoginTab,
} from '../utils/loginPrefs'
import { resolveLoginNext, resolveLoginTab, resolveStaffNext } from '../utils/loginNext'

// 迁移自 public/login.html：登录 / 首次初始化管理员 / 已登录三态页面。
// 票 03 起同一个面板装两种身份（管理员 / 员工）：两个显式 Tab 各打自己原来那套登录接口、
// 各发自己原来的 cookie —— 后端 `sessions` 与 `hygiene_staff_sessions` 两张表、两个
// cookie、两条守卫链一个字不改，也没有统一登录接口或账号形态自动判别。
//
// 该页不经过 api/client.js（避免其 401 重定向逻辑与本页自身状态机冲突）：
// 管理员栏用原生 fetch（与旧页逐字一致），员工栏用 `staffRequest`（同样是直连，
// 但带员工端超时与中文网络文案）。
//
// 视觉：两栏共用一套骨架（品牌 + Tab + 副标题 + 提示区 + 表单区），主题按身份切；
// 员工那套配色写在本组件自己的 scoped 样式里，不挂 `/hygiene-admin.css`
// （那是卫生端的样式表，管理端登录页不该依赖它）。
const route = useRoute()
const router = useRouter()
const imageUploads = useImageUploadQueueStore()

/** 两个显式 Tab。`scope` 是品牌副题，跟着身份走。 */
const PANEL_TABS = [
  { key: 'admin', label: '管理员', scope: '数据中心' },
  { key: 'staff', label: '员工', scope: '员工端' },
]

/**
 * 默认开在哪一栏：记住上次选的；`?next=` 落在员工端前缀内时**强制**员工栏
 * （优先于记住值，员工被守卫踢回来就落在正确那一栏）。判据在 `utils/loginNext.js`。
 */
const activeTab = ref(resolveLoginTab(route.query.next, loadLoginTab()))

// phase: 'loading' | 'error' | 'loggedIn' | 'login' | 'init'
// 'init'（首次创建管理员账号）只属于管理员栏，员工栏没有这一态。
// 两栏各自一份状态：一栏的错误、表单与确认面板不会串到另一栏。
const adminPhase = ref('loading')
const staffPhase = ref('loading')
const phaseByTab = { admin: adminPhase, staff: staffPhase }
// 每栏只在第一次成为当前栏时查一次会话（切来切去不重复打状态接口）。
const loadedTabs = { admin: false, staff: false }
const phase = computed(() => phaseByTab[activeTab.value].value)

const alert = ref({ show: false, type: 'error', message: '' })
/** 当前栏的已登录身份：管理员是用户名，员工是姓名（回落到手机号）。 */
const loggedInName = ref('')

const initForm = ref({ username: '', password: '', confirmPassword: '' })
const initSubmitting = ref(false)

// 「记住登录」偏好与上次账号来自本地存储：勾选状态跨会话保留，账号预填，
// 密码交给浏览器密码管理器自动填充（见 loginPrefs.js）。两栏各一个命名空间。
const adminPrefs = loadLoginPrefs('admin')
const adminForm = ref({
  username: adminPrefs.account,
  password: '',
  remember: adminPrefs.remember,
})
const adminSubmitting = ref(false)

const staffPrefs = loadLoginPrefs('staff')
const staffForm = ref({
  phone: staffPrefs.account,
  password: '',
  remember: staffPrefs.remember,
})
const staffSubmitting = ref(false)

const initUsernameInput = ref(null)
const adminUsernameInput = ref(null)
const adminPasswordInput = ref(null)
const staffPhoneInput = ref(null)
const staffPasswordInput = ref(null)

const brandScope = computed(
  () => PANEL_TABS.find((tab) => tab.key === activeTab.value)?.scope || '',
)

const subtitle = computed(() => {
  if (phase.value === 'loading') return '正在检查登录状态…'
  if (phase.value === 'error') return '无法连接服务器，请稍后重试。'
  if (phase.value === 'loggedIn') return '您已登录，可直接进入系统或退出后换账号。'
  if (activeTab.value === 'admin') {
    return phase.value === 'init' ? '首次使用，请创建管理员账号。' : '请登录以访问数据中心。'
  }
  return '用手机号和密码进入员工端。未批准或已停用的账号无法登录。'
})

function showAlert(message, type = 'error') {
  alert.value = { show: true, type, message }
}

function hideAlert() {
  alert.value = { show: false, type: 'error', message: '' }
}

function parseErrorDetail(data) {
  if (!data) return '请求失败'
  const detail = data.detail
  if (typeof detail === 'string') {
    if (/72 bytes|truncate manually/i.test(detail)) {
      return '密码过长，请缩短后重试'
    }
    return detail
  }
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item.msg || String(item)).join('；')
  }
  return '请求失败'
}

/** `?switch=1`：已登录时仍停在面板上换账号，而不是被自动带进系统。 */
function wantsAccountSwitch() {
  const value = route.query.switch
  return value === '1' || value === 'true'
}

/** 浏览器自动填充有时只改 DOM 不触发 input 事件，取值时以 DOM 兜底。 */
function fieldValue(model, inputRef) {
  return model || inputRef.value?.value || ''
}

async function focusField(inputRef) {
  await nextTick()
  inputRef.value?.focus()
}

// ===== 落点 =====

/** 管理员栏登录后的落点；员工端路径不认（身份互斥，判据在 utils/loginNext.js）。
 *
 *  用 `replace`：登录页不该留在后退历史里 —— 否则登录成功后按浏览器后退会退回
 *  登录页（票 10 消掉的 audit 条目 9）。员工栏本来就是这么做的，两栏的 history
 *  语义现在只有一种。 */
function redirectAsAdmin() {
  setAuthLoggedIn(true)
  router.replace(resolveLoginNext(route.query.next, '/'))
}

/** 员工栏登录后的落点；只认员工端前缀，别的一律回落到员工默认落点。 */
function enterStaffLanding() {
  router.replace(resolveStaffNext(route.query.next))
}

// ===== 面板状态机 =====

function selectTab(tab) {
  if (tab === activeTab.value) return
  activeTab.value = tab
  // 「记住上次选的」：只有用户自己点的才算选择；`?next=` 强制开的那次不写盘。
  saveLoginTab(tab)
  // 票 09 起清单归属只看路径（`/login` 归管理端那份），换栏不再换清单 ——
  // 一条路径两份清单的判据吃不下「当下停在哪一栏」，这正是本票要收敛掉的漂移。
  // 一栏的错误不跟着串到另一栏。
  hideAlert()
  if (!loadedTabs[tab]) {
    loadedTabs[tab] = true
    void loadStatus(tab)
  }
}

function loadStatus(tab = activeTab.value) {
  return tab === 'admin' ? loadAdminStatus() : loadStaffStatus()
}

async function loadAdminStatus() {
  adminPhase.value = 'loading'
  try {
    const resp = await fetch('/api/auth/status', { credentials: 'include' })
    if (!resp.ok) throw new Error('无法获取登录状态')
    const data = await resp.json()
    if (data.logged_in) {
      setAuthLoggedIn(true)
      loggedInName.value = data.username || ''
      if (wantsAccountSwitch()) {
        adminPhase.value = 'loggedIn'
        return
      }
      // 会话仍有效就直接进系统——门店机器打开书签/PWA 图标即用，不必再点一次。
      redirectAsAdmin()
      return
    }
    if (data.initialized) {
      adminPhase.value = 'login'
      await focusField(adminUsernameInput)
      return
    }
    adminPhase.value = 'init'
    await focusField(initUsernameInput)
  } catch (err) {
    adminPhase.value = 'error'
    showAlert(err.message || '无法连接服务器', 'error')
  }
}

async function loadStaffStatus() {
  staffPhase.value = 'loading'
  try {
    const data = await staffRequest('/api/hygiene/staff/me', {
      timeoutMs: STAFF_SESSION_PROBE_TIMEOUT_MS,
    })
    loggedInName.value = data?.employee?.name || data?.employee?.phone || ''
    if (wantsAccountSwitch()) {
      staffPhase.value = 'loggedIn'
      return
    }
    // 会话仍有效就直接进员工端，不必再登一次。
    enterStaffLanding()
  } catch {
    // 401 是「确实没登录」；断网 / 超时也停在登录表单上（提交时会把网络问题说清楚），
    // 跟原来员工登录页一样 —— 别把员工挡在一句「连不上」后面。
    staffPhase.value = 'login'
    await focusField(staffPhoneInput)
  }
}

// ===== 管理员栏 =====

async function submitInit() {
  hideAlert()
  initSubmitting.value = true
  const payload = {
    username: initForm.value.username.trim(),
    password: initForm.value.password,
    confirm_password: initForm.value.confirmPassword,
  }
  try {
    const resp = await fetch('/api/auth/init', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await resp.json().catch(() => null)
    if (!resp.ok) {
      showAlert(parseErrorDetail(data))
      return
    }
    redirectAsAdmin()
  } catch (err) {
    showAlert(err.message || '网络错误')
  } finally {
    initSubmitting.value = false
  }
}

async function submitAdminLogin() {
  hideAlert()
  adminSubmitting.value = true
  const username = fieldValue(adminForm.value.username, adminUsernameInput).trim()
  const password = fieldValue(adminForm.value.password, adminPasswordInput)
  const payload = {
    username,
    password,
    remember: adminForm.value.remember,
  }
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await resp.json().catch(() => null)
    if (!resp.ok) {
      showAlert(parseErrorDetail(data))
      return
    }
    saveLoginPrefs('admin', { remember: adminForm.value.remember, account: username })
    redirectAsAdmin()
  } catch (err) {
    showAlert(err.message || '网络错误')
  } finally {
    adminSubmitting.value = false
  }
}

async function logoutAdmin() {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } catch (err) {
    showAlert(err.message || '退出失败', 'error')
    return
  }
  clearAuthStatusCache()
  hideAlert()
  adminPhase.value = 'login'
  await focusField(adminUsernameInput)
}

// ===== 员工栏 =====

async function submitStaffLogin() {
  hideAlert()
  staffSubmitting.value = true
  const account = fieldValue(staffForm.value.phone, staffPhoneInput).trim()
  const secret = fieldValue(staffForm.value.password, staffPasswordInput)
  try {
    await staffRequest('/api/hygiene/staff/login', {
      method: 'POST',
      body: { phone: account, password: secret, remember: staffForm.value.remember },
    })
    saveLoginPrefs('staff', { remember: staffForm.value.remember, account })
    // 换人了：清掉上一个人的待上传照片，否则会传成新登录者的。
    imageUploads.clearTasksByTransport('staff')
    enterStaffLanding()
  } catch (err) {
    showAlert(err.message || parseApiDetail(null))
  } finally {
    staffSubmitting.value = false
  }
}

async function logoutStaff() {
  try {
    await staffRequest('/api/hygiene/staff/logout', { method: 'POST' })
  } catch {
    // 会话可能已经没了；仍然把面板切回登录表单。
  }
  imageUploads.clearTasksByTransport('staff')
  hideAlert()
  staffPhase.value = 'login'
  await focusField(staffPhoneInput)
}

// ===== 已登录确认面板（两种身份共用一套按钮，各自走自己的出口） =====

function enterSystem() {
  if (activeTab.value === 'admin') {
    redirectAsAdmin()
    return
  }
  enterStaffLanding()
}

function logoutFromPanel() {
  return activeTab.value === 'admin' ? logoutAdmin() : logoutStaff()
}

onMounted(() => {
  loadedTabs[activeTab.value] = true
  void loadStatus(activeTab.value)
})
</script>

<template>
  <div class="login-page" :class="`is-${activeTab}`">
    <div class="login-card">
      <div class="login-brand">
        <span class="login-brand-name">厨务管家</span>
        <span class="login-brand-sep">·</span>
        <span class="login-brand-scope">{{ brandScope }}</span>
      </div>

      <div class="login-tabs" role="tablist" aria-label="选择登录身份">
        <button
          v-for="tab in PANEL_TABS"
          :key="tab.key"
          type="button"
          role="tab"
          class="login-tab"
          :class="{ active: activeTab === tab.key }"
          :aria-selected="activeTab === tab.key ? 'true' : 'false'"
          @click="selectTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>

      <p class="login-subtitle">{{ subtitle }}</p>

      <div v-if="alert.show" class="login-alert" :class="alert.type" role="alert">{{ alert.message }}</div>

      <div v-if="phase === 'loading'" class="loading">{{ subtitle }}</div>

      <div v-else-if="phase === 'loggedIn'">
        <p class="hint logged-in-name">
          {{ loggedInName ? `当前账号：${loggedInName}` : '当前会话有效' }}
        </p>
        <div class="logged-in-actions">
          <button type="button" class="btn btn-primary btn-block" @click="enterSystem">进入系统</button>
          <button type="button" class="btn btn-block" @click="logoutFromPanel">退出登录</button>
        </div>
      </div>

      <!-- 首次初始化只属于管理员栏；员工栏没有这一态。 -->
      <form v-else-if="phase === 'init'" autocomplete="off" @submit.prevent="submitInit">
        <div class="form-row">
          <label for="initUsername">管理员用户名</label>
          <input
            id="initUsername"
            ref="initUsernameInput"
            v-model="initForm.username"
            class="input"
            type="text"
            required
            autocomplete="username"
            placeholder="例如 admin"
          >
        </div>
        <div class="form-row">
          <label for="initPassword">密码</label>
          <input
            id="initPassword"
            v-model="initForm.password"
            class="input"
            type="password"
            required
            autocomplete="new-password"
            placeholder="至少 8 位"
          >
          <p class="hint">首次使用需创建共享管理员账号，密码至少 8 位。</p>
        </div>
        <div class="form-row">
          <label for="initConfirm">确认密码</label>
          <input
            id="initConfirm"
            v-model="initForm.confirmPassword"
            class="input"
            type="password"
            required
            autocomplete="new-password"
            placeholder="再次输入密码"
          >
        </div>
        <button type="submit" class="btn btn-primary btn-block" :disabled="initSubmitting">创建账号并登录</button>
      </form>

      <!-- 不设 autocomplete="off"：这里要让浏览器密码管理器保存并自动填充账号密码。 -->
      <form v-else-if="activeTab === 'admin'" @submit.prevent="submitAdminLogin">
        <div class="form-row">
          <label for="loginUsername">用户名</label>
          <input
            id="loginUsername"
            ref="adminUsernameInput"
            v-model="adminForm.username"
            class="input"
            type="text"
            required
            autocomplete="username"
            placeholder="管理员用户名"
          >
        </div>
        <div class="form-row">
          <label for="loginPassword">密码</label>
          <input
            id="loginPassword"
            ref="adminPasswordInput"
            v-model="adminForm.password"
            class="input"
            type="password"
            required
            autocomplete="current-password"
            placeholder="登录密码"
          >
        </div>
        <label class="remember-row">
          <LuyunCheckbox v-model="adminForm.remember" />
          <span>记住密码，自动登录（30 天）</span>
        </label>
        <p class="hint remember-hint">密码由浏览器保存并自动填充，本系统不存储明文密码。</p>
        <button type="submit" class="btn btn-primary btn-block" :disabled="adminSubmitting">登录</button>
      </form>

      <form v-else @submit.prevent="submitStaffLogin">
        <div class="form-row">
          <label for="staffPhone">手机号</label>
          <input
            id="staffPhone"
            ref="staffPhoneInput"
            v-model="staffForm.phone"
            class="input"
            type="tel"
            inputmode="numeric"
            maxlength="11"
            required
            autocomplete="username"
            placeholder="11 位中国大陆手机号"
          >
        </div>
        <div class="form-row">
          <label for="staffPassword">密码</label>
          <input
            id="staffPassword"
            ref="staffPasswordInput"
            v-model="staffForm.password"
            class="input"
            type="password"
            required
            autocomplete="current-password"
            placeholder="登录密码"
          >
        </div>
        <label class="remember-row">
          <input v-model="staffForm.remember" type="checkbox">
          <span>记住密码，自动登录（30 天）</span>
        </label>
        <p class="hint remember-hint">密码由浏览器保存并自动填充，本系统不存储明文密码。</p>
        <button type="submit" class="btn btn-primary btn-block" :disabled="staffSubmitting">登录</button>
        <p class="login-switch">
          还没有账号？
          <router-link to="/register">自助注册</router-link>
        </p>
      </form>
    </div>
  </div>
</template>

<style scoped>
/* 共用骨架 + 按身份切主题：管理员栏是现有的深色卡片（数据中心），员工栏是原来员工
   登录页那套青绿（浅色卡片、薄荷信号色）。两套都只在 `.login-page.is-*` 下生效。 */
.login-page {
  --login-bg:
    radial-gradient(circle at 12% 0%, rgba(99, 102, 241, 0.14), transparent 30%),
    radial-gradient(circle at 88% 10%, rgba(6, 182, 212, 0.08), transparent 28%),
    var(--bg);
  --login-ink: var(--text);
  --login-dim: var(--text-dim);
  --login-line: var(--border);
  --login-card-bg: rgba(17, 24, 39, 0.92);
  --login-card-line: var(--border);
  --login-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
  --login-accent: var(--accent);
  --login-accent-ink: #fff;
  --login-accent-ring: rgba(99, 102, 241, 0.24);
  --login-input-bg: var(--card2);
  --login-input-line: var(--border);
  --login-tab-bg: rgba(148, 163, 184, 0.08);
  --login-alert-bg: rgba(239, 68, 68, 0.12);
  --login-alert-line: rgba(239, 68, 68, 0.3);
  --login-alert-ink: #fca5a5;
  --login-radius: 14px;

  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--login-bg);
  color: var(--login-ink);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}

/* 员工栏：原来员工登录页那套（深青墨底 + 薄荷信号色），样式写在这里而不是挂
   /hygiene-admin.css —— 那是卫生端的样式表，登录面板不该依赖它。 */
.login-page.is-staff {
  --login-bg:
    radial-gradient(900px 460px at 82% -12%, rgba(63, 224, 176, 0.16), transparent 62%),
    radial-gradient(700px 460px at -12% 110%, rgba(63, 224, 176, 0.08), transparent 60%),
    #0a1719;
  --login-ink: #e4f0ee;
  --login-dim: #85aaa7;
  --login-line: rgba(133, 205, 198, 0.28);
  --login-card-bg: rgba(17, 37, 41, 0.94);
  --login-card-line: rgba(133, 205, 198, 0.28);
  --login-shadow: 0 18px 44px -18px rgba(1, 8, 7, 0.85);
  --login-accent: #3fe0b0;
  --login-accent-ink: #052018;
  --login-accent-ring: rgba(63, 224, 176, 0.28);
  --login-input-bg: #0c1d20;
  --login-input-line: rgba(133, 205, 198, 0.2);
  --login-tab-bg: rgba(63, 224, 176, 0.08);
  --login-alert-bg: rgba(217, 72, 47, 0.13);
  --login-alert-line: rgba(217, 72, 47, 0.42);
  --login-alert-ink: #ef6a4f;
  --login-radius: 16px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: var(--login-card-bg);
  border: 1px solid var(--login-card-line);
  border-radius: var(--login-radius);
  padding: 28px 32px;
  box-shadow: var(--login-shadow);
}

.login-card .input { width: 100%; }

.login-brand {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.login-brand-sep,
.login-brand-scope { color: var(--login-accent); }

.login-tabs {
  display: flex;
  gap: 6px;
  padding: 4px;
  margin-bottom: 16px;
  background: var(--login-tab-bg);
  border: 1px solid var(--login-card-line);
  border-radius: 10px;
}

.login-tab {
  flex: 1 1 0;
  padding: 8px 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--login-dim);
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background .15s, color .15s;
}
.login-tab:hover { color: var(--login-ink); }
.login-tab.active {
  background: var(--login-accent);
  color: var(--login-accent-ink);
}

.login-subtitle {
  color: var(--login-dim);
  font-size: 13px;
  margin-bottom: 20px;
  line-height: 1.6;
}

.login-alert {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 16px;
}
.login-alert.error {
  background: var(--login-alert-bg);
  border: 1px solid var(--login-alert-line);
  color: var(--login-alert-ink);
}
.login-alert.info {
  background: rgba(59, 130, 246, 0.10);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
}

.hint {
  font-size: 11px;
  color: var(--login-dim);
  margin-top: 4px;
  line-height: 1.5;
}

.remember-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 6px;
  font-size: 13px;
  color: var(--login-dim);
  cursor: pointer;
}
.remember-row input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--login-accent);
  cursor: pointer;
}

.remember-hint {
  margin: 0 0 16px;
}

.logged-in-name { margin-bottom: 16px; }
.logged-in-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.login-switch {
  margin: 14px 0 0;
  font-size: 13px;
  color: var(--login-dim);
  text-align: center;
}
.login-switch a {
  color: var(--login-accent);
  font-weight: 700;
  text-underline-offset: .2em;
}

.loading {
  text-align: center;
  color: var(--login-dim);
  font-size: 13px;
  padding: 24px 0;
}

/* 员工栏的输入与按钮：主题色跟着身份走（管理员栏沿用全局深色样式，一个字不动）。 */
.login-page.is-staff :deep(.input),
.login-page.is-staff .input {
  background: var(--login-input-bg);
  border-color: var(--login-input-line);
  color: var(--login-ink);
  min-height: 46px;
  font-size: 16px;
}
.login-page.is-staff .input:focus {
  border-color: var(--login-accent);
  box-shadow: 0 0 0 2px var(--login-accent-ring);
}
.login-page.is-staff .form-row label {
  display: block;
  font-size: 12px;
  margin-bottom: 6px;
  color: var(--login-dim);
  font-weight: 600;
}
.login-page.is-staff .btn {
  min-height: 46px;
  background: transparent;
  border-color: var(--login-input-line);
  color: var(--login-ink);
  font-size: 14px;
}
.login-page.is-staff .btn:hover:not(:disabled) { border-color: var(--login-accent); }
.login-page.is-staff .btn-primary {
  background: var(--login-accent);
  border-color: var(--login-accent);
  color: var(--login-accent-ink);
  font-weight: 700;
}

@media (max-width: 480px) {
  .login-card { padding: 22px 18px; }
}
</style>
