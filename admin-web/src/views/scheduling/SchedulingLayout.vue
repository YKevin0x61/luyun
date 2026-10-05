<script setup>
// 人事这一组（月历 / 待办 / 班次表 / 花名册）的壳（2026-10-04 从后台壳里独立出来；
// 票 05 按分组落位：花名册从卫生那八页搬进来，顶栏补上本组导航与进「现场」组的门）。
//
// 它仍是**当场干活的界面**：顶上一条自己的窄栏（回后台、本组四页、工作台级导航、身份
// 切换器、实时状态），页面本体挂在 `<router-view />` 里 —— 页内结构一个不动。后台那条
// 九个模块的导航不在这里渲染（这些页在页面清单里是 `standalone`）。
//
// **B1 的小步（这次）**：三套壳里原来只有 `WorkbenchLayout` 那条有工作台级导航，
// 人事 / 现场两组的 11 页因此连一条回工作台首页「今天」的链接都没有（两组 href 的全集里
// 都不含 `/workbench`，连写着「工作台」的牌子都指回本组首页）。这里把工作台级那一排
// （`components/workbench/WorkbenchNav.vue`：今天 / 人事 / 现场 / 后勤 / 我的）并进这条栏，
// 与本组四页的导航共用**一条**横滑的导航带：
//   - 「现场 ›」那扇门随之撤掉 —— 导航里「现场」那一格指的就是同一个落点，留着就是同一件
//     事写两遍（它还带着一个假的 `›`，看着像下拉，点了却直接跳页）；
//   - 窄屏仍是两行：第一行两个入口 + 切换器 + 退出，第二行整条导航自己横滑 —— 行数没有
//     变多（导航带在 721–1080 之间也是**一条**，放不下就自己滑，不给顶栏再加一行）。
// 三组收进同一个 `WorkbenchLayout` 那一步没做（那是大改结构）；这里是让三套壳共用同一份
// 工作台导航表与同一颗组件，差异只剩"每条栏怎么摆"。
//
// **C 方向（2026-10-05 用户裁定）**：工作台级那一排（今天 / 人事 / 现场 / 后勤 / 我的）不再
// 占顶栏，改由底部那条 `components/workbench/WorkbenchTabBar.vue` 承担 —— 手机上一只手拿着、
// 另一只手在忙，底部拇指区才是够得着的地方（iOS / Android / 微信都是这个范式）。顶栏因此
// 少一排，只剩「返回 + 本组四页 + 身份 + 退出」。顶栏那一排**不删**（桌面档还用它），只在
// ≤720px 收起来：同一排入口不在顶上和底下同时出现（两处入口 = 两个高亮，看着像两组导航）。
//
// 组里的页与工作台导航都从**单一来源**来：`utils/workbenchNav.js`（组表 + 页面清单
// 派生），这里不抄第二份路径。
import { computed, inject, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import WorkbenchNav from '../../components/workbench/WorkbenchNav.vue'
import WorkbenchTabBar from '../../components/workbench/WorkbenchTabBar.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow } from '../../router/pageRoutes.js'
import { WORKBENCH_HOME, WORKBENCH_TITLE, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { workbenchGroup, workbenchPagesOf } from '../../utils/workbenchNav'

// 共享的深青墨令牌（跟卫生管理端同一张表，不是卫生模块的东西）。
// 由**壳**加载一次就够了 —— 四个子页本来各加载一份，收进来之后它们不用再管。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()
// `App.vue` provide 的实时连接状态（原先显示在后台导航上）。
const connected = inject('wsConnected', null)
const offline = computed(() => connected && connected.value === false)

// 本组四页：路径与名字都从页面清单派生（组里加一页只改那张表）。
const groupPages = workbenchPagesOf('hr')
// 本组那一行（牌子与工作台导航里「人事」那一格的落点是它，不写第二份）。
const hrGroup = workbenchGroup('hr')

function backToAdmin() {
  router.push('/')
}

/** 认「现在在哪一页」只看路径最后一段：不带尾斜杠与带尾斜杠两种写法都认
 *  （跟现场壳同一个口径 —— 绑前缀的写法在票 02 并存期踩过坑，标题会退回第一项）。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const currentKey = computed(() => navKey(route.path))
function isCurrent(page) {
  return navKey(page.path) === currentKey.value
}

// 页面标题也从清单来：花名册这类不自带标题的页面由此拿到名字（它原来由卫生壳给，
// 票 05 跟着分组一起搬过来）。子页自己设标题的那两页照旧，它们设的就是清单里的名字。
watch(
  () => route.path,
  () => {
    const row = pageRow(route.path)
    if (row) document.title = workbenchDocumentTitle(row.title)
  },
  { immediate: true },
)
</script>

<template>
  <div class="hygiene-admin sched-shell">
    <header class="sched-top">
      <button class="sched-back" type="button" @click="backToAdmin()">‹ 后台</button>
      <!-- 牌子写的是「工作台」，就指工作台**首页**（B2）：原来它指本组首页（月历），
           于是这条栏上没有任何一个出口回得到「今天」——标签说工作台、落点却是人事组，
           两个说法在同一颗胶囊上打架。 -->
      <router-link class="sched-name" :to="WORKBENCH_HOME">{{ WORKBENCH_TITLE }}</router-link>
      <!-- 一条导航带装两条导航：工作台级那排（今天 / 人事 / 现场 / 后勤 / 我的）+ 本组
           四页。并排放在一行里（窄屏整条横滑），而不是各自占一行 —— 顶栏行数不变，
           「今天」却在这四页上都点得到。**C 方向起手机档只剩本组四页**：工作台级那排
           （`.sched-wb-nav`）在 ≤720px 收起来，落点移到底部那条底栏。 -->
      <div class="sched-navbar">
        <WorkbenchNav class="sched-wb-nav" />
        <nav class="sched-nav" :aria-label="hrGroup.label">
          <router-link
            v-for="page in groupPages"
            :key="page.path"
            class="sched-nav-item"
            :class="{ 'is-on': isCurrent(page) }"
            :aria-current="isCurrent(page) ? 'page' : undefined"
            :to="page.path"
          >{{ page.title }}</router-link>
        </nav>
      </div>
      <WorkbenchIdentitySwitcher class="sched-id" />
      <!-- 退出入口（票 06）：这一页是独立外壳（`standalone`，后台那条导航不渲染），
           原先没有退出的地方。行为只有一处（`composables/useWorkbenchLogout.js`）。 -->
      <WorkbenchExitButton class="sched-exit" />
      <span
        class="sched-conn"
        :class="{ off: offline }"
        :title="offline ? '实时已断开：别人改了排班这一页不会自己刷新' : '实时已连接'"
      ><i aria-hidden="true"></i>{{ offline ? '实时已断开' : '' }}</span>
    </header>
    <router-view />
    <!-- 工作台级导航在手机档的落点（C 方向）：底栏一格一组、拇指区可达；桌面档它自己
         不渲染（`display: none`），那里用顶栏那条 tab。同一排入口不在两处同时出现。 -->
    <WorkbenchTabBar class="sched-tabbar" />
  </div>
</template>

<style scoped>
/* 顶栏粘住：这一页的滚动容器是 `.app-shell` 的 `.page-body`（standalone 时它是
   `overflow-y: auto` 且没有内边距），所以 sticky 挂在这里正好压在内容上方。 */
.sched-top {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 9px;
  min-height: 40px; padding: 4px 12px;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
.sched-back {
  flex: 0 0 auto;
  display: inline-flex; align-items: center; gap: 3px;
  font: inherit; font-size: 12px; color: var(--hy-muted);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px; cursor: pointer;
}
.sched-back:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
.sched-name {
  flex: 0 0 auto;
  font-family: var(--font-song); font-size: 14px; letter-spacing: .12em; color: var(--hy-ink);
  text-decoration: none;
}
.sched-name:hover { color: var(--hy-mint); }
/* 导航带（工作台级一排 + 本组四页）：一条带子装两条导航，行数不变。
   `min-width: 0` + `overflow-x: auto` 是这条的关键 —— 中屏（721–1080）顶栏放不下时
   让**带子自己横滑**，而不是把「退出」挤到下一行。 */
.sched-navbar { display: flex; align-items: center; gap: 10px; min-width: 0; overflow-x: auto; }
/* 本组四页：一格一页，当前那一格亮（判据是路径最后一段，见 `isCurrent`）。 */
.sched-nav { display: flex; align-items: center; gap: 5px; min-width: 0; }
.sched-nav-item {
  flex: 0 0 auto;
  display: inline-flex; align-items: center;
  font-size: 12px; color: var(--hy-muted); text-decoration: none;
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px; white-space: nowrap;
}
.sched-nav-item:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
.sched-nav-item.is-on {
  color: var(--hy-mint); border-color: var(--hy-mint-line); background: var(--hy-mint-soft);
}
.sched-wb-nav { flex: 0 0 auto; }
/* 身份切换器（票 04 那颗）与实时点贴右端。窄屏下它跟「本组导航」换行：切换器是顶栏里
   最不该被挤出屏幕的一件，所以导航整条另起一行、自己横向滑。 */
.sched-id { flex: 0 0 auto; margin-left: auto; }
.sched-exit { flex: 0 0 auto; }
.sched-conn { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--hy-faint); }
.sched-conn i { width: 7px; height: 7px; border-radius: 50%; background: var(--hy-mint); }
.sched-conn.off { color: var(--hy-seal-bright); }
.sched-conn.off i { background: var(--hy-seal-bright); }

@media (max-width: 720px) {
  .sched-top { flex-wrap: wrap; row-gap: 5px; gap: 8px; padding: 4px 8px; }
  /* 本组四页整条另起一行（order 放到最后），上面那一行留给两个入口、切换器与实时点；
     这条带子在窄屏自己横滑。 */
  .sched-navbar { order: 1; flex-basis: 100%; }
  /* C 方向：工作台级那一排（今天 / 人事 / 现场 / 后勤 / 我的）下到底栏
     （`components/workbench/WorkbenchTabBar.vue`）—— 拇指区可达。顶栏这一排因此收起来，
     顶栏少一行。**是 `display: none` 不是删组件**：桌面档（>720px）还是它在干活，
     这里只收掉它在手机档的那一份；两处同时出现才是错的（同一排入口两遍、两个高亮）。 */
  .sched-shell .sched-wb-nav { display: none; }
  /* 底栏（`WorkbenchTabBar`）在手机档钉在视口底 —— 与样板 `WorkbenchLayout` 同一份契约：
     组件自己是 `position: fixed; left: 0; right: 0; bottom: 0`，外壳负责给内容让出那一条的
     高度（见下面 `.sched-shell` 的 padding-bottom，样板里是 `.wb-main` 的同一条）。
     这里把 `position` 与 `z-index` 再钉一遍（`left / right / bottom` 仍来自组件）：
     共享样式表的 `.hygiene-admin > *:not(.modal-overlay) { position: relative; z-index: 1 }`
     与组件那条**同特异度**（都是两个类），而它更晚进 head（`useScopedStylesheet` 在挂载时
     才 append 那张表）—— 平局按文档顺序判，`fixed` 会被打回 `relative`：底栏脱不出文档流，
     内容一长就跟着排到页面末尾（底栏"手机上够不着"的现象就是这个）。`.sched-shell` 这个
     父级把特异度抬到 (0,3,0)，无论哪张表先加载都赢。 */
  .sched-shell .sched-tabbar { position: fixed; z-index: 15; }
  /* 底栏已脱离文档流，内容末尾要让出「56px + 1px 边框 + iPhone home indicator」的高度，
     否则最后一屏内容压在栏下、滚不到底（数字与样板 `.wb-main` 那条一致）。 */
  .sched-shell { padding-bottom: calc(57px + env(safe-area-inset-bottom, 0px)); }
  /* 触控下限（B6）：手机上「‹后台」与导航胶囊原来高 24–29px，抬到 44px。 */
  .sched-back { min-height: 44px; padding: 4px 14px; }
  .sched-nav-item { min-height: 44px; padding: 4px 14px; }
}
@media (max-width: 560px) {
  /* 牌子让位给切换器与两扇门；子系统名字在页面标题与后台导航里都还在。 */
  .sched-name { display: none; }
}

/* 花名册（票 05 从卫生那八页搬进人事组）原来套在卫生壳的 `.hy-main` 里，内边距是那一层
   给的；搬到这条窄栏下面之后由这里补上（数字与 `.hy-main` 那条一致），页面组件自己的
   样式一个字没动。其余三页自带内边距，不吃这条。 */
.sched-shell > .roster-page {
  padding: var(--hy-page);
  padding-left: max(var(--hy-page), env(safe-area-inset-left));
  padding-right: max(var(--hy-page), env(safe-area-inset-right));
}
@media (min-width: 900px) {
  .sched-shell > .roster-page {
    padding: clamp(1.1rem, 2.2vw, 2rem) clamp(1.2rem, 3vw, 2.6rem) 2rem;
  }
}
</style>
