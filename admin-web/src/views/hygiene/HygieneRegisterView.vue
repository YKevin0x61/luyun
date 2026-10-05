<script setup>
import { ref } from 'vue'
import { parseApiDetail, staffRequest } from '../../utils/hygieneStaff'

const phone = ref('')
const name = ref('')
const password = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errorText = ref('')
const submitted = ref(false)

/** 中国大陆手机号（与服务端同一口径：`1` + 3-9 + 9 位数字）。 */
const PHONE_RE = /^1[3-9]\d{9}$/
/** 密码长度下限（字符数）。与服务端 `AUTH_MIN_PASSWORD_LENGTH` 的默认值一致。 */
const MIN_PASSWORD_LENGTH = 8

/** 提交前的校验，**文案与服务端 `api/hygiene.py` 的 `_ERROR_DETAILS` 逐字一致**
 *  （`请输入有效的中国大陆手机号` / `密码至少 8 位` / `该手机号已注册`）。
 *
 *  表单上挂了 `novalidate`：不这么做的话浏览器会先弹**英文原生提示**
 *  （`Please match the requested format.` / `Please lengthen this text to 8 characters
 *  or more`），跟整页中文打架，而且原生校验先拦下来，服务端备好的中文永远走不到
 *  （D12）。校验顺序也照服务端：姓名 → 手机号 → 密码长度 → 两次一致 —— 顺序不同
 *  会出现在这一侧先报、到那一侧又换一句的情况。
 *
 *  返回第一条错误，全通过返回空串。单独写成一个纯函数是为了能在单测里逐条压。
 */
function validateRegistration({ name, phone, password, confirmPassword }) {
  if (!String(name || '').trim()) return '请填写员工姓名'
  if (!PHONE_RE.test(String(phone || '').trim())) return '请输入有效的中国大陆手机号'
  if (String(password || '').length < MIN_PASSWORD_LENGTH) {
    return `密码至少 ${MIN_PASSWORD_LENGTH} 位`
  }
  if (password !== confirmPassword) return '两次输入的密码不一致'
  return ''
}

/** 输入变化时清掉上一次的错误（D18：改好了提示还挂着，用户会以为没生效）。
 *  提交级与接口级的提示都在 `errorText` 一处，改任意一格就作废。 */
function clearError() {
  if (errorText.value) errorText.value = ''
}

async function submit() {
  errorText.value = ''
  const invalid = validateRegistration({
    name: name.value,
    phone: phone.value,
    password: password.value,
    confirmPassword: confirmPassword.value,
  })
  if (invalid) {
    errorText.value = invalid
    return
  }
  submitting.value = true
  try {
    await staffRequest('/api/hygiene/staff/register', {
      method: 'POST',
      body: {
        name: name.value.trim(),
        phone: phone.value.trim(),
        password: password.value,
      },
    })
    submitted.value = true
  } catch (err) {
    errorText.value = err.message || parseApiDetail(null)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <h1>员工注册</h1>
  <p class="hy-staff-lead">填姓名、手机号和密码自助注册。超级管理员批准后才能登录，职位和卫生权限由后台设置。</p>

  <div v-if="submitted" class="hy-staff-done">
    <p>已提交。请等超级管理员在花名册里批准后再登录。</p>
    <router-link class="btn btn-primary btn-block hy-staff-submit" to="/login">去登录</router-link>
  </div>

  <template v-else>
    <p v-if="errorText" id="regError" class="hy-staff-alert" role="alert">{{ errorText }}</p>
    <!-- `novalidate`：校验与文案都由上面那一份中文判据说了算，不让浏览器先弹英文
         （D12）。字段上原来的 `required` / `pattern` / `minlength` 随之撤掉 —— 留着它们
         只会让读屏与表单控件以为还有一层原生校验，而那一层已经被关掉。
         `autocomplete` 不关：新密码要让密码管理器收存（原来整表 `autocomplete="off"`
         会把刚设的密码丢掉，D17）。 -->
    <form novalidate @submit.prevent="submit">
      <div class="form-row">
        <label for="regName">姓名</label>
        <input
          id="regName"
          v-model="name"
          class="input"
          type="text"
          maxlength="40"
          autocomplete="name"
          placeholder="请输入真实姓名"
          :aria-invalid="errorText ? 'true' : 'false'"
          :aria-describedby="errorText ? 'regError' : undefined"
          @input="clearError"
        >
      </div>
      <div class="form-row">
        <label for="regPhone">手机号</label>
        <input
          id="regPhone"
          v-model="phone"
          class="input"
          type="tel"
          inputmode="numeric"
          maxlength="11"
          autocomplete="username"
          placeholder="11 位中国大陆手机号"
          :aria-invalid="errorText ? 'true' : 'false'"
          :aria-describedby="errorText ? 'regError' : undefined"
          @input="clearError"
        >
      </div>
      <div class="form-row">
        <label for="regPassword">密码</label>
        <input
          id="regPassword"
          v-model="password"
          class="input"
          type="password"
          autocomplete="new-password"
          placeholder="至少 8 位"
          :aria-invalid="errorText ? 'true' : 'false'"
          :aria-describedby="errorText ? 'regError' : undefined"
          @input="clearError"
        >
      </div>
      <div class="form-row">
        <label for="regConfirm">确认密码</label>
        <input
          id="regConfirm"
          v-model="confirmPassword"
          class="input"
          type="password"
          autocomplete="new-password"
          placeholder="再次输入密码"
          :aria-invalid="errorText ? 'true' : 'false'"
          :aria-describedby="errorText ? 'regError' : undefined"
          @input="clearError"
        >
      </div>
      <button type="submit" class="btn btn-primary btn-block hy-staff-submit" :disabled="submitting">
        {{ submitting ? '正在提交…' : '提交注册' }}
      </button>
    </form>
    <p class="hy-staff-switch">
      已经注册？
      <router-link to="/login">去登录</router-link>
    </p>
  </template>
</template>
