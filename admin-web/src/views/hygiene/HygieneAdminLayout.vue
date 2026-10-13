<script setup>
// 卫生验收这一组（八页）的落点壳。
//
// **③-2 起它只是统一壳的一层薄包装**（ADR 0105）：顶栏 / 身份 / 退出 / 工作台级导航 /
// 手机档页头 / 底栏 / 样式表挂载都在 `views/workbench/WorkbenchShell.vue` 里。这里只留
// 三样本组自己的东西：
//   ① 本组那份**手写名单**（`HYGIENE_ADMIN_NAV`：八页带图标与短名）—— 交给统一壳的 rail
//      与页头下拉渲染；
//   ② rail 的脚注（造型注记 + 「回后台」）；
//   ③ 页面标题（每页自己的 title 由 `hygieneDocumentTitle` 拼）与离线标准图的更新探针。
//
// 「组内八页」的可见性与 rail 的显隐不在这里判断：条目给了壳，单页组不渲染 rail 的判据
// 在 `components/workbench/WorkbenchRail.vue` 里一处。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import WorkbenchShell from '../workbench/WorkbenchShell.vue'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'
import { workbenchGroup } from '../../utils/workbenchNav'

const route = useRoute()
const standardPhotoCache = useStandardPhotoCacheStore()

// 「卫生验收」那一行给 rail 与手机档页头一个自己的名字（工作台导航里那一格的落点与门牌
// 由那一份组表给）。
const fieldGroup = workbenchGroup('floor')

/** 只取路径最后一段来认「现在在哪一页」：不跟前缀绑死，带尾斜杠的写法也认。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const currentNav = computed(() => (
  HYGIENE_ADMIN_NAV.find((item) => navKey(route.path) === navKey(item.path)) || HYGIENE_ADMIN_NAV[0]
))

watch(currentNav, (item) => {
  document.title = hygieneDocumentTitle(item.title)
}, { immediate: true })

watch(
  () => route.path,
  () => {
    if (standardPhotoCache.initialized) standardPhotoCache.checkForUpdates()
  },
)
</script>

<template>
  <WorkbenchShell
    :items="HYGIENE_ADMIN_NAV"
    :label="`${HYGIENE_BRAND_TITLE} · ${fieldGroup.label}`"
  >
    <!-- rail 的脚注：造型注记 + 回后台那条门（桌面档的 rail 里才看得见，
         手机档整条 rail 收起来，出口在页头那个 `⋮` 里）。 -->
    <template #rail>
      <div class="hy-rail-foot">
        <p class="hy-rail-note">对照实拍 · 当日验收<br>INSPECTION DESK</p>
        <router-link class="hy-back" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
          {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
        </router-link>
      </div>
    </template>

    <!-- 页面本体：`.hy-main` 的横向与上内边距由壳按 group 给（票 04），最大宽度与**下**内边距
         只有共享样式表（`public/hygiene-admin.css` 的 `.hygiene-admin .hy-main`）这一处
         —— 本组壳不再出内边距（票 08 删掉了那条手机档的重复让位，见下面的 style）。 -->
    <div id="hygiene-admin-main" class="hy-main">
      <router-view />
    </div>
  </WorkbenchShell>
</template>

<style scoped>
.hy-rail-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 10px 0;
}
.hy-rail-note {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.6;
  letter-spacing: .04em;
  color: var(--hy-faint);
}
.hy-back {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  font-size: 12px;
  color: var(--hy-muted);
  text-decoration: none;
}
.hy-back:hover { color: var(--hy-ink); }

/* 现场八页**不再自己让开底栏**（票 08）。手机档底栏（`WorkbenchTabBar`）是 `fixed`，那
   57px + 安全区由统一壳算成一个数挂在内容容器上（`--wb-inset-bottom` → `.wb-main` 的下
   内边距，ADR 0106 决策 2），页面侧再补一份就是**叠加**：这里原来那条
   `@media (max-width: 720px) { .wb-shell :deep(.hy-main) { … } }` 就在 `--hy-page` 之上
   又补了一次 57px + 安全区，390px 实测底部空白 70.6 + 57 = 127.6px（比需要多 57px），已删。
   `.hy-main` 的下内边距只剩共享样式表那份「页面正文自己的收尾留白」：`<900px` 一律取
   `--hy-page`（390 → 13.6px / 800 → 16px），≥900px = `2rem`。
   当年钩子用 `.wb-shell` 而**不是**旧卫生壳那个 `hygiene-app` 类，是因为后者在共享样式表里
   还带着 `<900px` 的整屏锁与 `≥900px` 的 `flex-direction: row`（挂到统一壳上会把顶栏与
   内容排成左右两栏，2026-10-10 实测）—— 这条规则删了，那个钩子也别再挂回去。 */
</style>
