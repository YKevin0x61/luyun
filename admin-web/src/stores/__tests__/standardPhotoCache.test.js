import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({ cache: null }))

vi.mock('../../utils/standardPhotoCache', () => ({
  createBrowserStandardPhotoCache: () => mocks.cache,
}))

import { useStandardPhotoCacheStore } from '../standardPhotoCache.js'

function fakeCache(overrides = {}) {
  const manifest = {
    version: 'v2',
    standards: [{
      item_id: 1,
      item_name: '案板',
      standard_id: 2,
      byte_size: 10,
      sha256: 'hash',
      image_url: '/api/hygiene/standards/2/image',
    }],
  }
  const cache = {
    loadManifest: vi.fn(async () => manifest),
    inspect: vi.fn(async () => ({
      complete: false,
      missing: manifest.standards,
      completeEntries: [],
      obsolete: [],
    })),
    stats: vi.fn(async () => ({
      entryCount: 1,
      byteSize: 10,
      failureCount: 0,
      missingCount: 1,
      missingBytes: 10,
      baselineReady: false,
      totalCount: 1,
      lastSyncAt: '',
    })),
    sync: vi.fn(async () => ({ status: 'up-to-date', updated: 0, failures: [], missing: [] })),
    downloadAll: vi.fn(async () => ({ updated: 1, failures: [], aborted: false })),
    retryFailed: vi.fn(),
    clear: vi.fn(),
    resolve: vi.fn(),
    release: vi.fn(),
    currentStandardId: vi.fn(),
    ...overrides,
  }
  cache.snapshot = vi.fn(async () => ({
    state: await cache.inspect(),
    stats: await cache.stats(),
  }))
  if (overrides.snapshot) cache.snapshot = overrides.snapshot
  return cache
}

describe('standard photo cache store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const next = fakeCache()
    if (mocks.cache) Object.assign(mocks.cache, next)
    else mocks.cache = next
  })

  it('prompts for the first baseline instead of silently filling a skipped cache', async () => {
    const store = useStandardPhotoCacheStore()
    await store.initialize()
    expect(store.firstPromptOpen).toBe(true)
    expect(mocks.cache.sync).not.toHaveBeenCalled()
  })

  it('updates silently after the baseline was completed', async () => {
    Object.assign(mocks.cache, fakeCache({
      stats: vi.fn(async () => ({
        entryCount: 1,
        byteSize: 10,
        failureCount: 0,
        missingCount: 1,
        missingBytes: 10,
        baselineReady: true,
        totalCount: 1,
        lastSyncAt: '',
      })),
    }))
    const store = useStandardPhotoCacheStore()
    await store.initialize()
    expect(store.firstPromptOpen).toBe(false)
    expect(mocks.cache.sync).toHaveBeenCalled()
  })

  it('never prompts when there are zero standard photos', async () => {
    Object.assign(mocks.cache, fakeCache({
      loadManifest: vi.fn(async () => ({ version: 'empty', standards: [] })),
      inspect: vi.fn(async () => ({
        complete: true,
        missing: [],
        completeEntries: [],
        obsolete: [],
      })),
      stats: vi.fn(async () => ({
        entryCount: 0,
        byteSize: 0,
        failureCount: 0,
        missingCount: 0,
        missingBytes: 0,
        baselineReady: false,
        totalCount: 0,
        lastSyncAt: '',
      })),
    }))
    const store = useStandardPhotoCacheStore()
    await store.initialize()
    expect(store.firstPromptOpen).toBe(false)
    expect(mocks.cache.sync).not.toHaveBeenCalled()
  })

  describe('首次下载失败时的弹窗排队', () => {
    async function storeWithFailedDownload() {
      Object.assign(mocks.cache, fakeCache({
        downloadAll: vi.fn(async () => { throw new Error('网络中断') }),
      }))
      const store = useStandardPhotoCacheStore()
      await store.initialize()
      expect(store.firstPromptOpen).toBe(true)
      return store
    }

    it('弹层关着时直接重开首次下载弹窗', async () => {
      const store = await storeWithFailedDownload()
      await store.startFirstDownload()
      expect(store.errorText).toBe('网络中断')
      expect(store.firstPromptOpen).toBe(true)
      expect(store.queuedNotice).toBeNull()
    })

    it('拍摄弹层开着时排队，不压在相机上', async () => {
      const store = await storeWithFailedDownload()
      // 模拟员工已经打开拍摄弹层（store 只记状态，层级由组件负责）。
      store.setTaskSheetOpen(true)
      await store.startFirstDownload()

      expect(store.firstPromptOpen).toBe(false)
      expect(store.queuedNotice).toMatchObject({ reopenFirstPrompt: true })

      store.setTaskSheetOpen(false)
      expect(store.firstPromptOpen).toBe(true)
      expect(store.queuedNotice).toBeNull()
      // 排队期间不能顺手把它当成普通 notice 弹一次（那等于又挡一次画面）。
      expect(store.notice).toBeNull()
    })

    it('普通通知仍然走 notice，不会被当成首次下载弹窗', async () => {
      const store = useStandardPhotoCacheStore()
      await store.initialize()
      store.skipFirstRun()
      store.setTaskSheetOpen(true)
      store._publishNotice({ type: 'success', message: '已缓存 3 张' })

      expect(store.firstPromptOpen).toBe(false)
      store.setTaskSheetOpen(false)

      expect(store.notice).toEqual({ type: 'success', message: '已缓存 3 张' })
      expect(store.firstPromptOpen).toBe(false)
    })
  })
})
