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
  <h1>员工登录</h1>
  <p class="hy-staff-lead">用手机号和密码进入卫生入口。未批准或已停用的账号无法登录。</p>
  <p v-if="errorText" class="hy-staff-alert" role="alert">{{ errorText }}</p>
  <form autocomplete="off" @submit.prevent="submit">
    <div class="form-row">
      <label for="staffPhone">手机号</label>
      <input
        id="staffPhone"
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
        v-model="password"
        class="input"
        type="password"
        required
        autocomplete="current-password"
        placeholder="登录密码"
      >
    </div>
    <button type="submit" class="btn btn-primary btn-block hy-staff-submit" :disabled="submitting">
      {{ submitting ? '正在登录…' : '登录' }}
    </button>
  </form>
  <p class="hy-staff-switch">
    还没有账号？
    <router-link to="/hygiene/register">自助注册</router-link>
  </p>
</template>
