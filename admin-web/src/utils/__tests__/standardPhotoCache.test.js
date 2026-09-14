import { describe, expect, it, vi } from 'vitest'
import {
  classifyNetwork,
  createStandardPhotoCache,
  formatChinaSyncTime,
  inspectManifest,
  shouldAutoDownload,
  standardVersionChanged,
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
    async readIndex() { return JSON.parse(JSON.stringify(index)) },
    async writeIndex(next) { Object.assign(index, JSON.parse(JSON.stringify(next))) },
    async put(cacheEntry, blob) { blobs.set(String(cacheEntry.standard_id), blob) },
    async get(cacheEntry) { return blobs.get(String(cacheEntry.standard_id)) || null },
    async exists(cacheEntry) { return blobs.has(String(cacheEntry.standard_id)) },
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

function cacheHarness({ networkKind = 'wifi', files = {}, now = () => 1_700_000_000_000 } = {}) {
  const storage = fakeStorage()
  const cache = createStandardPhotoCache({
    manifestLoader: async () => manifest('v1', []),
    imageLoader: async (standard) => blobFor(files[standard.standard_id]),
    storage,
    network: { getKind: async () => networkKind },
    now,
    hashBytes: async (blob) => `hash-${(await blob.text()).slice(0, 1)}`,
    createObjectUrl: () => `blob:${Math.random()}`,
    revokeObjectUrl: vi.fn(),
  })
  return { cache, storage }
}

describe('standardPhotoCache policy', () => {
  it('classifies Wi-Fi, cellular, unknown and offline without treating unknown as free', () => {
    expect(classifyNetwork({ type: 'wifi' }, true)).toBe('wifi')
    expect(classifyNetwork({ effectiveType: '4g' }, true)).toBe('cellular')
    expect(classifyNetwork({}, true)).toBe('unknown')
    expect(classifyNetwork({}, false)).toBe('offline')
    expect(shouldAutoDownload([entry(1, '123')], 'unknown')).toBe(true)
    expect(shouldAutoDownload([entry(1, '123'), entry(2, '456')], 'unknown')).toBe(true)
    expect(shouldAutoDownload([
      { ...entry(1, '123'), byte_size: 6 * 1024 * 1024 },
      { ...entry(2, '456'), byte_size: 6 * 1024 * 1024 },
    ], 'unknown')).toBe(false)
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

  it('defers a large batch on unknown network but supports a forced update', async () => {
    const one = { ...entry(1, 'A', 'hash-A'), byte_size: 6 * 1024 * 1024 }
    const two = { ...entry(2, 'B', 'hash-B'), byte_size: 6 * 1024 * 1024 }
    const storage = fakeStorage()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v2', [one, two]),
      imageLoader: async (standard) => ({
        arrayBuffer: async () => new Uint8Array(standard.byte_size),
        text: async () => (standard.standard_id === 1 ? 'A' : 'B'),
      }),
      storage,
      network: { getKind: async () => 'unknown' },
      hashBytes: async (blob) => `hash-${await blob.text()}`,
    })
    const deferred = await cache.sync()
    expect(deferred.status).toBe('deferred')
    const forced = await cache.sync({ force: true })
    expect(forced.status).toBe('updated')
    expect(forced.updated).toBe(2)
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

  it('preserves completed images and stops before the next image when aborted', async () => {
    const one = entry(1, 'A', 'hash-A')
    const two = entry(2, 'B', 'hash-B')
    const storage = fakeStorage()
    const controller = new AbortController()
    const cache = createStandardPhotoCache({
      manifestLoader: async () => manifest('v1', [one, two]),
      imageLoader: async (standard) => blobFor(standard.standard_id === 1 ? 'A' : 'B'),
      storage,
      hashBytes: async (blob) => `hash-${await blob.text()}`,
    })
    const result = await cache.downloadAll(null, {
      signal: controller.signal,
      onProgress: () => controller.abort(),
    })
    expect(result.aborted).toBe(true)
    expect(result.updated).toBe(1)
    expect(storage.blobs.has('1')).toBe(true)
    expect(storage.blobs.has('2')).toBe(false)
  })
})
