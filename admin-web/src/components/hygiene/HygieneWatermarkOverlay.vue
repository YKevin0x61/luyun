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
  left: 8px;
  bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  background: rgba(2, 6, 23, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 6px;
  color: #f8fafc;
  font-size: 12px;
  line-height: 1.35;
  pointer-events: none;
  max-width: calc(100% - 16px);
}
.wm span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
