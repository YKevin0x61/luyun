<script setup>
// 现场（卫生验收八页）这一组的落点壳。
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

// 这一行给 rail 与手机档页头一个自己的名字（工作台导航里「现场」那一格的落点与门牌
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
        <p class="hy-rail-note"><b>INSPECTION DESK</b>对照实拍 · 当日验收<br>当日验收 · 红黑榜 · 趋势</p>
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
/* B 案 v2（2026-10-17）：rail 脚注收成一枚小面板 —— kicker / 说明 / 回后台按钮，
   与页面里的面板同一套字号与间距。桌面档的 rail 才是它的位置，手机档整条收起。 */
.hy-rail-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 10px 0;
  border-top: 1px solid var(--line);
}
.hy-rail-note {
  margin: 0;
  font: 500 9.5px/1.7 var(--font-sans);
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--ink-faint);
}
.hy-rail-note b { display: block; margin-bottom: 3px; font-weight: 600; color: var(--ink-dim); }
.hy-back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  font-size: 11px;
  color: var(--ink);
  text-decoration: none;
}
.hy-back:hover { border-color: var(--action); color: var(--action); }
</style>
