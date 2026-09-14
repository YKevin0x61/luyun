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
  return {
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
})
