<script setup>
// 工作台外壳（票 03 立、票 04 接上身份、③-2 收进统一壳）。
//
// **这一页现在只剩一句**：本组的附加件都没有，页面本体挂在统一壳里（`WorkbenchShell`，
// `views/workbench/WorkbenchShell.vue`）。壳的规矩、两个视觉变体、手机档那一条都在那儿 ——
// 三个壳（工作台 / 人事 / 卫生验收）共用同一个实现，这里不再各写一份（ADR 0105）。
//
// 它服务的是「今天」（首页）、后勤（配方 / 备货计划）、「我的」（今天 / 整月 / 加班与补钟）
// 与「入口」这几组；本组由壳按当前路径算出来，这里不传 `group`。
import WorkbenchShell from './WorkbenchShell.vue'
</script>

<template>
  <WorkbenchShell>
    <router-view />
  </WorkbenchShell>
</template>
