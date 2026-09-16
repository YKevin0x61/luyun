import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { staffUpload } from '../utils/hygieneStaff'

export const IMAGE_UPLOAD_CONCURRENCY = 2

const ACTIVE_STATUSES = new Set(['queued', 'uploading', 'processing'])

function runUpload(transport, path, formData, onProgress) {
  if (transport === 'staff') {
    return staffUpload(path, formData, onProgress)
  }
  return api.upload(path, formData, onProgress)
}

export const useImageUploadQueueStore = defineStore('imageUploadQueue', () => {
  const tasks = ref([])
  const activeUploads = ref(0)
  const successHandlers = new Map()
  let nextTaskId = 1

  const activeTasks = computed(() => tasks.value.filter((task) => (
    ACTIVE_STATUSES.has(task.status)
  )))
  const failedTasks = computed(() => tasks.value.filter((task) => task.status === 'error'))
  const completedTasks = computed(() => tasks.value.filter((task) => task.status === 'success'))
  const pendingCount = computed(() => tasks.value.filter((task) => task.status === 'queued').length)
  const uploadingCount = computed(() => tasks.value.filter((task) => (
    task.status === 'uploading' || task.status === 'processing'
  )).length)

  function findTask(taskId) {
    return tasks.value.find((task) => task.id === taskId) || null
  }

  function applyProgress(task, progress) {
    if (!ACTIVE_STATUSES.has(task.status)) return
    const percent = Number(progress && progress.percent)
    if (Number.isFinite(percent)) {
      task.percent = Math.max(0, Math.min(100, Math.round(percent)))
    }
    task.status = progress && progress.done ? 'processing' : 'uploading'
  }

  function pump() {
    while (activeUploads.value < IMAGE_UPLOAD_CONCURRENCY) {
      const task = tasks.value.find((item) => item.status === 'queued')
      if (!task) return
      void startTask(task)
    }
  }

  async function startTask(task) {
    activeUploads.value += 1
    task.status = 'uploading'
    task.percent = 0
    task.error = ''

    let result
    try {
      result = await runUpload(
        task.transport,
        task.path,
        task.formData,
        (progress) => {
          if (findTask(task.id)) applyProgress(task, progress)
        },
      )
    } catch (error) {
      activeUploads.value -= 1
      if (findTask(task.id)) {
        task.status = 'error'
        task.error = (error && error.message) || '图片上传失败'
      }
      pump()
      return
    }

    activeUploads.value -= 1
    if (!findTask(task.id)) {
      pump()
      return
    }

    task.status = 'success'
    task.percent = 100
    task.formData = null
    pump()

    const onSuccess = successHandlers.get(task.id)
    successHandlers.delete(task.id)
    if (!onSuccess) return
    try {
      await onSuccess(result)
    } catch {
      // The upload succeeded; a stale page refresh must not flip it back to failed.
    }
  }

  function enqueue({
    path,
    formData,
    label = '图片',
    detail = '',
    transport = 'admin',
    onSuccess = null,
  }) {
    if (!path || !formData) {
      throw new Error('上传任务缺少接口地址或图片数据')
    }
    const task = {
      id: `image-upload-${nextTaskId++}`,
      path,
      formData,
      label,
      detail,
      transport,
      status: 'queued',
      percent: 0,
      error: '',
      createdAt: Date.now(),
    }
    tasks.value.push(task)
    if (typeof onSuccess === 'function') successHandlers.set(task.id, onSuccess)
    pump()
    return task.id
  }

  function retry(taskId) {
    const task = findTask(taskId)
    if (!task || task.status !== 'error') return false
    task.status = 'queued'
    task.percent = 0
    task.error = ''
    pump()
    return true
  }

  function remove(taskId) {
    const task = findTask(taskId)
    if (!task || ACTIVE_STATUSES.has(task.status)) return false
    task.formData = null
    tasks.value = tasks.value.filter((item) => item.id !== taskId)
    successHandlers.delete(taskId)
    return true
  }

  function clearCompleted() {
    const completed = new Set(completedTasks.value.map((task) => task.id))
    if (!completed.size) return 0
    for (const task of completedTasks.value) task.formData = null
    tasks.value = tasks.value.filter((task) => !completed.has(task.id))
    for (const taskId of completed) successHandlers.delete(taskId)
    return completed.size
  }

  function clearTasksByTransport(transport) {
    const matching = tasks.value.filter((task) => task.transport === transport)
    const removed = new Set(matching.map((task) => task.id))
    if (!removed.size) return 0
    for (const task of matching) task.formData = null
    tasks.value = tasks.value.filter((task) => !removed.has(task.id))
    for (const taskId of removed) successHandlers.delete(taskId)
    return removed.size
  }

  return {
    tasks,
    activeUploads,
    activeTasks,
    failedTasks,
    completedTasks,
    pendingCount,
    uploadingCount,
    enqueue,
    retry,
    remove,
    clearCompleted,
    clearTasksByTransport,
  }
})
