<script setup>
import SvgIcon from '../SvgIcon.vue'

// 「系统健康状态」顶部总览条：结论 pill + 版本 + 运行时长 + 重新检查。
// 结论同时以文字给出，颜色只是辅助，不单独承载状态。
defineProps({
  pillClass: { type: String, default: 'empty' },
  overallLabel: { type: String, default: '未知' },
  version: { type: String, default: '—' },
  uptimeLabel: { type: String, default: '—' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

defineEmits(['refresh'])
</script>

<template>
  <section class="health-overview" aria-labelledby="health-overview-title">
    <div class="health-overview__main">
      <h2 id="health-overview-title" class="health-overview__title">系统健康状态</h2>
      <span class="health-overview__pill" :class="`is-${pillClass}`">
        <span class="health-overview__pill-dot" aria-hidden="true"></span>
        总体{{ overallLabel }}
      </span>
    </div>
    <dl class="health-overview__facts">
      <div><dt>版本</dt><dd>{{ version }}</dd></div>
      <div><dt>运行时长</dt><dd>{{ uptimeLabel }}</dd></div>
    </dl>
    <button
      type="button"
      class="btn health-overview__refresh"
      :disabled="loading"
      @click="$emit('refresh')"
    >
      <SvgIcon name="refresh-cw" :size="14" />
      {{ loading ? '检查中…' : '重新检查' }}
    </button>
    <p v-if="error" class="health-overview__error">部分检查不可用：{{ error }}</p>
  </section>
</template>

<style scoped>
.health-overview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  padding: 14px 16px;
  margin-bottom: 14px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.health-overview__main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  flex: 1 1 auto;
  min-width: 0;
}
.health-overview__title { font-size: 15px; margin: 0; }
.health-overview__pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700;
  border: 1px solid transparent;
}
.health-overview__pill-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.health-overview__pill.is-ok { background: rgba(34, 197, 94, 0.16); color: var(--green); border-color: rgba(34, 197, 94, 0.35); }
.health-overview__pill.is-empty { background: rgba(245, 158, 11, 0.16); color: var(--yellow); border-color: rgba(245, 158, 11, 0.35); }
.health-overview__pill.is-error { background: rgba(239, 68, 68, 0.16); color: var(--red); border-color: rgba(239, 68, 68, 0.4); }

.health-overview__facts { display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 0; font-size: 12px; flex: 0 0 auto; }
.health-overview__facts > div { display: flex; gap: 6px; }
.health-overview__facts dt { color: var(--text-dim); }
.health-overview__facts dd {
  margin: 0; color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

/* 触控目标 >= 44px，键盘可达（原生 button，focus 有可见描边）。 */
.health-overview__refresh {
  min-height: 44px; min-width: 44px; padding: 0 16px; font-size: 13px; justify-content: center;
}
.health-overview__refresh:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.health-overview__error {
  flex: 1 0 100%;
  margin: 0; padding: 8px 10px; border-radius: 8px; font-size: 12px; line-height: 1.5;
  background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5;
}

@media (max-width: 560px) {
  .health-overview__main { flex: 1 0 100%; }
  .health-overview__refresh { width: 100%; }
}
</style>
