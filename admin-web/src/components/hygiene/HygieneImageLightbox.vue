<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import HygieneMarkupOverlay from './HygieneMarkupOverlay.vue'
import HygieneWatermarkOverlay from './HygieneWatermarkOverlay.vue'

defineProps({
  src: { type: String, default: '' },
  alt: { type: String, default: '图片预览' },
  markup: { type: Array, default: () => [] },
  watermark: { type: Object, default: null },
})

const emit = defineEmits(['close'])
const closeButton = ref(null)
let previousActive = null
let previousOverflow = ''

function onKeydown(event) {
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  if (event.key === 'Tab') {
    event.preventDefault()
    closeButton.value?.focus()
  }
}

onMounted(async () => {
  previousActive = document.activeElement
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
  await nextTick()
  closeButton.value?.focus()
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  window.removeEventListener('keydown', onKeydown)
  previousActive?.focus?.()
})
</script>

<template>
  <Teleport to="body">
    <div class="hy-lightbox" role="dialog" aria-modal="true" :aria-label="alt" @click.self="$emit('close')">
      <button
        ref="closeButton"
        type="button"
        class="hy-lightbox-close"
        aria-label="关闭全屏预览"
        @click="$emit('close')"
      >关闭</button>
      <div class="hy-lightbox-photo">
        <img :src="src" :alt="alt">
        <HygieneMarkupOverlay :markup="markup" />
        <HygieneWatermarkOverlay :watermark="watermark" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hy-lightbox {
  --hy-ink: #e4f0ee;
  --hy-mint: #3fe0b0;
  --hy-mint-bright: #63efc5;
  --hy-mint-line: rgba(63, 224, 176, .34);
  --hy-line-strong: rgba(133, 205, 198, .28);
  --font-sans: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
  position: fixed;
  inset: 0;
  z-index: 200;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(2, 8, 9, .96);
}
.hy-lightbox-photo {
  position: relative;
  display: inline-block;
  max-width: 100vw;
  max-height: 100vh;
  line-height: 0;
}
.hy-lightbox-photo img {
  display: block;
  width: auto;
  height: auto;
  max-width: calc(100vw - 36px);
  max-height: calc(100vh - 36px);
  object-fit: contain;
}
.hy-lightbox-close {
  position: fixed;
  top: max(12px, env(safe-area-inset-top));
  right: max(12px, env(safe-area-inset-right));
  z-index: 1;
  border: 1px solid var(--hy-line-strong);
  border-radius: 999px;
  background: rgba(8, 22, 20, .92);
  color: var(--hy-ink);
  padding: 9px 14px;
  font: 700 12px/1 var(--font-sans);
}
</style>
