<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const emit = defineEmits(['captured', 'error'])

const videoEl = ref(null)
const starting = ref(true)
const snapping = ref(false)
const errorText = ref('')
let stream = null

async function startCamera() {
  errorText.value = ''
  starting.value = true
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    })
    if (videoEl.value) {
      videoEl.value.srcObject = stream
      await videoEl.value.play()
    }
  } catch (err) {
    errorText.value = '打不开相机。必须现场拍，没有相册入口。'
    emit('error', err)
  } finally {
    starting.value = false
  }
}

function stopCamera() {
  if (stream) {
    for (const track of stream.getTracks()) track.stop()
    stream = null
  }
  if (videoEl.value) videoEl.value.srcObject = null
}

async function snap() {
  if (snapping.value || !videoEl.value || !stream) return
  snapping.value = true
  try {
    const video = videoEl.value
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth || 1280
    canvas.height = video.videoHeight || 720
    const ctx = canvas.getContext('2d')
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
    if (!blob) {
      errorText.value = '拍照失败，请再拍一张'
      return
    }
    emit('captured', blob)
  } finally {
    snapping.value = false
  }
}

onMounted(startCamera)
onBeforeUnmount(stopCamera)
</script>

<template>
  <div class="live-camera">
    <p v-if="errorText" class="live-alert">{{ errorText }}</p>
    <p v-else-if="starting" class="live-hint">正在打开后置相机…</p>
    <video ref="videoEl" class="live-video" playsinline muted autoplay></video>
    <button
      type="button"
      class="btn btn-primary btn-block live-snap"
      :disabled="starting || snapping || Boolean(errorText)"
      @click="snap"
    >
      {{ snapping ? '正在拍照…' : '拍一张' }}
    </button>
  </div>
</template>

<style scoped>
.live-camera {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.live-video {
  width: 100%;
  border-radius: 10px;
  background: #020617;
  min-height: 220px;
  object-fit: cover;
}
.live-snap { min-height: 48px; font-size: 16px; }
.live-hint {
  margin: 0;
  color: var(--text-dim);
  font-size: 13px;
}
.live-alert {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
</style>
