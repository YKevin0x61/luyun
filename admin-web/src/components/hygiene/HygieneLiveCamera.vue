<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  cameraLensPair,
  supportsNativeCameraCapture,
} from '../../utils/cameraCapabilities'

const emit = defineEmits(['captured', 'error'])

const videoEl = ref(null)
const nativeCameraInput = ref(null)
const starting = ref(true)
const snapping = ref(false)
const switchingCamera = ref(false)
const wideAvailable = ref(false)
const cameraActionText = ref('')
const errorText = ref('')
let stream = null
let wideMode = false
let standardDeviceId = ''
let wideDeviceId = ''
let savedZoom = null

const canUseNativeCamera = supportsNativeCameraCapture()

function videoTrack() {
  return stream && stream.getVideoTracks()[0]
}

function canZoomWide(track = videoTrack()) {
  const zoom = track && track.getCapabilities
    ? track.getCapabilities().zoom
    : null
  return Boolean(zoom && Number(zoom.min) < 1 && Number(zoom.max) > Number(zoom.min))
}

async function attachStream(nextStream) {
  stream = nextStream
  if (videoEl.value) {
    videoEl.value.srcObject = nextStream
    await videoEl.value.play()
  }
}

async function refreshCameraCapabilities() {
  wideAvailable.value = false
  cameraActionText.value = ''
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    const track = videoTrack()
    const currentId = track && track.getSettings ? track.getSettings().deviceId : ''
    const zoomWide = canZoomWide(track)
    const pair = cameraLensPair(devices, currentId)
    standardDeviceId = pair.standardDevice ? pair.standardDevice.deviceId : currentId
    wideDeviceId = pair.wideDevice ? pair.wideDevice.deviceId : ''
    wideAvailable.value = Boolean(zoomWide || pair.canSwitchDevices)
    if (wideAvailable.value) {
      cameraActionText.value = wideMode ? '标准视角' : '广角'
    }
  } catch {
    // Device enumeration is optional; keep using the browser-selected camera.
  }
}

async function switchToCamera(deviceId) {
  stopCamera()
  const nextStream = await navigator.mediaDevices.getUserMedia({
    video: {
      deviceId: { exact: deviceId },
      facingMode: { ideal: 'environment' },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    },
    audio: false,
  })
  await attachStream(nextStream)
  await refreshCameraCapabilities()
}

async function applyWideDefault() {
  const track = videoTrack()
  if (canZoomWide(track)) {
    const settings = track.getSettings ? track.getSettings() : {}
    savedZoom = settings.zoom == null ? 1 : settings.zoom
    const zoom = track.getCapabilities().zoom
    wideMode = true
    cameraActionText.value = '标准视角'
    await track.applyConstraints({ advanced: [{ zoom: zoom.min }] })
    return
  }
  const currentId = track && track.getSettings ? track.getSettings().deviceId : ''
  if (!wideDeviceId) return
  if (wideDeviceId === currentId) {
    wideMode = true
    cameraActionText.value = '标准视角'
    return
  }
  wideMode = true
  await switchToCamera(wideDeviceId)
}

async function toggleWide() {
  if (!wideAvailable.value || switchingCamera.value) return
  switchingCamera.value = true
  errorText.value = ''
  try {
    const track = videoTrack()
    if (canZoomWide(track)) {
      const zoom = track.getCapabilities().zoom
      if (wideMode) {
        wideMode = false
        cameraActionText.value = '广角'
        await track.applyConstraints({
          advanced: [{ zoom: savedZoom == null ? 1 : savedZoom }],
        })
      } else {
        const settings = track.getSettings ? track.getSettings() : {}
        savedZoom = settings.zoom == null ? 1 : settings.zoom
        wideMode = true
        cameraActionText.value = '标准视角'
        await track.applyConstraints({ advanced: [{ zoom: zoom.min }] })
      }
      return
    }

    const currentId = track && track.getSettings ? track.getSettings().deviceId : ''
    const target = wideMode ? standardDeviceId : wideDeviceId
    if (!target || target === currentId) return
    wideMode = !wideMode
    await switchToCamera(target)
  } catch (err) {
    errorText.value = '切换相机镜头失败，请重新打开相机。'
    emit('error', err)
  } finally {
    switchingCamera.value = false
  }
}

async function startCamera() {
  errorText.value = ''
  starting.value = true
  try {
    const nextStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    })
    await attachStream(nextStream)
    await refreshCameraCapabilities()
    if (wideAvailable.value && !wideMode) await applyWideDefault()
  } catch (err) {
    errorText.value = '无法打开相机。请在浏览器设置中允许相机权限，或换一部手机；卫生拍照必须现场完成。'
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

function openNativeCamera() {
  nativeCameraInput.value?.click()
}

function onNativeCameraChange(event) {
  const input = event.target
  const file = input && input.files && input.files[0]
  if (!file) return
  emit('captured', file)
  input.value = ''
}

onMounted(startCamera)
onBeforeUnmount(stopCamera)
</script>

<template>
  <div class="live-camera">
    <p v-if="errorText" class="live-alert">{{ errorText }}</p>
    <p v-else-if="starting" class="live-hint">正在打开后置相机…</p>
    <video ref="videoEl" class="live-video" playsinline muted autoplay></video>
    <div class="live-actions">
      <button
        type="button"
        class="btn btn-primary btn-block live-snap"
        :disabled="starting || switchingCamera || snapping || Boolean(errorText)"
        @click="snap"
      >
        {{ snapping ? '正在拍照…' : '拍一张' }}
      </button>
      <button
        v-if="canUseNativeCamera"
        type="button"
        class="btn live-native"
        aria-label="使用系统原相机拍摄"
        :disabled="switchingCamera || snapping"
        @click="openNativeCamera"
      >原相机</button>
      <button
        v-if="wideAvailable"
        type="button"
        class="btn live-wide"
        :disabled="switchingCamera || starting || Boolean(errorText)"
        @click="toggleWide"
      >{{ switchingCamera ? '切换中…' : cameraActionText }}</button>
      <input
        ref="nativeCameraInput"
        class="native-camera-input"
        type="file"
        accept="image/*"
        capture="environment"
        @change="onNativeCameraChange"
      >
    </div>
  </div>
</template>

<style scoped>
.live-camera {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.live-actions {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 9px;
}
.live-video {
  width: 100%;
  border-radius: var(--hy-radius-md);
  background: var(--hy-ink);
  border: 1px solid var(--hy-line);
  min-height: 240px;
  object-fit: contain;
}
.live-snap {
  min-height: 56px;
  font-size: 17px;
  font-weight: 800;
  letter-spacing: .12em;
}
.live-wide {
  min-width: 92px;
}
.live-native {
  min-width: 76px;
}
.native-camera-input {
  display: none;
}
.live-hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--hy-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: .08em;
}
.live-hint::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--hy-mint);
  box-shadow: 0 0 8px var(--hy-mint);
  animation: hy-live-pulse 1.4s ease-in-out infinite;
}
.live-alert {
  margin: 0;
  padding: 11px 13px;
  border-radius: var(--hy-radius-sm);
  background: var(--hy-seal-soft);
  border: 1px solid var(--hy-seal-line);
  color: var(--hy-seal-bright);
  font-size: 13px;
  line-height: 1.6;
}
@keyframes hy-live-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .35; }
}
</style>
