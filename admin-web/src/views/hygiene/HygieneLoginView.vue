<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { parseApiDetail, staffRequest } from '../../utils/hygieneStaff'

const router = useRouter()
const phone = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref('')

onMounted(() => {
  document.getElementById('staffPhone')?.focus()
})

async function submit() {
  errorText.value = ''
  submitting.value = true
  try {
    await staffRequest('/api/hygiene/staff/login', {
      method: 'POST',
      body: { phone: phone.value.trim(), password: password.value },
    })
    router.replace('/hygiene')
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
      <h1 class="staff-title">员工登录</h1>
      <p class="staff-lead">用手机号和密码进入卫生入口。未批准或已停用的账号无法登录。</p>
      <p v-if="errorText" class="staff-alert" role="alert">{{ errorText }}</p>
      <form autocomplete="off" @submit.prevent="submit">
        <div class="form-row">
          <label for="staffPhone">手机号</label>
          <input
            id="staffPhone"
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
          <label for="staffPassword">密码</label>
          <input
            id="staffPassword"
            v-model="password"
            class="input staff-input"
            type="password"
            required
            autocomplete="current-password"
            placeholder="登录密码"
          >
        </div>
        <button type="submit" class="btn btn-primary btn-block staff-submit" :disabled="submitting">
          登录
        </button>
      </form>
      <p class="staff-switch">
        还没有账号？
        <router-link to="/hygiene/register">自助注册</router-link>
      </p>
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
.staff-title {
  font-size: 22px;
  margin: 0 0 8px;
}
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
