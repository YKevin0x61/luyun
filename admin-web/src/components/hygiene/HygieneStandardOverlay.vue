<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import { clamp01 } from '../../utils/hygieneMarkup'

const props = defineProps({
  src: { type: String, default: '' },
  standardId: { type: [Number, String], default: null },
  markup: { type: Array, default: () => [] },
  alt: { type: String, default: '标准图' },
  editable: { type: Boolean, default: false },
})

const emit = defineEmits(['point'])
const standardPhotoCache = useStandardPhotoCacheStore()
const resolvedSrc = ref(props.src)
let resolveGeneration = 0

const markerId = `hygiene-arrow-${Math.random().toString(36).slice(2, 10)}`

const missingOffline = computed(() => Boolean(
  props.standardId
  && !standardPhotoCache.online
  && standardPhotoCache.isMissing(props.standardId),
))

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

async function resolveSource() {
  resolveGeneration += 1
  const generation = resolveGeneration
  const previous = resolvedSrc.value
  if (missingOffline.value) {
    resolvedSrc.value = ''
    if (previous && previous.startsWith('blob:')) standardPhotoCache.releaseImage(previous)
    return
  }
  if (!props.standardId) {
    resolvedSrc.value = props.src
    if (previous && previous !== props.src && previous.startsWith('blob:')) {
      standardPhotoCache.releaseImage(previous)
    }
    return
  }
  const resolved = await standardPhotoCache.resolveImage(props.standardId)
  if (generation !== resolveGeneration) {
    if (resolved && resolved.url) standardPhotoCache.releaseImage(resolved.url)
    return
  }
  const next = (resolved && resolved.url) || props.src
  resolvedSrc.value = next
  if (previous && previous !== next && previous.startsWith('blob:')) {
    standardPhotoCache.releaseImage(previous)
  }
}

watch(
  () => [props.src, props.standardId, missingOffline.value],
  resolveSource,
  { immediate: true },
)
watch(
  () => standardPhotoCache.cacheGeneration,
  resolveSource,
)

onBeforeUnmount(() => {
  resolveGeneration += 1
  if (resolvedSrc.value && resolvedSrc.value.startsWith('blob:')) {
    standardPhotoCache.releaseImage(resolvedSrc.value)
  }
})
</script>

<template>
  <div class="std-frame">
    <div v-if="missingOffline" class="std-empty">
      <p>尚未缓存，需联网下载</p>
      <button type="button" @click="standardPhotoCache.checkForUpdates({ force: true })">重试下载</button>
    </div>
    <p v-else-if="!resolvedSrc" class="std-empty">还没有标准图</p>
    <div v-else class="std-photo" :class="{ editable }" @click="onPoint">
      <img :src="resolvedSrc" :alt="alt">
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
        :style="{ left: `${(mark.x || 0) * 100}%`, top: `${(mark.y || 0) * 100}%` }"
      >{{ mark.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.std-frame {
  position: relative;
  background: var(--hy-ink);
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-md);
  overflow: hidden;
  min-height: 180px;
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
.std-photo.editable img {
  outline: 1px dashed var(--hy-mint-line);
  outline-offset: -1px;
}
.std-photo img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 440px;
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
  pointer-events: none;
}
.std-empty {
  margin: 0;
  padding: 56px 16px;
  text-align: center;
  color: var(--hy-faint);
  font-size: 13px;
  letter-spacing: .08em;
}
.std-empty p {
  margin: 0;
}
.std-empty button {
  margin-top: 8px;
  border: 0;
  background: transparent;
  color: var(--hy-mint);
  font: inherit;
  text-decoration: underline;
}
</style>
