<script setup>
// 工作台外壳（票 03：第一次以员工视角渲染；票 04：顶栏加上身份切换器）。
//
// 它只做一件事：顶上一条工作台自己的窄栏（牌子 + 身份切换器 + 按身份过滤的导航），页面
// 本体挂在 `<router-view />` 里 —— 页内结构、五个 tab、各自的顶栏都还是原来的样子（员工端
// 「保持现有信息架构」是这次改造的硬约束）。后台那条九个模块的导航不在这里渲染
// （这些页在页面清单里是 `standalone`，跟 `SchedulingLayout` / `HygieneAdminLayout`
// 同一个路子）。
//
// 导航**跟着此刻的身份走**（不再跟着"这一页是谁的"）：身份读 `stores/workbenchIdentity`
// （票 04），切档时这一条栏与导航面一起变。切换**只改视图与导航面** —— 页面能不能打开
// 仍是路由守卫与服务端页面墙的事，这里一个字都不碰。
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { WORKBENCH_TITLE } from '../../utils/workbenchCopy'
import { workbenchAudienceFor, workbenchGroupOf, workbenchNavFor } from '../../utils/workbenchNav'

// 深青墨令牌跟工作台其他两组同一张表（`/hygiene-admin.css`）。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()
const identityStore = useWorkbenchIdentityStore()

// 探针由切换器自己开场（`onMounted`）。这里只在**已经探出结论**时消费它：没探完时
// `identity` 是 null，导航不渲染 —— 不给一个可能点不通的入口。
const identity = computed(() => identityStore.identity)
const navItems = computed(() => workbenchNavFor(identity.value))
const currentGroup = computed(() => workbenchGroupOf(route.path))

/** 此刻这一页还属于当前身份吗（判据是页面清单里那一行的 `audience`，不猜路径前缀）。
 *
 *  身份还没探出来（`null`）时一律算「属于」：那时候还不知道是哪一档，不该先把人弹走
 *  —— 真正需要送回的是**切档之后**站在别人的页上那一下。清单里没有的路径（单测里那种
 *  临时路由）同理，按「属于」算。 */
function viewAllowedHere() {
  if (identity.value === null) return true
  const row = pageRow(route.path)
  if (!row) return true
  const audience = workbenchAudienceFor(identity.value)
  return row.audience === 'both' || row.audience === audience
}

/** 切档之后人还站在别人的页上时，把他送回**这一档**能看的最近一页；导航里那几格就是
 *  落点（票 05 把人事 / 现场并进来之前，店长这一档还没有可去的格，于是什么也不做 ——
 *  这是当前中间态，不是漏判）。**不由这里改权限**：守卫怎么判还是怎么判，这条只负责
 *  别让人停在一个"不是你现在这档看的"页面上。 */
function keepViewAllowed() {
  if (viewAllowedHere()) return
  const fallback = navItems.value[0]
  if (fallback) void router.replace(fallback.to)
}

onMounted(keepViewAllowed)
watch(identity, keepViewAllowed)
</script>

<template>
  <div class="hygiene-admin wb-shell">
    <a class="hy-skip" href="#workbench-main">跳到内容</a>

    <header class="wb-top">
      <b class="wb-brand">{{ WORKBENCH_TITLE }}</b>
      <WorkbenchIdentitySwitcher class="wb-id-switcher" />
      <nav class="wb-nav" aria-label="工作台导航">
        <router-link
          v-for="item in navItems"
          :key="item.key"
          class="wb-nav-item"
          :class="{ 'is-on': item.key === currentGroup }"
          :aria-current="item.key === currentGroup ? 'page' : undefined"
          :to="item.to"
        >{{ item.label }}</router-link>
      </nav>
      <!-- 退出入口（票 06）：员工那三页原来自带一颗（`StaffExitButton`），首页与切档之后
           的店长视角原先没有 —— 工作台是子应用，页页都得退得出去。行为只有一处
           （`composables/useWorkbenchLogout.js`），票 10 会把四处登出收敛成一条。 -->
      <WorkbenchExitButton class="wb-exit-btn" />
    </header>

    <main id="workbench-main" class="wb-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
/* 顶栏粘住：这一页的滚动容器是 `.app-shell` 的 `.page-body`（standalone 时它是
   `overflow-y: auto` 且没有内边距），所以 sticky 挂在这里正好压在内容上方。 */
.wb-top {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 10px;
  height: 40px; padding: 0 12px;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
.wb-brand {
  /* 牌子占掉剩下的宽度：切换器与导航一起贴在右端（原来靠 `.wb-nav` 的 auto margin，
     中间插了切换器之后那点间距就不够看了）。 */
  flex: 1; min-width: 0;
  font-family: var(--font-song); font-size: 14px;
  letter-spacing: .12em; color: var(--hy-ink);
}
/* 切换器与导航之间一条细分隔：两件事（我是谁 / 去哪一页），别挤成一团。 */
.wb-id-switcher { margin-left: 4px; padding-right: 10px; border-right: 1px solid var(--hy-line); }
.wb-nav { display: flex; align-items: center; gap: 6px; margin-left: auto; }
.wb-exit-btn { margin-left: 8px; }
.wb-nav-item {
  font-size: 12px; color: var(--hy-muted); text-decoration: none;
  border: 1px solid var(--hy-line); background: var(--hy-surface-2);
  border-radius: 999px; padding: 4px 12px;
}
.wb-nav-item:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
/* 高亮按「组」算，不按链接自己的路径（见 `workbenchGroupOf`）。 */
.wb-nav-item.is-on {
  color: var(--hy-mint); border-color: var(--hy-mint-line); background: var(--hy-mint-soft);
}
.wb-main { display: flex; flex-direction: column; flex: 1; min-height: 0; }
</style>
