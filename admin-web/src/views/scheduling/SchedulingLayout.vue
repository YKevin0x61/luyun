<script setup>
// 人事这一组（月历 / 待办 / 班次表 / 花名册）的壳（2026-10-04 从后台壳里独立出来；
// 票 05 按分组落位：花名册从卫生那八页搬进来，顶栏补上本组导航与进「现场」组的门）。
//
// 它仍是**当场干活的界面**：顶上一条自己的窄栏（回后台、本组四页、去现场组、身份切换器、
// 实时状态），页面本体挂在 `<router-view />` 里 —— 页内结构一个不动。后台那条九个模块的
// 导航不在这里渲染（这些页在页面清单里是 `standalone`）。
//
// 组里的页与「现场 ›」那扇门都从**单一来源**来：`utils/workbenchNav.js`（组表 + 页面清单
// 派生），这里不抄第二份路径 —— 两组之间双向可达的另一半在现场壳里（「人事」那扇门）。
import { computed, inject, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow } from '../../router/pageRoutes.js'
import { WORKBENCH_TITLE, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { workbenchGroup, workbenchPagesOf } from '../../utils/workbenchNav'

// 共享的深青墨令牌（跟卫生管理端同一张表，不是卫生模块的东西）。
// 由**壳**加载一次就够了 —— 四个子页本来各加载一份，收进来之后它们不用再管。
useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()
// `App.vue` provide 的实时连接状态（原先显示在后台导航上）。
const connected = inject('wsConnected', null)
const offline = computed(() => connected && connected.value === false)

// 本组四页：路径与名字都从页面清单派生（组里加一页只改那张表）。
const groupPages = workbenchPagesOf('hr')
// 本组与「现场」那一组那两行：门牌与落点都取自组表（不写第二份），去现场那扇门到了
// 那边由那一组的 rail 接手。
const hrGroup = workbenchGroup('hr')
const fieldGroup = workbenchGroup('floor')

function backToAdmin() {
  router.push('/')
}

/** 认「现在在哪一页」只看路径最后一段：不带尾斜杠与带尾斜杠两种写法都认
 *  （跟现场壳同一个口径 —— 绑前缀的写法在票 02 并存期踩过坑，标题会退回第一项）。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const currentKey = computed(() => navKey(route.path))
function isCurrent(page) {
  return navKey(page.path) === currentKey.value
}

// 页面标题也从清单来：花名册这类不自带标题的页面由此拿到名字（它原来由卫生壳给，
// 票 05 跟着分组一起搬过来）。子页自己设标题的那两页照旧，它们设的就是清单里的名字。
watch(
  () => route.path,
  () => {
    const row = pageRow(route.path)
    if (row) document.title = workbenchDocumentTitle(row.title)
  },
  { immediate: true },
)
</script>

<template>
  <div class="hygiene-admin sched-shell">
    <header class="sched-top">
      <button class="sched-back" type="button" @click="backToAdmin()">‹ 后台</button>
      <router-link class="sched-name" :to="hrGroup.to">{{ WORKBENCH_TITLE }}</router-link>
      <nav class="sched-nav" :aria-label="hrGroup.label">
        <router-link
          v-for="page in groupPages"
          :key="page.path"
          class="sched-nav-item"
          :class="{ 'is-on': isCurrent(page) }"
          :aria-current="isCurrent(page) ? 'page' : undefined"
          :to="page.path"
        >{{ page.title }}</router-link>
      </nav>
      <!-- 进「现场」那一组：到了那边由那一组自己的导航接手（双向可达的这一半）。 -->
      <router-link class="sched-field" :to="fieldGroup.to">{{ fieldGroup.label }} ›</router-link>
      <WorkbenchIdentitySwitcher class="sched-id" />
      <!-- 退出入口（票 06）：这一页是独立外壳（`standalone`，后台那条导航不渲染），
           原先没有退出的地方。行为只有一处（`composables/useWorkbenchLogout.js`）。 -->
      <WorkbenchExitButton class="sched-exit" />
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
  min-height: 40px; padding: 4px 12px;
  background: var(--hy-bg);
  border-bottom: 1px solid var(--hy-line);
}
.sched-back {
  flex: 0 0 auto;
  display: inline-flex; align-items: center; gap: 3px;
  font: inherit; font-size: 12px; color: var(--hy-muted);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px; cursor: pointer;
}
.sched-back:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
.sched-name {
  flex: 0 0 auto;
  font-family: var(--font-song); font-size: 14px; letter-spacing: .12em; color: var(--hy-ink);
  text-decoration: none;
}
.sched-name:hover { color: var(--hy-mint); }
/* 本组四页：一格一页，当前那一格亮（判据是路径最后一段，见 `isCurrent`）。 */
.sched-nav { display: flex; align-items: center; gap: 5px; min-width: 0; overflow-x: auto; }
.sched-nav-item {
  flex: 0 0 auto;
  font-size: 12px; color: var(--hy-muted); text-decoration: none;
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: 999px; padding: 4px 11px; white-space: nowrap;
}
.sched-nav-item:hover { color: var(--hy-ink); border-color: var(--hy-line-strong); }
.sched-nav-item.is-on {
  color: var(--hy-mint); border-color: var(--hy-mint-line); background: var(--hy-mint-soft);
}
.sched-field {
  flex: 0 0 auto;
  font-size: 12px; color: var(--hy-mint); text-decoration: none;
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  border-radius: 999px; padding: 4px 11px;
}
.sched-field:hover { border-color: var(--hy-mint); }
/* 身份切换器（票 04 那颗）与实时点贴右端。窄屏下它跟「本组导航」换行：切换器是顶栏里
   最不该被挤出屏幕的一件，所以导航整条另起一行、自己横向滑。 */
.sched-id { flex: 0 0 auto; margin-left: auto; }
.sched-exit { flex: 0 0 auto; }
.sched-conn { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--hy-faint); }
.sched-conn i { width: 7px; height: 7px; border-radius: 50%; background: var(--hy-mint); }
.sched-conn.off { color: var(--hy-seal-bright); }
.sched-conn.off i { background: var(--hy-seal-bright); }

@media (max-width: 720px) {
  .sched-top { flex-wrap: wrap; row-gap: 5px; }
  /* 本组导航整条另起一行（order 放到最后），上面那一行留给两个入口、切换器与实时点。 */
  .sched-nav { order: 1; flex-basis: 100%; }
}
@media (max-width: 560px) {
  /* 牌子让位给切换器与两扇门；子系统名字在页面标题与后台导航里都还在。 */
  .sched-name { display: none; }
}

/* 花名册（票 05 从卫生那八页搬进人事组）原来套在卫生壳的 `.hy-main` 里，内边距是那一层
   给的；搬到这条窄栏下面之后由这里补上（数字与 `.hy-main` 那条一致），页面组件自己的
   样式一个字没动。其余三页自带内边距，不吃这条。 */
.sched-shell > .roster-page {
  padding: var(--hy-page);
  padding-left: max(var(--hy-page), env(safe-area-inset-left));
  padding-right: max(var(--hy-page), env(safe-area-inset-right));
}
@media (min-width: 900px) {
  .sched-shell > .roster-page {
    padding: clamp(1.1rem, 2.2vw, 2rem) clamp(1.2rem, 3vw, 2.6rem) 2rem;
  }
}
</style>
