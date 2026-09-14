<script setup>
import { computed } from 'vue'

const props = defineProps({
  markup: { type: Array, default: () => [] },
})

const markerId = `hygiene-arrow-${Math.random().toString(36).slice(2, 10)}`
const circles = computed(() => (props.markup || []).filter((mark) => mark.kind === 'circle'))
const arrows = computed(() => (props.markup || []).filter((mark) => mark.kind === 'arrow'))
const captions = computed(() => (props.markup || []).filter((mark) => mark.kind === 'caption'))
</script>

<template>
  <div class="hy-markup-layer">
    <svg class="std-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker :id="markerId" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
          <path d="M0,0 L5,2.5 L0,5 z" fill="#3fe0b0" />
        </marker>
      </defs>
      <circle
        v-for="(mark, index) in circles"
        :key="`c-${index}`"
        :cx="mark.x * 100"
        :cy="mark.y * 100"
        :r="(mark.r || 0.08) * 100"
        fill="none"
        stroke="#3fe0b0"
        stroke-width="1.4"
        vector-effect="non-scaling-stroke"
      />
      <line
        v-for="(mark, index) in arrows"
        :key="`a-${index}`"
        :x1="mark.x1 * 100"
        :y1="mark.y1 * 100"
        :x2="mark.x2 * 100"
        :y2="mark.y2 * 100"
        stroke="#3fe0b0"
        stroke-width="1.6"
        :marker-end="`url(#${markerId})`"
        vector-effect="non-scaling-stroke"
      />
    </svg>
    <span
      v-for="(mark, index) in captions"
      :key="`t-${index}`"
      class="std-caption"
      :style="{ left: `${mark.x * 100}%`, top: `${mark.y * 100}%` }"
    >{{ mark.text }}</span>
  </div>
</template>

<style scoped>
.hy-markup-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.std-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.std-svg circle,
.std-svg line {
  filter: drop-shadow(0 0 3px rgba(63, 224, 176, .65));
}
.std-caption {
  position: absolute;
  transform: translate(-50%, -110%);
  background: rgba(8, 22, 20, .92);
  color: var(--hy-mint-bright);
  border: 1px solid var(--hy-mint-line);
  border-radius: 6px;
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: .04em;
  white-space: nowrap;
  max-width: 80%;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
