<script setup>
// 纯 CSS 的「当前值 vs 阈值」量表：一条轨道 + 区间带 + 阈值刻度线 +（可选）峰值标记。
// 几何全部由 utils/systemHealthCharts.js 算好传进来，这里只画。
// 无障碍：整块是 role="img" + 完整 aria-label，内部图形 aria-hidden，数值与阈值另有可见文字。
defineProps({
  title: { type: String, required: true },
  level: { type: String, default: 'unknown' },
  levelLabel: { type: String, default: '' },
  pct: { type: Number, default: 0 },
  zones: { type: Array, default: () => [] },
  markerPct: { type: Number, default: null },
  valueText: { type: String, default: '—' },
  thresholdText: { type: String, default: '' },
  caption: { type: String, default: '' },
  peak: { type: Object, default: null },
  ariaLabel: { type: String, required: true },
})
</script>

<template>
  <div class="bullet" :class="`is-${level}`" role="img" :aria-label="ariaLabel">
    <div class="bullet__head">
      <span class="bullet__title">{{ title }}</span>
      <span class="bullet__level">{{ levelLabel || '未知' }}</span>
      <span class="bullet__value">{{ valueText }}</span>
    </div>
    <div class="bullet__track" aria-hidden="true">
      <div class="bullet__bands">
        <span
          v-for="(z, i) in zones"
          :key="`zone-${i}`"
          class="bullet__zone"
          :class="`is-${z.level}`"
          :style="{ left: `${z.left}%`, width: `${z.width}%` }"
        ></span>
        <span class="bullet__fill" :style="{ width: `${pct}%` }"></span>
      </div>
      <span
        v-if="peak"
        class="bullet__peak"
        :style="{ left: `${peak.pct}%` }"
      ></span>
      <span
        v-if="markerPct !== null"
        class="bullet__marker"
        :style="{ left: `${markerPct}%` }"
      ></span>
    </div>
    <div class="bullet__foot">
      <span class="bullet__threshold">{{ thresholdText }}</span>
      <span v-if="peak" class="bullet__peak-label">{{ peak.label }}</span>
    </div>
    <p class="bullet__caption">{{ caption }}</p>
  </div>
</template>

<style scoped>
/* 预留固定高度：数据未到 / 加载中也不跳动。 */
.bullet {
  display: flex;
  flex-direction: column;
  min-height: 118px;
  min-width: 0;
}
.bullet__head {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  font-size: 12px;
}
.bullet__title { color: var(--text-dim); font-weight: 600; }
.bullet__level {
  padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
  border: 1px solid var(--border); background: rgba(10, 13, 22, 0.6); color: var(--text);
}
.is-ok .bullet__level { border-color: rgba(34, 197, 94, 0.45); color: var(--green); }
.is-warning .bullet__level { border-color: rgba(245, 158, 11, 0.45); color: var(--yellow); }
.is-critical .bullet__level { border-color: rgba(239, 68, 68, 0.5); color: #fca5a5; }
.bullet__value {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px; font-weight: 700; color: var(--text);
}

/* 轨道留出上下各 3px：阈值刻度线/峰值线画在裁切区之外，贴边时也能看见。 */
.bullet__track {
  position: relative;
  height: 24px;
  margin: 8px 0 6px;
}
.bullet__bands {
  position: absolute;
  left: 0; right: 0; top: 3px; bottom: 3px;
  border-radius: 6px;
  background: rgba(10, 13, 22, 0.75);
  border: 1px solid var(--border);
  overflow: hidden;
}
.bullet__zone { position: absolute; top: 0; bottom: 0; }
.bullet__zone.is-ok { background: rgba(34, 197, 94, 0.1); }
.bullet__zone.is-warning { background: rgba(245, 158, 11, 0.14); }
.bullet__zone.is-critical { background: rgba(239, 68, 68, 0.16); }
.bullet__zone.is-unknown { background: rgba(107, 114, 128, 0.14); }

.bullet__fill {
  position: absolute; left: 0; top: 0; bottom: 0;
  min-width: 2px;
  border-radius: 6px 0 0 6px;
  background: var(--accent);
  transition: width 0.25s ease;
}
.is-ok .bullet__fill { background: var(--green); }
.is-warning .bullet__fill { background: var(--yellow); }
.is-critical .bullet__fill { background: var(--red); }
.is-unknown .bullet__fill { background: var(--text-dim); }

.bullet__marker {
  position: absolute; top: 0; bottom: 0; width: 2px;
  transform: translateX(-1px);
  background: var(--text);
  box-shadow: 0 0 0 1px rgba(10, 13, 22, 0.85);
}
.bullet__peak {
  position: absolute; top: 0; bottom: 0; width: 2px;
  transform: translateX(-1px);
  background: var(--cyan);
  box-shadow: 0 0 0 1px rgba(10, 13, 22, 0.85);
}

.bullet__foot {
  display: flex; gap: 10px; justify-content: space-between; flex-wrap: wrap;
  font-size: 11px; line-height: 1.5;
}
.bullet__threshold { color: var(--yellow); }
.is-ok .bullet__threshold { color: var(--text-dim); }
.bullet__peak-label { color: var(--cyan); }

.bullet__caption {
  margin: 4px 0 0; min-height: 1.5em;
  font-size: 11px; line-height: 1.5; color: var(--text-dim);
}

@media (prefers-reduced-motion: reduce) {
  .bullet__fill { transition: none; }
}
</style>
