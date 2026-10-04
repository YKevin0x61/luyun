<script setup>
import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import ImageUploadQueuePanel from './components/ImageUploadQueuePanel.vue'
import NavBar from './components/NavBar.vue'
import PwaUpdateBanner from './components/PwaUpdateBanner.vue'
import { useRealtime } from './composables/useRealtime'
import { usePwaUpdate } from './composables/usePwaUpdate'
import { useStationsStore } from './stores/stations'
import { isHygieneAdminPath } from './utils/hygieneCopy'
import { resolveLoginTab } from './utils/loginNext'
import { loadLoginTab } from './utils/loginPrefs'
import { applyPwaManifest } from './utils/pwaManifest'

const route = useRoute()
const pwaUpdate = usePwaUpdate()
// 登录 / 配置页是独立全屏页，不显示主导航壳（见 router meta.standalone）。
const isStandalone = computed(() => !!route.meta.standalone)
const isHygieneAdmin = computed(() => isHygieneAdminPath(route.path))
const realtimeEnabled = computed(() => route.meta.realtime === true || !route.meta.public)
// 员工端页面的实时连接显式声明自己是员工（票 08）：同一浏览器可能同时持管理端与
// 员工端两套 cookie，服务端默认「管理端优先」，不声明就会被判成管理端身份——主题
// 白名单全开、员工之间「只看得到与自己有关的那条」的隔离失效。判据用页面自己那条
// `meta.staffAuth`（与路由守卫同一个标记），不另写一份路径正则；管理端页面不带声明，
// 默认优先级本来就对。声明写在连接 URL 上，路由换了身份就换连接（composable 负责重连）。
const realtimeIdentity = computed(() => (route.meta.staffAuth ? 'staff' : null))

// PWA 清单归属跟面板用的是同一个判据（`utils/loginNext.js` 的 `resolveLoginTab`）：
// `/login` 一条路径装两种身份，装出来是哪份应用看当下停在哪一栏 —— 员工栏是员工应用
// （打开即 `/staff/today`），管理栏是管理应用。`?next=` 会改这一栏，所以也盯着它；
// 其余路径由路径本身决定归属，多带一个身份不影响（面板里点 Tab 时由 `LoginView.vue` 换）。
watch(
  [() => route.path, () => route.query.next],
  () => applyPwaManifest(route.path, resolveLoginTab(route.query.next, loadLoginTab())),
  { immediate: true },
)

const listeners = new Set()
function onRealtimeEvent(event) {
  for (const fn of listeners) fn(event)
}
provide('onRealtimeEvent', (fn) => {
  listeners.add(fn)
  return () => listeners.delete(fn)
})

const { connected, latencyMs, subscribe, unsubscribe } = useRealtime(
  onRealtimeEvent,
  { enabled: realtimeEnabled, identity: realtimeIdentity },
)
provide('wsSubscribe', subscribe)
provide('wsUnsubscribe', unsubscribe)
provide('wsConnected', connected)
provide('wsLatencyMs', latencyMs)

// 档口名单只有后台那几个页面用（数据管理的分类弹窗）。原来是"挂载那一刻不是独立页就拉
// 一次"——从排班/员工端这类**独立页**进后台、或者直接以独立页为首页（PWA 快捷方式）打开，
// 那一次就永远不补，弹窗里档口是空的。改成盯着路由：第一次走到非独立页就拉一次
// （`load()` 自己按 `loaded` 去重，重复调用不发请求）。
const stationsStore = useStationsStore()
watch(
  isStandalone,
  (standalone) => {
    if (!standalone) stationsStore.load()
  },
  { immediate: true },
)
</script>

<template>
  <div class="app-shell">
    <NavBar v-if="!isStandalone" :connected="connected" :latency-ms="latencyMs" />
    <div
      class="page-body luyun-scrollbar"
      :class="{
        'page-body-standalone': isStandalone || isHygieneAdmin,
        'page-body-hygiene': isHygieneAdmin,
      }"
    >
      <router-view />
    </div>
    <PwaUpdateBanner
      :visible="pwaUpdate.visible.value"
      :busy="pwaUpdate.applying.value"
      :error="pwaUpdate.error.value"
      @apply="pwaUpdate.apply"
      @dismiss="pwaUpdate.dismiss"
    />
    <ImageUploadQueuePanel />
  </div>
</template>

<style scoped>
/* 登录 / 配置页自带全屏背景与内边距，去掉主壳给 .page-body 加的外边距，避免双重滚动条。 */
.page-body-standalone {
  padding: 0;
  overflow-y: auto;
}

.page-body-hygiene {
  overflow: hidden;
}
</style>
