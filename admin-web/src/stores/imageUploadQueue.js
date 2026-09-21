import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { staffUpload } from '../utils/hygieneStaff'
import {
  createUploadDraftStore,
  deserializeFormData,
  serializeTask,
} from '../utils/uploadDrafts'

/** 未完成任务落 IndexedDB：刷新/回收 webview 后还能续传，见 utils/uploadDrafts.js。 */
const drafts = createUploadDraftStore()

export const IMAGE_UPLOAD_CONCURRENCY = 2
/** 总尝试次数上限（首次 + 自动重试）；撞满后落到 error 让员工手动重试。 */
export const IMAGE_UPLOAD_MAX_ATTEMPTS = 3
/** 退避间隔，逐次取用；长度与尝试预算对齐（第 3 次失败即终态，不会用到第 3 个间隔）。 */
export const IMAGE_UPLOAD_BACKOFF_MS = [2000, 8000]

const ACTIVE_STATUSES = new Set(['queued', 'uploading', 'processing'])

function runUpload(transport, path, formData, onProgress) {
  if (transport === 'staff') {
    return staffUpload(path, formData, onProgress)
  }
  return api.upload(path, formData, onProgress)
}

/** 会话失效、图片太大这类错误重试多少次都一样，不如早早告诉员工。 */
function shouldAutoRetry(task, error) {
  // 非幂等的写接口（开整改单、建检查项、换标准图）禁掉自动重试：服务端已经提交、
  // 只是响应丢了的场合，重试会再插一条。员工端日常提交有服务端重传去重，那些才敢重试。
  if (task.autoRetry === false) return false
  if ((task.attempt || 0) >= IMAGE_UPLOAD_MAX_ATTEMPTS) return false
  const status = error && error.status
  if (!status) return true // 网络层失败（断网 / 超时）
  if (status === 408 || status === 429) return true
  return status >= 500
}

function backoffDelay(attempt) {
  const index = Math.min(Math.max(attempt, 1), IMAGE_UPLOAD_BACKOFF_MS.length) - 1
  return IMAGE_UPLOAD_BACKOFF_MS[index]
}

export const useImageUploadQueueStore = defineStore('imageUploadQueue', () => {
  const tasks = ref([])
  const activeUploads = ref(0)
  const successHandlers = new Map()
  const errorHandlers = new Map()
  let nextTaskId = 1
  let restoreStarted = false
  // 每次清理都 +1。恢复是异步的（先 await drafts.list()），如果这期间队列被清过
  // （401/登出），挂起的恢复必须放弃，否则会把刚清掉的任务又捞回来传一次。
  let clearedGeneration = 0

  /** 恢复的 id 必须让位，否则新任务会撞上同一个 id。 */
  function bumpNextTaskId(id) {
    const matched = /^image-upload-(\d+)$/.exec(String(id || ''))
    if (!matched) return
    const value = Number(matched[1])
    if (Number.isFinite(value) && value >= nextTaskId) nextTaskId = value + 1
  }

  function persist(task) {
    void drafts.put(serializeTask(task))
  }

  function forget(taskId) {
    void drafts.remove(taskId)
  }

  /** 页面重开时把上次没传完的任务捞回来接着传（只做一次）。 */
  async function restoreDrafts() {
    if (restoreStarted) return 0
    restoreStarted = true
    const generation = clearedGeneration
    const records = await drafts.list()
    if (generation !== clearedGeneration) return 0
    let restored = 0
    for (const record of records) {
      if (!record || !record.path || tasks.value.some((task) => task.id === record.id)) {
        continue
      }
      bumpNextTaskId(record.id)
      tasks.value.push({
        id: record.id,
        path: record.path,
        formData: deserializeFormData(record),
        label: record.label || '图片',
        detail: record.detail || '',
        transport: record.transport || 'admin',
        pendingKey: record.pendingKey || '',
        status: 'queued',
        percent: 0,
        error: '',
        attempt: Number(record.attempt) || 0,
        retryAt: 0,
        createdAt: record.createdAt || Date.now(),
        restored: true,
      })
      restored += 1
    }
    if (restored) pump()
    return restored
  }

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

  /** 等待退避的任务不能被 pump 立刻捞走，否则退避等于没有。 */
  function isReady(task) {
    return !task.retryAt || task.retryAt <= Date.now()
  }

  function scheduleRetry(task) {
    const delay = backoffDelay(task.attempt || 1)
    task.retryAt = Date.now() + delay
    setTimeout(() => {
      const live = findTask(task.id)
      if (!live || live.status !== 'queued') return
      live.retryAt = 0
      pump()
    }, delay)
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
      const task = tasks.value.find((item) => item.status === 'queued' && isReady(item))
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
      const live = findTask(task.id)
      if (!live) {
        pump()
        return
      }
      task.attempt = (task.attempt || 0) + 1
      task.error = (error && error.message) || '图片上传失败'
      if (shouldAutoRetry(task, error)) {
        // 留在队列里等退避到点，员工不需要手动点五次重试。
        task.status = 'queued'
        scheduleRetry(task)
        pump()
        return
      }
      task.status = 'error'
      task.retryAt = 0
      // 终态失败要把草稿删掉：否则下次打开页面又把它捞回来重传，「4xx/413 不重试」
      // 与「总共只试 3 次」这两个契约只在单次页面生命周期内成立。
      forget(task.id)
      pump()
      const onError = errorHandlers.get(task.id)
      if (!onError) return
      try {
        onError(task)
      } catch {
        // 回调是提示用的，别让它影响队列状态。
      }
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
    task.retryAt = 0
    errorHandlers.delete(task.id)
    forget(task.id)
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
    onError = null,
    pendingKey = '',
    autoRetry = true,
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
      // 这个任务对应哪条待办（"daily:7:白班" 之类）。视图据此把"已入队未确认"的项
      // 从待办里拿掉；放在 task 上而不是视图的局部状态里，是为了刷新/回收 webview
      // 之后仍然成立。
      pendingKey: pendingKey || '',
      // false = 这个接口不是幂等的，失败只允许人工重试（见 shouldAutoRetry）。
      autoRetry: autoRetry !== false,
      status: 'queued',
      percent: 0,
      error: '',
      attempt: 0,
      retryAt: 0,
      createdAt: Date.now(),
    }
    tasks.value.push(task)
    if (typeof onSuccess === 'function') successHandlers.set(task.id, onSuccess)
    if (typeof onError === 'function') errorHandlers.set(task.id, onError)
    persist(task)
    pump()
    return task.id
  }

  function retry(taskId) {
    const task = findTask(taskId)
    if (!task || task.status !== 'error') return false
    task.status = 'queued'
    task.percent = 0
    task.error = ''
    task.attempt = 0
    task.retryAt = 0
    pump()
    return true
  }

  function remove(taskId) {
    const task = findTask(taskId)
    if (!task || ACTIVE_STATUSES.has(task.status)) return false
    task.formData = null
    tasks.value = tasks.value.filter((item) => item.id !== taskId)
    successHandlers.delete(taskId)
    errorHandlers.delete(taskId)
    forget(taskId)
    return true
  }

  function clearCompleted() {
    const completed = new Set(completedTasks.value.map((task) => task.id))
    if (!completed.size) return 0
    for (const task of completedTasks.value) task.formData = null
    tasks.value = tasks.value.filter((task) => !completed.has(task.id))
    for (const taskId of completed) {
      successHandlers.delete(taskId)
      errorHandlers.delete(taskId)
      forget(taskId)
    }
    return completed.size
  }

  function clearTasksByTransport(transport) {
    clearedGeneration += 1
    const matching = tasks.value.filter((task) => task.transport === transport)
    const removed = new Set(matching.map((task) => task.id))
    if (!removed.size) {
      void drafts.clearByTransport(transport)
      return 0
    }
    for (const task of matching) task.formData = null
    tasks.value = tasks.value.filter((task) => !removed.has(task.id))
    for (const taskId of removed) {
      successHandlers.delete(taskId)
      errorHandlers.delete(taskId)
    }
    // 登出/换人要连带清掉暂存的草稿，否则下一个账号登录会把上一个人的照片传上去。
    void drafts.clearByTransport(transport)
    return removed.size
  }

  // 上次没传完的任务自动续传（IndexedDB 不可用时是 no-op，不影响本次使用）。
  void restoreDrafts()

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
    restoreDrafts,
  }
})
