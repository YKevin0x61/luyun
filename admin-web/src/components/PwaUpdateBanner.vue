<template>
  <aside v-if="visible" class="pwa-update-banner" role="status" aria-live="polite">
    <div class="pwa-update-copy">
      <strong>新版本已准备好</strong>
      <span>{{ error || '更新后会重新加载当前页面' }}</span>
    </div>
    <div class="pwa-update-actions">
      <button type="button" class="pwa-update-primary" :disabled="busy" @click="$emit('apply')">
        {{ busy ? '更新中…' : '立即更新' }}
      </button>
      <button type="button" class="pwa-update-secondary" :disabled="busy" @click="$emit('dismiss')">
        稍后
      </button>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  visible: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

defineEmits(['apply', 'dismiss'])
</script>

<style scoped>
.pwa-update-banner {
  position: fixed;
  z-index: 1200;
  top: 72px;
  right: 18px;
  width: min(420px, calc(100vw - 36px));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 16px;
  border: 1px solid rgba(99, 102, 241, 0.5);
  border-radius: 10px;
  background: #161c2e;
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.38);
  color: var(--text);
}

.pwa-update-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.pwa-update-copy strong {
  font-size: 13px;
}

.pwa-update-copy span {
  color: var(--text-dim);
  font-size: 11px;
  line-height: 1.45;
}

.pwa-update-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.pwa-update-actions button {
  min-height: 32px;
  padding: 0 12px;
  border-radius: 6px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.pwa-update-actions button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.pwa-update-primary {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
}

.pwa-update-secondary {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
}

@media (max-width: 640px) {
  .pwa-update-banner {
    top: auto;
    right: 12px;
    bottom: 12px;
    left: 12px;
    width: auto;
    align-items: flex-start;
  }
}
</style>
