<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useStandardPhotoCacheStore } from '../../stores/standardPhotoCache'
import { clamp01 } from '../../utils/hygieneMarkup'
import HygieneImageLightbox from './HygieneImageLightbox.vue'
import HygieneMarkupOverlay from './HygieneMarkupOverlay.vue'

const props = defineProps({
  src: { type: String, default: '' },
  standardId: { type: [Number, String], default: null },
  markup: { type: Array, default: () => [] },
  alt: { type: String, default: '标准图' },
  editable: { type: Boolean, default: false },
  zoomable: { type: Boolean, default: true },
  lightboxWatermark: { type: Object, default: null },
})

const emit = defineEmits(['point'])
const standardPhotoCache = useStandardPhotoCacheStore()
const resolvedSrc = ref(props.src)
const lightboxOpen = ref(false)
let resolveGeneration = 0

const missingOffline = computed(() => Boolean(
  props.standardId
  && !standardPhotoCache.online
  && standardPhotoCache.isMissing(props.standardId),
))

function onPoint(event) {
  if (!props.editable) return
  const rect = event.currentTarget.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  emit('point', {
    x: clamp01((event.clientX - rect.left) / rect.width),
    y: clamp01((event.clientY - rect.top) / rect.height),
  })
}

function openLightbox() {
  if (props.zoomable && resolvedSrc.value) lightboxOpen.value = true
}

function onSurfaceClick(event) {
  if (props.editable) onPoint(event)
  else openLightbox()
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
    <div
      v-else
      class="std-photo"
      :class="{ editable, zoomable: !editable && zoomable }"
      :role="!editable && zoomable ? 'button' : undefined"
      :tabindex="!editable && zoomable ? 0 : undefined"
      @click="onSurfaceClick"
      @keydown.enter.prevent="openLightbox"
    >
      <img :src="resolvedSrc" :alt="alt">
      <HygieneMarkupOverlay :markup="markup" />
      <button
        v-if="editable && zoomable"
        type="button"
        class="std-zoom"
        @click.stop="openLightbox"
      >全屏</button>
    </div>
    <HygieneImageLightbox
      v-if="lightboxOpen"
      :src="resolvedSrc"
      :alt="alt"
      :markup="markup"
      :watermark="lightboxWatermark"
      @close="lightboxOpen = false"
    />
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
.std-photo.zoomable {
  cursor: zoom-in;
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
.std-zoom {
  position: absolute;
  top: 8px;
  right: 8px;
  border: 1px solid var(--hy-mint-line);
  border-radius: 999px;
  background: rgba(8, 22, 20, .9);
  color: var(--hy-mint-bright);
  padding: 6px 10px;
  font: 700 11px/1 var(--font-sans);
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
