<script setup>
// 数据量横向条：按最大值等比（pct 由调用方算好），真实数字始终以文字给出，
// 小值即使条宽接近 0 也能读到数量。
defineProps({
  label: { type: String, required: true },
  display: { type: String, default: '—' },
  pct: { type: Number, default: 0 },
  ariaLabel: { type: String, required: true },
})
</script>

<template>
  <div class="usage" role="img" :aria-label="ariaLabel">
    <span class="usage__label">{{ label }}</span>
    <span class="usage__track" aria-hidden="true">
      <span class="usage__fill" :style="{ width: `${pct}%` }"></span>
    </span>
    <span class="usage__value">{{ display }}</span>
  </div>
</template>

<style scoped>
.usage {
  display: grid;
  grid-template-columns: minmax(64px, 88px) minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 32px;
  font-size: 12px;
}
.usage__label { color: var(--text-dim); }
.usage__track {
  position: relative;
  height: 10px;
  border-radius: 999px;
  background: rgba(10, 13, 22, 0.75);
  border: 1px solid var(--border);
  overflow: hidden;
}
.usage__fill {
  display: block;
  height: 100%;
  min-width: 3px;
  border-radius: 999px;
  background: var(--accent);
  transition: width 0.25s ease;
}
.usage__value {
  min-width: 4ch;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text);
  font-weight: 600;
}

@media (prefers-reduced-motion: reduce) {
  .usage__fill { transition: none; }
}
</style>
