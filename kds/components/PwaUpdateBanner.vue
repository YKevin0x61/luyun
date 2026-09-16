<template>
  <view v-if="store.visible" class="pwa-update-banner" role="alert">
    <view class="pwa-update-copy">
      <text class="pwa-update-title">新版本已准备好</text>
      <text class="pwa-update-hint">{{ store.error || '更新后会重新加载当前页面' }}</text>
    </view>
    <view class="pwa-update-actions">
      <button class="pwa-update-primary" :disabled="store.applying" @click="store.apply">
        {{ store.applying ? '更新中…' : '立即更新' }}
      </button>
      <text class="pwa-update-dismiss" @click="store.dismiss">稍后</text>
    </view>
  </view>
</template>

<script setup>
import { usePwaUpdateStore } from '../stores/pwaUpdate.js'

const store = usePwaUpdateStore()
</script>

<style scoped>
.pwa-update-banner {
  position: relative;
  z-index: 80;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid #8ab8e6;
  background: #e7f1fb;
  color: var(--ops-ink);
  box-shadow: 0 4px 14px rgba(11, 107, 203, 0.12);
}

.pwa-update-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pwa-update-title {
  font-size: 14px;
  font-weight: 700;
}

.pwa-update-hint {
  color: var(--ops-muted);
  font-size: 12px;
}

.pwa-update-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.pwa-update-primary {
  min-height: 32px;
  margin: 0;
  padding: 0 14px;
  border: 1px solid var(--ops-accent);
  border-radius: 8px;
  background: var(--ops-accent);
  color: #ffffff;
  font-size: 12px;
  line-height: 30px;
}

.pwa-update-primary[disabled] {
  opacity: 0.65;
}

.pwa-update-dismiss {
  padding: 6px 2px;
  color: var(--ops-muted);
  font-size: 12px;
}

@media (max-width: 520px) {
  .pwa-update-banner {
    align-items: flex-start;
  }

  .pwa-update-hint {
    max-width: 180px;
  }
}
</style>
