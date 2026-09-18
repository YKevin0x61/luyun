<script setup>
// 就绪检查网格：符号（✓ / ! / ✗）+ 文字 + 语义色，三者同时表达状态，不靠颜色单独传达。
// 未就绪时把后端 details 原文摊开；全部通过时折进 <details>。
defineProps({
  items: { type: Array, default: () => [] },
  details: { type: Array, default: () => [] },
  hasFailure: { type: Boolean, default: false },
})
</script>

<template>
  <div class="readiness">
    <ul class="readiness__list">
      <li
        v-for="item in items"
        :key="item.key"
        class="readiness__item"
        :class="`is-${item.level}`"
      >
        <span class="readiness__symbol" aria-hidden="true">{{ item.symbol }}</span>
        <span class="readiness__label">{{ item.label }}</span>
        <span class="readiness__status">{{ item.statusText }}</span>
      </li>
    </ul>

    <div v-if="hasFailure && details.length" class="readiness__details is-visible">
      <p class="readiness__details-title">未就绪详情</p>
      <ul>
        <li v-for="(d, i) in details" :key="`fail-${i}`">{{ d }}</li>
      </ul>
    </div>
    <details v-else-if="details.length" class="readiness__details">
      <summary>全部检查详情（{{ details.length }}）</summary>
      <ul>
        <li v-for="(d, i) in details" :key="`all-${i}`">{{ d }}</li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.readiness { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.readiness__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px;
  margin: 0; padding: 0; list-style: none;
}
.readiness__item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 8px; font-size: 12px;
  background: rgba(10, 13, 22, 0.6);
  border: 1px solid var(--border);
}
.readiness__symbol { font-size: 15px; font-weight: 700; line-height: 1; }
.readiness__label { color: var(--text); }
.readiness__status { margin-left: auto; color: var(--text-dim); white-space: nowrap; }

.readiness__item.is-ok .readiness__symbol { color: var(--green); }
.readiness__item.is-ok .readiness__status { color: var(--green); }
.readiness__item.is-critical { border-color: rgba(239, 68, 68, 0.45); }
.readiness__item.is-critical .readiness__symbol,
.readiness__item.is-critical .readiness__status { color: #fca5a5; }
.readiness__item.is-unknown .readiness__symbol,
.readiness__item.is-unknown .readiness__status { color: var(--yellow); }

.readiness__details { font-size: 11px; color: var(--text-dim); line-height: 1.6; }
.readiness__details > ul { margin: 6px 0 0; padding-left: 18px; }
.readiness__details-title { margin: 0; font-weight: 700; color: var(--text); }
.readiness__details.is-visible {
  padding: 8px 10px; border-radius: 8px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
}
.readiness__details.is-visible .readiness__details-title { color: #fca5a5; }
.readiness__details summary {
  cursor: pointer; min-height: 32px; display: flex; align-items: center; color: var(--text-dim);
}
</style>
