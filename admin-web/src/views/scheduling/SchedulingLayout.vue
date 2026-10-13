<script setup>
// 人事这一组（月历 / 待办 / 班次表 / 花名册 / 人事提醒 / 加班统计）的落点壳。
//
// **③-2 起它只是统一壳的一层薄包装**：顶上那条栏、身份切换器、工作台级导航、退出、手机档
// 页头、底栏、样式表挂载全在 `views/workbench/WorkbenchShell.vue` 里（三个壳共用一份，
// ADR 0105）。这里只留本组自己的东西：
//   ① 实时连接点（`App.vue` provide 的 `wsConnected`）—— 排班页要能看出「别人改了会不会
//      自己刷新」，它是人事这一组的附加件。
// 花名册那条补内边距在票 04 退场（见下面那段注释）：值与页面组件的样式一起走。
import { computed, inject } from 'vue'
import WorkbenchShell from '../workbench/WorkbenchShell.vue'

// `App.vue` provide 的实时连接状态。
const connected = inject('wsConnected', null)
const offline = computed(() => connected && connected.value === false)
</script>

<template>
  <WorkbenchShell class="sched-shell">
    <template #meta>
      <span
        class="sched-conn"
        :class="{ off: offline }"
        :title="offline ? '实时已断开：别人改了排班这一页不会自己刷新' : '实时已连接'"
      ><i aria-hidden="true"></i>{{ offline ? '实时已断开' : '' }}</span>
    </template>
    <router-view />
  </WorkbenchShell>
</template>

<style scoped>
/* 实时点：贴在顶栏右端（退出按钮之前）。手机档那条顶栏整个收起来，它就跟着收 ——
   手机上顶栏只有 56px 一行（组名 / 当前页 / 身份 / ⋮），塞不进第四件。 */
.sched-conn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--hy-faint);
}
.sched-conn i { width: 7px; height: 7px; border-radius: 50%; background: var(--hy-mint); }
.sched-conn.off { color: var(--hy-seal-bright); }
.sched-conn.off i { background: var(--hy-seal-bright); }

/* 花名册那条补内边距（票 05 从卫生搬过来时补的 `:deep(.roster-page)`）随票 04 退场：
   组壳只剩本组自己的东西（实时点）。它的**数字一字不差**地落在花名册页面自己的样式里
   （`views/hygiene/HygieneRosterView.vue`）—— 人事组里另外五页的根自带内边距
   （`18px 16px 40px` 那一档），组级给一个非零值会把它们推走 4–14px，所以壳给人事组 0、
   这一页的值跟着它自己走。 */
</style>
