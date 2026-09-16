import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  adminUpload: vi.fn(),
  staffUpload: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  api: { upload: mocks.adminUpload },
}))

vi.mock('../../utils/hygieneStaff', () => ({
  staffUpload: mocks.staffUpload,
}))

import { useImageUploadQueueStore } from '../imageUploadQueue.js'

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
    mocks.staffUpload
      .mockRejectedValueOnce(new Error('网络错误，上传失败'))
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
      error: '网络错误，上传失败',
    })
    expect(store.retry(taskId)).toBe(true)
    await flushTasks()

    expect(store.tasks[0].status).toBe('success')
    expect(store.tasks[0].formData).toBeNull()
    expect(mocks.staffUpload).toHaveBeenCalledTimes(2)
    expect(mocks.staffUpload.mock.calls[1][1]).toEqual(formData)
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
      .mockRejectedValueOnce(new Error('失败'))

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
