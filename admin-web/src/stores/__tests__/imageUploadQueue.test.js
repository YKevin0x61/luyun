import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  adminUpload: vi.fn(),
  staffUpload: vi.fn(),
  draftPut: vi.fn(async () => true),
  draftRemove: vi.fn(async () => true),
  draftList: vi.fn(async () => []),
  draftClear: vi.fn(async () => 0),
}))

vi.mock('../../api/client', () => ({
  api: { upload: mocks.adminUpload },
}))

vi.mock('../../utils/hygieneStaff', () => ({
  staffUpload: mocks.staffUpload,
}))

vi.mock('../../utils/uploadDrafts', () => ({
  createUploadDraftStore: () => ({
    put: mocks.draftPut,
    remove: mocks.draftRemove,
    list: mocks.draftList,
    clearByTransport: mocks.draftClear,
    available: true,
  }),
  serializeTask: (task) => ({
    id: task.id,
    path: task.path,
    transport: task.transport,
    label: task.label,
    detail: task.detail,
    pendingKey: task.pendingKey,
    attempt: task.attempt,
  }),
  deserializeFormData: () => ({ restored: true }),
}))

import { useImageUploadQueueStore } from '../imageUploadQueue.js'
import {
  IMAGE_UPLOAD_BACKOFF_MS,
  IMAGE_UPLOAD_MAX_ATTEMPTS,
} from '../imageUploadQueue.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function flushTasks() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

describe('image upload queue store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.adminUpload.mockReset()
    mocks.staffUpload.mockReset()
    mocks.draftPut.mockReset().mockResolvedValue(true)
    mocks.draftRemove.mockReset().mockResolvedValue(true)
    mocks.draftList.mockReset().mockResolvedValue([])
    mocks.draftClear.mockReset().mockResolvedValue(0)
  })

  it('persists a queued task and drops the draft once it lands', async () => {
    mocks.staffUpload.mockResolvedValue({ ok: true })

    const store = useImageUploadQueueStore()
    const taskId = store.enqueue({ transport: 'staff', path: '/x', formData: {} })
    expect(mocks.draftPut).toHaveBeenCalledTimes(1)
    expect(mocks.draftPut.mock.calls[0][0]).toMatchObject({
      id: taskId,
      path: '/x',
      transport: 'staff',
    })

    await flushTasks()
    expect(mocks.draftRemove).toHaveBeenCalledWith(taskId)
  })

  it('restores unfinished uploads after a reload and keeps later ids unique', async () => {
    mocks.draftList.mockResolvedValue([
      {
        id: 'image-upload-7',
        path: '/restored',
        transport: 'staff',
        label: '日常实拍',
        detail: '案板 · 白班',
        createdAt: 1,
      },
    ])
    mocks.staffUpload.mockResolvedValue({ ok: true })

    const store = useImageUploadQueueStore()
    await flushTasks()

    expect(store.tasks).toHaveLength(1)
    expect(store.tasks[0]).toMatchObject({ id: 'image-upload-7', restored: true })
    expect(mocks.staffUpload).toHaveBeenCalledWith(
      '/restored',
      { restored: true },
      expect.any(Function),
    )

    const freshId = store.enqueue({ transport: 'staff', path: '/new', formData: {} })
    expect(freshId).not.toBe('image-upload-7')
  })

  it('drops the persisted draft once an upload finally fails', async () => {
    // 终态失败还留着草稿的话，下次打开页面会把它捞回来再传一轮，
    // 「4xx/413 不重试」与「总共只试 3 次」就只在单次页面生命周期内成立了。
    mocks.staffUpload.mockRejectedValue(
      Object.assign(new Error('照片不能超过 20 MB'), { status: 413 }),
    )

    const store = useImageUploadQueueStore()
    const taskId = store.enqueue({ transport: 'staff', path: '/x', formData: {} })
    await flushTasks()

    expect(store.failedTasks).toHaveLength(1)
    expect(mocks.draftRemove).toHaveBeenCalledWith(taskId)
  })

  it('carries the queue key and attempt count across a reload', async () => {
    mocks.draftList.mockResolvedValue([
      {
        id: 'image-upload-3',
        path: '/restored',
        transport: 'staff',
        pendingKey: 'daily:7:白班',
        attempt: 2,
      },
    ])
    mocks.staffUpload.mockResolvedValue({ ok: true })

    const store = useImageUploadQueueStore()
    await flushTasks()

    expect(store.tasks[0]).toMatchObject({
      pendingKey: 'daily:7:白班',
      attempt: 2, // 重试预算不因刷新而重置
    })
  })

  it('persists the queue key saved by enqueue', async () => {
    mocks.staffUpload.mockReturnValue(new Promise(() => {}))

    const store = useImageUploadQueueStore()
    store.enqueue({ transport: 'staff', path: '/x', formData: {}, pendingKey: 'daily:7:白班' })
    await flushTasks()

    expect(mocks.draftPut.mock.calls[0][0]).toMatchObject({ pendingKey: 'daily:7:白班' })
  })

  it('clears the persisted drafts too when a transport signs out', async () => {
    mocks.staffUpload.mockReturnValue(new Promise(() => {}))

    const store = useImageUploadQueueStore()
    store.enqueue({ transport: 'staff', path: '/x', formData: {} })
    await flushTasks()

    expect(store.clearTasksByTransport('staff')).toBe(1)
    expect(mocks.draftClear).toHaveBeenCalledWith('staff')
  })

  it('keeps two uploads active, reports byte progress, and flushes queued work', async () => {
    const first = deferred()
    const second = deferred()
    const third = deferred()
    const onSuccess = vi.fn()
    mocks.adminUpload
      .mockImplementationOnce((_path, _form, onProgress) => {
        onProgress({ percent: 36, done: false })
        return first.promise
      })
      .mockImplementationOnce(() => second.promise)
      .mockImplementationOnce(() => third.promise)

    const store = useImageUploadQueueStore()
    const firstId = store.enqueue({
      path: '/first',
      formData: { name: 'first' },
      label: '第一张',
      onSuccess,
    })
    store.enqueue({ path: '/second', formData: { name: 'second' } })
    store.enqueue({ path: '/third', formData: { name: 'third' } })

    expect(mocks.adminUpload).toHaveBeenCalledTimes(2)
    expect(store.activeUploads).toBe(2)
    expect(store.pendingCount).toBe(1)
    expect(store.tasks.find((task) => task.id === firstId)).toMatchObject({
      status: 'uploading',
      percent: 36,
    })

    first.resolve({ ok: true })
    await flushTasks()

    expect(onSuccess).toHaveBeenCalledWith({ ok: true })
    expect(store.tasks.find((task) => task.id === firstId).formData).toBeNull()
    expect(mocks.adminUpload).toHaveBeenCalledTimes(3)
    expect(store.activeUploads).toBe(2)
    expect(store.pendingCount).toBe(0)
  })

  it('uses the staff transport and supports retrying the same form data', async () => {
    const formData = { name: 'capture' }
    // 用"服务端明确拒绝"的错误：这类不会被自动重试（网络抖动才重试），
    // 正好验证手动 retry 复用同一份 formData。
    mocks.staffUpload
      .mockRejectedValueOnce(
        Object.assign(new Error('照片不能超过 20 MB'), { status: 413 }),
      )
      .mockResolvedValueOnce({ ok: true })

    const store = useImageUploadQueueStore()
    const taskId = store.enqueue({
      transport: 'staff',
      path: '/capture',
      formData,
      label: '日常实拍',
    })
    await flushTasks()

    expect(store.tasks[0]).toMatchObject({
      status: 'error',
      error: '照片不能超过 20 MB',
    })
    expect(store.retry(taskId)).toBe(true)
    await flushTasks()

    expect(store.tasks[0].status).toBe('success')
    expect(store.tasks[0].formData).toBeNull()
    expect(mocks.staffUpload).toHaveBeenCalledTimes(2)
    expect(mocks.staffUpload.mock.calls[1][1]).toEqual(formData)
  })

  it('auto-retries a network failure after a backoff instead of parking it', async () => {
    vi.useFakeTimers()
    try {
      mocks.staffUpload
        .mockRejectedValueOnce(new TypeError('Failed to fetch'))
        .mockResolvedValueOnce({ ok: true })

      const store = useImageUploadQueueStore()
      const taskId = store.enqueue({ transport: 'staff', path: '/x', formData: {} })
      await flushTasks()

      const waiting = store.tasks.find((task) => task.id === taskId)
      expect(waiting.status).toBe('queued') // 等退避，不是 error
      expect(waiting.attempt).toBe(1)

      await vi.advanceTimersByTimeAsync(IMAGE_UPLOAD_BACKOFF_MS[0])
      await flushTasks()

      expect(store.tasks.find((task) => task.id === taskId).status).toBe('success')
      expect(mocks.staffUpload).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('gives up after the attempt budget and reports the failure once', async () => {
    vi.useFakeTimers()
    try {
      mocks.staffUpload.mockRejectedValue(new TypeError('Failed to fetch'))
      const onError = vi.fn()
      const store = useImageUploadQueueStore()
      const taskId = store.enqueue({
        transport: 'staff',
        path: '/x',
        formData: {},
        onError,
      })

      for (let i = 0; i < IMAGE_UPLOAD_MAX_ATTEMPTS; i += 1) {
        await flushTasks()
        await vi.advanceTimersByTimeAsync(40000)
      }
      await flushTasks()

      const task = store.tasks.find((item) => item.id === taskId)
      expect(task.status).toBe('error')
      expect(task.attempt).toBe(IMAGE_UPLOAD_MAX_ATTEMPTS)
      expect(mocks.staffUpload).toHaveBeenCalledTimes(IMAGE_UPLOAD_MAX_ATTEMPTS)
      expect(onError).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not retry a payload the server rejected outright', async () => {
    mocks.staffUpload.mockRejectedValue(
      Object.assign(new Error('照片不能超过 20 MB'), { status: 413 }),
    )
    const onError = vi.fn()

    const store = useImageUploadQueueStore()
    store.enqueue({ transport: 'staff', path: '/x', formData: {}, onError })
    await flushTasks()

    expect(store.failedTasks).toHaveLength(1)
    expect(mocks.staffUpload).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('drops queued work for a signed-out transport without touching other work', async () => {
    const activeStaff = deferred()
    const onStaffSuccess = vi.fn()
    mocks.staffUpload.mockReturnValueOnce(activeStaff.promise)
    mocks.adminUpload.mockResolvedValueOnce({ ok: true })

    const store = useImageUploadQueueStore()
    store.enqueue({
      transport: 'staff',
      path: '/staff-active',
      formData: { name: 'active' },
      onSuccess: onStaffSuccess,
    })
    store.enqueue({
      transport: 'staff',
      path: '/staff-queued',
      formData: { name: 'queued' },
    })
    store.enqueue({
      transport: 'admin',
      path: '/admin',
      formData: { name: 'admin' },
    })
    await flushTasks()

    expect(store.tasks).toHaveLength(3)
    expect(store.clearTasksByTransport('staff')).toBe(2)
    expect(store.tasks).toHaveLength(1)
    expect(store.tasks[0].transport).toBe('admin')

    activeStaff.resolve({ ok: true })
    await flushTasks()

    expect(onStaffSuccess).not.toHaveBeenCalled()
    expect(store.activeUploads).toBe(0)
  })

  it('clears completed rows without dropping failed work', async () => {
    mocks.adminUpload
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(Object.assign(new Error('失败'), { status: 400 }))

    const store = useImageUploadQueueStore()
    store.enqueue({ path: '/done', formData: {} })
    store.enqueue({ path: '/failed', formData: {} })
    await flushTasks()

    expect(store.completedTasks).toHaveLength(1)
    expect(store.failedTasks).toHaveLength(1)
    expect(store.clearCompleted()).toBe(1)
    expect(store.tasks).toHaveLength(1)
    expect(store.tasks[0].status).toBe('error')
  })
})
