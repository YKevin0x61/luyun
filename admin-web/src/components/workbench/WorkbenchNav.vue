<script setup>
// 工作台**级**导航（今天 / 人事 / 现场 / 后勤 / 我的）：从一个壳里抽出来，三个壳共用。
//
// 为什么会有这个文件（B1 那一条的结构性来龙去脉）：工作台现在是「一个子系统、三套壳」——
//   - `/workbench`、`/workbench/me/*`、`/workbench/kitchen/*` 套 `WorkbenchLayout`（一条顶栏）；
//   - `/workbench/hr/*` 套 `SchedulingLayout`（人事自己的窄栏）；
//   - `/workbench/floor/*` 套 `HygieneAdminLayout`（左侧 238px rail + 内容区横条）。
// 三套壳各有各的导航，于是**人事 / 现场那 11 页里根本没有一条通往工作台首页「今天」的链接**
// （两组的 href 全集里都不含 `/workbench`，连写着「工作台」的牌子都指向本组首页）。
//
// 这次不把三组硬并进同一个 Layout（那是大改结构，风险与收益不成比例），走的是**小步**：
// 把工作台级那一排导航抽成这一颗组件，让三个壳都渲染同一份 ——
//   - 表还是那唯一一张（`utils/workbenchNav.js`，一格一组、按身份过滤）；
//   - 高亮还是那一个判据（页面的 `group`，不按路径前缀比）；
//   - 布局（要不要换行、横滑、靠哪边）留给各自的壳：组件自己只出**一条 nav 与它的胶囊**，
//     不带 `margin-left: auto` 这类"我在顶栏最右"的假设（那是 `WorkbenchLayout` 的事）。
// 于是三套壳的差异收敛成"每条栏怎么摆"，而**「今天」在每一页都点得到**（B2）。
//
// 导航跟着**此刻的身份**走（`stores/workbenchIdentity`），不跟着"这一页是谁的"：切档时
// 这一条与导航面一起变。切换只改视图与导航面 —— 页面能不能打开仍是路由守卫与服务端
// 页面墙的事，这里一个字都不碰。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { pageTitle } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { workbenchGroupOf, workbenchNavFor } from '../../utils/workbenchNav'

const route = useRoute()
const identityStore = useWorkbenchIdentityStore()

// 身份还没探出来（`null`）时不渲染任何一格：不给一个可能点不通的入口。
// 探针由身份切换器开场（`onMounted`），三个壳里都挂着它。
const identity = computed(() => identityStore.identity)
const navItems = computed(() => workbenchNavFor(identity.value))
const currentGroup = computed(() => workbenchGroupOf(route.path))

/** 按**格**把条目分好组（一处渲染）：一格里的条目共享一个 key，键序仍是组表里的顺序。
 *
 *  标题的取法跟着"这一格有几扇门"走：
 *  - **一格一门**（今天 / 人事 / 现场 / 我的）—— 用组表里的 `label`；
 *  - **一格多门**（后勤：配方 + 备货计划）—— 每一半用它**自己那一页在清单里的名字**
 *    （同一颗胶囊里写两遍「后勤」看不出哪一半是哪一页）。名字从页面清单取（`pageTitle`），
 *    不在这里另写一份词表。 */
const navCells = computed(() => {
  const cells = []
  for (const item of navItems.value) {
    let cell = cells.find((row) => row.key === item.key)
    if (!cell) {
      cell = { key: item.key, links: [] }
      cells.push(cell)
    }
    cell.links.push(item)
  }
  for (const cell of cells) {
    const titled = cell.links.length > 1
    cell.links = cell.links.map((item) => ({
      ...item,
      label: titled ? pageTitle(item.to) : item.label,
    }))
  }
  return cells
})

/** 这一扇门算不算"当前"：得同时是**这一格**（`workbenchGroupOf`，判据是页面的 `group`）
 *  与这一条链接自己指着的路径。同一格里因此只有一扇门亮 —— 后勤那一格两扇门指着两页，
 *  站在备货计划上时亮的是备货计划那一半，「后勤」整格仍然算当前（`is-on`）。 */
function isCurrentLink(cell, item) {
  return cell.key === currentGroup.value && item.to === route.path
}
</script>

<template>
  <nav class="wb-nav" aria-label="工作台导航">
    <!-- 一格一颗胶囊：单条目那一格就是一个链接；多条目那一格（后勤）是同一颗里的两条
         链接，`is-multi` 只收窄内边距。整格是否当前由 `is-on` 判（按页面清单的 group），
         哪一半贴着"当前"由 `is-current` 判（这一格 + 这条链接自己的路径）。 -->
    <span
      v-for="cell in navCells"
      :key="cell.key"
      class="wb-nav-cell"
      :class="{ 'is-on': cell.key === currentGroup, 'is-multi': cell.links.length > 1 }"
    >
      <router-link
        v-for="item in cell.links"
        :key="item.to"
        class="wb-nav-item"
        :class="{ 'is-on': cell.key === currentGroup, 'is-current': isCurrentLink(cell, item) }"
        :aria-current="cell.key === currentGroup ? 'page' : undefined"
        :to="item.to"
      >{{ item.label }}</router-link>
    </span>
  </nav>
</template>

<style scoped>
/* 只出**这一条导航与它的格子**，不碰外层摆放（那是各壳自己的事，见文件头的注释）。
 *
 * 形状：**下划线 tab，不是胶囊**。原来一格一颗圆角胶囊、一屏最多 8 颗（身份两档 + 退出 +
 * 五格导航），形状语言重复又抢眼，看着像一排药丸；而导航本该靠**位置与指示线**说"我在哪"，
 * 不靠每一格都描边填底。改法：文字态，当前格在**格**上加一条 2.5px 的薄荷下划线 ——
 * 一格有多扇门时（后勤）也只画一条，不会出现两条并排的线。 */
.wb-nav { display: flex; align-items: stretch; gap: 2px; min-width: 0; }
.wb-nav-cell {
  position: relative; display: inline-flex; align-items: stretch;
  /* 下划线挂在格上（不是链接上）：`.is-multi`（后勤两扇门）因此只有一条线。 */
}
.wb-nav-item {
  display: inline-flex; align-items: center;
  font-size: 13.5px; color: var(--hy-muted); text-decoration: none;
  padding: 0 13px; white-space: nowrap;
  border: 0; background: none;
  min-height: 38px;
}
.wb-nav-item:hover { color: var(--hy-ink); }
/* 当前格：文字提亮 + 底部一条指示线（贴在这一条导航的下沿 —— 各壳把它放在顶栏里时，
   这条线就落在顶栏的分隔线上）。 */
.wb-nav-cell.is-on::after {
  content: ""; position: absolute; left: 11px; right: 11px; bottom: 0;
  height: 2.5px; border-radius: 2.5px 2.5px 0 0; background: var(--hy-mint);
}
.wb-nav-cell.is-on .wb-nav-item { color: var(--hy-mint); font-weight: 700; }
/* 一格里的第二条门（后勤：配方 + 备货计划）：用一条内阴影分隔，不再各画一颗胶囊。 */
.wb-nav-cell.is-multi > .wb-nav-item + .wb-nav-item { box-shadow: inset 1px 0 0 var(--hy-line); }
/* 「哪一半贴着当前」：整格已亮，这一半再加一档字重（站在备货计划上时，亮的仍只有它）。 */
.wb-nav-item.is-current { color: var(--hy-ink); font-weight: 600; }
.wb-nav-item.is-current.is-on { color: var(--hy-mint); }

/* 窄屏（B6）：手机上这一排是**点得最多**的东西，抬到 44px 的触控下限。
   高度由 `min-height` 给（不是定高），汉字换行时不会被裁。 */
@media (max-width: 720px) {
  .wb-nav-item { min-height: 44px; padding: 0 15px; }
}
</style>
