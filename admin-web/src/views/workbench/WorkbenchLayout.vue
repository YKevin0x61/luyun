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
import { pageRow, pageTitle } from '../../router/pageRoutes.js'
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

/** 按**格**把条目分好组（一处渲染）：一格里的条目共享一个 key，键序仍是组表里的顺序。
 *
 *  标题的取法跟着"这一格有几扇门"走：
 *  - **一格一门**（今天 / 人事 / 现场 / 我的）—— 用组表里的 `label`，与票 07 之前一字不差；
 *  - **一格多门**（票 08 的后勤：配方 + 备货计划）—— 每一半用它**自己那一页在清单里的名字**
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
      <!-- 回管理后台的门（spec 故事 11：两边各留一个入口、双向）。人事 / 现场两个壳各自
           也有一个「‹ 后台」，这里补上工作台首页、后勤与「我的」这几个页面的那一扇 ——
           少了它，店长站在子应用首页回不去后台。 -->
      <router-link class="wb-back" to="/">‹ 后台</router-link>
      <b class="wb-brand">{{ WORKBENCH_TITLE }}</b>
      <WorkbenchIdentitySwitcher class="wb-id-switcher" />
      <nav class="wb-nav" aria-label="工作台导航">
        <!-- 一格一颗胶囊（票 03 的规矩）：单条目那一格就是一个链接；多条目那一格
             （票 08 的后勤）是同一颗里的两条链接，`is-multi` 只收窄内边距。整格是否
             当前由 `is-on` 判（按页面清单的 group），哪一半贴着"当前"由 `is-current`
             判（这一格 + 这条链接自己的路径）—— 两个类都挂在链接上，样式里按类取值。 -->
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
  /* 高度是**下限**不是定值（票 12 收的 O3）：原来是 `height: 40px` 的单行 flex，
     390px 手机上胶囊被压到内容宽度以下、汉字逐字换行，整条栏溢出后被裁掉 ——
     退出按钮当场看不见（实测 clientWidth 390 / scrollWidth 442、高度 39/62）。 */
  min-height: 40px; padding: 4px 12px;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
.wb-brand {
  /* 牌子占掉剩下的宽度：切换器与导航一起贴在右端（原来靠 `.wb-nav` 的 auto margin，
     中间插了切换器之后那点间距就不够看了）。窄屏放不下时它先让位（见下面的媒体查询）。 */
  flex: 1 1 auto; min-width: 0;
  font-family: var(--font-song); font-size: 14px;
  letter-spacing: .12em; color: var(--hy-ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* 与人事壳的 `.sched-back` 同一身：一颗描边小胶囊，别抢牌子的视线。 */
.wb-back {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 12px; color: var(--hy-muted); text-decoration: none;
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px;
  white-space: nowrap;
}
.wb-back:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
/* 切换器与导航之间一条细分隔：两件事（我是谁 / 去哪一页），别挤成一团。 */
.wb-id-switcher { margin-left: 4px; padding-right: 10px; border-right: 1px solid var(--hy-line); }
.wb-nav { display: flex; align-items: center; gap: 6px; margin-left: auto; min-width: 0; }
.wb-exit-btn { margin-left: 8px; }
.wb-nav-item {
  font-size: 12px; color: var(--hy-muted); text-decoration: none;
  border: 1px solid var(--hy-line); background: var(--hy-surface-2);
  border-radius: 999px; padding: 4px 12px; white-space: nowrap;
}
.wb-nav-item:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
/* 高亮按「格」算，不按链接自己的路径（见 `workbenchGroupOf`）。
   一格里有多扇门时（票 08 的后勤：配方 + 备货计划）两颗链接合成**一颗胶囊**：
   边框与底色挂在外层，里面那两条只留文字（否则两颗胶囊并排，看着像两个分组）。
   **整格是否当前**由 `is-on` 判（哪一格亮），**哪一半贴着"当前"**由 `is-current` 判
   —— 一格两门时两半都算"这一格"（`.wb-nav-cell.is-on` 一起点着），只有指到这一页的
   那半加粗着色。 */
.wb-nav-cell {
  display: inline-flex; align-items: stretch;
  border: 1px solid var(--hy-line); background: var(--hy-surface-2); border-radius: 999px;
}
.wb-nav-cell > .wb-nav-item { border: none; background: none; padding: 4px 12px; }
.wb-nav-cell.is-multi > .wb-nav-item + .wb-nav-item { border-left: 1px solid var(--hy-line); }
.wb-nav-cell.is-multi > .wb-nav-item { padding: 4px 10px; }
.wb-nav-item.is-on { color: var(--hy-mint); }
.wb-nav-cell.is-on { border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.wb-nav-item.is-current { color: var(--hy-ink); font-weight: 600; }
.wb-nav-item.is-current.is-on { color: var(--hy-mint); }
.wb-main { display: flex; flex-direction: column; flex: 1; min-height: 0; }

/* 窄屏（票 12 收的 O3）：顶栏换行 —— 身份切换器与退出是这一条里最不该被挤出屏幕的两件
   （切换器自己 `flex: 0 0 auto`，见 `WorkbenchIdentitySwitcher.vue`），所以本组导航整条
   另起一行、自己横向滑。与人事壳 `.sched-top` 同一套路。 */
@media (max-width: 720px) {
  .wb-top { flex-wrap: wrap; row-gap: 6px; }
  .wb-nav { order: 1; flex-basis: 100%; margin-left: 0; overflow-x: auto; }
}
@media (max-width: 560px) {
  /* 牌子让位给切换器与两扇门（人事壳同一条：`.sched-name` 在 560px 收起）：
     子系统名字在页面标题与工作台导航里都还在。 */
  .wb-brand { display: none; }
}
</style>
