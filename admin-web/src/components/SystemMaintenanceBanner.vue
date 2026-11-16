<template>
  <div class="maintenance-banner" role="status" aria-live="polite">
    <span class="maintenance-dot" aria-hidden="true" />
    <span class="maintenance-text">
      <span class="maintenance-message">{{ message }}</span>
      <span class="maintenance-hint">期间可离开此页，完成后自动消失。</span>
    </span>
  </div>
</template>

<script setup>
defineProps({
  message: { type: String, default: '' },
})
</script>

<style scoped>
/* 更新 / 整库恢复期间全站可见的一条提示。用 fixed 而不是塞进页面流：
   工作台各页与登录页的根容器各自锁了 100vh，插入兄弟节点会把它们挤出屏幕。
   位置贴顶居中，避开右上角 PWA 更新提示，也不挡底部员工底栏。 */
.maintenance-banner {
  position: fixed;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: calc(var(--z-queue) + 2);
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: min(720px, calc(100vw - 24px));
  padding: 9px 14px;
  border: 1px solid var(--yellow);
  border-radius: 999px;
  background: var(--card);
  box-shadow: 0 12px 30px color-mix(in srgb, var(--border) 55%, transparent);
  color: var(--yellow);
  font-size: 12px;
  line-height: 1.45;
  pointer-events: none;
}

.maintenance-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--yellow);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--yellow) 22%, transparent);
  animation: maintenance-pulse 1.6s ease-in-out infinite;
}

.maintenance-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.maintenance-hint {
  font-size: 11px;
  opacity: 0.78;
}

@keyframes maintenance-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

@media (max-width: 560px) {
  .maintenance-banner {
    border-radius: 12px;
    font-size: 12px;
  }
}
</style>
