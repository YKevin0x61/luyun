<script setup>
/**
 * 网站备案号（页脚一行）。
 *
 * 工信部要求网站在首页底部标明 ICP 备案号，并把备案号链到工信部备案系统 ——
 * 号码与链接只写在这一处（登录页与管理端主壳共用），各写一遍迟早漂移。
 */
const ICP_NUMBER = '粤ICP备2025390747号-2'
const ICP_SYSTEM_URL = 'https://beian.miit.gov.cn/'
</script>

<template>
  <div class="icp-footer">
    <a
      class="icp-link"
      :href="ICP_SYSTEM_URL"
      target="_blank"
      rel="noopener noreferrer"
    >{{ ICP_NUMBER }}</a>
  </div>
</template>

<style scoped>
.icp-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 6px 12px;
  font-size: 11px;
  line-height: 1.6;
  /* 承载页可覆写 --icp-footer-ink 换色（登录页的员工栏是另一套浅色 dim）。 */
  color: var(--icp-footer-ink, var(--text-dim));
}

.icp-link {
  color: inherit;
  text-decoration: none;
  text-underline-offset: .2em;
}

.icp-link:hover,
.icp-link:focus-visible {
  color: var(--icp-footer-ink-hover, var(--text));
  text-decoration: underline;
}
</style>
