<script setup>
// 备货计划的模块壳（2026-11-17 用户裁定）：计划 / 备货品 / 换算规则 / 复盘 四个子页
// 共用一条模块 tab。
//
// 为什么把 tab 放在这里而不是四个页面各画一遍：这四个地址本来就是同一件事的分面
// （计划看参考值、复盘看准不准、另外两页维护它依赖的数据），过去是四扇并列的门，
// 用户的原话是「入口太杂」。收成一层 tab 之后，工作台只留一个「备货计划」入口。
//
// 权限过滤与工作台导航用同一个判据（`pageVisibleFor`）：员工只看到「计划」，
// 勾了复盘能力的员工多看到「复盘」，备货品 / 换算规则只给管理端。没权限的 tab
// 不渲染、不置灰 —— 置灰的入口在手机上就是一行点不动的噪音。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import WorkbenchShell from '../workbench/WorkbenchShell.vue'
import { pageMeta } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { pageVisibleFor, workbenchAudienceFor } from '../../utils/workbenchNav'
import { PREP_PLAN_PATH, PREP_REVIEW_PATH } from '../../utils/prepPlanPaths'
import { PREP_RULES_PATH } from '../../utils/prepRulePaths'
import { PREP_ITEMS_PATH } from '../../utils/prepItemPaths'

const route = useRoute()
const identityStore = useWorkbenchIdentityStore()
const audience = computed(() => workbenchAudienceFor(identityStore.identity))
const caps = computed(() => identityStore.staffCaps)

// 顺序是用户定的（计划 / 备货品 / 换算规则 / 复盘）：先看今天要备什么，再看它依赖的
// 两份主数据，最后才看昨天备得准不准。
const ALL_TABS = [
  { to: PREP_PLAN_PATH, label: '计划' },
  { to: PREP_ITEMS_PATH, label: '备货品' },
  { to: PREP_RULES_PATH, label: '换算规则' },
  { to: PREP_REVIEW_PATH, label: '复盘' },
]

const tabs = computed(() =>
  ALL_TABS.filter((tab) => pageVisibleFor(pageMeta(tab.to), audience.value, caps.value)),
)

/** 只认精确地址：`/prep-plan` 是父路由，不能因为它是 `/prep-plan/items` 的前缀
 *  就把「计划」也点亮。 */
const isActive = (tab) => route.path === tab.to
</script>

<template>
  <WorkbenchShell>
    <div v-if="tabs.length > 1" class="pp-tabs-row">
      <nav class="pp-tabs" aria-label="备货">
        <router-link
          v-for="tab in tabs"
          :key="tab.to"
          :to="tab.to"
          class="pp-tab"
          :class="{ 'is-on': isActive(tab) }"
          :aria-current="isActive(tab) ? 'page' : undefined"
        >
          {{ tab.label }}
        </router-link>
      </nav>
    </div>
    <router-view />
  </WorkbenchShell>
</template>

<style scoped>
/* 内容顶部、不吸顶（用户裁定）：往上滚时让位给内容，多一条常驻横条只会更挤。
   宽度与计划页的 `.pp` 对齐（980 / 12px），复盘页自己的 16px 内边距会让页头比
   tab 往右缩 4px —— 这是四个页面历史内边距不一致，先按主页面（计划）对齐。

   2026-11-17 用户说「这个 tab 不是很好看」：原来的四条下划线像一排裸文字，
   和下面的卡片页头没有关系。改成一颗浅底的分段控件（segmented control）：
   整颗宽度只包住四个词，当前项是浮在浅灰底上的白药丸；四个页面仍是一级页面，
   只是把「同一件事的分面」收进一颗控件里。 */
.pp-tabs-row {
  width: 100%;
  max-width: 980px;
  margin: 0 auto;
  padding: 10px 12px 0;
  box-sizing: border-box;
}

.pp-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  width: max-content;
  max-width: 100%;
  margin: 0 0 12px;
  padding: 4px;
  box-sizing: border-box;
  background: var(--surface-2);
  border: var(--edge-w, 2px) solid var(--edge);
  /* 2px 描边 + 4px 内边距下，20px 圆角在 44px 高的条上等于胶囊 —— 与贴纸格调不合。
     压到 12px，选中项用 2px 内描边示意"这一格是按下的"。 */
  border-radius: var(--r-1);
  overflow-x: auto;
  scrollbar-width: none;
}

.pp-tabs::-webkit-scrollbar {
  display: none;
}

.pp-tab {
  flex: 0 0 auto;
  padding: 9px 18px;
  border-radius: var(--r-1);
  color: var(--ink-dim);
  font-size: 13.5px;
  line-height: 1.2;
  text-decoration: none;
  white-space: nowrap;
  transition: color .16s ease, background .16s ease;
}

.pp-tab:hover {
  color: var(--ink);
}

.pp-tab.is-on {
  color: var(--ink);
  background: var(--surface-1);
  font-weight: 700;
  box-shadow: inset 0 0 0 2px var(--edge);
}

@media (max-width: 720px) {
  .pp-tabs-row {
    padding: 8px 12px 0;
  }

  .pp-tabs {
    width: 100%;
    margin: 0 0 14px;
    padding: 3px;
  }

  .pp-tab {
    flex: 1 0 0;
    padding: 9px 0;
    font-size: 13px;
    text-align: center;
  }
}
</style>
