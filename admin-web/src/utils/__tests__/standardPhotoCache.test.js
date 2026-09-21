import { describe, expect, it, vi } from 'vitest'
import {
  createStandardPhotoCache,
  formatChinaSyncTime,
  inspectManifest,
  standardVersionChanged,
  STANDARD_PHOTO_DOWNLOAD_CONCURRENCY,
} from '../standardPhotoCache.js'

function manifest(version, entries) {
  return { version, generated_at: '2026-09-14T10:00:00+08:00', standards: entries }
}

function entry(standardId, bytes, sha256 = `hash-${standardId}`) {
  return {
    item_id: standardId,
    standard_id: standardId,
    image_url: `/api/hygiene/standards/${standardId}/image`,
    byte_size: bytes.length,
    sha256,
  }
}

function blobFor(text) {
  const bytes = new TextEncoder().encode(text)
  return new Blob([bytes], { type: 'image/jpeg' })
}

function fakeStorage() {
  const index = {
    manifestVersion: '',
    lastSyncAt: '',
    baselineReady: false,
    entries: {},
    failures: {},
  }
  const blobs = new Map()
  return {
    index,
    blobs,
    existsCalls: 0,
    listUrlsCalls: 0,
    async readIndex() { return JSON.parse(JSON.stringify(index)) },
    async writeIndex(next) { Object.assign(index, JSON.parse(JSON.stringify(next))) },
    async put(cacheEntry, blob) { blobs.set(String(cacheEntry.standard_id), blob) },
    async get(cacheEntry) { return blobs.get(String(cacheEntry.standard_id)) || null },
    async exists(cacheEntry) {
      this.existsCalls += 1
      return blobs.has(String(cacheEntry.standard_id))
    },
    async listUrls() {
      this.listUrlsCalls += 1
      return Object.values(index.entries)
        .filter((cacheEntry) => blobs.has(String(cacheEntry.standard_id)))
        .map((cacheEntry) => cacheEntry.image_url)
    },
    async remove(cacheEntry) { blobs.delete(String(cacheEntry.standard_id)) },
    async clear() {
      blobs.clear()
      index.manifestVersion = ''
      index.lastSyncAt = ''
      index.baselineReady = false
      index.entries = {}
      index.failures = {}
    },
  }
}

function hashCharacter(bytes) {
  return `hash-${String.fromCharCode(bytes[0])}`
}

function cacheHarness({ networkKind = 'wifi', files = {}, now = () => 1_700_000_000_000 } = {}) {
  const storage = fakeStorage()
  const cache = createStandardPhotoCache({
    manifestLoader: async () => manifest('v1', []),
    imageLoader: async (standard) => blobFor(files[standard.standard_id]),
    storage,
    network: { getKind: async () => networkKind },
    now,
    hashBytes: async (bytes) => hashCharacter(bytes),
    createObjectUrl: () => `blob:${Math.random()}`,
    revokeObjectUrl: vi.fn(),
  })
  return { cache, storage }
}

describe('standardPhotoCache policy', () => {
  it('flags a version change and formats the sync stamp', () => {
    // 「按网络类型决定要不要自动下载」的判据已整条移除：现在只提示、手动更新。
    expect(standardVersionChanged(3, 4)).toBe(true)
    expect(standardVersionChanged(4, '4')).toBe(false)
    expect(formatChinaSyncTime('2026-09-14T17:06:00.000Z')).toBe(
      '2026-09-15 01:06 (+08:00)',
    )
  })

  it('inspects missing and obsolete versions independently of implementation details', () => {
    const first = entry(1, 'A', 'hash-A')
    const second = entry(2, 'B', 'hash-B')
    const result = inspectManifest({
      entries: { 1: first, 3: { ...entry(3, 'C', 'hash-C'), orphanedAt: 'old' } },
    }, manifest('v2', [first, second]))
    expect(result.complete).toBe(false)
    expect(result.missing).toEqual([second])
    expect(result.obsolete.map((row) => row.standard_id)).toEqual([3])
  })
})

describe('createStandardPhotoCache', () => {
  it('downloads, verifies and resolves a complete manifest', async () => {
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      network: { getKind: async () => 'wifi' },
      hashBytes: async () => 'hash-A',
      createObjectUrl: () => 'blob:one',
    })
    const progress = []
    const result = await cache.downloadAll(null, { onProgress: (value) => progress.push(value) })
    expect(result.updated).toBe(1)
    expect(progress).toEqual([{ completed: 1, total: 1, downloadedBytes: 1, current: first }])
    expect((await cache.inspect()).complete).toBe(true)
    expect(storage.index.baselineReady).toBe(true)
    expect(storage.index.lastSyncAt).toBeTruthy()
    expect(await cache.resolve(1)).toEqual({ url: 'blob:one', entry: expect.objectContaining({ standard_id: 1 }) })
  })

  it('never commits a version with the wrong hash', async () => {
    const first = entry(1, 'A', 'expected-hash')
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      hashBytes: async () => 'wrong-hash',
    })
    const result = await cache.downloadAll()
    expect(result.failures).toEqual([first])
    expect(storage.blobs.size).toBe(0)
  })

  it('treats an index entry with missing bytes as incomplete', async () => {
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    storage.index.entries['1'] = { ...first, lastAccessAt: 1 }
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      hashBytes: async () => 'hash-A',
    })
    const state = await cache.inspect(manifest('v1', [first]))
    expect(state.complete).toBe(false)
    expect(state.missing).toEqual([first])
  })

  it('inspects by default and only downloads when the user asks', async () => {
    const one = { ...entry(1, 'A', 'hash-A'), byte_size: 6 * 1024 * 1024 }
    const two = { ...entry(2, 'B', 'hash-B'), byte_size: 6 * 1024 * 1024 }
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v2', [one, two]),
      imageLoader: async (standard) => {
        const bytes = new Uint8Array(standard.byte_size)
        bytes[0] = standard.standard_id === 1 ? 65 : 66
        return new Blob([bytes], { type: 'image/jpeg' })
      },
      storage,
      hashBytes: async (bytes) => hashCharacter(bytes),
    })
    // 默认只核对：有缺图就返回 deferred 让界面提示，不偷偷下载。
    const inspected = await cache.sync()
    expect(inspected.status).toBe('deferred')
    expect(inspected.missing).toHaveLength(2)
    // 员工点了「立即更新」才真的拉。
    const applied = await cache.sync({ download: true })
    expect(applied.status).toBe('updated')
    expect(applied.updated).toBe(2)
    expect(storage.index.lastSyncAt).toBeTruthy()
  })

  it('keeps two downloads in flight and preserves manifest order for progress', async () => {
    const rows = [
      entry(1, 'A', 'hash-A'),
      entry(2, 'B', 'hash-B'),
      entry(3, 'C', 'hash-C'),
      entry(4, 'D', 'hash-D'),
    ]
    const storage = fakeStorage()
    let active = 0
    let peak = 0
    const started = []
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', rows),
      imageLoader: async (standard) => {
        active += 1
        peak = Math.max(peak, active)
        started.push(standard.standard_id)
        await new Promise((resolve) => setTimeout(resolve, 0))
        active -= 1
        return blobFor(String.fromCharCode(64 + standard.standard_id))
      },
      storage,
      hashBytes: async (bytes) => hashCharacter(bytes),
    })
    const progress = []
    const result = await cache.downloadAll(null, {
      onProgress: (value) => progress.push(value.current.standard_id),
    })
    expect(peak).toBe(STANDARD_PHOTO_DOWNLOAD_CONCURRENCY)
    expect(started.slice(0, STANDARD_PHOTO_DOWNLOAD_CONCURRENCY)).toEqual([1, 2])
    expect(result.updated).toBe(4)
    expect(new Set(progress)).toEqual(new Set([1, 2, 3, 4]))
  })

  it('reads each response body once and uses one bulk cache listing for inspection', async () => {
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    const arrayBuffer = vi.fn(async () => new Uint8Array([65]).buffer)
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => ({ arrayBuffer, headers: { get: () => 'image/jpeg' } }),
      storage,
      hashBytes: async (bytes) => hashCharacter(bytes),
    })
    await cache.downloadAll()
    storage.listUrlsCalls = 0
    await cache.inspect(manifest('v1', [first]))
    expect(arrayBuffer).toHaveBeenCalledTimes(1)
    expect(storage.listUrlsCalls).toBe(1)
    expect(storage.existsCalls).toBe(0)
  })

  it('keeps current versions, expires old versions after seven days and reports stats', async () => {
    let currentNow = 1_700_000_000_000
    const one = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [one]),
      imageLoader: async () => blobFor('A'),
      storage,
      now: () => currentNow,
      hashBytes: async () => 'hash-A',
    })
    await cache.downloadAll()
    await cache.sync({ manifest: manifest('v2', []) })
    currentNow += 7 * 24 * 60 * 60 * 1000 + 1
    await cache.sync({ manifest: manifest('v3', []) })
    expect(storage.blobs.size).toBe(0)
    expect(await cache.stats(manifest('v3', []))).toEqual(expect.objectContaining({
      entryCount: 0,
      byteSize: 0,
      missingCount: 0,
    }))
  })

  it('prunes an old version before failing a new current version on quota', async () => {
    const old = entry(1, 'OLD', 'hash-OLD')
    const current = entry(2, 'NEW', 'hash-NEW')
    const storage = fakeStorage()
    storage.index.entries['1'] = {
      ...old,
      lastAccessAt: 100,
      orphanedAt: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    }
    storage.blobs.set('1', blobFor('OLD'))
    let failed = false
    const originalPut = storage.put
    storage.put = async (cacheEntry, blob) => {
      if (!failed && String(cacheEntry.standard_id) === '2') {
        failed = true
        const error = new Error('quota exceeded')
        error.name = 'QuotaExceededError'
        throw error
      }
      return originalPut(cacheEntry, blob)
    }
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v2', [current]),
      imageLoader: async () => blobFor('NEW'),
      storage,
      hashBytes: async () => 'hash-NEW',
    })
    const result = await cache.downloadAll()
    expect(result.updated).toBe(1)
    expect(storage.blobs.has('1')).toBe(false)
    expect(storage.blobs.has('2')).toBe(true)
  })

  it('stops starting new downloads after abort and keeps committed images', async () => {
    const rows = [entry(1, 'A', 'hash-A'), entry(2, 'B', 'hash-B'), entry(3, 'C', 'hash-C')]
    const storage = fakeStorage()
    const controller = new AbortController()
    const calls = []
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', rows),
      imageLoader: async (standard, { signal }) => {
        calls.push(standard.standard_id)
        expect(signal).toBe(controller.signal)
        if (standard.standard_id === 1) {
          controller.abort()
          return blobFor('A')
        }
        throw new Error('should not start')
      },
      storage,
      hashBytes: async (bytes) => hashCharacter(bytes),
    })
    const result = await cache.downloadAll(null, { signal: controller.signal })
    expect(calls).toEqual([1])
    expect(result.aborted).toBe(true)
    expect(result.updated).toBe(1)
    expect(storage.blobs.has('1')).toBe(true)
    expect(storage.blobs.has('2')).toBe(false)
  })

  it('removes a corrupted cached file and records it as a failure', async () => {
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      hashBytes: async () => 'hash-A',
    })
    await cache.downloadAll()
    storage.blobs.set('1', blobFor('short'))
    expect(await cache.resolve(1)).toBeNull()
    expect(storage.index.entries['1']).toBeUndefined()
    expect(storage.index.failures['1'].reason).toBe('缓存大小不一致')
  })

  it('verifies legacy entries once and throttles repeated access writes', async () => {
    let currentNow = 1_700_000_000_000
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    storage.index.entries['1'] = { ...first, lastAccessAt: currentNow - 120_000 }
    storage.blobs.set('1', blobFor('A'))
    const writeIndex = vi.spyOn(storage, 'writeIndex')
    const hash = vi.fn(async () => 'hash-A')
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      now: () => currentNow,
      hashBytes: hash,
      createObjectUrl: () => 'blob:one',
    })
    await cache.resolve(1)
    currentNow += 1_000
    await cache.resolve(1)
    expect(hash).toHaveBeenCalledTimes(1)
    expect(writeIndex).toHaveBeenCalledTimes(1)
    expect(storage.index.entries['1'].verifiedAt).toBeTruthy()
  })

  it('does not repopulate index failures when cache is cleared during resolve', async () => {
    const first = entry(1, 'A', 'hash-A')
    const storage = fakeStorage()
    storage.index.entries['1'] = { ...first, verifiedAt: 'already-verified' }
    let releaseGet
    storage.get = () => new Promise((resolve) => { releaseGet = resolve })
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [first]),
      imageLoader: async () => blobFor('A'),
      storage,
      hashBytes: async () => 'hash-A',
      createObjectUrl: () => 'blob:one',
    })
    const pending = cache.resolve(1)
    await vi.waitFor(() => expect(releaseGet).toBeTypeOf('function'))
    await cache.clear()
    releaseGet(blobFor('A'))
    expect(await pending).toBeNull()
    expect(storage.index.entries).toEqual({})
    expect(storage.index.failures).toEqual({})
  })
})
