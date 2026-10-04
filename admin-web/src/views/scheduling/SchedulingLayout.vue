<script setup>
// 排班这一组（月历 / 待办 / 班次表）从后台壳里独立出来（2026-10-04）。
//
// 它是**当场干活的界面**，跟员工端、卫生管理端一个路子：顶上一条自己的窄栏，
// 不再压着后台那条九个模块的导航。后台那条在手机上要占 86px（`theme.css` 的
// `@media (max-width: 720px)` 把它做成两行），排班一进去就少近一成屏 —— 而店长
// 在这页上要的是"这周谁上班"，不是"切去配方/销售报表"。
//
// 壳只做三件事：回后台的入口、页面名、实时连接状态（原先这三样都挂在后台导航上）。
// 三页共用一层壳，跟 `HygieneAdminLayout` 同一个理由：新加排班子页自动就有这套头。
import { computed, inject } from 'vue'
import { useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { WORKBENCH_FIELD_HOME, WORKBENCH_TITLE } from '../../utils/workbenchCopy'

// 共享的深青墨令牌（跟卫生管理端同一张表，不是卫生模块的东西）。
// 由**壳**加载一次就够了 —— 三个子页本来各加载一份，收进来之后它们不用再管。
useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
// `App.vue` provide 的实时连接状态（原先显示在后台导航上）。
const connected = inject('wsConnected', null)
const offline = computed(() => connected && connected.value === false)

function backToAdmin() {
  router.push('/')
}
</script>

<template>
  <div class="hygiene-admin sched-shell">
    <header class="sched-top">
      <button class="sched-back" type="button" @click="backToAdmin()">‹ 后台</button>
      <b class="sched-name">{{ WORKBENCH_TITLE }}</b>
      <!-- 进「现场」那一组（现在的卫生八页）：到了那边由那一组自己的导航接手。 -->
      <router-link class="sched-field" :to="WORKBENCH_FIELD_HOME">现场 ›</router-link>
      <span
        class="sched-conn"
        :class="{ off: offline }"
        :title="offline ? '实时已断开：别人改了排班这一页不会自己刷新' : '实时已连接'"
      ><i aria-hidden="true"></i>{{ offline ? '实时已断开' : '' }}</span>
    </header>
    <router-view />
  </div>
</template>

<style scoped>
/* 顶栏粘住：这一页的滚动容器是 `.app-shell` 的 `.page-body`（standalone 时它是
   `overflow-y: auto` 且没有内边距），所以 sticky 挂在这里正好压在内容上方。 */
.sched-top {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 9px;
  height: 40px; padding: 0 12px;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
.sched-back {
  display: inline-flex; align-items: center; gap: 3px;
  font: inherit; font-size: 12px; color: var(--hy-muted);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px; cursor: pointer;
}
.sched-back:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
.sched-name {
  font-family: var(--font-song); font-size: 14px; letter-spacing: .12em; color: var(--hy-ink);
}
.sched-field {
  font-size: 12px; color: var(--hy-mint); text-decoration: none;
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  border-radius: 999px; padding: 4px 11px;
}
.sched-field:hover { border-color: var(--hy-mint); }
.sched-conn { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--hy-faint); }
.sched-conn i { width: 7px; height: 7px; border-radius: 50%; background: var(--hy-mint); }
.sched-conn.off { color: var(--hy-seal-bright); }
.sched-conn.off i { background: var(--hy-seal-bright); }
</style>
