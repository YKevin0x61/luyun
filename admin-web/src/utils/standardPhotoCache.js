export const STANDARD_PHOTO_CACHE_NAME = 'luyun-hygiene-standard-photos-v1'
export const STANDARD_PHOTO_INDEX_KEY = 'luyun.hygiene.standardPhotoCache.index'
export const STANDARD_PHOTO_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
export const STANDARD_PHOTO_DOWNLOAD_CONCURRENCY = 2

const STANDARD_PHOTO_TOUCH_THROTTLE_MS = 60 * 1000

function emptyIndex() {
  return {
    manifestVersion: '',
    lastSyncAt: '',
    baselineReady: false,
    entries: {},
    failures: {},
  }
}

function normalizeIndex(raw) {
  const value = raw && typeof raw === 'object' ? raw : {}
  const entries = {}
  for (const [key, entry] of Object.entries(
    value.entries && typeof value.entries === 'object' ? value.entries : {},
  )) {
    if (entry && typeof entry === 'object') entries[key] = { ...entry }
  }
  const failures = {}
  for (const [key, failure] of Object.entries(
    value.failures && typeof value.failures === 'object' ? value.failures : {},
  )) {
    if (failure && typeof failure === 'object') {
      failures[key] = {
        ...failure,
        entry: failure.entry && typeof failure.entry === 'object'
          ? { ...failure.entry }
          : failure.entry,
      }
    }
  }
  return {
    manifestVersion: value.manifestVersion || '',
    lastSyncAt: value.lastSyncAt || '',
    baselineReady: Boolean(value.baselineReady),
    entries,
    failures,
  }
}

function entryKey(entry) {
  return String(entry && entry.standard_id)
}

function sameVersion(left, right) {
  return Boolean(
    left
    && right
    && String(left.standard_id) === String(right.standard_id)
    && left.sha256 === right.sha256
    && Number(left.byte_size) === Number(right.byte_size),
  )
}

async function bytesFromValue(value) {
  if (!value) throw new Error('图片响应不是可读取的文件')
  if (value instanceof Uint8Array) return value
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  }
  if (typeof value.arrayBuffer === 'function') {
    return new Uint8Array(await value.arrayBuffer())
  }
  throw new Error('图片响应不是可读取的文件')
}

function blobFromBytes(bytes, contentType) {
  return new Blob([bytes], { type: contentType || 'image/jpeg' })
}

function contentTypeFromValue(value, fallback = 'image/jpeg') {
  if (value && value.headers && typeof value.headers.get === 'function') {
    return value.headers.get('content-type') || fallback
  }
  if (value && typeof value.type === 'string' && value.type) return value.type
  return fallback
}

function toHex(value) {
  if (typeof value === 'string') return value
  return Array.from(value || [], (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function cacheUrlKey(value) {
  const raw = String(value || '')
  if (!raw) return ''
  try {
    const base = typeof location !== 'undefined' && location.origin
      ? location.origin
      : 'http://luyun.local'
    return new URL(raw, base).href
  } catch {
    return raw
  }
}

function isQuotaError(error) {
  const name = String(error && error.name || '')
  const message = String(error && error.message || '')
  return name === 'QuotaExceededError' || /quota|空间不足/i.test(message)
}

export async function sha256Hex(value) {
  const bytes = await bytesFromValue(value)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return toHex(new Uint8Array(digest))
}

export function inspectManifest(index, manifest) {
  const current = normalizeIndex(index)
  const standards = Array.isArray(manifest && manifest.standards) ? manifest.standards : []
  const currentIds = new Set(standards.map(entryKey))
  const missing = []
  const completeEntries = []
  for (const entry of standards) {
    const cached = current.entries[entryKey(entry)]
    if (sameVersion(cached, entry)) completeEntries.push(entry)
    else missing.push(entry)
  }
  const obsolete = Object.values(current.entries).filter(
    (entry) => !currentIds.has(entryKey(entry)),
  )
  return {
    complete: missing.length === 0,
    missing,
    completeEntries,
    obsolete,
    totalBytes: standards.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
  }
}

export function standardVersionChanged(seenStandardId, latestStandardId) {
  if (!seenStandardId || !latestStandardId) return false
  return String(seenStandardId) !== String(latestStandardId)
}

export function formatChinaSyncTime(value) {
  const stamp = Date.parse(String(value || ''))
  if (!Number.isFinite(stamp)) return ''
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(stamp))
  const pick = (type) => (parts.find((part) => part.type === type) || {}).value || ''
  return `${pick('year')}-${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')} (+08:00)`
}

export function createStandardPhotoCache({
  manifestLoader,
  imageLoader,
  storage,
  now = () => Date.now(),
  hashBytes = sha256Hex,
  createObjectUrl = (blob) => URL.createObjectURL(blob),
  revokeObjectUrl = (url) => URL.revokeObjectURL(url),
} = {}) {
  if (typeof manifestLoader !== 'function') throw new Error('manifestLoader is required')
  if (typeof imageLoader !== 'function') throw new Error('imageLoader is required')
  if (!storage) throw new Error('storage is required')

  let latestManifest = null
  const objectUrls = new Set()
  let mutationTail = Promise.resolve()
  let invalidationGeneration = 0

  function enqueueExclusive(task) {
    const run = mutationTail.then(task, task)
    mutationTail = run.catch(() => {})
    return run
  }

  async function readIndex() {
    return normalizeIndex(await storage.readIndex())
  }

  async function writeIndex(index) {
    await storage.writeIndex(normalizeIndex(index))
  }

  function withIndex(mutator) {
    return enqueueExclusive(async () => {
      const index = await readIndex()
      const outcome = (await mutator(index)) || {}
      if (outcome.changed !== false) await writeIndex(index)
      return outcome.value
    })
  }

  function applyManifestInPlace(index, manifest) {
    const ids = new Set((manifest.standards || []).map(entryKey))
    const stamp = new Date(now()).toISOString()
    for (const [key, entry] of Object.entries(index.entries)) {
      if (ids.has(key)) {
        delete entry.orphanedAt
      } else if (!entry.orphanedAt) {
        entry.orphanedAt = stamp
      }
    }
    for (const key of Object.keys(index.failures)) {
      if (!ids.has(key)) delete index.failures[key]
    }
    index.manifestVersion = manifest.version || ''
  }

  async function pruneExpiredInPlace(index) {
    const cutoff = now() - STANDARD_PHOTO_RETENTION_MS
    let changed = false
    for (const entry of Object.values(index.entries)) {
      if (!entry.orphanedAt) continue
      const orphanedAt = Date.parse(entry.orphanedAt)
      if (Number.isFinite(orphanedAt) && orphanedAt <= cutoff) {
        await storage.remove(entry)
        delete index.entries[entryKey(entry)]
        changed = true
      }
    }
    return changed
  }

  async function pruneOldestInPlace(index) {
    const candidates = Object.values(index.entries)
      .filter((entry) => entry.orphanedAt)
      .sort((left, right) => Number(left.lastAccessAt || 0) - Number(right.lastAccessAt || 0))
    if (!candidates.length) return false
    await storage.remove(candidates[0])
    delete index.entries[entryKey(candidates[0])]
    return true
  }

  async function cachedUrlSet(index) {
    if (typeof storage.listUrls === 'function') {
      try {
        const urls = await storage.listUrls()
        return new Set((urls || []).map(cacheUrlKey))
      } catch {
        // Fall back to per-entry probes on browsers without bulk key listing.
      }
    }
    const urls = []
    for (const entry of Object.values(index.entries)) {
      if (await storage.exists(entry)) urls.push(cacheUrlKey(entry.image_url))
    }
    return new Set(urls)
  }

  function buildStats(index, state) {
    return {
      entryCount: Object.keys(index.entries).length,
      byteSize: Object.values(index.entries)
        .reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
      failureCount: Object.keys(index.failures).length,
      baselineReady: index.baselineReady,
      totalCount: state && Array.isArray(state.completeEntries)
        ? state.completeEntries.length + state.missing.length
        : 0,
      missingCount: state ? state.missing.length : 0,
      missingBytes: state
        ? state.missing.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0)
        : 0,
      lastSyncAt: index.lastSyncAt || '',
    }
  }

  async function loadManifest() {
    latestManifest = await manifestLoader()
    return latestManifest
  }

  async function snapshot(manifest = latestManifest) {
    const effective = manifest || (await loadManifest())
    return withIndex(async (index) => {
      const urls = await cachedUrlSet(index)
      let changed = false
      for (const [key, entry] of Object.entries(index.entries)) {
        if (!urls.has(cacheUrlKey(entry.image_url))) {
          delete index.entries[key]
          changed = true
        }
      }
      const state = inspectManifest(index, effective)
      return {
        value: { state, stats: buildStats(index, state) },
        changed,
      }
    })
  }

  async function inspect(manifest = latestManifest) {
    return (await snapshot(manifest)).state
  }

  async function commitVerified(entry, blob, bytes, hash) {
    await withIndex(async (index) => {
      const put = () => storage.put(entry, blob)
      try {
        await put()
      } catch (error) {
        if (!isQuotaError(error) || !(await pruneOldestInPlace(index))) throw error
        await put()
      }
      index.entries[entryKey(entry)] = {
        ...entry,
        sha256: hash,
        byte_size: bytes.length,
        verifiedAt: new Date(now()).toISOString(),
        lastAccessAt: now(),
      }
      delete index.failures[entryKey(entry)]
      return { changed: true }
    })
  }

  async function recordFailure(entry, error) {
    await withIndex((index) => {
      index.failures[entryKey(entry)] = {
        entry,
        reason: error && error.message ? error.message : '下载失败',
      }
      return { changed: true }
    })
  }

  async function downloadEntries(entries, { onProgress, signal } = {}) {
    const total = entries.length
    let nextIndex = 0
    let completed = 0
    let downloadedBytes = 0
    let updated = 0
    let aborted = Boolean(signal && signal.aborted)
    const failures = []

    async function downloadOne(entry) {
      try {
        const response = await imageLoader(entry, { signal })
        const bytes = await bytesFromValue(response)
        if (bytes.length !== Number(entry.byte_size)) {
          throw new Error('图片大小不一致')
        }
        const hash = toHex(await hashBytes(bytes))
        if (hash !== entry.sha256) throw new Error('图片校验失败')
        const blob = blobFromBytes(
          bytes,
          contentTypeFromValue(response, entry.content_type),
        )
        await commitVerified(entry, blob, bytes, hash)
        completed += 1
        updated += 1
        downloadedBytes += bytes.length
        if (onProgress) {
          onProgress({ completed, total, downloadedBytes, current: entry })
        }
      } catch (error) {
        if (aborted || (signal && signal.aborted) || String(error && error.name || '') === 'AbortError') {
          aborted = true
          return
        }
        failures.push(entry)
        await recordFailure(entry, error)
        completed += 1
        if (onProgress) {
          onProgress({ completed, total, downloadedBytes, current: entry })
        }
      }
    }

    async function worker() {
      while (!aborted && !(signal && signal.aborted)) {
        const currentIndex = nextIndex
        nextIndex += 1
        if (currentIndex >= total) return
        await downloadOne(entries[currentIndex])
      }
    }

    const workerCount = Math.min(STANDARD_PHOTO_DOWNLOAD_CONCURRENCY, total)
    await Promise.all(Array.from({ length: workerCount }, () => worker()))
    if (signal && signal.aborted) aborted = true
    return { updated, failures, completed, total, downloadedBytes, aborted }
  }

  async function downloadAll(manifest, options = {}) {
    const effective = manifest || (await loadManifest())
    await withIndex(async (index) => {
      applyManifestInPlace(index, effective)
      await pruneExpiredInPlace(index)
      return { changed: true }
    })
    const state = (await snapshot(effective)).state
    const result = await downloadEntries(state.missing, options)
    await withIndex((index) => {
      index.lastSyncAt = new Date(now()).toISOString()
      index.baselineReady = Boolean(!result.aborted && result.failures.length === 0)
      return { changed: true }
    })
    return { ...result, manifest: effective, missing: result.failures }
  }

  async function sync({ manifest, download = false, onProgress } = {}) {
    const effective = manifest || (await loadManifest())
    await withIndex(async (index) => {
      applyManifestInPlace(index, effective)
      await pruneExpiredInPlace(index)
      return { changed: true }
    })
    const state = (await snapshot(effective)).state
    if (!state.missing.length) {
      await withIndex((index) => {
        index.lastSyncAt = new Date(now()).toISOString()
        return { changed: true }
      })
      return { status: 'up-to-date', updated: 0, failures: [], missing: [] }
    }
    // 只提示、不自动下载：原来按网络类型判——wifi 直接下、移动网不足 10MB 也直接
    // 下，员工既不知情（后台悄悄吃流量），也看不到「有新版本」。现在一律返回
    // deferred 让界面提示，真要拉图必须由用户点「立即更新」（download=true）。
    if (!download) {
      return {
        status: 'deferred',
        updated: 0,
        failures: [],
        missing: state.missing,
        totalBytes: state.missing.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
      }
    }
    const result = await downloadEntries(state.missing, { onProgress })
    await withIndex((index) => {
      if (!result.aborted && !result.failures.length) index.baselineReady = true
      index.lastSyncAt = new Date(now()).toISOString()
      return { changed: true }
    })
    return { status: 'updated', ...result, missing: result.failures }
  }

  async function removeCorruptEntry(entry, reason, generation) {
    await withIndex(async (index) => {
      if (generation !== invalidationGeneration) return { changed: false }
      await storage.remove(entry)
      delete index.entries[entryKey(entry)]
      index.failures[entryKey(entry)] = { entry, reason }
      return { changed: true }
    })
  }

  async function resolve(standardId, { touch = true } = {}) {
    const key = String(standardId)
    const operation = await withIndex((index) => ({
      value: {
        entry: index.entries[key] ? { ...index.entries[key] } : null,
        generation: invalidationGeneration,
      },
      changed: false,
    }))
    const snapshotEntry = operation.entry
    if (!snapshotEntry) return null
    try {
      const blob = await storage.get(snapshotEntry)
      if (!blob) throw new Error('缓存内容不存在')
      if (operation.generation !== invalidationGeneration) return null
      if (Number(blob.size) !== Number(snapshotEntry.byte_size)) {
        throw new Error('缓存大小不一致')
      }
      let verifiedAt = snapshotEntry.verifiedAt || ''
      if (!verifiedAt) {
        const hash = toHex(await hashBytes(blob))
        if (hash !== snapshotEntry.sha256) throw new Error('缓存校验失败')
        verifiedAt = new Date(now()).toISOString()
      }
      const lastAccessAt = Number(snapshotEntry.lastAccessAt || 0)
      const shouldTouch = touch && now() - lastAccessAt >= STANDARD_PHOTO_TOUCH_THROTTLE_MS
      if (!snapshotEntry.verifiedAt || shouldTouch) {
        await withIndex((index) => {
          const current = index.entries[key]
          if (!sameVersion(current, snapshotEntry)) return { changed: false }
          if (!current.verifiedAt) current.verifiedAt = verifiedAt
          if (shouldTouch) current.lastAccessAt = now()
          return { changed: true }
        })
      }
      const url = createObjectUrl(blob)
      objectUrls.add(url)
      return { url, entry: snapshotEntry }
    } catch (error) {
      if (operation.generation !== invalidationGeneration) return null
      const reason = error && error.message ? error.message : '缓存损坏'
      await removeCorruptEntry(snapshotEntry, reason, operation.generation)
      return null
    }
  }

  async function retryFailed({ onProgress } = {}) {
    const entries = await withIndex((index) => {
      const failed = Object.values(index.failures)
        .map((failure) => failure.entry)
        .filter(Boolean)
      index.failures = {}
      return { value: failed, changed: true }
    })
    const result = await downloadEntries(entries, { onProgress })
    await withIndex((index) => {
      index.lastSyncAt = new Date(now()).toISOString()
      return { changed: true }
    })
    return result
  }

  async function stats(manifest = latestManifest) {
    if (manifest) return (await snapshot(manifest)).stats
    return withIndex((index) => ({
      value: buildStats(index, null),
      changed: false,
    }))
  }

  async function clear() {
    await enqueueExclusive(async () => {
      invalidationGeneration += 1
      await storage.clear()
      latestManifest = null
    })
  }

  function release(url) {
    if (!url || !objectUrls.has(url)) return
    revokeObjectUrl(url)
    objectUrls.delete(url)
  }

  function currentStandardId(itemId, manifest = latestManifest) {
    const entry = (manifest && manifest.standards || [])
      .find((standard) => Number(standard.item_id) === Number(itemId))
    return entry ? entry.standard_id : null
  }

  return {
    loadManifest,
    snapshot,
    inspect,
    downloadAll,
    sync,
    resolve,
    retryFailed,
    stats,
    clear,
    release,
    currentStandardId,
    getManifest: () => latestManifest,
  }
}

export function createBrowserStandardPhotoCache({
  cacheName = STANDARD_PHOTO_CACHE_NAME,
  indexKey = STANDARD_PHOTO_INDEX_KEY,
} = {}) {
  let memoryIndex = emptyIndex()
  let indexLoaded = false
  let cachePromise = null

  function openCache() {
    if (typeof caches === 'undefined') {
      return Promise.reject(new Error('当前浏览器不支持本地缓存'))
    }
    if (!cachePromise) {
      cachePromise = caches.open(cacheName).catch((error) => {
        cachePromise = null
        throw error
      })
    }
    return cachePromise
  }

  const storage = {
    async readIndex() {
      if (!indexLoaded) {
        indexLoaded = true
        try {
          const raw = localStorage.getItem(indexKey)
          if (raw) memoryIndex = normalizeIndex(JSON.parse(raw))
        } catch {
          memoryIndex = emptyIndex()
        }
      }
      return normalizeIndex(memoryIndex)
    },
    async writeIndex(index) {
      memoryIndex = normalizeIndex(index)
      try {
        localStorage.setItem(indexKey, JSON.stringify(memoryIndex))
      } catch {
        // Cache Storage remains usable even if metadata persistence is unavailable.
      }
    },
    async put(entry, blob) {
      const cache = await openCache()
      await cache.put(
        entry.image_url,
        new Response(blob, {
          headers: { 'Content-Type': blob.type || entry.content_type || 'image/jpeg' },
        }),
      )
    },
    async get(entry) {
      const cache = await openCache()
      const response = await cache.match(entry.image_url)
      return response ? response.blob() : null
    },
    async exists(entry) {
      const cache = await openCache()
      return Boolean(await cache.match(entry.image_url))
    },
    async listUrls() {
      const cache = await openCache()
      const requests = await cache.keys()
      return requests.map((request) => request.url)
    },
    async remove(entry) {
      const cache = await openCache()
      await cache.delete(entry.image_url)
    },
    async clear() {
      if (typeof caches !== 'undefined') await caches.delete(cacheName)
      cachePromise = null
      memoryIndex = emptyIndex()
      indexLoaded = true
      try {
        localStorage.removeItem(indexKey)
      } catch {
        // Ignore storage cleanup failures.
      }
    },
  }
  async function loadManifest() {
    const response = await fetch('/api/hygiene/standard-manifest', {
      credentials: 'include',
      cache: 'no-store',
    })
    if (!response.ok) throw new Error(`标准图清单加载失败 (${response.status})`)
    return response.json()
  }
  async function loadImage(entry, { signal } = {}) {
    // 不再强制 no-store：URL 里带着 standard_id（换版就是新 id）与 variant，服务端
    // 也按 immutable 下发，所以放行 HTTP 缓存是安全的。好处是 Cache Storage 被清掉
    // 之后还能从 HTTP 缓存立刻恢复，不必重下几百 MB。
    const response = await fetch(entry.image_url, {
      credentials: 'include',
      cache: 'default',
      signal,
    })
    if (!response.ok) throw new Error(`标准图下载失败 (${response.status})`)
    return response
  }
  return createStandardPhotoCache({
    manifestLoader: loadManifest,
    imageLoader: loadImage,
    storage,
  })
}
