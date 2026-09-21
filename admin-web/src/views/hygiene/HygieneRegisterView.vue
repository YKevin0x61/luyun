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
    <router-link class="btn btn-primary btn-block hy-staff-submit" to="/hygiene/login">去登录</router-link>
  </div>

  <template v-else>
    <p v-if="errorText" class="hy-staff-alert" role="alert">{{ errorText }}</p>
    <form autocomplete="off" @submit.prevent="submit">
      <div class="form-row">
        <label for="regName">姓名</label>
        <input
          id="regName"
          v-model="name"
          class="input"
          type="text"
          maxlength="40"
          required
          autocomplete="name"
          placeholder="请输入真实姓名"
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
          pattern="1[3-9]\d{9}"
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
          class="input"
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
          class="input"
          type="password"
          required
          minlength="8"
          autocomplete="new-password"
          placeholder="再次输入密码"
        >
      </div>
      <button type="submit" class="btn btn-primary btn-block hy-staff-submit" :disabled="submitting">
        {{ submitting ? '正在提交…' : '提交注册' }}
      </button>
    </form>
    <p class="hy-staff-switch">
      已经注册？
      <router-link to="/hygiene/login">去登录</router-link>
    </p>
  </template>
</template>
