<template>
  <image
    class="svg-icon"
    :src="dataUri"
    :style="iconStyle"
    mode="aspectFit"
    aria-hidden="true"
  />
</template>

<script setup>
import { computed } from 'vue'
import { ICON_PATHS } from '@/utils/iconPaths.js'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  color: { type: String, default: 'currentColor' },
})

const paths = computed(() => ICON_PATHS[props.name] || '')

const dataUri = computed(() => {
  if (!paths.value) return ''
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${props.size}" height="${props.size}" viewBox="0 0 24 24" fill="none" stroke="${props.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths.value}</svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
})

const iconStyle = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
}))
</script>

<style scoped>
.svg-icon {
  display: inline-block;
  vertical-align: middle;
  flex-shrink: 0;
}
</style>
