import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getQueue: vi.fn(),
}))

vi.mock('../../utils/storage.js', () => ({
  PrintQueueManager: {
    getQueue: mocks.getQueue,
  },
}))

import { confirmPendingPrintUpdate } from '../pwaUpdate.js'

describe('KDS print queue update confirmation', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    mocks.getQueue.mockReset()
  })

  it('updates immediately when no print jobs remain', async () => {
    mocks.getQueue.mockReturnValue([])
    expect(await confirmPendingPrintUpdate()).toBe(true)
  })

  it('requires explicit confirmation while print jobs remain', async () => {
    mocks.getQueue.mockReturnValue([
      { status: 'pending' },
      { status: 'pending' },
      { status: 'failed' },
    ])
    const showModal = vi.fn((options) => options.success({ confirm: true }))
    vi.stubGlobal('uni', { showModal })

    expect(await confirmPendingPrintUpdate()).toBe(true)
    expect(showModal).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '更新前确认',
        confirmText: '更新并重启',
        content: expect.stringContaining('2 个待打印任务'),
      }),
    )
  })

  it('keeps the old version when confirmation is cancelled or unavailable', async () => {
    mocks.getQueue.mockReturnValue([{ status: 'pending' }])
    vi.stubGlobal('uni', {
      showModal: (options) => options.success({ confirm: false }),
    })
    expect(await confirmPendingPrintUpdate()).toBe(false)

    vi.stubGlobal('uni', undefined)
    expect(await confirmPendingPrintUpdate()).toBe(false)
  })
})
