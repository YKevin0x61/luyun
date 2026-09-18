<script setup>
// 「逐条结论 + 一句话说明」的检查清单。备份健康与更新环境自检返回的结构相同
// （{ code, ok, message }），共用这一个组件，避免两处各写一遍列表样式。
// 状态用符号 + 文字表达，颜色只是辅助。
defineProps({
  items: { type: Array, default: () => [] },
  passLabel: { type: String, default: '通过' },
  failLabel: { type: String, default: '未通过' },
  /** 没有条目时的占位文案；留空则不渲染。 */
  emptyText: { type: String, default: '' },
})
</script>

<template>
  <ul v-if="items.length" class="checks">
    <li
      v-for="item in items"
      :key="item.code || item.message"
      class="checks__item"
      :class="item.ok ? 'is-ok' : 'is-fail'"
    >
      <span class="checks__symbol" aria-hidden="true">{{ item.ok ? '✓' : '!' }}</span>
      <span class="checks__status">{{ item.ok ? passLabel : failLabel }}</span>
      <span class="checks__message">{{ item.message }}</span>
    </li>
  </ul>
  <p v-else-if="emptyText" class="checks__empty">{{ emptyText }}</p>
</template>

<style scoped>
.checks { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.checks__item {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.55;
  background: rgba(10, 13, 22, 0.55);
  border: 1px solid var(--border);
}
.checks__symbol { font-size: 13px; font-weight: 700; line-height: 1; }
.checks__status { font-weight: 700; white-space: nowrap; }
.checks__message { color: var(--text); min-width: 0; }

.checks__item.is-ok .checks__symbol,
.checks__item.is-ok .checks__status { color: var(--green); }
.checks__item.is-fail {
  background: rgba(245, 158, 11, 0.1);
  border-color: rgba(245, 158, 11, 0.35);
}
.checks__item.is-fail .checks__symbol,
.checks__item.is-fail .checks__status { color: var(--yellow); }

.checks__empty { margin: 0; font-size: 11px; color: var(--text-dim); line-height: 1.5; }

@media (max-width: 560px) {
  .checks__item { grid-template-columns: auto minmax(0, 1fr); }
  .checks__status { grid-column: 2; }
  .checks__message { grid-column: 1 / -1; }
}
</style>
