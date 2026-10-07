<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SvgIcon from './SvgIcon.vue'
import { logoutAdminSession } from '../utils/adminLogout'
import { createScrollHints } from '../utils/scrollHints'
import { WORKBENCH_TITLE } from '../utils/workbenchCopy'

defineProps({
  connected: { type: Boolean, default: false },
  latencyMs: { type: Number, default: null },
})

const route = useRoute()
const router = useRouter()

// 简单映射，对齐旧原生页各自的 data-subtitle（如 public/logs.html:130）
const PAGE_SUBTITLES = [
  // 工作台（2026-10-04：排班与卫生合并成一个子系统，票 04 两组同住 `/workbench/*`）。
  // 票 07 / 08 起配方与备货计划也在这一条前缀底下（后勤组），所以这里不再各留一条。
  { prefix: '/workbench', subtitle: WORKBENCH_TITLE },
  { prefix: '/logs', subtitle: '日志中心' },
  { prefix: '/wecom-push', subtitle: '企微推送' },
  { prefix: '/sales-report', subtitle: '销售报表' },
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
  // 客户端登出（票 10）：不再整页重载、不再丢原目标。实现只有一处
  // （`utils/adminLogout.js`）—— 配置页、备份中心、工作台与配方阅读面的退出按钮
  // 走的都是它。误点的保护留在这里（浏览器原生 confirm），不是登出实现的事。
  await logoutAdminSession(router, route.fullPath)
}

// tab 条在 390 下只有 374px 可用，「日志」那一格（382–432px）整个在视口外。
// 光加滚动还不够：容器藏了滚动条，用户看不出右边还有内容；而且切到 `/logs` 时
// 容器 `scrollLeft` 仍是 0——**在日志页自己也看不出自己在哪**。所以两件事都做：
// 把 active 项滚进视野 + 两端溢出渐隐（见 utils/scrollHints.js）。
const tabs = createScrollHints()
// 模板里 `ref="tabsScroller"` 是字符串 ref，靠 setup 作用域里的同名变量绑定；
// `createScrollHints` 返回的是 `scrollerRef`，这里换名接上。
const tabsScroller = tabs.scrollerRef

// 路由换了才滚：同一页内的实时状态更新不该让 tab 条自己动。
// `flush: 'post'` 不能省：默认的 flush 跑在 DOM 更新**之前**，那一刻 `.active` 还挂在
// 上一页那一格上，滚过去的会是上一页（实测：从 `/admin` 进 `/logs`，滚的是「数据管理」）。
watch(
  () => route.path,
  () => tabs.scrollActiveIntoView(),
  { flush: 'post' },
)

/** 首屏补滚的最后一拍：连接状态 / 延迟文本等异步内容落进 `.nav-right` 之后。 */
const FIRST_PAINT_RESCROLL_MS = 600

/**
 * 首屏补滚当前项（UI 走查 U17：834 下「企微推送」被导航条右边缘裁掉，用户看不出自己
 * 在哪一页，390 与 1440 都正常）。
 *
 * 根因是**列宽在 onMounted 那一刻还没定**：`.nav-right` 里的实时时钟刚被 `tickClock()`
 * 填上（DOM 要等下一个微任务）、「实时已连接 / 延迟 30ms」还要等 WS 回来，中间那列因此
 * 比最终宽 —— 834 实测「内容 392 / 可视 ~401」，并不溢出，`scrollIntoView` 于是**什么都
 * 不做**；等这些文字落进 DOM，列被压到 301，导航条才真的溢出，而这一刻已经没有人再去滚
 * 它。390 那一档不受影响（标签行独占一整行，从第一帧起就溢出），所以只有 834 看得见。
 *
 * 补滚的每一拍都是**幂等**的：不溢出时 `scrollIntoView` 自己不做任何事。只在首屏这一段
 * 短窗口里跑，用户手动滚到别的格子之后不会再被拉回来。
 */
function scrollActiveIntoViewOnFirstPaint() {
  const run = () => tabs.scrollActiveIntoView('auto')
  const raf = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : (callback) => setTimeout(callback, 16)
  // 双 rAF：等这一帧的布局落地（Vue 的 DOM 更新在微任务里，早于下一帧）。
  raf(() => raf(run))
  // 连接状态与延迟文本要等 WS 回来才出现，它们同样会改列宽。
  setTimeout(run, FIRST_PAINT_RESCROLL_MS)
  // 字体换掉会改每一格的宽度（系统字体同步，WebView 里的字体可能异步就绪）。
  if (document.fonts && typeof document.fonts.ready?.then === 'function') {
    document.fonts.ready.then(run).catch(() => {})
  }
}

onMounted(() => {
  // 首屏（可能直接落在 `/logs`）用 auto：进场动画期间做平滑滚动只会看到条在抖。
  tabs.scrollActiveIntoView('auto')
  // 首屏这一次很可能**什么都没做**（那时列宽还没定），见上面这个函数的注释。
  scrollActiveIntoViewOnFirstPaint()
})
</script>

<template>
  <div class="global-nav">
    <div class="global-nav-brand">厨务管家<span v-if="pageSubtitle" class="global-nav-subtitle"> · {{ pageSubtitle }}</span></div>
    <div
      class="global-nav-tabs-wrap"
      :class="{ 'is-scroll-start': tabs.atStart.value, 'is-scroll-end': tabs.atEnd.value }"
    >
      <div ref="tabsScroller" class="global-nav-tabs">
        <router-link to="/" class="nav-tab" :class="{ active: route.path === '/' }">仪表盘</router-link>
        <router-link to="/admin" class="nav-tab" :class="{ active: route.path.startsWith('/admin') }">数据管理</router-link>
        <!-- 排班与卫生合成一个子系统「工作台」（2026-10-04）：一格进排班月历，卫生那一组
             从工作台窄栏里的「现场」进 —— 这里不再并排两格。高亮覆盖两组。 -->
        <router-link
          to="/workbench"
          class="nav-tab"
          :class="{ active: route.path.startsWith('/workbench') }"
        >{{ WORKBENCH_TITLE }}</router-link>
        <router-link to="/sales-report" class="nav-tab" :class="{ active: route.path.startsWith('/sales-report') }">销售报表</router-link>
        <!-- 票 07（spec 故事 12）：配方那一格撤掉 —— 它已经在工作台的「后勤」组里，
             从上面「工作台」那一格进。管理后台的导航为它单列一格只会让人以为有两个配方域。
             票 08 同理：备货计划那一格也撤掉（后勤组里，与配方同住一格）。 -->
        <router-link to="/wecom-push" class="nav-tab" :class="{ active: route.path.startsWith('/wecom-push') }">企微推送</router-link>
        <router-link to="/logs" class="nav-tab" :class="{ active: route.path.startsWith('/logs') }">日志</router-link>
      </div>
    </div>
    <div class="nav-right">
      <span class="nav-status-dot" :class="connected ? 'online' : 'offline'" :title="connected ? '实时已连接' : '实时已断开'"></span>
      <span class="nav-conn-label">{{ connected ? '实时已连接' : '实时已断开' }}</span>
      <span v-if="connected && latencyMs != null" class="nav-latency">延迟 {{ latencyMs }}ms</span>
      <span class="nav-clock">{{ clockText }}</span>
      <router-link
        to="/settings"
        class="nav-setup-btn"
        :class="{ active: route.path.startsWith('/settings') }"
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

<style scoped>
/* 溢出提示（A5）：tab 条本身是 theme.css 里的 `.global-nav-tabs`（滚动条被显式藏掉），
   这里只加一层定位用的壳，用 :deep 去够里面那条，不改它的既有布局。遮罩只在真的还
   有内容时出现（is-scroll-start / is-scroll-end 由 utils/scrollHints.js 算），
   否则文字会被无谓地淡掉一截。 */
.global-nav-tabs-wrap {
  position: relative;
  min-width: 0;
}

.global-nav-tabs-wrap :deep(.global-nav-tabs) {
  /* 让 scrollActiveIntoView 居中后不会把当前项顶到遮罩底下 */
  scroll-padding-inline: 24px;
}

.global-nav-tabs-wrap::before,
.global-nav-tabs-wrap::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 22px;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
}

.global-nav-tabs-wrap::before {
  left: 0;
  background: linear-gradient(to right, var(--sidebar-bg), transparent);
}

.global-nav-tabs-wrap::after {
  right: 0;
  background: linear-gradient(to left, var(--sidebar-bg), transparent);
}

/* 右边还有内容 → 右端渐隐；已经滚过去了 → 左端也渐隐。 */
.global-nav-tabs-wrap:not(.is-scroll-end)::after { opacity: 1; }
.global-nav-tabs-wrap:not(.is-scroll-start)::before { opacity: 1; }

/* 不随系统动效偏好做平滑滚动：滚动是位移，不是动画。 */
@media (prefers-reduced-motion: reduce) {
  .global-nav-tabs-wrap :deep(.global-nav-tabs) { scroll-behavior: auto; }
}
</style>
