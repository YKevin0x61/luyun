<script setup>
// 现场这一组（卫生七页）的壳：桌面是左侧 238px 的 rail，手机是底部标签栏；内容区顶上
// 还有一条窄横条（面包屑 + 身份切换器 + 退出）。
//
// **C 方向（2026-10-05 用户裁定）**：手机档的底部那一格归**工作台级**导航
// （`components/workbench/WorkbenchTabBar.vue`：今天 / 人事 / 现场 / 后勤 / 我的，拇指区
// 可达）。两条底栏不能叠（一块屏上一上一下两条底栏，谁是谁都分不清），所以组内这八项
// （`.hy-tabbar`）在 ≤720px 从底部挪到**内容区顶部**、做成一条横滑带；桌面档的左 rail
// 与既有布局一个字没动（那一套在 `@media (min-width: 900px)` 里，不走手机档这段）。
//   - 顺带收掉上一轮审查的 B8：那条「标准图缓存 N」浮标原来压在底部标签栏上，底部这一格
//     腾空之后不再压住底栏（浮标本身在 `components/hygiene/StandardPhotoCachePanel.vue`
//     里，不在这两个壳的范围，这里只负责把底栏让开）。
//
// **B1 的小步（这次）**：三套壳里原来只有 `WorkbenchLayout` 那条有工作台级导航，人事 /
// 现场两组的 11 页因此连一条回工作台首页「今天」的链接都没有（两组 href 的全集里都不含
// `/workbench`，连写着「工作台」的牌子都指回本组首页）。这里把工作台级那一排
// （`components/workbench/WorkbenchNav.vue`：今天 / 人事 / 现场 / 后勤 / 我的）放进内容区
// 顶上那条横条，替换掉 rail 里那扇单独的「人事」门 —— 那一格本来干的就是工作台级导航的
// 事，现在由完整的一排接手（多出来的今天 / 后勤 / 我的也一并到位）。
//   - rail 里那扇「人事」门随之撤掉：它是工作台导航的一个真子集，留着就是同一件事两遍；
//     手机上的底部标签栏也从 8 格回到 7 格（原来 6+2 换行，现在 6+1）。
//   - 横条在窄屏会换行：手机上把装饰性的「工作台」牌子收起来（页内每页都有自己的 h1，
//     牌子在这里只是重复），腾出来的位置正好放这排导航 —— 横条的行数不变。
//     （**C 方向之后**：手机档这条横条里不再有工作台级那排，它下到底栏了 —— 见上面那段。）
// 三组收进同一个 `WorkbenchLayout` 那一步没做（那是大改结构）；这里是让三套壳共用同一份
// 工作台导航表与同一颗组件，差异只剩"每条栏怎么摆"。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import SvgIcon from '../../components/SvgIcon.vue'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import WorkbenchNav from '../../components/workbench/WorkbenchNav.vue'
import WorkbenchTabBar from '../../components/workbench/WorkbenchTabBar.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import { WORKBENCH_HOME } from '../../utils/workbenchCopy'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'
import { workbenchGroup } from '../../utils/workbenchNav'

useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const standardPhotoCache = useStandardPhotoCacheStore()

// 「现场」那一行给 rail 一个自己的名字（工作台导航里那一格的落点与门牌由那一份组表给）。
const fieldGroup = workbenchGroup('floor')

/** 只取路径最后一段来认"现在在哪一页"：不跟前缀绑死，`/workbench/floor/daily` 与带尾斜杠的
 *  `/workbench/floor/daily/` 都认。绑前缀的写法在票 02 并存期踩过坑 —— 标题会退回第一项、
 *  底部导航也不高亮（那一版就是这样被验证抓出来的）。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const currentNav = computed(() => (
  HYGIENE_ADMIN_NAV.find((item) => navKey(route.path) === navKey(item.path)) || HYGIENE_ADMIN_NAV[0]
))

function navIndex(item) {
  return String(HYGIENE_ADMIN_NAV.indexOf(item) + 1).padStart(2, '0')
}

watch(currentNav, (item) => {
  document.title = hygieneDocumentTitle(item.title)
}, { immediate: true })

watch(
  () => route.path,
  () => {
    if (standardPhotoCache.initialized) standardPhotoCache.checkForUpdates()
  },
)
</script>

<template>
  <div class="hygiene-admin hygiene-app">
    <a class="hy-skip" href="#hygiene-admin-main">跳到内容</a>

    <nav class="hy-tabbar" :aria-label="`${HYGIENE_BRAND_TITLE} · ${fieldGroup.label}`">
      <!-- 组内八项：桌面档是左侧 rail，手机档是**内容区顶部**那条横滑带（C 方向，见样式里
           的 ≤720px 一段）—— 底部那一格让给工作台级底栏（`WorkbenchTabBar`）。 -->
      <!-- 牌子写的是「工作台」，就指工作台**首页**（B2）：原来指本组首页（日常验收），
           于是这条栏上没有任何一个出口回得到「今天」。 -->
      <router-link class="hy-brand hy-brand-rail" :to="WORKBENCH_HOME">
        <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
        <span class="hy-brand-text">
          <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
          <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
        </span>
      </router-link>

      <div class="hy-rail-items">
        <!-- `router-link` 自动高亮是按 `to` 的字面路径比的：并存期在新前缀（`/workbench/...`）
             上它认不出来，所以再按"最后一段"补一次高亮 —— 两个前缀下都亮，票 05 之后也照旧。 -->
        <router-link
          v-for="item in HYGIENE_ADMIN_NAV"
          :key="item.path"
          class="hy-tab"
          :class="{ 'router-link-active': navKey(item.path) === navKey(route.path) }"
          :to="item.path"
        >
          <SvgIcon :name="item.icon" :size="19" />
          <span class="hy-tab-full">{{ item.title }}</span>
          <span class="hy-tab-short">{{ item.shortTitle }}</span>
          <span class="hy-rail-index">{{ navIndex(item) }}</span>
        </router-link>
        <!-- 回「人事」那一组那扇门（票 05）**这次撤掉了**：它干的事（跨组）现在由内容区顶上
             那排工作台导航（`WorkbenchNav`）整排接手，连「今天」也一并到位。同一件事写两遍
             只会让两组各说各话（B1 那一条的根因）。 -->
      </div>

      <div class="hy-rail-foot">
        <p class="hy-rail-note">对照实拍 · 当日验收<br>INSPECTION DESK</p>
        <router-link class="hy-back" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
          {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
        </router-link>
      </div>
    </nav>

    <div class="hy-shell">
      <header class="hy-header">
        <div class="hy-header-inner">
          <router-link class="hy-brand hy-brand-top" :to="WORKBENCH_HOME">
            <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
            <span class="hy-brand-text">
              <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
              <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
            </span>
          </router-link>
          <p class="hy-crumb">
            {{ currentNav.title }}
            <code>{{ navIndex(currentNav) }} · {{ currentNav.code }}</code>
          </p>
          <!-- 工作台级导航（今天 / 人事 / 现场 / 后勤 / 我的）：表与高亮都在
               `components/workbench/WorkbenchNav.vue` 里，三个壳渲染的是同一颗。放在这条
               横条上是因为它原来只有面包屑 + 切换器 + 退出，右侧大片空着；窄屏这条会换行，
               由 `.hy-brand-top` 让位（见样式）。**C 方向起手机档收起来**（`display: none`）：
               它下到底部那条 `WorkbenchTabBar` 去了，同一排入口不在顶上和底下同时出现。 -->
          <WorkbenchNav class="hy-wb-nav" />
          <!-- 身份切换器（票 04 那颗，自包含）：店长在自己的页面里也看得到那两档。 -->
          <WorkbenchIdentitySwitcher class="hy-id" />
          <!-- 退出入口（票 06）：现场这七页也是独立外壳，原先一个退出按钮都没有。
               行为只有一处（`composables/useWorkbenchLogout.js`）。 -->
          <WorkbenchExitButton />
          <router-link class="hy-back hy-back-top" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
            {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
          </router-link>
        </div>
      </header>
      <main id="hygiene-admin-main" class="hy-main">
        <router-view />
      </main>
    </div>
    <!-- 工作台级导航在手机档的落点（C 方向）：底栏一格一组、拇指区可达；桌面档它自己
         不渲染（`display: none`），那里 rail 里的七页与横条上的那排 tab 在干活。 -->
    <WorkbenchTabBar class="hy-wb-tabbar" />
    <!-- 这里**不放** `StandardPhotoCachePanel`（原来放在这儿）：那个面板是给**要拍照的人**
         离线缓存标准图用的，而现场这八页是店长的复核面 —— 他没有拍摄动作，进页面却被一个
         「36 张标准图 · 约 8.2 MB，先下载标准图」的模态挡住首屏（真机走查 N2，手机档尤其
         明显：点进来想判一单，先得处理一个下载框）。员工端那一页（`HygieneHomeView`）是
         真拍摄面，保留它。 -->
  </div>
</template>

<style scoped>
/* 工作台级导航：吃掉横条里剩下的宽度（原来那一块右侧是空的），放不下就**自己**横滑，
   别把切换器 / 退出挤到下一行。 */
.hy-wb-nav { flex: 1 1 auto; overflow-x: auto; }
/* 顶栏窄屏时把品牌那句副题收起来，给切换器与「后台」让位（牌子本身还在）。 */
@media (max-width: 720px) {
  .hy-brand-tagline { display: none; }
  /* C 方向：工作台级那一排（今天 / 人事 / 现场 / 后勤 / 我的）下到底部那条
     `WorkbenchTabBar`（拇指区可达）。这条横条因此收掉它，顶栏少一行。
     **`display: none` 不是删组件**：桌面档（>720px）还是这排在干活（那是横条右侧唯一
     有内容的东西），这里只收掉手机档那一份；两处同时出现才是错的（同一排入口两遍、
     两个高亮）。 */
  .hygiene-app .hy-wb-nav { display: none; }

  /* ---- 组内八项（`.hy-tabbar`）：底部 → 内容区顶部横滑带（C 方向）---------------
     底部那一格归工作台级底栏，两条底栏不能叠（一块屏上一上一下两条底栏谁都分不清
     哪条管什么）。原来它是 `grid-template-columns: repeat(6…)` 在底部排成 6+2 两行，
     还被「标准图缓存」浮标压着（上一轮审查的 B8）—— 现在一条横滑带，一行放不下就自己滑，
     不换行、不压内容。 */
  /* 要在「顶栏之后、内容之前」插这一条，只能把 `.hy-shell` 摊平：它把 header 与 main
     包成一个整体，而 `.hy-tabbar` 是外壳这一列的直接子项（不是 shell 的孩子）——
     在同一个 flex 列里，任何 `order` 都只能落在「整个 shell 之前」或「整个 shell 之后」。
     `display: contents`（共享样式表里 `.hy-rail-items` 用的也是这一手）让 header / 这条
     带子 / 内容 / 底栏都成为外壳这一列的直接子项，再用 `order` 排出手机档的上下次序。
     桌面档（≥900px 的左 rail）不在这段媒体查询里，一个字没动。 */
  .hygiene-admin.hygiene-app .hy-shell { display: contents; }
  .hygiene-admin.hygiene-app .hy-header { order: 0; }
  .hygiene-admin.hygiene-app .hy-tabbar {
    order: 1;
    display: flex;
    flex-wrap: nowrap;
    overflow-x: auto;
    /* 那条 `env(safe-area-inset-bottom)` 是底部栏的账（home indicator），挪到顶上就不该
       再留；底栏的刘海留白由 `WorkbenchTabBar` 自己付。边框跟着换边：现在它是内容区
       顶上的一条，分隔线画在下沿。 */
    padding: .25rem .15rem;
    border-top: 0;
    border-bottom: 1px solid var(--hy-line);
  }
  /* 一格一页：`flex: 0 0 auto` 是横滑带的关键 —— 默认 `flex-shrink: 1` 会把八格挤进一屏，
     标签跟着缩排/折行；给它固定宽度，放不下才轮到带子横向滚。 */
  .hygiene-admin.hygiene-app .hy-tabbar .hy-tab { flex: 0 0 auto; min-width: 62px; }
  /* 当前那一格的指示线原来画在格子上沿（栏在底部时线朝上、指着上方的内容）；这条栏现在
     在内容**之上**，线改画在下沿，才指得着下面那一页。 */
  .hygiene-admin.hygiene-app .hy-tabbar .hy-tab.router-link-active::after {
    top: auto;
    bottom: 0;
  }
  .hygiene-admin.hygiene-app .hy-main {
    order: 2;
    /* 底栏（`WorkbenchTabBar`）是 `position: fixed`（契约与样板 `WorkbenchLayout` 一致，
       样板里给它让高度的是 `.wb-main`），脱离了文档流 —— 内容区要自己让出那一条的高度
       （56px + 1px 边框 + iPhone home indicator），否则最后一屏被压在栏下、滚不到底。
       这里在共享样式表的 `1.4rem` 之上再加那一条。 */
    padding-bottom: calc(1.4rem + 57px + env(safe-area-inset-bottom, 0px));
  }

  /* 工作台级底栏（三套壳共用同一颗）：手机档钉在视口底。
     `position` 必须在这里再钉一遍：共享样式表的
     `.hygiene-admin > *:not(.modal-overlay) { position: relative; z-index: 1 }` 与组件那条
     **同特异度**（都是两个类），而它更晚进 head（`useScopedStylesheet` 在挂载时才 append
     那张表）—— 平局按文档顺序判，`fixed` 会被打回 `relative`：底栏脱不出文档流，内容一长
     就跟着排到内容末尾（底栏"手机上够不着"的现象就是这个）。这两条前缀把特异度抬到
     (0,4,0)，无论哪张表先加载都赢。
     `order: 3` 是给「万一它回到流里」留的后路：这一列里 header / 带子 / 内容都排好了序，
     底栏回到流里时必须仍排在最后（默认 order 是 0，那会跑到最上面去）。 */
  .hygiene-admin.hygiene-app .hy-wb-tabbar {
    order: 3;
    position: fixed;
    z-index: 15;
  }
}
</style>
