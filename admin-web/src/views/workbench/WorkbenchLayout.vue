<script setup>
// 工作台外壳（票 03：第一次以员工视角渲染）。
//
// 它只做一件事：顶上一条工作台自己的窄栏（牌子 + 按身份过滤的导航），页面本体挂在
// `<router-view />` 里 —— 页内结构、五个 tab、各自的顶栏都还是原来的样子（员工端
// 「保持现有信息架构」是这次改造的硬约束）。后台那条九个模块的导航不在这里渲染
// （这些页在页面清单里是 `standalone`，跟 `SchedulingLayout` / `HygieneAdminLayout`
// 同一个路子）。
//
// 导航**一组一格**（`utils/workbenchNav.js`）：员工这一档现在只有「我的」一格，
// 配方与备货计划在票 07 / 08 并入时加进去，这里不用改。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { WORKBENCH_TITLE } from '../../utils/workbenchCopy'
import { workbenchGroupOf, workbenchNavFor } from '../../utils/workbenchNav'

// 深青墨令牌跟工作台其他两组同一张表（`/hygiene-admin.css`）。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()

// 票 04 的「工作台身份」（顶栏切换器、记忆、只降不升）落地之前，这里就按**这一页**的身份
// 算：票 03 挂在这个外壳下的页都是员工页（清单里 `audience: 'staff'`）。票 04 把这一行换成
// 读那份身份即可 —— 导航过滤（`workbenchNavFor`）与模板都不动。
const identity = computed(() => (route.meta.audience === 'admin' ? 'admin' : 'staff'))
const navItems = computed(() => workbenchNavFor(identity.value))
const currentGroup = computed(() => workbenchGroupOf(route.path))
</script>

<template>
  <div class="hygiene-admin wb-shell">
    <a class="hy-skip" href="#workbench-main">跳到内容</a>

    <header class="wb-top">
      <b class="wb-brand">{{ WORKBENCH_TITLE }}</b>
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
  font-family: var(--font-song); font-size: 14px;
  letter-spacing: .12em; color: var(--hy-ink);
}
.wb-nav { display: flex; align-items: center; gap: 6px; margin-left: auto; }
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
