<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  markup: { type: Array, default: () => [] },
  fullscreen: { type: Boolean, default: false },
})

const layerEl = ref(null)
const layerSize = ref({ width: 0, height: 0 })
let resizeObserver = null

const markerId = `hygiene-arrow-${Math.random().toString(36).slice(2, 10)}`
const circles = computed(() => (props.markup || []).filter((mark) => mark.kind === 'circle'))
const arrows = computed(() => (props.markup || []).filter((mark) => mark.kind === 'arrow'))
const captions = computed(() => (props.markup || []).filter((mark) => mark.kind === 'caption'))
const captionSize = computed(() => {
  const base = Math.min(layerSize.value.width || 1, layerSize.value.height || 1)
  return Math.max(props.fullscreen ? 15 : 11, Math.min(props.fullscreen ? 24 : 16, base * 0.025))
})

function updateLayerSize() {
  const rect = layerEl.value?.getBoundingClientRect()
  layerSize.value = rect
    ? { width: rect.width, height: rect.height }
    : { width: 0, height: 0 }
}

function circleDiameter(mark) {
  return Math.max(
    props.fullscreen ? 22 : 16,
    Number(mark.r || 0.08) * 2 * Math.min(layerSize.value.width || 1, layerSize.value.height || 1),
  )
}

onMounted(() => {
  updateLayerSize()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(updateLayerSize)
    if (layerEl.value) resizeObserver.observe(layerEl.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})
</script>

<template>
  <div
    ref="layerEl"
    class="hy-markup-layer"
    :class="{ 'is-fullscreen': fullscreen }"
  >
    <svg class="std-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker :id="markerId" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
          <path d="M0,0 L5,2.5 L0,5 z" fill="#3fe0b0" />
        </marker>
      </defs>
      <line
        v-for="(mark, index) in arrows"
        :key="`a-${index}`"
        :x1="mark.x1 * 100"
        :y1="mark.y1 * 100"
        :x2="mark.x2 * 100"
        :y2="mark.y2 * 100"
        stroke="#3fe0b0"
        :stroke-width="fullscreen ? 2.2 : 1.6"
        :marker-end="`url(#${markerId})`"
        vector-effect="non-scaling-stroke"
      />
    </svg>
    <span
      v-for="(mark, index) in circles"
      :key="`c-${index}`"
      class="std-circle"
      :style="{
        left: `${mark.x * 100}%`,
        top: `${mark.y * 100}%`,
        width: `${circleDiameter(mark)}px`,
        height: `${circleDiameter(mark)}px`,
      }"
    />
    <span
      v-for="(mark, index) in captions"
      :key="`t-${index}`"
      class="std-caption"
      :style="{
        left: `${mark.x * 100}%`,
        top: `${mark.y * 100}%`,
        fontSize: `${captionSize}px`,
      }"
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
.std-svg line {
  filter: drop-shadow(0 0 3px rgba(63, 224, 176, .65));
}
.std-circle {
  position: absolute;
  transform: translate(-50%, -50%);
  border: 2px solid var(--hy-mint-bright);
  border-radius: 50%;
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
  letter-spacing: .04em;
  white-space: normal;
  max-width: min(80%, 360px);
  overflow-wrap: anywhere;
  line-height: 1.35;
}
.hy-markup-layer.is-fullscreen .std-circle {
  border-width: 3px;
}
.hy-markup-layer.is-fullscreen .std-caption {
  padding: 5px 9px;
  border-radius: 8px;
  line-height: 1.4;
}
</style>
