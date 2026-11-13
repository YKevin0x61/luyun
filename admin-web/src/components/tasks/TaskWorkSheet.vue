<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { submitTask, uploadTaskImage } from '../../api/tasks'
import HygieneLiveCamera from '../hygiene/HygieneLiveCamera.vue'
import {
  clearTaskDrafts,
  listTaskDrafts,
  markTaskDraftUploaded,
  removeTaskDraft,
  saveTaskDraft,
} from '../../utils/taskDrafts'

// 员工完成一件任务（票 03）：先看参考图 → 打开相机现场拍 → 够张数后提交。
//
// 两步是分开的两屏（ADR 0050 的口径）：参考图没看就开相机，店员不知道要拍成什么样。
// 没有参考图的任务直接进相机。
//
// 照片拍完立刻写 IndexedDB（`utils/taskDrafts.js`），刷新/切后台回来还能接着传；
// 上传成功后回写 capture_id，重试不会重复上传。提交成功才清草稿。

const props = defineProps({
  open: { type: Boolean, default: false },
  task: { type: Object, default: null },
})
const emit = defineEmits(['close', 'submitted'])

const step = ref('camera')
const photos = ref([])
const error = ref('')
const submitting = ref(false)
const uploadProgress = ref('')

const photoCount = computed(() => Number(props.task?.photo_count) || 1)
const hasReference = computed(() => Boolean(props.task?.reference_image_url))
const canSubmit = computed(
  () => photos.value.length === photoCount.value && !submitting.value,
)

function nextDraftIndex() {
  if (!photos.value.length) return 0
  return Math.max(...photos.value.map((photo) => photo.index)) + 1
}

function revokePreviews() {
  for (const photo of photos.value) {
    if (photo.previewUrl) URL.revokeObjectURL(photo.previewUrl)
  }
}

async function restoreDrafts() {
  photos.value = []
  if (!props.task) return
  const records = await listTaskDrafts(props.task.id)
  photos.value = records.map((record) => ({
    index: Number(record.index),
    blob: record.blob,
    captureId: record.captureId || '',
    previewUrl: record.blob instanceof Blob ? URL.createObjectURL(record.blob) : '',
  }))
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      revokePreviews()
      photos.value = []
      return
    }
    error.value = ''
    submitting.value = false
    uploadProgress.value = ''
    await restoreDrafts()
    if (photos.value.length) step.value = 'review'
    else step.value = hasReference.value ? 'reference' : 'camera'
  },
  { immediate: true },
)

onBeforeUnmount(revokePreviews)

async function onCaptured(blob) {
  if (!props.task || photos.value.length >= photoCount.value) return
  const index = nextDraftIndex()
  const previewUrl = URL.createObjectURL(blob)
  photos.value = [...photos.value, { index, blob, captureId: '', previewUrl }]
  await saveTaskDraft(props.task.id, index, blob)
  if (photos.value.length >= photoCount.value) step.value = 'review'
}

async function removePhoto(index) {
  const photo = photos.value.find((item) => item.index === index)
  if (photo?.previewUrl) URL.revokeObjectURL(photo.previewUrl)
  photos.value = photos.value.filter((item) => item.index !== index)
  await removeTaskDraft(props.task.id, index)
}

async function submitNow() {
  if (!props.task || submitting.value || photos.value.length !== photoCount.value) return
  submitting.value = true
  error.value = ''
  try {
    const captureIds = []
    for (let position = 0; position < photos.value.length; position += 1) {
      const photo = photos.value[position]
      let captureId = photo.captureId
      if (!captureId) {
        uploadProgress.value = `正在上传第 ${position + 1}/${photos.value.length} 张…`
        const result = await uploadTaskImage(photo.blob, {
          purpose: 'submission',
          live: true,
          staff: true,
        })
        captureId = result?.image?.capture_id
        if (!captureId) throw new Error('照片上传失败，请重试')
        photo.captureId = captureId
        await markTaskDraftUploaded(props.task.id, photo.index, captureId)
      }
      captureIds.push(captureId)
    }
    uploadProgress.value = '正在提交…'
    await submitTask(props.task.id, captureIds)
    await clearTaskDrafts(props.task.id)
    revokePreviews()
    photos.value = []
    emit('submitted')
    emit('close')
  } catch (err) {
    error.value = err?.detail || err?.message || '提交失败，请重试（照片还在，重新打开可继续）'
  } finally {
    submitting.value = false
    uploadProgress.value = ''
  }
}
</script>

<template>
  <div
    v-if="open"
    class="work-overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="task-work-title"
  >
    <div class="work-sheet">
      <header class="work-head">
        <div>
          <p class="work-kicker">完成任务</p>
          <h3 id="task-work-title">{{ task?.title || '' }}</h3>
        </div>
        <button class="work-close" type="button" @click="emit('close')">关闭</button>
      </header>

      <p class="work-hint">需要 {{ photoCount }} 张现场照，已拍 {{ photos.length }} 张。</p>

      <template v-if="step === 'reference'">
        <div class="work-reference">
          <img :src="task.reference_image_url" alt="参考图" />
          <p class="work-hint">先看发布者给的参考图，按差不多的角度和范围拍。</p>
        </div>
        <button class="work-primary" type="button" @click="step = 'camera'">看完了，去拍照</button>
      </template>

      <template v-else-if="step === 'camera'">
        <HygieneLiveCamera @captured="onCaptured" />
        <button
          v-if="photos.length"
          class="work-secondary"
          type="button"
          @click="step = 'review'"
        >拍够了，去提交（{{ photos.length }}/{{ photoCount }}）</button>
      </template>

      <template v-else>
        <div class="work-photos">
          <figure v-for="photo in photos" :key="photo.index">
            <img :src="photo.previewUrl" alt="完成照" />
            <button class="work-link" type="button" @click="removePhoto(photo.index)">重拍这张</button>
          </figure>
        </div>
        <button class="work-secondary" type="button" @click="step = 'camera'">再拍一张</button>
      </template>

      <p v-if="uploadProgress" class="work-hint">{{ uploadProgress }}</p>
      <p v-if="error" class="work-error" role="alert">{{ error }}</p>

      <div class="work-actions">
        <button class="work-secondary" type="button" @click="emit('close')">稍后再交</button>
        <button
          class="work-primary"
          type="button"
          :disabled="!canSubmit"
          @click="submitNow"
        >{{ submitting ? '提交中…' : '提交' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.work-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--scrim);
}
.work-sheet {
  width: min(560px, 100%);
  max-height: 92vh;
  overflow-y: auto;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-1);
}
.work-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.work-kicker {
  margin: 0 0 2px;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--ink-faint);
}
.work-head h3 {
  margin: 0;
  font-size: 17px;
  line-height: 1.45;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.work-close {
  flex: none;
  min-height: var(--tap);
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.work-hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-faint);
}
.work-reference img {
  width: 100%;
  max-height: 46vh;
  object-fit: contain;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
  margin-bottom: 8px;
}
.work-photos {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}
.work-photos figure {
  margin: 0;
}
.work-photos img {
  width: 100%;
  height: 110px;
  object-fit: cover;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
}
.work-link {
  margin-top: 2px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--action);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.work-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.work-primary,
.work-secondary {
  min-height: var(--tap);
  padding: 8px 16px;
  border-radius: var(--r-1);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.work-primary {
  border: 1px solid var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.work-secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-dim);
}
.work-primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.work-error {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: var(--r-1);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 12px;
  line-height: 1.6;
}
</style>
