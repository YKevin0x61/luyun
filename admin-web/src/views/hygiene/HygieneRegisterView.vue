<script setup>
import { ref } from 'vue'
import { parseApiDetail, staffRequest } from '../../utils/hygieneStaff'

const phone = ref('')
const password = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errorText = ref('')
const submitted = ref(false)

async function submit() {
  errorText.value = ''
  if (password.value !== confirmPassword.value) {
    errorText.value = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    await staffRequest('/api/hygiene/staff/register', {
      method: 'POST',
      body: { phone: phone.value.trim(), password: password.value },
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
  <div class="staff-phone">
    <div class="staff-card">
      <p class="staff-brand">LuckIn<span>卫生</span></p>
      <h1 class="staff-title">员工注册</h1>
      <p class="staff-lead">用手机号和密码自助注册。超级管理员批准后才能登录，职位和卫生权限由后台设置。</p>

      <div v-if="submitted" class="staff-done">
        <p>已提交。请等超级管理员在花名册里批准后再登录。</p>
        <router-link class="btn btn-primary btn-block staff-submit" to="/hygiene/login">去登录</router-link>
      </div>

      <template v-else>
        <p v-if="errorText" class="staff-alert" role="alert">{{ errorText }}</p>
        <form autocomplete="off" @submit.prevent="submit">
          <div class="form-row">
            <label for="regPhone">手机号</label>
            <input
              id="regPhone"
              v-model="phone"
              class="input staff-input"
              type="tel"
              inputmode="numeric"
              maxlength="11"
              required
              autocomplete="username"
              placeholder="11 位中国大陆手机号"
            >
          </div>
          <div class="form-row">
            <label for="regPassword">密码</label>
            <input
              id="regPassword"
              v-model="password"
              class="input staff-input"
              type="password"
              required
              minlength="8"
              autocomplete="new-password"
              placeholder="至少 8 位"
            >
          </div>
          <div class="form-row">
            <label for="regConfirm">确认密码</label>
            <input
              id="regConfirm"
              v-model="confirmPassword"
              class="input staff-input"
              type="password"
              required
              minlength="8"
              autocomplete="new-password"
              placeholder="再次输入密码"
            >
          </div>
          <button type="submit" class="btn btn-primary btn-block staff-submit" :disabled="submitting">
            提交注册
          </button>
        </form>
        <p class="staff-switch">
          已经注册？
          <router-link to="/hygiene/login">去登录</router-link>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.staff-phone {
  min-height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: max(20px, env(safe-area-inset-top)) 16px max(24px, env(safe-area-inset-bottom));
}
.staff-card {
  width: 100%;
  max-width: 420px;
  margin: auto 0;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px 22px;
}
.staff-brand {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  margin: 0 0 10px;
}
.staff-brand span { color: var(--accent); margin-left: 6px; }
.staff-title { font-size: 22px; margin: 0 0 8px; }
.staff-lead {
  color: var(--text-dim);
  font-size: 14px;
  line-height: 1.55;
  margin: 0 0 20px;
}
.staff-alert {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin: 0 0 16px;
}
.staff-done p {
  font-size: 15px;
  line-height: 1.6;
  margin: 0 0 16px;
}
.form-row { margin-bottom: 14px; }
.form-row label {
  display: block;
  font-size: 13px;
  margin-bottom: 6px;
  color: var(--text-dim);
}
.staff-input { min-height: 48px; font-size: 16px; width: 100%; }
.staff-submit { min-height: 48px; font-size: 16px; margin-top: 8px; }
.staff-switch {
  margin: 18px 0 0;
  font-size: 14px;
  color: var(--text-dim);
  text-align: center;
}
.staff-switch a { color: var(--accent); }
</style>
