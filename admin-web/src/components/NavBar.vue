<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/client'
import SvgIcon from './SvgIcon.vue'

defineProps({
  connected: { type: Boolean, default: false },
  latencyMs: { type: Number, default: null },
})

const route = useRoute()

// 简单映射，对齐旧原生页各自的 data-subtitle（如 public/logs.html:130）
const PAGE_SUBTITLES = [
  { prefix: '/logs', subtitle: '日志中心' },
  { prefix: '/prep-plan', subtitle: '备货计划' },
  { prefix: '/wecom-push', subtitle: '企微推送' },
  { prefix: '/sales-report', subtitle: '销售报表' },
  { prefix: '/recipe', subtitle: '配方 SOP' },
  { prefix: '/admin', subtitle: '数据管理' },
  { prefix: '/', subtitle: '运营仪表盘' },
]

const pageSubtitle = computed(() => {
  const found = PAGE_SUBTITLES.find((item) => route.path.startsWith(item.prefix) && item.prefix !== '/')
  if (found) return found.subtitle
  return route.path === '/' ? '运营仪表盘' : ''
})

// 实时时钟：纯 UI 展示定时器，非数据轮询，卸载时必须 clearInterval。
const CLOCK_TICK_INTERVAL_MS = 1000
const clockText = ref('')
let clockTimer = null

function tickClock() {
  clockText.value = new Date().toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

onMounted(() => {
  tickClock()
  clockTimer = setInterval(tickClock, CLOCK_TICK_INTERVAL_MS)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
})

const loggingOut = ref(false)

async function handleLogout() {
  if (loggingOut.value) return
  if (!window.confirm('确定要退出登录吗？')) return
  loggingOut.value = true
  try {
    await api.post('/api/auth/logout')
    window.location.href = '/login'
  } catch (e) {
    window.alert('退出失败：' + (e.message || '未知错误'))
    loggingOut.value = false
  }
}
</script>

<template>
  <div class="global-nav">
    <div class="global-nav-brand">LuckIn<span v-if="pageSubtitle" class="global-nav-subtitle"> · {{ pageSubtitle }}</span></div>
    <div class="global-nav-tabs">
      <router-link to="/" class="nav-tab" :class="{ active: route.path === '/' }">仪表盘</router-link>
      <router-link to="/admin" class="nav-tab" :class="{ active: route.path.startsWith('/admin') }">数据管理</router-link>
      <router-link to="/sales-report" class="nav-tab" :class="{ active: route.path.startsWith('/sales-report') }">销售报表</router-link>
      <router-link to="/recipe" class="nav-tab" :class="{ active: route.path.startsWith('/recipe') }">配方 SOP</router-link>
      <router-link to="/wecom-push" class="nav-tab" :class="{ active: route.path.startsWith('/wecom-push') }">企微推送</router-link>
      <router-link to="/prep-plan" class="nav-tab" :class="{ active: route.path.startsWith('/prep-plan') }">备货计划</router-link>
      <router-link to="/logs" class="nav-tab" :class="{ active: route.path.startsWith('/logs') }">日志</router-link>
    </div>
    <div class="nav-right">
      <span class="nav-status-dot" :class="connected ? 'online' : 'offline'"></span>
      <span style="font-size:11px;color:var(--text-dim)">{{ connected ? '实时已连接' : '实时已断开' }}</span>
      <span v-if="connected && latencyMs != null" class="nav-latency">延迟 {{ latencyMs }}ms</span>
      <span class="nav-clock">{{ clockText }}</span>
      <router-link
        to="/setup"
        class="nav-setup-btn"
        :class="{ active: route.path.startsWith('/setup') }"
        title="登录配置（POS 凭据 / 账号密码 / API Token）"
      ><SvgIcon name="settings" :size="14" /> 配置</router-link>
      <button
        type="button"
        class="nav-logout-btn"
        title="退出当前登录"
        :disabled="loggingOut"
        @click="handleLogout"
      >退出登录</button>
    </div>
  </div>
</template>
