<script setup>
import { computed, onMounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'
import NavBar from './components/NavBar.vue'
import { useRealtime } from './composables/useRealtime'
import { useStationsStore } from './stores/stations'
import { isHygieneAdminPath } from './utils/hygieneCopy'

const route = useRoute()
// 登录 / 配置页是独立全屏页，不显示主导航壳（见 router meta.standalone）。
const isStandalone = computed(() => !!route.meta.standalone)
const isHygieneAdmin = computed(() => isHygieneAdminPath(route.path))
const realtimeEnabled = computed(() => route.meta.realtime === true || !route.meta.public)

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
  { enabled: realtimeEnabled },
)
provide('wsSubscribe', subscribe)
provide('wsUnsubscribe', unsubscribe)
provide('wsConnected', connected)
provide('wsLatencyMs', latencyMs)

const stationsStore = useStationsStore()
onMounted(() => {
  if (!isStandalone.value) stationsStore.load()
})
</script>

<template>
  <div class="app-shell">
    <NavBar v-if="!isStandalone" :connected="connected" :latency-ms="latencyMs" />
    <div class="page-body luyun-scrollbar" :class="{ 'page-body-standalone': isStandalone || isHygieneAdmin }">
      <router-view />
    </div>
  </div>
</template>

<style scoped>
/* 登录 / 配置页自带全屏背景与内边距，去掉主壳给 .page-body 加的外边距，避免双重滚动条。 */
.page-body-standalone {
  padding: 0;
  overflow: hidden;
}
</style>
