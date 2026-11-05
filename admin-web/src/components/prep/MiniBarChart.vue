<script setup>
import { computed } from 'vue'

/** 近期用量柱形图。
 *
 *  一天一根柱（备货计划的「近 8 天」给 8 根：今天 + 前面 7 个完整营业日）：
 *  柱顶一个数值、柱底两行日期与星期，一条琥珀色虚线画参考线。
 *  柱宽与容器对齐（CSS 100%），所以手机铺满内容区、桌面到 520px 为止。
 *
 *  虚线默认是这几天的均值（「均 15」）；给了 line 就用它，标签换成 lineLabel
 *  （同星期几那张图传分位落点 + 「p50」，让人看到这一档选中的是哪一天的量）。
 */
const props = defineProps({
  // 元素可以是数字，也可以是 { value, label, weekday }
  points: { type: Array, default: () => [] },
  width: { type: Number, default: 340 },
  height: { type: Number, default: 100 },
  // 可选：虚线的位置与标签；不给就画均值
  line: { type: Number, default: null },
  lineLabel: { type: String, default: '' },
  // 最后一根要不要标成「今天」：同星期几那张图的最后一根是最近的同星期几，不是今天
  highlightLast: { type: Boolean, default: true },
  // 读屏用的整句话；不给就按「近 N 天用量 + 参考线」拼
  ariaLabel: { type: String, default: '' },
  /* 柱子用哪一族颜色：`action`（主色青蓝，画「实际用量」）或 `alt`（洋红/梅紫，画「候选日参照」）。
     两张图同屏时要一眼分开，颜色本身不承担别的语义 —— 虚线始终是琥珀色，别跟着换。 */
  tone: { type: String, default: 'action' },
})


const LABEL_H = 14
const FOOT_H = 26
const GAP = 7

/** 柱顶与参考线的数字：整数就写整数，带小数保留 1 位（半成品可能被取整，原料常带小数）。 */
function qtyLabel(value) {
  const num = Number(value) || 0
  return Number.isInteger(num) ? String(num) : num.toFixed(1)
}

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

// 参考线：给了 line 就是它（分位落点），否则是均值
const lineValue = computed(() => {
  const given = props.line == null ? null : Number(props.line)
  if (given == null || Number.isNaN(given)) return average.value
  return given
})
const lineText = computed(
  () => `${props.lineLabel || '均'} ${qtyLabel(lineValue.value)}`,
)
const ariaText = computed(
  () =>
    props.ariaLabel ||
    `近 ${items.value.length} 天用量，参考线 ${lineText.value}`,
)

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
      isToday: props.highlightLast && index === items.value.length - 1,
      labelY: Math.max(9, baselineY.value - barH - 3),
      centerX: index * (barWidth.value + GAP) + barWidth.value / 2,
    }
  }),
)

const lineY = computed(
  () => baselineY.value - (maxValue.value ? (lineValue.value / maxValue.value) * plotH.value : 0),
)
</script>

<template>
  <svg
    class="prep-bars"
    :class="{ 'is-alt': tone === 'alt' }"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    :aria-label="ariaText"
  >
    <line
      class="prep-average"
      :x1="0"
      :x2="width"
      :y1="lineY"
      :y2="lineY"
      stroke-width="1"
      stroke-dasharray="4 3"
    />
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
      {{ qtyLabel(bar.value) }}
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

.prep-bars.is-alt .prep-bar {
  fill: var(--accent-alt);
}

.prep-bar.is-today {
  opacity: 0.92;
}

.prep-value {
  fill: var(--ink-dim);
  font-variant-numeric: tabular-nums;
  /* 参考线从数字中间穿过时（柱高接近分位落点很常见），用底色描一圈把虚线挡掉，
     不然「13」会被划成两截，读起来像有删除线。 */
  paint-order: stroke;
  stroke: var(--surface-1);
  stroke-width: 2.5px;
  stroke-linejoin: round;
}

.prep-value.is-today {
  fill: var(--ink);
  font-weight: 600;
}

.prep-average {
  stroke: var(--warn);
  opacity: 0.9;
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
