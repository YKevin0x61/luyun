<script setup>
/**
 * 厨房采购的模块 tab（下单 / 采购单 / 汇总 / 目录 / 分析 / 迁移）。
 *
 * 只放「这一组里怎么走」；能不能打开由路由守卫与服务端判，组件不重复判权限。
 * 目录 / 分析 / 迁移只给管理端：员工会话下不渲染这三个入口（他们也没有那几页的权限）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import {
  PURCHASE_ANALYSIS_PATH,
  PURCHASE_CATALOG_PATH,
  PURCHASE_HOME_PATH,
  PURCHASE_MIGRATION_PATH,
  PURCHASE_ORDERS_PATH,
} from '../../utils/purchasePaths'

const props = defineProps({
  active: { type: String, default: 'order' },
})

const identity = useWorkbenchIdentityStore()
const isAdmin = computed(() => Boolean(identity.canUseAdmin))
const tabsEl = ref(null)
let revealObserver = null

// 手机上六个 tab 用短标签等宽排一行；只有屏幕更窄或放大字号时才横向滚。
// 切到靠后的页时把当前 tab 滚到中间，否则用户只看到半截「采购」两个字。
// 只滚导航本身，不带着页面动。导航可能先以 0 宽挂载（身份探针还没回来），
// 所以等它第一次有宽度再滚。
function revealActive() {
  const nav = tabsEl.value
  if (!nav || !nav.clientWidth) return false
  const el = nav.querySelector('.pn-tab.is-active')
  if (!el) return false
  const left = el.offsetLeft - (nav.clientWidth - el.offsetWidth) / 2
  nav.scrollTo({ left: Math.max(0, left), behavior: 'auto' })
  return true
}

function scheduleReveal() {
  nextTick(() => {
    if (revealActive()) return
    if (!tabsEl.value || typeof ResizeObserver === 'undefined') return
    if (revealObserver) revealObserver.disconnect()
    revealObserver = new ResizeObserver(() => {
      if (!revealActive() || !revealObserver) return
      revealObserver.disconnect()
      revealObserver = null
    })
    revealObserver.observe(tabsEl.value)
  })
}

onMounted(scheduleReveal)
watch(() => props.active, scheduleReveal)
onBeforeUnmount(() => {
  if (revealObserver) revealObserver.disconnect()
  revealObserver = null
})

const tabs = computed(() => {
  const rows = [
    { key: 'order', label: '下单', short: '下单', to: PURCHASE_HOME_PATH },
    { key: 'orders', label: '采购单', short: '采购单', to: PURCHASE_ORDERS_PATH },
    { key: 'summary', label: '今日汇总', short: '汇总', to: PURCHASE_ORDERS_PATH + '?tab=summary' },
  ]
  if (isAdmin.value) {
    rows.push({ key: 'catalog', label: '商品与供应商', short: '商品', to: PURCHASE_CATALOG_PATH })
    rows.push({ key: 'analysis', label: '采购分析', short: '分析', to: PURCHASE_ANALYSIS_PATH })
    rows.push({ key: 'migration', label: '数据迁移', short: '迁移', to: PURCHASE_MIGRATION_PATH })
  }
  return rows
})
watch(tabs, scheduleReveal)
</script>

<template>
  <nav ref="tabsEl" class="pn-tabs" aria-label="厨房采购">
    <router-link
      v-for="tab in tabs"
      :key="tab.key"
      :to="tab.to"
      class="pn-tab"
      :class="{ 'is-active': props.active === tab.key }"
      :aria-current="props.active === tab.key ? 'page' : undefined"
      :aria-label="tab.label"
      :title="tab.label"
    >
      <span class="pn-label-full">{{ tab.label }}</span>
      <span class="pn-label-short">{{ tab.short }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
.pn-tabs {
  display: flex;
  gap: 4px;
  width: fit-content;
  max-width: 100%;
  margin: 0 0 12px;
  padding: 4px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.pn-tabs::-webkit-scrollbar {
  display: none;
}
.pn-tab {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 7px 13px;
  border-radius: 9px;
  color: var(--ink);
  font-size: var(--fs-14);
  font-weight: 600;
  line-height: 1.3;
  text-decoration: none;
  white-space: nowrap;
}
.pn-tab.is-active {
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.pn-tab:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.pn-label-short {
  display: none;
}
@media (max-width: 560px) {
  .pn-tabs {
    width: 100%;
    gap: 0;
    margin-bottom: 8px;
    padding: 2px;
  }
  .pn-tab {
    flex: 1 1 0;
    min-width: 0;
    padding: 0 1px;
    font-size: var(--fs-14);
    font-weight: 700;
    text-align: center;
  }
  .pn-tab.is-active {
    font-weight: 700;
  }
  .pn-label-full {
    display: none;
  }
  .pn-label-short {
    display: inline;
  }
}
</style>
