<script setup>
// **统一的壳**（③-2 / ADR 0105：三壳合一）。
//
// 在此之前工作台有三套壳，各自实现导航 / 身份 / 退出 / 手机页头：`WorkbenchLayout`（首页 /
// 后勤 / 我的）、`SchedulingLayout`（人事）、`HygieneAdminLayout`（卫生验收）。三份的差异
// 只有「怎么摆」，共用件与导航表本来就已经是同一份 —— 于是这一票把「怎么摆」也收拢到一处：
// 三个壳都变成这一个外壳的薄包装，各自只留自己的附加件（实时点、本组牌子、页内边距）。
//
// **两个视觉变体（按 `audience`）**：
//   · 管理端（桌面 1440 优先）：顶栏（回后台 / 牌子 / 身份 / 工作台级导航 / 退出）+ **左 rail** 组内导航；
//   · 员工端（手机 390 优先）：56px 单行页头（`WorkbenchMobileHead`：组名 / 当前页下拉 / 身份 / ⋮）+ 底部标签栏。
//   两态不是两个组件：同一份 DOM，靠 `audience` 决定**渲染什么入口**、靠媒体查询决定**怎么摆**
//   （手机↔桌面分叉一律 `max-width:720px`，见 `_shell-spec §2`）。
//
// **壳只渲染、不判断**：导航模型只有 `utils/workbenchNav.js` 一份，它读 `pageRoutes.json`；
// 这里不认具体有哪些组、也不写第二份路径。身份读 `stores/workbenchIdentity`，切档时这一条栏
// 与导航面一起变 —— 切换**只改视图与导航面**，页面能不能打开仍是路由守卫与服务端页面墙的事。
//
// 组内页（rail 与页头下拉里的那些）= `workbenchPagesOf(group, audience)`：**group + 落点页
// `audience` 双层过滤**（spec §5.13），所以员工在「配方」组看不到「岗位二维码 / 配方管理」
// 那两扇点不通的门。
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import WorkbenchMobileHead from '../../components/workbench/WorkbenchMobileHead.vue'
import WorkbenchNav from '../../components/workbench/WorkbenchNav.vue'
import WorkbenchRail from '../../components/workbench/WorkbenchRail.vue'
import WorkbenchTabBar from '../../components/workbench/WorkbenchTabBar.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { WORKBENCH_HOME, WORKBENCH_TITLE } from '../../utils/workbenchCopy'
import { IDENTITY_STAFF } from '../../utils/workbenchIdentity'
import {
  workbenchAudienceFor,
  workbenchGroup,
  workbenchGroupOf,
  workbenchNavFor,
  workbenchPagesOf,
} from '../../utils/workbenchNav'

const props = defineProps({
  /** 本组（不给我就按当前路径算 —— 首页那种「不属于任何一组」的路径得到空串）。 */
  group: { type: String, default: '' },
  /** 本组条目（手写名单优先，如卫生八页带图标）；不给就从页面清单派生。 */
  items: { type: Array, default: null },
  /** rail 的 `aria-label`；不给就用组名（卫生那一组要「工作台 · 卫生验收」那种读法）。 */
  label: { type: String, default: '' },
})

// 深青墨令牌跟工作台其他两组同一张表（`/hygiene-admin.css`）。**收进壳加载一次**：原先四个
// 子页与三个壳各挂一份（10 处），收敛之后只剩这一处（`useScopedStylesheet` 自己不做 DOM
// 去重，所以「少挂几份」＝少几份重复的 `<link>`）。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()
const identityStore = useWorkbenchIdentityStore()

/** 自带底部工具栏的页面：配方阅读面那一页有自己的 `.sop-bottom-bar`（搜索 / 目录 / 字号 /
 *  份数 —— 全是阅读动作）。手机档这里**不再叠工作台底栏**：两条 `fixed` 底栏只差 4px，
 *  上面那条会把工作台那条整个盖住，留着只是白占一次渲染、内容区还要为它让一遍高度
 *  （2026-10-08 用户裁定）。桌面档那条不在视口底，不受影响。 */
const OWN_BOTTOM_BAR_PATHS = ['/workbench/kitchen/recipe/detail']
const hasOwnBottomBar = computed(() => OWN_BOTTOM_BAR_PATHS.includes(route.path))

// 探针由切换器自己开场（`onMounted`）。这里只在**已经探出结论**时消费它：没探完时
// `identity` 是 null，导航不渲染 —— 不给一个可能点不通的入口。
const identity = computed(() => identityStore.identity)
const audience = computed(() => workbenchAudienceFor(identity.value))
const navItems = computed(() => workbenchNavFor(identity.value))

// 本组的 key（三个调用点之一：显式给的组优先，否则按当前路径算）。
const groupKey = computed(() => props.group || workbenchGroupOf(route.path) || '')
const groupRow = computed(() => workbenchGroup(groupKey.value))
/** 本组名（rail 的 `aria-label` 与页头下拉的组名格都用它）。 */
const groupLabel = computed(() => (groupRow.value ? groupRow.value.label : ''))
/** 本组可见页：**group + audience 双层过滤**（spec §5.13）。手写名单（`items`）原样用。 */
const groupPages = computed(() =>
  props.items && props.items.length
    ? props.items
    : workbenchPagesOf(groupKey.value, audience.value))

/** 员工那一档不渲染「‹ 后台」（D7）：那一扇门通向 `/`（运营仪表盘）—— 超管专属页，
 *  员工点下去只会吃一张「无权访问」。挂着一扇**必然被拒**的门比没有门更糟：
 *  它天天在那里，点一次损失一次。超管那一档照旧（spec 故事 11 的双向入口）。 */
const showBackToAdmin = computed(() => identity.value !== IDENTITY_STAFF)

/** 此刻这一页还属于当前身份吗（判据是页面清单里那一行的 `audience`，不猜路径前缀）。 */
function viewAllowedHere() {
  if (identity.value === null) return true
  const row = pageRow(route.path)
  if (!row) return true
  return row.audience === 'both' || row.audience === audience.value
}

/** 切档之后人还站在别人的页上时，把他送回**这一档**能看的最近一页；导航里那几格就是落点。
 *  **不由这里改权限**：守卫怎么判还是怎么判，这条只负责别让人停在一个"不是你现在这档看的"页面上。 */
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
    <!-- 键盘第一站（B4）：`:focus` 才落进顶栏，鼠标用户看不见它。 -->
    <a class="hy-skip no-print" href="#workbench-main">跳到内容</a>

    <!-- 员工端那一条（方案 C）：一行装下「组名 / 当前页下拉 / 身份 / 更多」，组里的页从桌面
         那条顶栏收进这个下拉。桌面档它自己不渲染（组件 scoped 样式里 `max-width: 720px`
         那一段）。`items` 给了就用它（卫生八页带图标），不给就按本组派生。
         `no-print`：配方打印是 A4 预览页（也套这个壳），打印时顶栏与底栏都不该被打进去。 -->
    <WorkbenchMobileHead class="no-print" :items="props.items || undefined" />

    <!-- 管理端那一条：回后台 / 牌子 / 身份 / 工作台级导航 / 退出（+ 本组的附加件）。
         **从页顶到内容只有这一条横带**：层级收进 `--z-shell`，页内不许再自定 sticky 层级（B6）。 -->
    <header class="wb-top no-print">
      <router-link v-if="showBackToAdmin" class="wb-back" to="/">‹ 后台</router-link>
      <!-- 牌子写的是「工作台」，就指工作台首页（B2）。 -->
      <router-link class="wb-brand" :to="WORKBENCH_HOME">{{ WORKBENCH_TITLE }}</router-link>
      <WorkbenchIdentitySwitcher class="wb-id-switcher" />
      <!-- 工作台级导航（今天 / 人事 / 卫生验收 / 配方 / 我的）：表与高亮都在
           `components/workbench/WorkbenchNav.vue` 里，三个壳渲染的是同一颗。 -->
      <WorkbenchNav class="wb-nav" />
      <!-- 本组的附加件（人事壳那个实时点）。 -->
      <slot name="meta" />
      <!-- 退出入口：工作台是子应用，页页都得退得出去；行为只有一处（`useWorkbenchLogout`）。 -->
      <WorkbenchExitButton />
    </header>

    <div class="wb-body">
      <!-- 桌面档的组内导航（单页组不渲染 —— 判据在组件里）。 -->
      <WorkbenchRail :items="groupPages" :label="props.label || groupLabel">
        <slot name="rail" />
      </WorkbenchRail>
      <main id="workbench-main" class="wb-main" :class="{ 'has-own-bar': hasOwnBottomBar }">
        <slot />
      </main>
    </div>

    <!-- 工作台级导航在手机档的落点（C 方向）：底栏一格一组、拇指区可达；桌面档它自己
         不渲染（`display: none`），那里用顶栏那条 tab。同一排入口不在两处同时出现。 -->
    <WorkbenchTabBar v-if="!hasOwnBottomBar" class="no-print" />
  </div>
</template>


<style scoped>
/* 顶栏粘住：滚动容器是 `.app-shell` 的 `.page-body`（standalone 时它 `overflow-y:auto`
   且没有内边距），所以 sticky 挂在这里正好压在内容上方。**层级收进令牌**（B6）。 */
.wb-top {
  position: sticky;
  top: 0;
  z-index: var(--z-shell);
  display: flex;
  align-items: center;
  gap: 10px;
  /* 高度是**下限**不是定值：单行 flex 会把胶囊压到内容宽度以下、汉字逐字换行，整条栏溢出后
     被裁掉（实测 clientWidth 390 / scrollWidth 442）。**底部不留内边距**：导航是下划线 tab，
     那条指示线要落在这一栏的分隔线上。 */
  min-height: 40px;
  padding: 4px 12px 0;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
/* 不占满整行的那几件：补 4px 下边距；导航（下划线 tab）不在此列 —— 它要贴到底边。 */
.wb-back, .wb-brand, .wb-id-switcher { margin-bottom: 4px; }
/* 退出按钮要**:deep() 才选得中** —— 也别在挂它的地方传 class：它的模板是多根，父级传下去的
   class 不透传，Vue 还会当场报 `Extraneous non-props attributes`。 */
.wb-top :deep(.wb-exit) { margin-left: auto; margin-bottom: 4px; }
.wb-brand {
  /* 牌子占掉剩下的宽度：切换器与导航一起贴在右端。窄屏放不下时它先让位（见下面的媒体查询）。 */
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-song);
  font-size: 13.5px;
  letter-spacing: .06em;
  color: var(--hy-ink);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wb-back {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: var(--hy-muted);
  text-decoration: none;
  background: var(--hy-surface-2);
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  padding: 4px 11px;
  white-space: nowrap;
}
.wb-back:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
/* 切换器与导航之间一条细分隔：两件事（我是谁 / 去哪一页），别挤成一团。 */
.wb-id-switcher { margin-left: 4px; padding-right: 10px; border-right: 1px solid var(--hy-line); }
/* 工作台级导航：它自己只出胶囊，**贴右端**是这一条栏的事。 */
.wb-nav { margin-left: auto; }
/* 内容区：左 rail + 页面本体。 */
.wb-body { display: flex; align-items: flex-start; flex: 1; min-height: 0; }
.wb-main { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }

/* 窄屏：顶上那一条整个收进 `WorkbenchMobileHead` 的一行（方案 C）。是 `display: none`
   不是删组件：桌面档（>720px）还是它在干活。 */
@media (max-width: 720px) {
  .wb-top { display: none; }
  /* 底栏是 `fixed`（脱离文档流），内容区得自己让出这一条的高度，否则滚到底时最后一块内容
     压在它下面够不着。57px = 底栏自身高 + 1px 边框，再加 iPhone 的安全区。 */
  .wb-main { padding-bottom: calc(57px + env(safe-area-inset-bottom, 0px)); }
  /* 自带底栏的页（配方阅读面）：工作台那条不渲染，这份内边距也还回去。 */
  .wb-main.has-own-bar { padding-bottom: 0; }
}
@media (max-width: 560px) {
  /* 牌子让位给切换器与两扇门：子系统名字在页面标题与工作台导航里都还在。 */
  .wb-brand { display: none; }
}

/* 打印：壳的 chrome 一律不印（B4）。真正的白底黑字复位在 `theme.workbench.css`（③-1）。 */
@media print {
  .wb-top, .wb-rail, .no-print { display: none !important; }
}
</style>
