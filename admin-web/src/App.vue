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
import { applyPwaManifest, selectPwaManifest } from './utils/pwaManifest'

const route = useRoute()
// 页面属于哪个 App（工作台 / 管理端）：清单、图标、主题色与 Service Worker 四处都从
// 这一条判据来（`utils/pwaManifest.js` 的 `selectPwaManifest`）。票 09 起只看路径。
const pwaApp = computed(() => selectPwaManifest(route.path))
// 注册的是**当前 App** 那份 worker：管理面页面注册全站那份（scope `/`），工作台页面注册
// `/workbench/sw.js`（scope `/workbench`）。客户端路由跨过界时会补注册新的那份
// （`usePwaUpdate` 自己盯这条判据）—— 否则从管理面走进工作台会一直挂在根 worker 上，
// 根 worker 的导航回退就把工作台的导航吃掉了。
const pwaUpdate = usePwaUpdate(pwaApp)
// 登录 / 配置页是独立全屏页，不显示主导航壳（见 router meta.standalone）。
const isStandalone = computed(() => !!route.meta.standalone)
const isHygieneAdmin = computed(() => isHygieneAdminPath(route.path))
const realtimeEnabled = computed(() => route.meta.realtime === true || !route.meta.public)
// 员工端页面的实时连接显式声明自己是员工（票 08）：同一浏览器可能同时持管理端与
// 员工端两套 cookie，服务端默认「管理端优先」，不声明就会被判成管理端身份——主题
// 白名单全开、员工之间「只看得到与自己有关的那条」的隔离失效。判据用页面清单里的
// 「允许的身份」三态（与路由守卫同一个来源），不另写一份路径正则；管理端页面不带声明，
// 默认优先级本来就对。声明写在连接 URL 上，路由换了身份就换连接（composable 负责重连）。
const realtimeIdentity = computed(() => (route.meta.audience === 'staff' ? 'staff' : null))

// 清单归属跟着路由换（工作台前缀 ↔ 管理面）：换 `<link rel=manifest>`、apple-touch-icon
// 与 theme-color 三处。票 09 起判据只看路径 —— `/login` 不再随面板栏位换清单。
watch(() => route.path, () => applyPwaManifest(route.path), { immediate: true })

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
