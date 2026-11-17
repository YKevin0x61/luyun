<script setup>
// 工作台**级**导航（今天 / 人事 / 现场 / 后勤 / 任务 / 我的）：从一个壳里抽出来，三个壳共用。
//
// 为什么会有这个文件（B1 那一条的结构性来龙去脉）：工作台现在是「一个子系统、三套壳」——
//   - `/workbench`、`/workbench/staff/*`、`/workbench/kitchen/*` 套 `WorkbenchLayout`（一条顶栏）；
//   - `/workbench/hr/*` 套 `SchedulingLayout`（人事自己的窄栏）；
//   - `/workbench/floor/*` 套 `HygieneAdminLayout`（左侧 238px rail + 内容区横条）。
// 三套壳各有各的导航，于是**人事 / 现场那 11 页里根本没有一条通往工作台首页「今天」的链接**
// （两组的 href 全集里都不含 `/workbench`，连写着「工作台」的牌子都指向本组首页）。
//
// 这次不把三组硬并进同一个 Layout（那是大改结构，风险与收益不成比例），走的是**小步**：
// 把工作台级那一排导航抽成这一颗组件，让三个壳都渲染同一份 ——
//   - 表是 `utils/workbenchNav.js` 里的两张显式表（管理端 / 员工端各一张，按身份取）；
//   - 高亮还是那一个判据（页面的 `group`，不按路径前缀比）；
//   - 布局（要不要换行、横滑、靠哪边）留给各自的壳：组件自己只出**一条 nav 与它的格子**，
//     不带 `margin-left: auto` 这类"我在顶栏最右"的假设（那是 `WorkbenchLayout` 的事）。
// 于是三套壳的差异收敛成"每条栏怎么摆"，而**「今天」在每一页都点得到**（B2）。
//
// 导航跟着**此刻的身份**走（`stores/workbenchIdentity`），不跟着"这一页是谁的"：切档时
// 这一条与导航面一起变。切换只改视图与导航面 —— 页面能不能打开仍是路由守卫与服务端
// 页面墙的事，这里一个字都不碰。
//
// **2026-11-17：多页组都收成下拉**。七扇门并排时用户的原话是「入口太杂」；后勤先收成
// 一格后，人事 / 现场 / 任务也按同一规则收（组内多页才收，只有一页的「今天」仍是直链）。
// 点开是组内页面清单，点外面或 Esc 关闭。备货复盘 / 备货品 / 换算规则不在这张单里 ——
// 它们是「备货计划」页内的模块 tab。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { pageTitle } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { workbenchAudienceFor, workbenchGroup, workbenchGroupOf, workbenchNavFor, workbenchPagesOf } from '../../utils/workbenchNav'

const route = useRoute()
const identityStore = useWorkbenchIdentityStore()

// 身份还没探出来（`null`）时不渲染任何一格：不给一个可能点不通的入口。
// 探针由身份切换器开场（`onMounted`），三个壳里都挂着它。
const identity = computed(() => identityStore.identity)
const audience = computed(() => workbenchAudienceFor(identity.value))
// 员工那一档还要按能力键过滤（`staffCap` 页：排班三页与损耗分析，2026-10-14）。
const navItems = computed(() => workbenchNavFor(identity.value, identityStore.staffCaps))
const currentGroup = computed(() => workbenchGroupOf(route.path))

/** 按**格**把条目分好组（一处渲染）：一格里的条目共享一个 key，键序仍是组表里的顺序。
 *
 *  标题的取法跟着"这一格是不是下拉"走：
 *  - **直链格**（今天 / 卫生）—— 用组表里的 `label`；
 *  - **下拉格**（人事 / 现场 / 后勤 / 任务 / 我的）—— 下拉里的每一项用**自己那一页
 *    在清单里的名字**（同一颗胶囊里写两遍组名看不出哪一项是哪一页）。
 *
 *  `dropdown: true` 是强制标记（后勤）；没有标记但组内有多页时也自动收下拉 ——
 *  只有一页的组不值得多点一下。
 */
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
    // 下拉内容从页面清单派生：人事 8 页、现场 9 页、任务 2 页、后勤 4 门。
    // 后勤另有手写 `pages`，功能页（配方详情 / 打印）不会混进来；其余组的所有页
    // 都是能当入口的页，直接按 `group` 取。
    cell.menuItems = workbenchPagesOf(cell.key, audience.value, identityStore.staffCaps)
      .map(({ path, title }) => ({ to: path, title }))
    cell.group = workbenchGroup(cell.key, identity.value)
    // 组表标了 `dropdown`（后勤）或组内确实有多页时收下拉；只有一页的「今天」
    // 保持直链 —— 一颗只有一项的下拉只是多一次点击。
    cell.dropdown = !!cell.group?.dropdown || cell.menuItems.length > 1
    const titled = cell.links.length > 1
    cell.links = cell.links.map((item) => ({
      ...item,
      label: titled ? pageTitle(item.to) : item.label,
    }))
  }
  return cells
})

const openKey = ref(null)
let lastTrigger = null

function toggle(cellKey, event) {
  if (openKey.value === cellKey) {
    openKey.value = null
    return
  }
  openKey.value = cellKey
  lastTrigger = event?.currentTarget || null
}

function close() {
  openKey.value = null
}

function onDocumentClick() {
  close()
}

function onDocumentKeydown(event) {
  if (event.key !== 'Escape' || openKey.value == null) return
  close()
  lastTrigger?.focus?.()
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onDocumentKeydown)
})

// 点菜单里的页面后路由变了，浮层跟着收；否则它会一直盖在新页面上，直到下一次
// 点外面。整格高亮仍然按新的 `route.path` 重算。
watch(() => route.path, close)

/** 这一扇门算不算"当前"：得同时是**这一格**（`workbenchGroupOf`，判据是页面的 `group`）
 *  与这一条链接自己指着的路径。同一格里因此只有一扇门亮。
 *
 *  下拉格用**前缀**：`/prep-plan/items` 时亮「备货计划」，`/recipe/manage` 时亮「配方」；
 *  下拉里没有互为前缀的两项（计划 / 备货品 / 规则 / 复盘 不在同一张单里），不会双亮。
 *  单门格保持精确比较，和这条改动之前一字不差。 */
function currentMenuTarget(cell) {
  const items = cell.dropdown ? cell.menuItems : cell.links
  let best = null
  for (const item of items) {
    if (route.path !== item.to && !route.path.startsWith(`${item.to}/`)) continue
    if (!best || item.to.length > best.length) best = item.to
  }
  return best
}

function isCurrentItem(cell, item) {
  if (cell.key !== currentGroup.value) return false
  // 取**最长**命中的那一项：任务组同时有 `/workbench/tasks` 与
  // `/workbench/tasks/recurring`，按前缀取最短会两项一起亮。
  return item.to === currentMenuTarget(cell)
}

const isGroupCurrent = (cell) => cell.key === currentGroup.value
</script>

<template>
  <nav class="wb-nav" aria-label="工作台导航">
    <!-- 一格一颗胶囊：单条目那一格就是一个链接；多页组（`dropdown`）是一颗触发钮 + 浮层。
         整格是否当前由 `is-on` 判（按页面清单的 group），哪一项贴着"当前"由 `is-current`
         判（这一格 + 这一条链接自己的路径）。 -->
    <span
      v-for="cell in navCells"
      :key="cell.key"
      class="wb-nav-cell"
      :class="{ 'is-on': isGroupCurrent(cell), 'is-dropdown': cell.dropdown }"
    >
      <!-- 多页组：一扇组名门，点开组内页面。按钮本身不导航（用户裁定）——
           这样点开看一眼当前在哪一页，不会先把页面切走。 -->
      <template v-if="cell.dropdown && cell.group">
        <button
          type="button"
          class="wb-nav-trigger"
          :class="{ 'is-current': isGroupCurrent(cell) }"
          :aria-expanded="openKey === cell.key"
          :aria-current="isGroupCurrent(cell) ? 'page' : undefined"
          aria-haspopup="menu"
          :aria-controls="`wb-nav-menu-${cell.key}`"
          @click.stop="toggle(cell.key, $event)"
        >
          {{ cell.group.label }}
          <span class="wb-nav-caret" :class="{ 'is-open': openKey === cell.key }" aria-hidden="true">▾</span>
        </button>
        <div
          v-if="openKey === cell.key"
          :id="`wb-nav-menu-${cell.key}`"
          class="wb-nav-menu"
          role="menu"
          @click.stop
        >
          <router-link
            v-for="item in cell.menuItems"
            :key="item.to"
            class="wb-nav-menu-item"
            :class="{ 'is-current': isCurrentItem(cell, item) }"
            :to="item.to"
            role="menuitem"
            :aria-current="isCurrentItem(cell, item) ? 'page' : undefined"
            @click="close"
          >
            {{ item.title || pageTitle(item.to) }}
          </router-link>
        </div>
      </template>
      <template v-else>
        <router-link
          v-for="item in cell.links"
          :key="item.to"
          class="wb-nav-item"
          :class="{ 'is-current': isCurrentItem(cell, item) }"
          :to="item.to"
          :aria-current="isGroupCurrent(cell) ? 'page' : undefined"
        >
          {{ item.label }}
        </router-link>
      </template>
    </span>
  </nav>
</template>

<style scoped>
/* 只出**这一条导航与它的格子**，不碰外层摆放（那是各壳自己的事，见文件头的注释）。
 *
 * 形状：**下划线 tab，不是胶囊**。原来一格一颗圆角胶囊、一屏最多 8 颗（身份两档 + 退出 +
 * 五格导航），形状语言重复又抢眼，看着像一排药丸；而导航本该靠**位置与指示线**说"我在哪"，
 * 不靠每一格都描边填底。改法：文字态，当前格在**格**上加一条 2.5px 的薄荷下划线。 */
.wb-nav { display: flex; align-items: stretch; gap: 2px; min-width: 0; }
.wb-nav-cell {
  position: relative; display: inline-flex; align-items: stretch;
}
.wb-nav-item, .wb-nav-trigger {
  align-items: center;
  font-size: 13.5px; color: var(--hy-muted); text-decoration: none;
  padding: 0 13px; white-space: nowrap;
  border: 0; background: none;
  min-height: 38px;
}
.wb-nav-item { display: inline-flex; }
.wb-nav-trigger {
  display: inline-flex; gap: 5px;
  font-family: inherit; cursor: pointer;
}
.wb-nav-item:hover, .wb-nav-trigger:hover { color: var(--hy-ink); }
/* 当前格：文字提亮 + 底部一条指示线（贴在这一条导航的下沿 —— 各壳把它放在顶栏里时，
   这条线就落在顶栏的分隔线上）。 */
.wb-nav-cell.is-on::after {
  content: ""; position: absolute; left: 11px; right: 11px; bottom: 0;
  height: 2.5px; border-radius: 2.5px 2.5px 0 0; background: var(--hy-mint);
}
.wb-nav-cell.is-on .wb-nav-item,
.wb-nav-cell.is-on .wb-nav-trigger { color: var(--hy-mint); font-weight: 700; }
/* 「哪一项贴着当前」：整格已亮，这一项再加一档字重。 */
.wb-nav-item.is-current { color: var(--hy-ink); font-weight: 600; }
.wb-nav-item.is-current.is-on { color: var(--hy-mint); }

.wb-nav-caret {
  font-size: 10px; line-height: 1; opacity: .72;
  transition: transform .15s ease;
}
.wb-nav-caret.is-open { transform: rotate(180deg); }

/* 下拉浮层盖在内容上，层级取 `--z-dialog`（顶栏自己那档是 `--z-shell`，
   不够高，面板会被下面内容的层叠上下文吃掉）。 */
.wb-nav-menu {
  position: absolute;
  top: calc(100% - 1px);
  left: 0;
  z-index: var(--z-dialog);
  min-width: 176px;
  max-height: min(70vh, 420px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px;
  background: var(--surface-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-2);
}
.wb-nav-menu-item {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 12px;
  border-radius: var(--r-1);
  color: var(--ink-dim);
  font-size: 13.5px;
  text-decoration: none;
  white-space: nowrap;
}
.wb-nav-cell:last-child .wb-nav-menu { left: auto; right: 0; }
.wb-nav-menu-item:hover { background: var(--surface-2); color: var(--ink); }
.wb-nav-menu-item.is-current {
  background: var(--surface-2);
  color: var(--action);
  font-weight: 600;
}

/* 平板 / 小笔电（≤1024）：顶栏放不下九扇门，多页组已经收成下拉；触屏上把导航高度
   抬到 44px。 */
@media (max-width: 1024px) {
  .wb-nav-item, .wb-nav-trigger { min-height: 44px; padding: 0 12px; }
  .wb-nav-trigger { padding: 0 14px; }
  .wb-nav-menu-item { min-height: 44px; }
}

/* 窄屏（B6）：手机上这一排是**点得最多**的东西，抬到 44px 的触控下限。
   高度由 `min-height` 给（不是定高），汉字换行时不会被裁。 */
@media (max-width: 720px) {
  .wb-nav-item, .wb-nav-trigger { min-height: 44px; padding: 0 15px; }
}
</style>
