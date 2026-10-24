<script setup>
import { computed } from 'vue'

/** 近 7 天用量柱形图。
 *
 *  七根柱：柱顶一个数值、柱底两行日期与星期，一条琥珀色虚线是这七天均值。
 *  柱宽与容器对齐（CSS 100%），所以手机铺满内容区、桌面到 520px 为止。
 */
const props = defineProps({
  // 元素可以是数字，也可以是 { value, label, weekday }
  points: { type: Array, default: () => [] },
  width: { type: Number, default: 340 },
  height: { type: Number, default: 100 },
})

const LABEL_H = 14
const FOOT_H = 26
const GAP = 7

const items = computed(() =>
  props.points.map((point) => {
    if (typeof point === 'number') {
      return { value: Number(point) || 0, label: '', weekday: '' }
    }
    return {
      value: Number(point?.value) || 0,
      label: point?.label || '',
      weekday: point?.weekday || '',
    }
  }),
)

const maxValue = computed(() => Math.max(...items.value.map((item) => item.value), 1))
const average = computed(() => {
  const list = items.value
  if (!list.length) return 0
  return list.reduce((sum, item) => sum + item.value, 0) / list.length
})

const baselineY = computed(() => props.height - FOOT_H)
const plotH = computed(() => baselineY.value - LABEL_H - 2)

const barWidth = computed(() => {
  const count = items.value.length
  if (!count) return 0
  return Math.max(4, (props.width - GAP * (count - 1)) / count)
})

const bars = computed(() =>
  items.value.map((item, index) => {
    const ratio = maxValue.value ? item.value / maxValue.value : 0
    const barH = Math.max(item.value > 0 ? 2 : 0, ratio * plotH.value)
    return {
      key: index,
      x: index * (barWidth.value + GAP),
      y: baselineY.value - barH,
      w: barWidth.value,
      h: barH,
      value: item.value,
      label: item.label,
      weekday: item.weekday,
      isToday: index === items.value.length - 1,
      labelY: Math.max(9, baselineY.value - barH - 3),
      centerX: index * (barWidth.value + GAP) + barWidth.value / 2,
    }
  }),
)

const averageY = computed(
  () => baselineY.value - (maxValue.value ? (average.value / maxValue.value) * plotH.value : 0),
)
</script>

<template>
  <svg
    class="prep-bars"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    :aria-label="`近 7 天用量，平均 ${Math.round(average)}`"
  >
    <rect
      v-for="bar in bars"
      :key="bar.key"
      class="prep-bar"
      :class="{ 'is-today': bar.isToday }"
      :x="bar.x"
      :y="bar.y"
      :width="bar.w"
      :height="bar.h"
      rx="3"
    />
    <text
      v-for="bar in bars"
      :key="`v-${bar.key}`"
      class="prep-value"
      :class="{ 'is-today': bar.isToday }"
      :x="bar.centerX"
      :y="bar.labelY"
      text-anchor="middle"
      font-size="9"
    >
      {{ Math.round(bar.value) }}
    </text>

    <line
      class="prep-average"
      :x1="0"
      :x2="width"
      :y1="averageY"
      :y2="averageY"
      stroke-width="1"
      stroke-dasharray="4 3"
    />
    <text
      class="prep-average-label"
      :x="width"
      :y="Math.max(9, averageY - 4)"
      text-anchor="end"
      font-size="9"
    >
      均 {{ Math.round(average) }}
    </text>

    <text
      v-for="bar in bars"
      :key="`d-${bar.key}`"
      class="prep-tick"
      :class="{ 'is-today': bar.isToday }"
      :x="bar.centerX"
      :y="baselineY + 12"
      text-anchor="middle"
      font-size="9"
    >
      {{ bar.label }}
    </text>
    <text
      v-for="bar in bars"
      :key="`w-${bar.key}`"
      class="prep-tick is-weekday"
      :class="{ 'is-today': bar.isToday }"
      :x="bar.centerX"
      :y="baselineY + 23"
      text-anchor="middle"
      font-size="9"
    >
      {{ bar.weekday }}
    </text>
  </svg>
</template>

<style scoped>
.prep-bars {
  display: block;
  width: 100%;
  max-width: 520px;
  height: auto;
  flex: 0 0 auto;
}

.prep-bar {
  fill: var(--action);
  opacity: 0.38;
}

.prep-bar.is-today {
  opacity: 0.92;
}

.prep-value {
  fill: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}

.prep-value.is-today {
  fill: var(--ink);
  font-weight: 600;
}

.prep-average {
  stroke: var(--warn);
  opacity: 0.9;
}

.prep-average-label {
  fill: var(--warn);
  font-variant-numeric: tabular-nums;
}

.prep-tick {
  fill: var(--ink-faint);
}

.prep-tick.is-weekday {
  opacity: 0.75;
}

.prep-tick.is-today {
  fill: var(--ink);
  opacity: 1;
}
</style>
