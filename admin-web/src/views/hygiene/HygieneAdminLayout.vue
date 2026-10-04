<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import StandardPhotoCachePanel from '../../components/hygiene/StandardPhotoCachePanel.vue'
import SvgIcon from '../../components/SvgIcon.vue'
import WorkbenchExitButton from '../../components/workbench/WorkbenchExitButton.vue'
import WorkbenchIdentitySwitcher from '../../components/workbench/WorkbenchIdentitySwitcher.vue'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import { WORKBENCH_FIELD_HOME } from '../../utils/workbenchCopy'
import {
  HYGIENE_ADMIN_NAV,
  HYGIENE_BACK_TO_ADMIN_LABEL,
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'
import { workbenchGroup } from '../../utils/workbenchNav'

useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const standardPhotoCache = useStandardPhotoCacheStore()

// 「人事」那一组那一行：rail 最后一格那扇门（两组双向可达的这一半）的落点与名字从组表来，
// 花名册票 05 起是人事页（`/workbench/hr/roster`），不在这条 rail 上。「现场」那一行给
// rail 一个自己的名字。
const hrGroup = workbenchGroup('hr')
const fieldGroup = workbenchGroup('floor')

/** 只取路径最后一段来认"现在在哪一页"：不跟前缀绑死，`/workbench/floor/daily` 与带尾斜杠的
 *  `/workbench/floor/daily/` 都认。绑前缀的写法在票 02 并存期踩过坑 —— 标题会退回第一项、
 *  底部导航也不高亮（那一版就是这样被验证抓出来的）。 */
function navKey(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : ''
}

const currentNav = computed(() => (
  HYGIENE_ADMIN_NAV.find((item) => navKey(route.path) === navKey(item.path)) || HYGIENE_ADMIN_NAV[0]
))

function navIndex(item) {
  return String(HYGIENE_ADMIN_NAV.indexOf(item) + 1).padStart(2, '0')
}

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
  <div class="hygiene-admin hygiene-app">
    <a class="hy-skip" href="#hygiene-admin-main">跳到内容</a>

    <nav class="hy-tabbar" :aria-label="`${HYGIENE_BRAND_TITLE} · ${fieldGroup.label}`">
      <router-link class="hy-brand hy-brand-rail" :to="WORKBENCH_FIELD_HOME">
        <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
        <span class="hy-brand-text">
          <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
          <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
        </span>
      </router-link>

      <div class="hy-rail-items">
        <!-- `router-link` 自动高亮是按 `to` 的字面路径比的：并存期在新前缀（`/workbench/...`）
             上它认不出来，所以再按"最后一段"补一次高亮 —— 两个前缀下都亮，票 05 之后也照旧。 -->
        <router-link
          v-for="item in HYGIENE_ADMIN_NAV"
          :key="item.path"
          class="hy-tab"
          :class="{ 'router-link-active': navKey(item.path) === navKey(route.path) }"
          :to="item.path"
        >
          <SvgIcon :name="item.icon" :size="19" />
          <span class="hy-tab-full">{{ item.title }}</span>
          <span class="hy-tab-short">{{ item.shortTitle }}</span>
          <span class="hy-rail-index">{{ navIndex(item) }}</span>
        </router-link>
        <!-- 回「人事」那一组（票 05）：两组双向可达的这一半。它不是现场页 —— 现场壳的
             rail 里票 05 起没有花名册，回人事组走这里。 -->
        <router-link class="hy-tab hy-tab-cross" :to="hrGroup.to" :title="hrGroup.label">
          <SvgIcon name="clipboard" :size="19" />
          <span class="hy-tab-full">{{ hrGroup.label }}</span>
          <span class="hy-tab-short">{{ hrGroup.label }}</span>
        </router-link>
      </div>

      <div class="hy-rail-foot">
        <p class="hy-rail-note">对照实拍 · 当日验收<br>INSPECTION DESK</p>
        <router-link class="hy-back" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
          {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
        </router-link>
      </div>
    </nav>

    <div class="hy-shell">
      <header class="hy-header">
        <div class="hy-header-inner">
          <router-link class="hy-brand hy-brand-top" :to="WORKBENCH_FIELD_HOME">
            <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
            <span class="hy-brand-text">
              <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
              <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
            </span>
          </router-link>
          <p class="hy-crumb">
            {{ currentNav.title }}
            <code>{{ navIndex(currentNav) }} · {{ currentNav.code }}</code>
          </p>
          <!-- 身份切换器（票 04 那颗，自包含）：店长在自己的页面里也看得到那两档。 -->
          <WorkbenchIdentitySwitcher class="hy-id" />
          <!-- 退出入口（票 06）：现场这七页也是独立外壳，原先一个退出按钮都没有。
               行为只有一处（`composables/useWorkbenchLogout.js`），票 10 统一四处登出。 -->
          <WorkbenchExitButton class="hy-exit" />
          <router-link class="hy-back hy-back-top" to="/" :title="HYGIENE_BACK_TO_ADMIN_LABEL">
            {{ HYGIENE_BACK_TO_ADMIN_LABEL }}
          </router-link>
        </div>
      </header>
      <main id="hygiene-admin-main" class="hy-main">
        <router-view />
      </main>
    </div>
    <StandardPhotoCachePanel />
  </div>
</template>

<style scoped>
/* 退出入口（票 06）：贴在「后台」那颗旁边，窄屏下跟它一起让位给切换器。 */
.hy-exit { flex: 0 0 auto; }
/* 回人事组那扇门：桌面 rail 里跟现场七页之间一条分隔（它不是现场页）；手机上 rail 是
   底部两行网格，那一格跟别的 tab 一样，不加分隔线。 */
@media (min-width: 900px) {
  .hy-tab-cross { margin-top: .6rem; border-top: 1px solid var(--hy-line); }
}
/* 顶栏窄屏时把品牌那句副题收起来，给切换器与「后台」让位（牌子本身还在）。 */
@media (max-width: 720px) {
  .hy-brand-tagline { display: none; }
}
</style>
