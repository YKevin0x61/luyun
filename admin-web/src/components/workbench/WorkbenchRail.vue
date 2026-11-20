<script setup>
// 桌面左 rail：本组的**组内导航**（③-2 / `_shell-spec §5.12`）。
//
// 壳只渲染、不判断：条目由调用方给（`workbenchPagesOf(group, audience)` 派生，或本组那份
// 手写名单 —— 卫生八页带图标），这里不认身份、不认页面清单。所以 rail 里出现什么，等于
// 「这一组的可见页」这一份事实，不在第二处再写一遍。
//
// 三条硬约束（spec §5.12）：
//   · `position:sticky; top:var(--head-h); align-self:flex-start; max-height:calc(100vh - var(--head-h)); overflow:auto`
//     —— 长页滚动时组内导航不丢；
//   · **单页组不渲染 rail**（`v-if="items.length > 1"`），与手机档「单页组不给空下拉」同一判据；
//   · ≤720px 由外壳那条分叉收起来（手机档走页头下拉 + 底栏，见 `WorkbenchMobileHead` / `WorkbenchTabBar`）。
import { useRoute } from 'vue-router'
import SvgIcon from '../SvgIcon.vue'

defineProps({
  /** `[{ path, title, icon?, shortTitle? }]` —— 组内页；标题一律来自页面清单（不许硬编码）。 */
  items: { type: Array, default: () => [] },
  /** rail 的 `aria-label`（本组名，如「现场」「人事」）。 */
  label: { type: String, default: '' },
})

const route = useRoute()

/** 认「现在在哪一页」：本级或它的子路径都算（`/prep-plan` 与 `/prep-plan/items`）。
 *
 *  2026-11-17 之前这里比的是路径最后一段（`navKey`），因为组内没有子路由；备货计划
 *  收出模块 tab 之后，`/prep-plan/items` 的最后一段是 `items`，按最后一段比会让左 rail
 *  一页都不亮。改成整段前缀：`/prep-plan-other` 不会被误认成 `/prep-plan`，
 *  尾斜杠也先归一。 */
function isCurrent(page) {
  const path = String(page.path || '').replace(/\/+$/, '')
  const current = String(route.path || '').replace(/\/+$/, '')
  return current === path || current.startsWith(`${path}/`)
}
</script>

<template>
  <nav v-if="items.length > 1" class="wb-rail no-print" :aria-label="label">
    <router-link
      v-for="page in items"
      :key="page.path"
      class="wb-rail-item"
      :class="{ 'is-on': isCurrent(page) }"
      :aria-current="isCurrent(page) ? 'page' : undefined"
      :to="page.path"
    >
      <SvgIcon v-if="page.icon" :name="page.icon" :size="18" />
      <span class="wb-rail-title">{{ page.shortTitle || page.title }}</span>
    </router-link>
    <!-- 本组的附加件（卫生那一组的牌子与「回后台」脚注）：数据与文案留在各自的壳里，
         rail 只给一个落点。 -->
    <slot />
  </nav>
</template>

<style scoped>
/* 桌面档的左 rail。宽度用布局刻度 `--rail-w`（238px，与规范同值）。 */
.wb-rail {
  width: var(--rail-w);
  flex: 0 0 auto;
  position: sticky;
  top: var(--head-h);
  /* 铺满一屏：内容少的页（三张卡的现场日常、空列表）原来只有列表那么高，
     浅橙底在半空断开，看着像一块砖贴在暖白纸上。 */
  align-self: stretch;
  min-height: calc(100vh - var(--head-h));
  max-height: calc(100vh - var(--head-h));
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 8px 14px;
  background: var(--surface-3);
  border-right: var(--edge-w, 2px) solid var(--edge);
}
.wb-rail-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 0 10px;
  border: var(--edge-w, 2px) solid transparent;
  border-radius: var(--r-1);
  color: var(--hy-muted);
  font-size: 13px;
  text-decoration: none;
}
.wb-rail-item:hover { color: var(--hy-ink); background: var(--hy-surface-2); }
/* 选中项是一张贴纸，不是"白底 + 左色条"：旧写法那条 inset 会压在 2px 描边内侧，
   在圆角处切出一道断口。 */
.wb-rail-item.is-on {
  color: var(--ink);
  background: var(--action-soft);
  border-color: var(--edge);
  box-shadow: 3px 3px 0 var(--shadow-ink);
  font-weight: 700;
}
.wb-rail-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 手机档：rail 整个收起来（组内页走页头那个下拉，拇指区走底栏）。 */
@media (max-width: 720px) {
  .wb-rail { display: none; }
}
</style>
