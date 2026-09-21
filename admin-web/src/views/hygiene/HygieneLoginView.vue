<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { isStaffLoggedIn, parseApiDetail, staffRequest } from '../../utils/hygieneStaff'
import { loadLoginPrefs, saveLoginPrefs } from '../../utils/loginPrefs'

const route = useRoute()
const router = useRouter()
const imageUploads = useImageUploadQueueStore()

/**
 * 登录成功后的落点：会话失效时 `leaveForStaffLogin` 会带上 `?next=`，
 * 这里把它用起来，员工不必自己找回去。
 *
 * 只接受站内 `/hygiene` 开头的路径 —— 直接拿 query 去 replace 就是开放重定向。
 */
const nextPath = (() => {
  const raw = route.query.next
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value === 'string' && /^\/hygiene(\/|\?|$)/.test(value)) return value
  return '/hygiene'
})()

// 「记住密码，自动登录」偏好与上次手机号来自本地存储：勾选状态跨会话保留、
// 手机号预填，密码交给浏览器密码管理器自动填充（见 utils/loginPrefs.js）。
const loginPrefs = loadLoginPrefs('staff')
const phone = ref(loginPrefs.account)
const password = ref('')
const remember = ref(loginPrefs.remember)
const submitting = ref(false)
// 先确认一次员工会话：仍有效就直接进卫生入口，不必再登一次。
const checking = ref(true)
const errorText = ref('')

const phoneInput = ref(null)
const passwordInput = ref(null)

onMounted(async () => {
  if (await isStaffLoggedIn()) {
    router.replace(nextPath)
    return
  }
  checking.value = false
  await nextTick()
  phoneInput.value?.focus()
})

/** 浏览器自动填充有时只改 DOM 不触发 input 事件，取值时以 DOM 兜底。 */
function fieldValue(model, inputRef) {
  return model || inputRef.value?.value || ''
}

async function submit() {
  errorText.value = ''
  submitting.value = true
  const account = fieldValue(phone.value, phoneInput).trim()
  const secret = fieldValue(password.value, passwordInput)
  try {
    await staffRequest('/api/hygiene/staff/login', {
      method: 'POST',
      body: { phone: account, password: secret, remember: remember.value },
    })
    saveLoginPrefs('staff', { remember: remember.value, account })
    imageUploads.clearTasksByTransport('staff')
    router.replace(nextPath)
  } catch (err) {
    errorText.value = err.message || parseApiDetail(null)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <h1>员工登录</h1>
  <p v-if="checking" class="hy-staff-lead">正在检查登录状态…</p>
  <template v-else>
    <p class="hy-staff-lead">用手机号和密码进入卫生入口。未批准或已停用的账号无法登录。</p>
    <p v-if="errorText" class="hy-staff-alert" role="alert">{{ errorText }}</p>
    <!-- 不设 autocomplete="off"：这里要让浏览器密码管理器保存并自动填充账号密码。 -->
    <form @submit.prevent="submit">
      <div class="form-row">
        <label for="staffPhone">手机号</label>
        <input
          id="staffPhone"
          ref="phoneInput"
          v-model="phone"
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
          ref="passwordInput"
          v-model="password"
          class="input"
          type="password"
          required
          autocomplete="current-password"
          placeholder="登录密码"
        >
      </div>
      <label class="hy-staff-remember">
        <input v-model="remember" type="checkbox">
        <span>记住密码，自动登录（30 天）</span>
      </label>
      <p class="hy-staff-remember-hint">密码由浏览器保存并自动填充，本系统不存储明文密码。</p>
      <button type="submit" class="btn btn-primary btn-block hy-staff-submit" :disabled="submitting">
        {{ submitting ? '正在登录…' : '登录' }}
      </button>
    </form>
    <p class="hy-staff-switch">
      还没有账号？
      <router-link to="/hygiene/register">自助注册</router-link>
    </p>
  </template>
</template>
