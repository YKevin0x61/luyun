<script setup>
// 工作台外壳（票 03：第一次以员工视角渲染；票 04：顶栏加上身份切换器）。
//
// 它只做一件事：顶上一条工作台自己的窄栏（牌子 + 身份切换器 + 按身份过滤的导航），页面
// 本体挂在 `<router-view />` 里 —— 页内结构、五个 tab、各自的顶栏都还是原来的样子（员工端
// 「保持现有信息架构」是这次改造的硬约束）。后台那条九个模块的导航不在这里渲染
// （这些页在页面清单里是 `standalone`，跟 `SchedulingLayout` / `HygieneAdminLayout`
// 同一个路子）。
//
// **方案 C（2026-10-08 用户裁定）**：手机档这一条收进 `WorkbenchMobileHead` 的一行
// （`[组名] [当前页 ▾] … [● 身份] [⋮]`）。这一壳装着三组（今天 / 产品 / 我的），页头里的
// 组名与页面下拉跟着**当前路径属于哪一组**走（`workbenchGroupOf`），所以三处共用同一颗
// 组件、不用各配一份。后勤（产品）组原来更夸张：配方页自己还带着一条 133px 的白色工具栏
// （`header.site-header`，手机档在 `recipe.css` 里收掉），加上这一条就是 182px 的「两条
// 顶栏」。**桌面档一个字没动** —— 那里空间宽裕，顶栏那条 tab 更好用。
//
// 导航**跟着此刻的身份走**（不再跟着"这一页是谁的"）：身份读 `stores/workbenchIdentity`
// （票 04），切档时这一条栏与导航面一起变。切换**只改视图与导航面** —— 页面能不能打开
// 仍是路由守卫与服务端页面墙的事，这里一个字都不碰。
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import WorkbenchMobileHead from '../../components/workbench/WorkbenchMobileHead.vue'
import WorkbenchNav from '../../components/workbench/WorkbenchNav.vue'
import WorkbenchTabBar from '../../components/workbench/WorkbenchTabBar.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { WORKBENCH_TITLE } from '../../utils/workbenchCopy'
import { workbenchAudienceFor, workbenchNavFor } from '../../utils/workbenchNav'
import { IDENTITY_STAFF } from '../../utils/workbenchIdentity'

// 深青墨令牌跟工作台其他两组同一张表（`/hygiene-admin.css`）。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()
const identityStore = useWorkbenchIdentityStore()

/** 自带底部工具栏的页面：配方阅读面那一页有自己的 `.sop-bottom-bar`（搜索 / 目录 / 字号 /
 *  份数 —— 全是阅读动作）。手机档这里**不再叠工作台底栏**：两条 `fixed` 底栏只差 4px，
 *  上面那条（`z-index: 50`）会把工作台那条整个盖住，留着只是白占一次渲染、内容区还要为它
 *  让一遍高度（2026-10-08 用户裁定）。桌面档那条不在视口底，不受影响。 */
const OWN_BOTTOM_BAR_PATHS = ['/workbench/kitchen/recipe/detail']
const hasOwnBottomBar = computed(() => OWN_BOTTOM_BAR_PATHS.includes(route.path))

// 探针由切换器自己开场（`onMounted`）。这里只在**已经探出结论**时消费它：没探完时
// `identity` 是 null，导航不渲染 —— 不给一个可能点不通的入口。
const identity = computed(() => identityStore.identity)
// 导航面（今天 / 人事 / 现场 / 后勤 / 我的）由 `WorkbenchNav` 渲染，这里留一份同样的
// 条目只为了「切档之后把人送回他这一档的第一格」（见 `keepViewAllowed`）。
const navItems = computed(() => workbenchNavFor(identity.value))

/** 员工这一档不渲染「‹ 后台」（D7）：那一扇门通向 `/`（运营仪表盘）—— 店长专属页，
 *  员工点下去只会吃一张「无权访问」。挂着一扇**必然被拒**的门比没有门更糟：
 *  它天天在那里，点一次损失一次。店长那一档照旧（spec 故事 11 的双向入口）。 */
const showBackToAdmin = computed(() => identity.value !== IDENTITY_STAFF)

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

    <!-- 手机档页头（方案 C）：一行装下「组名 / 当前页下拉 / 身份 / 更多」，组里的页从
         那条顶栏收进这个下拉。桌面档它自己不渲染（组件 scoped 样式里 `max-width: 720px`
         那一段）。
         `no-print`：产品组的「配方打印」是 A4 预览页（2026-10-08 起也套这个壳），打印时
         顶栏与底栏都不该被打进去 —— `.no-print` 那条在 `recipe.css` 的 `@media print` 里。 -->
    <WorkbenchMobileHead class="no-print" />

    <header class="wb-top no-print">
      <!-- 回管理后台的门（spec 故事 11：两边各留一个入口、双向）。人事 / 现场两个壳各自
           也有一个「‹ 后台」，这里补上工作台首页、后勤与「我的」这几个页面的那一扇 ——
           少了它，店长站在子应用首页回不去后台。**员工那一档不渲染**（见 `showBackToAdmin`）。 -->
      <router-link v-if="showBackToAdmin" class="wb-back" to="/">‹ 后台</router-link>
      <b class="wb-brand">{{ WORKBENCH_TITLE }}</b>
      <WorkbenchIdentitySwitcher class="wb-id-switcher" />
      <!-- 工作台级导航（今天 / 人事 / 现场 / 后勤 / 我的）：表与高亮都在
           `components/workbench/WorkbenchNav.vue` 里，人事 / 现场两个壳渲染的是同一颗
           （B1 的小步：三套壳共用同一排工作台导航，「今天」因此在每一页都点得到）。 -->
      <WorkbenchNav class="wb-nav" />
      <!-- 退出入口（票 06）：员工那三页原来自带一颗（`StaffExitButton`），首页与切档之后
           的店长视角原先没有 —— 工作台是子应用，页页都得退得出去。行为只有一处
           （`composables/useWorkbenchLogout.js`）。**员工那三页页内那颗已去掉**（D5）：
           同一个动作在同一屏里不该有两颗按钮，外壳这一颗覆盖全部工作台页面。 -->
      <WorkbenchExitButton />
    </header>

    <main id="workbench-main" class="wb-main" :class="{ 'has-own-bar': hasOwnBottomBar }">
      <router-view />
    </main>
    <!-- 工作台级导航在手机档的落点（C 方向）：底栏一格一组、拇指区可达；桌面档它自己
         不渲染（`display: none`），那里用顶栏那条 tab。同一排入口不在两处同时出现。 -->
    <WorkbenchTabBar v-if="!hasOwnBottomBar" class="no-print" />
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
     退出按钮当场看不见（实测 clientWidth 390 / scrollWidth 442、高度 39/62）。
     **底部不留内边距**：导航是下划线 tab，那条指示线要落在这一栏的分隔线上。
     不占满整行高的那几件各自补下边距（见下一条），不会被拉到底。 */
  min-height: 40px; padding: 4px 12px 0;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
/* 不占满整行的那几件：补 4px 下边距，与导航 tab 的文字基线对齐后仍居中；
   导航（下划线 tab）不在此列 —— 它要贴到底边。 */
.wb-back, .wb-brand, .wb-id-switcher { margin-bottom: 4px; }
/* 退出按钮要**:deep() 才选得中** —— 也别在挂它的地方传 class：`WorkbenchExitButton`
   的模板是多根（`<button>` + 那个「还有照片没传完」的 `<ConfirmDialog>`），父级传下去
   的 class 不透传，Vue 还会当场报 `Extraneous non-props attributes`；挂在外面的
   `.wb-exit-btn` 从来没命中过元素，`margin-left: auto`（让它贴右端）一直是死规则。 */
.wb-top :deep(.wb-exit) { margin-left: auto; margin-bottom: 4px; }
.wb-brand {
  /* 牌子占掉剩下的宽度：切换器与导航一起贴在右端（原来靠 `.wb-nav` 的 auto margin，
     中间插了切换器之后那点间距就不够看了）。窄屏放不下时它先让位（见下面的媒体查询）。
     B 方向：这是**同一块屏上的第三个身份标注**（导航的当前格、切档器、还有它），
     所以字号与字距都收一档 —— 留着它当栏首的锚点，但不与页面标题争。 */
  flex: 1 1 auto; min-width: 0;
  font-family: var(--font-song); font-size: 13.5px;
  letter-spacing: .06em; color: var(--hy-ink);
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
/* 工作台级导航（`WorkbenchNav.vue`）：它自己只出胶囊，**贴右端**是这一条栏的事 ——
   `margin-left: auto` 因此挂在这里，不挂在组件里（人事 / 现场两个壳要把它并进自己的
   导航条，那里不兴贴右端）。 */
.wb-nav { margin-left: auto; }
.wb-main { display: flex; flex-direction: column; flex: 1; min-height: 0; }

/* 窄屏：顶上那一条整个收进 `WorkbenchMobileHead` 的一行（方案 C，2026-10-08）。
 * 「‹后台 + 牌子 + 切换器 + 退出」在这个壳里原本已经被 C 方向压到只剩一行（49px），
 * 后勤（产品）组的配方页却还自带一条 133px 的白条 —— 页头把三件事（换页 / 身份 /
 * 退出·后台）一并接过来之后，这一条在手机档就没有存在理由了。
 * **是 `display: none` 不是删组件**：桌面档（>720px）还是它在干活，那里空间宽裕。 */
@media (max-width: 720px) {
  .wb-top { display: none; }
  /* 底栏是 `fixed`（脱离文档流），内容区得自己让出这一条的高度，否则滚到底时
     最后一块内容压在它下面够不着。57px = 底栏自身高，再加 iPhone 的安全区。 */
  .wb-main { padding-bottom: calc(57px + env(safe-area-inset-bottom, 0px)); }
  /* 自带底栏的页（配方阅读面）：工作台那条不渲染，这份内边距也还回去 ——
     阅读面自己的 `sop-bottom-bar` 会给自己那份。 */
  .wb-main.has-own-bar { padding-bottom: 0; }
}
@media (max-width: 560px) {
  /* 牌子让位给切换器与两扇门（人事壳同一条：`.sched-name` 在 560px 收起）：
     子系统名字在页面标题与工作台导航里都还在。 */
  .wb-brand { display: none; }
}
</style>
