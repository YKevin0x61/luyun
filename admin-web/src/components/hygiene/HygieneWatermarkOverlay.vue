<script setup>
import { computed } from 'vue'
import { formatWatermarkTime } from '../../utils/hygieneMarkup'

const props = defineProps({
  watermark: { type: Object, default: null },
})

const lines = computed(() => {
  const mark = props.watermark || {}
  return [
    formatWatermarkTime(mark.time),
    mark.item_name || mark.zone,
    mark.photographer,
  ].filter(Boolean)
})
</script>

<template>
  <div v-if="lines.length" class="wm" aria-label="水印">
    <span v-for="(line, index) in lines" :key="index">{{ line }}</span>
  </div>
</template>

<style scoped>
.wm {
  position: absolute;
  left: 10px;
  bottom: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 7px 9px;
  background: rgba(6, 17, 16, .78);
  border: 1px solid var(--hy-mint-line);
  border-left: 2px solid var(--hy-mint);
  border-radius: 6px;
  color: #eafaf5;
  font-size: 11px;
  line-height: 1.4;
  letter-spacing: .03em;
  pointer-events: none;
  max-width: calc(100% - 20px);
  backdrop-filter: blur(2px);
}
.wm span:first-child {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--hy-mint-bright);
}
.wm span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
