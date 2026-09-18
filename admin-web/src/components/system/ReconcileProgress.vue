<script setup>
// 对账进度：运行中给 role="progressbar" 的进度条 + stage_label + current/total 百分比；
// 未运行时明确说明没有任务在跑（不画空条，避免误读为 0%）。
defineProps({
  state: { type: Object, required: true },
})
</script>

<template>
  <div class="reconcile">
    <template v-if="state.running">
      <div
        class="reconcile__bar"
        :class="{ 'is-indeterminate': state.indeterminate }"
        role="progressbar"
        :aria-label="state.ariaLabel"
        :aria-valuemin="0"
        :aria-valuenow="state.indeterminate ? undefined : state.current"
        :aria-valuemax="state.indeterminate ? undefined : state.total"
      >
        <span
          class="reconcile__fill"
          :style="state.indeterminate ? null : { width: `${state.pct}%` }"
        ></span>
      </div>
      <p class="reconcile__text">{{ state.message }}</p>
    </template>
    <p v-else class="reconcile__idle">{{ state.message }}</p>
  </div>
</template>

<style scoped>
.reconcile { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.reconcile__bar {
  position: relative;
  height: 12px;
  border-radius: 999px;
  background: rgba(10, 13, 22, 0.75);
  border: 1px solid var(--border);
  overflow: hidden;
}
.reconcile__fill {
  display: block; height: 100%; min-width: 3px; border-radius: 999px;
  background: var(--cyan);
  transition: width 0.25s ease;
}
.reconcile__bar.is-indeterminate .reconcile__fill {
  width: 35%;
  animation: reconcile-slide 1.4s ease-in-out infinite;
}
.reconcile__text {
  margin: 0; font-size: 12px; color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.reconcile__idle { margin: 0; font-size: 12px; color: var(--text-dim); }

@keyframes reconcile-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}
@media (prefers-reduced-motion: reduce) {
  .reconcile__fill { transition: none; }
  .reconcile__bar.is-indeterminate .reconcile__fill { animation: none; width: 100%; opacity: 0.5; }
}
</style>
