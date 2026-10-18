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

<style scoped>
/* B 案 v2（2026-10-17）：入口壳去掉共享表那层 hero（径向渐变 / 角标 / 毛玻璃），
   收成一块钢面面板：1px 边 + 小圆角 + 一点投影。颜色/字体只走语义令牌。
   选择器带 `.hy-staff-card` 双类，稳定压过共享表同特异度的规则。 */
/* v3：入口卡从「垂直居中」改成顶部对齐（顶留 28px），与工作台其它页的页头起点一致 */
.hy-staff-main.hy-staff-main {
  align-items: flex-start;
  padding-top: max(28px, var(--safe-t));
}
.hy-staff-card.hy-staff-card {
  margin: 0 auto;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: 0 1px 2px var(--wb-ink-a08);
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
  overflow: visible;
  padding: 20px 18px 18px;
}
.hy-staff-card.hy-staff-card::before,
.hy-staff-card.hy-staff-card::after { display: none; }
.hy-staff-card .hy-brand { padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid var(--line); }
.hy-staff-card .hy-brand-title { font: 600 15px/1.2 var(--font-sans); letter-spacing: .04em; color: var(--ink); }
.hy-staff-card .hy-brand-tagline { font: 500 9.5px/1.6 var(--font-mono); letter-spacing: .14em; color: var(--ink-faint); }
.hy-staff-card :deep(.btn.btn) { min-height: 48px !important; border-radius: var(--r-1); }
@media (max-width: 720px) {
  .hy-staff-card.hy-staff-card { padding: 16px 14px 14px; }
}
</style>
