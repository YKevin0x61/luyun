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
const cameraReady = ref(false)
const snapping = ref(false)
const switchingCamera = ref(false)
const wideAvailable = ref(false)
const errorText = ref('')
// 提示性质的文字：不能写进 errorText —— 那个非空会把「拍一张」「广角」都禁掉，
// 而这条提示恰恰是让店员改用「拍一张」的。
const hintText = ref('')
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
  cameraReady.value = true
}

async function refreshCameraCapabilities() {
  wideAvailable.value = false
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
    await track.applyConstraints({ advanced: [{ zoom: zoom.min }] })
    return
  }
  const currentId = track && track.getSettings ? track.getSettings().deviceId : ''
  if (!wideDeviceId) return
  if (wideDeviceId === currentId) {
    wideMode = true
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
        await track.applyConstraints({
          advanced: [{ zoom: savedZoom == null ? 1 : savedZoom }],
        })
      } else {
        const settings = track.getSettings ? track.getSettings() : {}
        savedZoom = settings.zoom == null ? 1 : settings.zoom
        wideMode = true
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
  cameraReady.value = false
  if (stream) {
    for (const track of stream.getTracks()) track.stop()
    stream = null
  }
  if (videoEl.value) videoEl.value.srcObject = null
}

// 抓拍尺寸：相机给的是 1920×1080（或更高），而这张图只用来看清脏污 + 存档，
// 长边 1600 / q0.75 足够。原来按视频原始尺寸 1:1 画布 + q0.92，一张 0.5–1.2MB，
// 弱网下传一张要几十秒。
const CAPTURE_LONG_EDGE = 1600
const CAPTURE_QUALITY = 0.75
// 服务端上限（services/hygiene/work.py 的 MAX_STANDARD_BYTES）：超了会在传完之后
// 才被 413 拒掉，白耗流量，所以在选图这一步就先挡。
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

async function snap() {
  if (snapping.value || !videoEl.value || !stream) return
  snapping.value = true
  try {
    const video = videoEl.value
    const sourceWidth = video.videoWidth || 1280
    const sourceHeight = video.videoHeight || 720
    const scale = Math.min(
      1,
      CAPTURE_LONG_EDGE / Math.max(sourceWidth, sourceHeight),
    )
    const width = Math.max(1, Math.round(sourceWidth * scale))
    const height = Math.max(1, Math.round(sourceHeight * scale))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    // 直接把视频帧缩放到目标尺寸：画布的 backing store 也只有目标大小，
    // 不必先画一张全尺寸位图再缩（那会多占 ~8MB 并多一次重采样）。
    canvas.getContext('2d').drawImage(video, 0, 0, width, height)
    const blob = await new Promise((resolve) => (
      canvas.toBlob(resolve, 'image/jpeg', CAPTURE_QUALITY)
    ))
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
  input.value = ''
  if (!file) return
  hintText.value = ''
  if (file.size > MAX_UPLOAD_BYTES) {
    // iPhone 的 HEIC / 高像素 JPEG 很容易超 20MB：与其等它慢慢传完再被 413 拒掉，
    // 不如当场告诉店员换「拍一张」。
    const sizeMb = (file.size / 1024 / 1024).toFixed(1)
    hintText.value = `这张照片 ${sizeMb}MB，超过 20MB 上限。请改用「拍一张」，或把手机相机设置改成「兼容性最佳」。`
    return
  }
  emit('captured', file)
}

onMounted(startCamera)
onBeforeUnmount(stopCamera)
</script>

<template>
  <div class="live-camera">
    <p v-if="errorText" class="live-alert">{{ errorText }}</p>
    <p v-else-if="hintText" class="live-hint is-warn">{{ hintText }}</p>
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
        v-if="cameraReady"
        type="button"
        class="btn live-wide"
        :class="{ 'is-active': wideMode }"
        :aria-pressed="wideMode"
        :aria-label="wideAvailable
          ? (wideMode ? '切换到标准视角' : '切换到广角')
          : '当前镜头不支持广角'"
        :title="wideAvailable
          ? (wideMode ? '切换到标准视角' : '切换到广角')
          : '当前镜头不支持广角，可用原相机手动选择'"
        :disabled="switchingCamera || starting || Boolean(errorText) || !wideAvailable"
        @click="toggleWide"
      >{{ switchingCamera ? '切换中…' : '广角' }}</button>
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
.live-wide.is-active {
  border-color: var(--hy-mint-line);
  background: var(--hy-mint-soft);
  color: var(--hy-mint-bright);
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
