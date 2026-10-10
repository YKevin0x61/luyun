<script setup>
// 人事这一组（月历 / 待办 / 班次表 / 花名册 / 人事提醒 / 加班统计）的落点壳。
//
// **③-2 起它只是统一壳的一层薄包装**：顶上那条栏、身份切换器、工作台级导航、退出、手机档
// 页头、底栏、样式表挂载全在 `views/workbench/WorkbenchShell.vue` 里（三个壳共用一份，
// ADR 0105）。这里只留两样本组自己的东西：
//   ① 实时连接点（`App.vue` provide 的 `wsConnected`）—— 排班页要能看出「别人改了会不会
//      自己刷新」，它是人事这一组的附加件；
//   ② 花名册页的页内边距（页面组件自己的样式一个字没动，见下面那段）。
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

/* 花名册（票 05 从卫生那八页搬进人事组）原来套在卫生壳的 `.hy-main` 里，内边距是那一层
   给的；搬到这条窄栏下面之后由这里补上（数字与 `.hy-main` 那条一致），页面组件自己的
   样式一个字没动。其余几页自带内边距，不吃这条。 */
.sched-shell :deep(.roster-page) {
  padding: var(--hy-page);
  padding-left: max(var(--hy-page), env(safe-area-inset-left));
  padding-right: max(var(--hy-page), env(safe-area-inset-right));
}
@media (min-width: 900px) {
  .sched-shell :deep(.roster-page) {
    padding: clamp(1.1rem, 2.2vw, 2rem) clamp(1.2rem, 3vw, 2.6rem) 2rem;
  }
}
</style>
