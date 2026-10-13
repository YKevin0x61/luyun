<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import IcpFooter from '../../components/IcpFooter.vue'
import {
  HYGIENE_BRAND_MARK,
  HYGIENE_BRAND_TAGLINE,
  HYGIENE_BRAND_TITLE,
  hygieneDocumentTitle,
} from '../../utils/hygieneCopy'

// 共享样式表（`hygiene-admin.css`）不再由这里挂：这一页住在顶层路径，靠页面清单里那行
// `workbenchTheme: true` 从**文档层**拿到（`utils/workbenchDocument.js`，路由一处安装）。

const route = useRoute()
const pageTitle = computed(() => route.meta.staffPageTitle || HYGIENE_BRAND_TITLE)

watch(pageTitle, (title) => {
  document.title = hygieneDocumentTitle(title)
}, { immediate: true })
</script>

<template>
  <div class="hygiene-staff">
    <a class="hy-skip" href="#hygiene-staff-main">跳到内容</a>
    <main id="hygiene-staff-main" class="hy-staff-main">
      <div class="hy-staff-card">
        <div class="hy-brand hy-staff-brand">
          <span class="hy-brand-mark" aria-hidden="true">{{ HYGIENE_BRAND_MARK }}</span>
          <span class="hy-brand-text">
            <span class="hy-brand-title">{{ HYGIENE_BRAND_TITLE }}</span>
            <span class="hy-brand-tagline">{{ HYGIENE_BRAND_TAGLINE }}</span>
          </span>
        </div>
        <router-view />
      </div>
    </main>
    <!-- 备案号：`/register`（员工自助注册）是除 `/login` 之外另一个公开入口，同样要标。
         颜色跟着这一层的 `--text-dim` 走（本壳把它重定义成了浅青 `--hy-muted`）。 -->
    <IcpFooter />
  </div>
</template>
