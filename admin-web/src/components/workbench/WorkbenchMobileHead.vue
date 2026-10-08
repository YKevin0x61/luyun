<script setup>
// 工作台三套壳共用的**手机档页头**（方案 C，2026-10-08 用户裁定）。
//
// 为什么要有它：手机档顶部原来可能摞着两条横带 —— 工作台壳的 `hy-header` / `wb-top`
// （身份 / 退出 / 后台，49–87px），加上组内那一条（卫生是八格的横滑带 93px；产品组更狠，
// 配方页自己还带一条 133px 的 `header.site-header` 白条）。两块一叠就是「两条黑带」，
// 首屏 180px 上下没了，而组里的页在 390px 下一屏本来也摆不下。方案 C 把组内导航整个收进
// 一个下拉，顶部只剩一行：
//   `[组名] [当前页 ▾] ……… [● 身份] [⋮]`
// 56px，剩下的全还给内容。
//
// 三件事各有各的出口，一个都没丢：
//   - **换页**（原来横滑带 / 白条上的入口）→ 点页名展开那一组的页面列表（当前页高亮）；
//   - **身份**（原来顶栏那颗）→ 直接挂 `WorkbenchIdentitySwitcher`，它自己是收起态 + 菜单；
//   - **退出 / 回后台**（原来顶栏那两颗按钮）→ 收进 `⋮`。
//
// **只在手机档渲染**（`max-width: 720px`）：桌面档是左 rail / 顶栏那条 tab，空间宽裕，
// 横滑带与白条的问题根本不存在。
//
// 页名与组名都从 `utils/workbenchNav.js` 那张表来（不在这里写第二份）：
// 组由当前路径判（`workbenchGroupOf`），组里的页由页面清单派生（`workbenchPagesOf`）。
// 调用方可以传 `items` 覆盖那份名单（卫生壳传自己那份带图标的 `HYGIENE_ADMIN_NAV`）。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import SvgIcon from '../SvgIcon.vue'
import WorkbenchIdentitySwitcher from './WorkbenchIdentitySwitcher.vue'
import { useWorkbenchLogout } from '../../composables/useWorkbenchLogout'
import { pageRow } from '../../router/pageRoutes.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { workbenchGroup, workbenchGroupOf, workbenchPagesOf } from '../../utils/workbenchNav'
import { IDENTITY_STAFF } from '../../utils/workbenchIdentity'

const props = defineProps({
  /** 组内页面名单（可选）：不传就从页面清单按当前组派生。传的项可以有 `icon` / `code`。 */
  items: { type: Array, default: null },
})

const route = useRoute()
const identityStore = useWorkbenchIdentityStore()
const { ask, exit, confirmOpen, queuedCount, cancel } = useWorkbenchLogout()

/** 员工那一档不渲染「回管理后台」（D7，与 `WorkbenchLayout` 的 `showBackToAdmin` 同一条
 *  判据）：那一扇门通向 `/` —— 店长专属的运营仪表盘，员工点下去只会吃一张「无权访问」。
 *  挂着一扇**必然被拒**的门比没有门更糟：它天天在那儿，点一次损失一次。 */
const showBackToAdmin = computed(() => identityStore.identity !== IDENTITY_STAFF)

/** 只取路径最后一段认「现在在哪一页」——与各壳里那条导航同一套判据（不跟前缀绑死）。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const groupKey = computed(() => workbenchGroupOf(route.path))
const group = computed(() => workbenchGroup(groupKey.value))
/** 组名：拿不到组（清单里没有的路径，比如测试里的临时路由）时整条不渲染页面那一段。 */
const groupLabel = computed(() => (group.value ? group.value.label : ''))

const pages = computed(() => {
  if (props.items && props.items.length) return props.items
  return groupKey.value ? workbenchPagesOf(groupKey.value) : []
})

/** 这一刻站的那一页：**页面清单优先**。
 *
 *  不能只在组内名单里找 —— 像「配方详情」「配方打印」这种是**从列表点进去**的功能页，
 *  它们不在入口名单里（名单只放能当入口的页），但在站在它们上面时页头仍要报出正确的页名；
 *  只查名单会回退到第一项（实测显示成「选择岗位」，与本页不符）。
 *  清单里也没有（测试里的临时路由）才回退到名单第一项。 */
const current = computed(() => {
  const row = pageRow(route.path)
  if (row) return { path: route.path, title: row.title }
  return pages.value.find((item) => navKey(item.path) === navKey(route.path)) || pages.value[0] || null
})

/** 只有一页的组（「今天」就是）不给下拉：点开是空的比不显示更糟。 */
const canSwitch = computed(() => pages.value.length > 1)

/** 组名那个小标签：组名与页名一个字不差时（「今天」那一格）不把同一个词写两遍。 */
const showGroupTag = computed(
  () => Boolean(groupLabel.value) && (!current.value || current.value.title !== groupLabel.value),
)

const pagesOpen = ref(false)
const moreOpen = ref(false)

function closeAll() {
  pagesOpen.value = false
  moreOpen.value = false
}

/** 两个浮层互斥：点页名时先把 `⋮` 那个收掉，反之亦然（同屏开两个面板会叠在一起）。 */
function togglePages() {
  if (!canSwitch.value) return
  const next = !pagesOpen.value
  closeAll()
  pagesOpen.value = next
}

function toggleMore() {
  const next = !moreOpen.value
  closeAll()
  moreOpen.value = next
}

function onExit() {
  closeAll()
  void ask()
}

/** ESC 关浮层（键盘 / 外接键盘的场景）。 */
function onKeydown(event) {
  if (event.key === 'Escape') closeAll()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header v-if="group" class="wmh">
    <span v-if="showGroupTag" class="wmh-grp">{{ groupLabel }}</span>

    <button
      class="wmh-page"
      :class="{ 'is-static': !canSwitch }"
      type="button"
      :aria-haspopup="canSwitch ? 'true' : undefined"
      :aria-expanded="canSwitch ? String(pagesOpen) : undefined"
      :aria-label="canSwitch ? `切换页面，当前：${current ? current.title : groupLabel}` : undefined"
      @click="togglePages"
    >
      <span class="wmh-page-name">{{ current ? current.title : groupLabel }}</span>
      <SvgIcon v-if="canSwitch" class="wmh-caret" name="chevron-down" :size="14" />
    </button>

    <span class="wmh-sp"></span>

    <WorkbenchIdentitySwitcher class="wmh-id" />

    <button
      class="wmh-more"
      type="button"
      aria-haspopup="true"
      :aria-expanded="String(moreOpen)"
      aria-label="更多：退出 / 回管理后台"
      @click="toggleMore"
    >
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <circle cx="12" cy="5.4" r="1.7" fill="currentColor" />
        <circle cx="12" cy="12" r="1.7" fill="currentColor" />
        <circle cx="12" cy="18.6" r="1.7" fill="currentColor" />
      </svg>
    </button>

    <!-- 组里的页：原来那条横滑带 / 白条上的入口，收到这儿。 -->
    <div v-if="pagesOpen" class="wmh-panel wmh-pages" role="menu">
      <p class="wmh-panel-hd">{{ groupLabel }} · {{ pages.length }} 个页面</p>
      <router-link
        v-for="item in pages"
        :key="item.path"
        class="wmh-item"
        :class="{ 'is-on': navKey(item.path) === navKey(route.path) }"
        :to="item.path"
        role="menuitem"
        @click="closeAll"
      >
        <SvgIcon v-if="item.icon" :name="item.icon" :size="18" />
        <span class="wmh-item-name">{{ item.title }}</span>
        <span v-if="item.code" class="wmh-item-code">{{ item.code }}</span>
      </router-link>
    </div>

    <!-- 退出 / 回后台：原来顶栏那两颗按钮，收到这儿。 -->
    <div v-if="moreOpen" class="wmh-panel wmh-more-menu" role="menu">
      <button class="wmh-item wmh-item-btn" type="button" role="menuitem" @click="onExit">
        <SvgIcon name="x-circle" :size="18" />
        <span class="wmh-item-name">退出登录</span>
      </button>
      <router-link v-if="showBackToAdmin" class="wmh-item" to="/" role="menuitem" @click="closeAll">
        <SvgIcon name="home" :size="18" />
        <span class="wmh-item-name">回管理后台</span>
      </router-link>
    </div>

    <!-- 点浮层外面关掉：一层遮罩而不是 document 监听 —— 手机上它同时把内容压暗，
         一眼能看出"现在点的是浮层"。 -->
    <div v-if="pagesOpen || moreOpen" class="wmh-backdrop" @click="closeAll"></div>

    <ConfirmDialog
      v-if="confirmOpen"
      title="还有照片没传完"
      :message="`还有 ${queuedCount} 张照片在上传队列里。退出登录会清掉它们，需要重新拍。`"
      confirm-label="仍然退出"
      danger
      @confirm="exit"
      @cancel="cancel"
    />
  </header>
</template>

<style scoped>
/* 桌面档不渲染这一条：那里是左 rail / 顶栏那条 tab，空间宽裕。 */
.wmh { display: none; }

@media (max-width: 720px) {
  .wmh {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
    /* 浮层要相对这一条定位；z-index 压在内容之上、底栏（15）之上。 */
    position: relative;
    z-index: 20;
    min-height: 56px;
    padding: 6px max(12px, env(safe-area-inset-right)) 6px max(12px, env(safe-area-inset-left));
    background: rgba(8, 19, 21, .96);
    border-bottom: 1px solid var(--hy-line);
  }
}

/* 组名：一个安静的标签，不做链接（回组落点 / 首页的入口在底栏那几格）。 */
.wmh-grp {
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 8px;
  background: rgba(133, 205, 198, .08);
  color: var(--hy-faint);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

/* 当前页 = 这一行里最重的东西：它同时是"我在哪"和"换页"的入口。 */
.wmh-page {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--hy-mint-line);
  border-radius: 12px;
  background: var(--hy-mint-soft);
  color: var(--hy-ink);
  font: inherit;
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
/* 组里只有一页（「今天」）：它是标题不是按钮，别画成可点的样子。 */
.wmh-page.is-static { cursor: default; }
.wmh-page-name { max-width: 7.5em; overflow: hidden; text-overflow: ellipsis; }
.wmh-caret { color: var(--hy-mint); }

.wmh-sp { flex: 1 1 auto; min-width: 0; }

/* 身份那一颗（`WorkbenchIdentitySwitcher`）：手机上这一行放不下「共享账号」那个后缀，
   收掉它 —— 页头里只有一处身份，不存在跟谁混淆。 */
.wmh-id :deep(.wb-id-hint) { display: none; }
.wmh-id :deep(.wb-id-current) { padding: 0 6px; }

.wmh-more {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 10px;
  background: none;
  color: var(--hy-muted);
  cursor: pointer;
}
.wmh-more:hover { background: var(--hy-surface-2); color: var(--hy-ink); }

.wmh-panel {
  position: absolute;
  top: calc(100% - 2px);
  z-index: 41;
  padding: 6px;
  background: var(--hy-surface);
  border: 1px solid var(--hy-line-strong);
  border-radius: 14px;
  box-shadow: var(--hy-shadow-md);
}
.wmh-pages { left: 8px; right: 8px; }
.wmh-more-menu { right: 8px; min-width: 190px; }

.wmh-panel-hd {
  margin: 0;
  padding: 8px 12px 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--hy-faint);
}

.wmh-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 48px;
  padding: 0 12px;
  border: 0;
  border-radius: 10px;
  background: none;
  color: var(--hy-muted);
  font: inherit;
  font-size: 14.5px;
  font-weight: 600;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}
.wmh-item:hover { background: var(--hy-surface-2); color: var(--hy-ink); }
.wmh-item.is-on { background: var(--hy-mint-soft); color: var(--hy-mint); }
.wmh-item-code {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: .12em;
  color: var(--hy-faint);
}
.wmh-item.is-on .wmh-item-code { color: var(--hy-mint); }

.wmh-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(5, 13, 15, .55);
}
</style>
