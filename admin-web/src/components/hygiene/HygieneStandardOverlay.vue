<script setup>
import { computed } from 'vue'
import { clamp01 } from '../../utils/hygieneMarkup'

const props = defineProps({
  src: { type: String, default: '' },
  markup: { type: Array, default: () => [] },
  alt: { type: String, default: '标准图' },
  editable: { type: Boolean, default: false },
})

const emit = defineEmits(['point'])

const markerId = `hygiene-arrow-${Math.random().toString(36).slice(2, 10)}`

const circles = computed(() => (props.markup || []).filter((m) => m.kind === 'circle'))
const arrows = computed(() => (props.markup || []).filter((m) => m.kind === 'arrow'))
const captions = computed(() => (props.markup || []).filter((m) => m.kind === 'caption'))

function onPoint(event) {
  if (!props.editable) return
  const rect = event.currentTarget.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  emit('point', {
    x: clamp01((event.clientX - rect.left) / rect.width),
    y: clamp01((event.clientY - rect.top) / rect.height),
  })
}
</script>

<template>
  <div class="std-frame">
    <p v-if="!src" class="std-empty">还没有标准图</p>
    <div v-else class="std-photo" :class="{ editable }" @click="onPoint">
      <img :src="src" :alt="alt">
      <svg class="std-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <marker :id="markerId" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
            <path d="M0,0 L5,2.5 L0,5 z" fill="#22d3ee" />
          </marker>
        </defs>
        <circle
          v-for="(mark, index) in circles"
          :key="`c-${index}`"
          :cx="mark.x * 100"
          :cy="mark.y * 100"
          :r="(mark.r || 0.08) * 100"
          fill="none"
          stroke="#22d3ee"
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
          stroke="#22d3ee"
          stroke-width="1.6"
          :marker-end="`url(#${markerId})`"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      <span
        v-for="(mark, index) in captions"
        :key="`t-${index}`"
        class="std-caption"
        :style="{ left: `${(mark.x || 0) * 100}%`, top: `${(mark.y || 0) * 100}%` }"
      >{{ mark.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.std-frame {
  background: #0b1220;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-height: 160px;
  display: flex;
  justify-content: center;
  align-items: center;
}
.std-photo {
  position: relative;
  display: inline-block;
  max-width: 100%;
}
.std-photo.editable {
  cursor: crosshair;
}
.std-photo img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 420px;
}
.std-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.std-caption {
  position: absolute;
  transform: translate(-50%, -110%);
  background: rgba(15, 23, 42, 0.88);
  color: #e2e8f0;
  border: 1px solid rgba(34, 211, 238, 0.45);
  border-radius: 6px;
  padding: 2px 7px;
  font-size: 12px;
  white-space: nowrap;
  max-width: 80%;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}
.std-empty {
  margin: 0;
  padding: 48px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
</style>
