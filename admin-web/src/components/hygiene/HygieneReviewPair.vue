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
  gap: 14px;
}
.review-pair h3 {
  margin: 0 0 8px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--hy-mint);
}
.review-pair h3::before {
  content: '';
  width: 14px;
  height: 1px;
  background: var(--hy-mint);
  box-shadow: 0 0 6px var(--hy-mint);
}
.capture-frame {
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
.capture-photo {
  position: relative;
  display: inline-block;
  max-width: 100%;
}
.capture-photo img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 440px;
}
.capture-empty {
  margin: 0;
  padding: 56px 16px;
  text-align: center;
  color: var(--hy-faint);
  font-size: 13px;
  letter-spacing: .08em;
}
@media (max-width: 720px) {
  .review-pair { grid-template-columns: 1fr; }
}
</style>
