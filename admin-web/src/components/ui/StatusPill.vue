<script setup>
// 语义状态胶囊：文本 + 色彩双通道表达状态，颜色只是辅助，永远不单独承载结论。
// tone 与取值来源无关（备份健康 / 更新阶段 / 校验结论共用），避免每个面板各写一套 pill 样式。
defineProps({
  /** ok | warn | error | info | neutral */
  tone: { type: String, default: 'neutral' },
  label: { type: String, default: '' },
  /** 前置圆点：用于「连接中 / 已连接」这类有生命周期的状态。 */
  dot: { type: Boolean, default: false },
})
</script>

<template>
  <span class="pill" :class="`is-${tone}`">
    <span v-if="dot" class="pill__dot" aria-hidden="true"></span>
    <slot>{{ label }}</slot>
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.6;
  white-space: nowrap;
}
.pill__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }

.pill.is-ok { background: rgba(34, 197, 94, 0.16); color: var(--green); border-color: rgba(34, 197, 94, 0.35); }
.pill.is-warn { background: rgba(245, 158, 11, 0.16); color: var(--yellow); border-color: rgba(245, 158, 11, 0.35); }
.pill.is-error { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border-color: rgba(239, 68, 68, 0.4); }
.pill.is-info { background: rgba(59, 130, 246, 0.14); color: #93c5fd; border-color: rgba(59, 130, 246, 0.3); }
.pill.is-neutral { background: rgba(107, 114, 128, 0.16); color: var(--text-dim); border-color: var(--border); }
</style>
