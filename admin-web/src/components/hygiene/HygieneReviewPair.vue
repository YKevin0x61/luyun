<script setup>
import HygieneStandardOverlay from './HygieneStandardOverlay.vue'
import HygieneWatermarkOverlay from './HygieneWatermarkOverlay.vue'

defineProps({
  standardSrc: { type: String, default: '' },
  standardMarkup: { type: Array, default: () => [] },
  standardAlt: { type: String, default: '标准图' },
  captureSrc: { type: String, default: '' },
  captureAlt: { type: String, default: '实拍' },
  watermark: { type: Object, default: null },
  leftWatermark: { type: Object, default: null },
  leftLabel: { type: String, default: '标准图' },
  rightLabel: { type: String, default: '实拍' },
})
</script>

<template>
  <div class="review-pair">
    <section>
      <h3>{{ leftLabel }}</h3>
      <div v-if="leftWatermark" class="capture-frame">
        <p v-if="!standardSrc" class="capture-empty">还没有清理前</p>
        <div v-else class="capture-photo">
          <img :src="standardSrc" :alt="standardAlt">
          <HygieneWatermarkOverlay :watermark="leftWatermark" />
        </div>
      </div>
      <HygieneStandardOverlay
        v-else
        :src="standardSrc"
        :markup="standardMarkup"
        :alt="standardAlt"
      />
    </section>
    <section>
      <h3>{{ rightLabel }}</h3>
      <div class="capture-frame">
        <p v-if="!captureSrc" class="capture-empty">还没有实拍</p>
        <div v-else class="capture-photo">
          <img :src="captureSrc" :alt="captureAlt">
          <HygieneWatermarkOverlay :watermark="watermark" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.review-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.review-pair h3 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--text-dim);
  font-weight: 600;
}
.capture-frame {
  background: #0b1220;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-height: 160px;
  display: flex;
  justify-content: center;
  align-items: center;
}
.capture-photo {
  position: relative;
  display: inline-block;
  max-width: 100%;
}
.capture-photo img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 420px;
}
.capture-empty {
  margin: 0;
  padding: 48px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
@media (max-width: 720px) {
  .review-pair { grid-template-columns: 1fr; }
}
</style>
