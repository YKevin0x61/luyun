<script setup>
import SvgIcon from '../SvgIcon.vue'
import StatusPill from './StatusPill.vue'

// 配置页各分节统一的头部：图标 + 标题 + 状态胶囊 + 说明 + 关键事实 + 操作区。
// 备份中心 / 系统更新 / 系统健康三块已用同一视觉的总览条，这里把它固化成可复用件，
// 其余分节不再各写一套头部样式。
// facts 用 props 而不是插槽：插槽内容属于父组件作用域，dt/dd 的样式会落不到。
defineProps({
  icon: { type: String, default: 'settings' },
  title: { type: String, required: true },
  /** 状态胶囊：给了 pillLabel 才渲染，tone 缺省为中性。 */
  tone: { type: String, default: 'neutral' },
  pillLabel: { type: String, default: '' },
  pillDot: { type: Boolean, default: false },
  description: { type: String, default: '' },
  /** 关键事实：[{ k, v, wide? }]，wide 项占满整行（长路径 / DSN 用）。 */
  facts: { type: Array, default: () => [] },
  /** 底部提示行，noteTone 支持 warn / error。 */
  note: { type: String, default: '' },
  noteTone: { type: String, default: '' },
})
</script>

<template>
  <section class="panel-header" :aria-label="title">
    <div class="panel-header__head">
      <h2 class="panel-header__title">
        <SvgIcon :name="icon" :size="15" />{{ title }}
      </h2>
      <StatusPill v-if="pillLabel" :tone="tone" :label="pillLabel" :dot="pillDot" />
      <div v-if="$slots.actions" class="panel-header__actions">
        <slot name="actions" />
      </div>
    </div>

    <p v-if="description" class="panel-header__description">{{ description }}</p>

    <dl v-if="facts.length" class="panel-header__facts">
      <div v-for="item in facts" :key="item.k" :class="{ 'is-wide': item.wide }">
        <dt>{{ item.k }}</dt>
        <dd>{{ item.v }}</dd>
      </div>
    </dl>

    <p v-if="note" class="panel-header__note" :class="noteTone ? `is-${noteTone}` : ''">{{ note }}</p>
  </section>
</template>

<style scoped>
.panel-header {
  padding: 14px 16px;
  margin-bottom: 14px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.panel-header__head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
.panel-header__title { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 15px; }
.panel-header__actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
.panel-header__actions :deep(.btn) { min-height: 36px; justify-content: center; }
.panel-header__actions :deep(.btn):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.panel-header__description {
  margin: 10px 0 0; font-size: 12px; line-height: 1.6; color: var(--text-dim);
}

.panel-header__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 4px 18px;
  margin: 12px 0 0;
  font-size: 12px;
}
.panel-header__facts > div { display: flex; gap: 8px; min-width: 0; }
.panel-header__facts dt { color: var(--text-dim); white-space: nowrap; }
.panel-header__facts dd {
  margin: 0 0 0 auto; min-width: 0; text-align: right;
  color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}
.panel-header__facts .is-wide { grid-column: 1 / -1; }

.panel-header__note {
  margin: 12px 0 0; padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px; line-height: 1.6; color: var(--text-dim);
}
.panel-header__note.is-warn { color: var(--yellow); }
.panel-header__note.is-error { color: #fca5a5; }

@media (max-width: 560px) {
  .panel-header__actions { margin-left: 0; width: 100%; }
  .panel-header__actions :deep(.btn) { flex: 1 1 0; }
}
</style>
