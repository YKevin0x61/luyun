<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LuyunCheckbox from '../components/ui/LuyunCheckbox.vue'

// 迁移自 public/login.html：登录 / 首次初始化管理员 / 已登录三态页面。
// 该页不经过 api/client.js（避免其 401 重定向逻辑与本页自身状态机冲突），
// 直接用原生 fetch + credentials:'include'，与旧页行为保持一致。

const route = useRoute()
const router = useRouter()

// phase: 'loading' | 'error' | 'loggedIn' | 'login' | 'init'
const phase = ref('loading')
const pageSubtitle = ref('正在加载…')

const alert = ref({ show: false, type: 'error', message: '' })

const loggedInUsername = ref('')

const initForm = ref({ username: '', password: '', confirmPassword: '' })
const initSubmitting = ref(false)

const loginForm = ref({ username: '', password: '', remember: false })
const loginSubmitting = ref(false)

const initUsernameInput = ref(null)
const loginUsernameInput = ref(null)

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

function redirectAfterSuccess() {
  const next = route.query.next
  router.push(next ? String(next) : '/')
}

async function focusField(inputRef) {
  await nextTick()
  inputRef.value?.focus()
}

function showInitForm() {
  phase.value = 'init'
  pageSubtitle.value = '首次使用，请创建管理员账号。'
  focusField(initUsernameInput)
}

function showLoginForm() {
  phase.value = 'login'
  pageSubtitle.value = '请登录以访问数据中心。'
  focusField(loginUsernameInput)
}

function showLoggedInPanel(username) {
  phase.value = 'loggedIn'
  pageSubtitle.value = '您已登录，可直接进入系统或退出后换账号。'
  loggedInUsername.value = username || ''
}

async function loadStatus() {
  try {
    const resp = await fetch('/api/auth/status', { credentials: 'include' })
    if (!resp.ok) throw new Error('无法获取登录状态')
    const data = await resp.json()
    if (data.logged_in) {
      showLoggedInPanel(data.username)
      return
    }
    if (data.initialized) showLoginForm()
    else showInitForm()
  } catch (err) {
    phase.value = 'error'
    showAlert(err.message || '无法连接服务器', 'error')
  }
}

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
    redirectAfterSuccess()
  } catch (err) {
    showAlert(err.message || '网络错误')
  } finally {
    initSubmitting.value = false
  }
}

async function submitLogin() {
  hideAlert()
  loginSubmitting.value = true
  const payload = {
    username: loginForm.value.username.trim(),
    password: loginForm.value.password,
    remember: loginForm.value.remember,
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
    redirectAfterSuccess()
  } catch (err) {
    showAlert(err.message || '网络错误')
  } finally {
    loginSubmitting.value = false
  }
}

async function logoutFromLogin() {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } catch (err) {
    showAlert(err.message || '退出失败', 'error')
    return
  }
  hideAlert()
  showLoginForm()
}

onMounted(loadStatus)
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">LuckIn <span>·</span> 数据中心</div>
      <p class="login-subtitle">{{ pageSubtitle }}</p>

      <div v-if="alert.show" class="login-alert" :class="alert.type" role="alert">{{ alert.message }}</div>

      <div v-if="phase === 'loading'" class="loading">正在检查登录状态…</div>

      <div v-else-if="phase === 'loggedIn'">
        <p class="hint" style="margin-bottom:16px;">
          {{ loggedInUsername ? `当前账号：${loggedInUsername}` : '当前会话有效' }}
        </p>
        <div style="display:flex;flex-direction:column;gap:10px;">
          <button type="button" class="btn btn-primary btn-block" @click="redirectAfterSuccess">进入系统</button>
          <button type="button" class="btn btn-block" @click="logoutFromLogin">退出登录</button>
        </div>
      </div>

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

      <form v-else-if="phase === 'login'" autocomplete="off" @submit.prevent="submitLogin">
        <div class="form-row">
          <label for="loginUsername">用户名</label>
          <input
            id="loginUsername"
            ref="loginUsernameInput"
            v-model="loginForm.username"
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
            v-model="loginForm.password"
            class="input"
            type="password"
            required
            autocomplete="current-password"
            placeholder="登录密码"
          >
        </div>
        <label class="remember-row">
          <LuyunCheckbox v-model="loginForm.remember" />
          <span>记住登录（30 天）</span>
        </label>
        <button type="submit" class="btn btn-primary btn-block" :disabled="loginSubmitting">登录</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(circle at 12% 0%, rgba(99, 102, 241, 0.14), transparent 30%),
    radial-gradient(circle at 88% 10%, rgba(6, 182, 212, 0.08), transparent 28%),
    var(--bg);
  color: var(--text);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px 32px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
}

.login-card .input { width: 100%; }

.login-brand {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.login-brand span { color: var(--accent); }

.login-subtitle {
  color: var(--text-dim);
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
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
}
.login-alert.info {
  background: rgba(59, 130, 246, 0.10);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
}

.hint {
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 4px;
  line-height: 1.5;
}

.remember-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 16px;
  font-size: 13px;
  color: var(--text-dim);
  cursor: pointer;
}

.loading {
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
  padding: 24px 0;
}

@media (max-width: 480px) {
  .login-card { padding: 22px 18px; }
}
</style>
