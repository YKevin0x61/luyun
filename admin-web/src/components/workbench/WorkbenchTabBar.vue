<script setup>
// 工作台**底部**标签栏（C 方向，2026-10-05 用户裁定）。
//
// 为什么要有它：工作台级导航（今天 / 人事 / 现场 / 后勤 / 我的）原本长在顶栏第二行 ——
// 手机上一只手拿着、另一只手在忙，顶栏那一条够起来别扭。挪到底部拇指区是移动端的主流
// 解法（iOS / Android / 微信都是这个范式），顶栏因此只剩「返回 + 页名 + 身份」。
//
// 与 `WorkbenchNav.vue` 的分工（同一个抽屉的两面，别各写一份表）：
//   - 两者都读 `utils/workbenchNav.js` 那一张表、都用 `workbenchGroupOf` 判当前格；
//   - **顶栏那条（`WorkbenchNav`）是"组的入口"**：后勤那格拆成两扇门（配方 / 备货计划），
//     站哪一页亮哪一半；
//   - **底栏这条是"一级导航"**：一格一组、只显示组名（后勤就是「后勤」），点进去是该组
//     第一页，组内怎么走交给顶栏。底部一格放不下两扇门，也不该放 —— 一级导航的粒度就是组。
//
// 只在手机档渲染（`max-width: 720px`）：桌面空间宽裕，顶栏那一条 tab 更好用；
// 底栏是移动端范式，不是"两处都放"。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { workbenchGroupOf, workbenchNavFor } from '../../utils/workbenchNav'

const route = useRoute()
const identityStore = useWorkbenchIdentityStore()

const identity = computed(() => identityStore.identity)
const currentGroup = computed(() => workbenchGroupOf(route.path))

/** 一格一组（与顶栏同一个抽屉顺序）。表里同 key 的多条链接（后勤两扇门）在这里只留
 *  第一条当落点，标签仍用**组名**。 */
const cells = computed(() => {
  const seen = new Map()
  for (const item of workbenchNavFor(identity.value)) {
    if (!seen.has(item.key)) {
      seen.set(item.key, { key: item.key, label: item.label, to: item.to })
    }
  }
  return [...seen.values()]
})

/** 图标：手写的 21px 描边 SVG（同一支笔：1.7 的线宽、圆头圆角）。
 *  不用 emoji / Unicode 字符顶替 —— 那在不同手机上字形不同、粗细也不齐。 */
const ICONS = {
  // 今天：日历
  home: [
    'M4.2 7.2A1.7 1.7 0 0 1 5.9 5.5h12.2a1.7 1.7 0 0 1 1.7 1.7v10.9a1.7 1.7 0 0 1-1.7 1.7H5.9a1.7 1.7 0 0 1-1.7-1.7z',
    'M4.2 9.8h15.6',
    'M8.6 3.8v3.2',
    'M15.4 3.8v3.2',
  ],
  // 人事：两个人（班组）
  hr: [
    'M9.2 11.2a3.1 3.1 0 1 0 0-6.2 3.1 3.1 0 0 0 0 6.2z',
    'M3.4 19.6c0-3.1 2.6-5.2 5.8-5.2s5.8 2.1 5.8 5.2',
    'M15.9 8.7a2.6 2.6 0 0 1 0 5.1',
    'M17.4 14.9c2 .6 3.3 2.3 3.3 4.7',
  ],
  // 现场：四格（工作区 / 日常 / 专项 / 整改这些"现场的事"）
  floor: [
    'M4.3 4.3h6.2v6.2H4.3z',
    'M13.5 4.3h6.2v6.2h-6.2z',
    'M4.3 13.5h6.2v6.2H4.3z',
    'M13.5 13.5h6.2v6.2h-6.2z',
  ],
  // 卫生（员工那一格）：一滴水 —— 清洁这件事最直白的形状
  hygiene: [
    'M12 3.6c3.2 3.9 5.2 6.7 5.2 9.2a5.2 5.2 0 0 1-10.4 0c0-2.5 2-5.3 5.2-9.2z',
    'M9.7 13.5a2.5 2.5 0 0 0 2.5 2.5',
  ],
  // 后勤：料箱
  kitchen: [
    'M4.6 9.4 6.1 5.2h11.8l1.5 4.2',
    'M4.6 9.4h14.8v9.1a1.3 1.3 0 0 1-1.3 1.3H5.9a1.3 1.3 0 0 1-1.3-1.3z',
    'M9.9 12.9h4.2',
  ],
  // 我的：一个人
  me: [
    'M12 11.4a3.4 3.4 0 1 0 0-6.8 3.4 3.4 0 0 0 0 6.8z',
    'M5.2 20c0-3.2 3-5.4 6.8-5.4s6.8 2.2 6.8 5.4',
  ],
}
</script>

<template>
  <nav class="wb-tabbar" aria-label="工作台主导航">
    <router-link
      v-for="cell in cells"
      :key="cell.key"
      class="wb-tab"
      :class="{ 'is-on': cell.key === currentGroup }"
      :aria-current="cell.key === currentGroup ? 'page' : undefined"
      :to="cell.to"
    >
      <svg
        class="wb-tab-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path v-for="(d, i) in ICONS[cell.key] || []" :key="i" :d="d" />
      </svg>
      <span class="wb-tab-label">{{ cell.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
/* 桌面档不渲染这一条：那里空间宽裕，顶栏的 tab 更顺手。底栏是移动端范式。 */
.wb-tabbar { display: none; }

@media (max-width: 720px) {
  .wb-tabbar {
    display: flex;
    align-items: stretch;
    /* **`fixed` 而不是 `sticky`**：这套结构里真正的滚动容器是更外层的 `.app-shell`
       的 `.page-body`，而这一条的包含块是工作台外壳 —— `sticky; bottom: 0` 只能在外壳
       自己的高度内粘住，外壳高度跟着内容长时它就等于"排在内容最后"
       （实测 390×844 下量到 y=990，整条在视口之外，用户根本看不见）。
       钉在视口底则与内容多长无关；代价是脱离文档流，内容区要自己让出高度
       （见各壳里 `.wb-main` 的 `padding-bottom`）。 */
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 15;
    background: var(--hy-bg);
    border-top: 1px solid var(--hy-line);
    /* iPhone 的 home indicator 区域：不给这一条留白，最后一格会被系统手势条压住。 */
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }

  .wb-tab {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    /* 触控下限（B6 的账）：底栏是手机上点得最多的东西，56px 起步。 */
    min-height: 56px;
    padding: 7px 2px 6px;
    color: var(--hy-faint);
    text-decoration: none;
    font-size: 11.5px;
    line-height: 1.1;
  }
  .wb-tab-label { white-space: nowrap; }
  .wb-tab-icon { width: 21px; height: 21px; flex: 0 0 auto; }

  .wb-tab.is-on { color: var(--hy-mint); font-weight: 700; }
}
</style>
