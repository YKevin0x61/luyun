export const STANDARD_PHOTO_CACHE_NAME = 'luyun-hygiene-standard-photos-v1'
export const STANDARD_PHOTO_INDEX_KEY = 'luyun.hygiene.standardPhotoCache.index'
export const STANDARD_PHOTO_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
export const STANDARD_PHOTO_BATCH_LIMIT_BYTES = 10 * 1024 * 1024

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
  return {
    manifestVersion: value.manifestVersion || '',
    lastSyncAt: value.lastSyncAt || '',
    baselineReady: Boolean(value.baselineReady),
    entries: value.entries && typeof value.entries === 'object' ? value.entries : {},
    failures: value.failures && typeof value.failures === 'object' ? value.failures : {},
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

function responseToBlob(value) {
  if (value && typeof value.blob === 'function') return value.blob()
  return Promise.resolve(value)
}

async function bytesFromBlob(blob) {
  if (!blob || typeof blob.arrayBuffer !== 'function') {
    throw new Error('图片响应不是可读取的文件')
  }
  return new Uint8Array(await blob.arrayBuffer())
}

function toHex(value) {
  if (typeof value === 'string') return value
  return Array.from(value || [], (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function isQuotaError(error) {
  const name = String(error && error.name || '')
  const message = String(error && error.message || '')
  return name === 'QuotaExceededError' || /quota|空间不足/i.test(message)
}

export async function sha256Hex(blob) {
  const bytes = await blob.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return toHex(new Uint8Array(digest))
}

export function classifyNetwork(connection = {}, online = true) {
  if (!online) return 'offline'
  const type = String(connection.type || '').toLowerCase()
  if (type === 'wifi') return 'wifi'
  if (type === 'cellular') return 'cellular'
  const effective = String(connection.effectiveType || '').toLowerCase()
  if (['slow-2g', '2g', '3g', '4g'].includes(effective)) return 'cellular'
  return 'unknown'
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
  const obsolete = Object.values(current.entries).filter((entry) => !currentIds.has(entryKey(entry)))
  return {
    complete: missing.length === 0,
    missing,
    completeEntries,
    obsolete,
    totalBytes: standards.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
  }
}

export function shouldAutoDownload(entries, networkKind) {
  const list = Array.isArray(entries) ? entries : []
  if (!list.length) return false
  if (networkKind === 'wifi') return true
  if (networkKind === 'offline') return false
  if (list.length === 1) return true
  const total = list.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0)
  return total <= STANDARD_PHOTO_BATCH_LIMIT_BYTES
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
  network = {},
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

  async function readIndex() {
    return normalizeIndex(await storage.readIndex())
  }

  async function writeIndex(index) {
    await storage.writeIndex(normalizeIndex(index))
  }

  function applyManifest(index, manifest) {
    const next = normalizeIndex(index)
    const ids = new Set((manifest.standards || []).map(entryKey))
    const stamp = new Date(now()).toISOString()
    for (const [key, entry] of Object.entries(next.entries)) {
      if (ids.has(key)) {
        delete entry.orphanedAt
      } else if (!entry.orphanedAt) {
        entry.orphanedAt = stamp
      }
    }
    for (const key of Object.keys(next.failures)) {
      if (!ids.has(key)) delete next.failures[key]
    }
    next.manifestVersion = manifest.version || ''
    return next
  }

  async function loadManifest() {
    latestManifest = await manifestLoader()
    return latestManifest
  }

  async function inspect(manifest = latestManifest) {
    const index = await readIndex()
    const effective = manifest || (await loadManifest())
    const state = inspectManifest(index, effective)
    if (!state.completeEntries.length) return state
    const present = []
    const missing = [...state.missing]
    let changed = false
    for (const entry of state.completeEntries) {
      if (await storage.exists(entry)) present.push(entry)
      else {
        missing.push(entry)
        delete index.entries[entryKey(entry)]
        changed = true
      }
    }
    if (changed) await writeIndex(index)
    return {
      ...state,
      complete: missing.length === 0,
      missing,
      completeEntries: present,
    }
  }

  async function removeEntry(index, entry) {
    await storage.remove(entry)
    delete index.entries[entryKey(entry)]
  }

  async function pruneExpired(index) {
    const cutoff = now() - STANDARD_PHOTO_RETENTION_MS
    let changed = false
    for (const entry of Object.values(index.entries)) {
      if (!entry.orphanedAt) continue
      const orphanedAt = Date.parse(entry.orphanedAt)
      if (Number.isFinite(orphanedAt) && orphanedAt <= cutoff) {
        await removeEntry(index, entry)
        changed = true
      }
    }
    if (changed) await writeIndex(index)
  }

  async function pruneOldest(index) {
    const candidates = Object.values(index.entries)
      .filter((entry) => entry.orphanedAt)
      .sort((left, right) => Number(left.lastAccessAt || 0) - Number(right.lastAccessAt || 0))
    if (!candidates.length) return false
    await removeEntry(index, candidates[0])
    await writeIndex(index)
    return true
  }

  async function storeVerified(index, entry, blob, bytes, hash) {
    await storage.put(entry, blob)
    index.entries[entryKey(entry)] = {
      ...entry,
      sha256: hash,
      byte_size: bytes.length,
      lastAccessAt: now(),
    }
    delete index.failures[entryKey(entry)]
    await writeIndex(index)
  }

  async function downloadEntries(index, entries, { onProgress, signal } = {}) {
    const changed = normalizeIndex(index)
    const total = entries.length
    let completed = 0
    let downloadedBytes = 0
    let updated = 0
    let aborted = false
    const failures = []
    for (const entry of entries) {
      if (signal && signal.aborted) {
        aborted = true
        break
      }
      try {
        const response = await imageLoader(entry)
        const blob = await responseToBlob(response)
        const bytes = await bytesFromBlob(blob)
        if (bytes.length !== Number(entry.byte_size)) {
          throw new Error('图片大小不一致')
        }
        const hash = toHex(await hashBytes(blob))
        if (hash !== entry.sha256) throw new Error('图片校验失败')
        try {
          await storeVerified(changed, entry, blob, bytes, hash)
        } catch (error) {
          if (!isQuotaError(error) || !(await pruneOldest(changed))) throw error
          await storeVerified(changed, entry, blob, bytes, hash)
        }
        completed += 1
        updated += 1
        downloadedBytes += bytes.length
        if (onProgress) {
          onProgress({ completed, total, downloadedBytes, current: entry })
        }
      } catch (error) {
        failures.push(entry)
        changed.failures[entryKey(entry)] = {
          entry,
          reason: error && error.message ? error.message : '下载失败',
        }
        await writeIndex(changed)
        completed += 1
        if (onProgress) {
          onProgress({ completed, total, downloadedBytes, current: entry })
        }
      }
    }
    changed.lastSyncAt = new Date(now()).toISOString()
    await writeIndex(changed)
    return { updated, failures, completed, total, downloadedBytes, aborted }
  }

  async function downloadAll(manifest, options = {}) {
    const effective = manifest || (await loadManifest())
    let index = applyManifest(await readIndex(), effective)
    await pruneExpired(index)
    await writeIndex(index)
    const state = await inspect(effective)
    index = await readIndex()
    const result = await downloadEntries(index, state.missing, options)
    index.baselineReady = Boolean(
      !result.aborted && result.failures.length === 0,
    )
    await writeIndex(index)
    return { ...result, manifest: effective, missing: result.failures }
  }

  async function sync({ manifest, force = false, onProgress } = {}) {
    const effective = manifest || (await loadManifest())
    let index = applyManifest(await readIndex(), effective)
    await pruneExpired(index)
    await writeIndex(index)
    const state = await inspect(effective)
    index = await readIndex()
    if (!state.missing.length) {
      index.lastSyncAt = new Date(now()).toISOString()
      await writeIndex(index)
      return { status: 'up-to-date', updated: 0, failures: [], missing: [] }
    }
    const networkKind = network.getKind ? await network.getKind() : 'unknown'
    if (!force && !shouldAutoDownload(state.missing, networkKind)) {
      await writeIndex(index)
      return {
        status: 'deferred',
        updated: 0,
        failures: [],
        missing: state.missing,
        totalBytes: state.missing.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
      }
    }
    const result = await downloadEntries(index, state.missing, { onProgress })
    if (!result.failures.length) index.baselineReady = true
    await writeIndex(index)
    return { status: 'updated', ...result, missing: result.failures }
  }

  async function resolve(standardId, { touch = true } = {}) {
    const index = await readIndex()
    const entry = index.entries[String(standardId)]
    if (!entry) return null
    try {
      const blob = await storage.get(entry)
      if (!blob) throw new Error('缓存内容不存在')
      if (touch) {
        entry.lastAccessAt = now()
        await writeIndex(index)
      }
      const url = createObjectUrl(blob)
      objectUrls.add(url)
      return { url, entry }
    } catch (error) {
      await removeEntry(index, entry)
      index.failures[String(standardId)] = {
        entry,
        reason: error && error.message ? error.message : '缓存损坏',
      }
      await writeIndex(index)
      return null
    }
  }

  async function retryFailed({ onProgress } = {}) {
    const index = await readIndex()
    const entries = Object.values(index.failures).map((failure) => failure.entry).filter(Boolean)
    delete index.failures
    index.failures = {}
    await writeIndex(index)
    return downloadEntries(index, entries, { onProgress })
  }

  async function stats(manifest = latestManifest) {
    let index = await readIndex()
    const state = manifest ? await inspect(manifest) : null
    index = await readIndex()
    return {
      entryCount: Object.keys(index.entries).length,
      byteSize: Object.values(index.entries)
        .reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0),
      failureCount: Object.keys(index.failures).length,
      baselineReady: index.baselineReady,
      totalCount: Array.isArray(manifest && manifest.standards)
        ? manifest.standards.length
        : 0,
      missingCount: state ? state.missing.length : 0,
      missingBytes: state
        ? state.missing.reduce((sum, entry) => sum + Number(entry.byte_size || 0), 0)
        : 0,
      lastSyncAt: index.lastSyncAt || '',
    }
  }

  async function clear() {
    await storage.clear()
    latestManifest = null
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
  const memoryIndex = emptyIndex()
  async function openCache() {
    if (typeof caches === 'undefined') throw new Error('当前浏览器不支持本地缓存')
    return caches.open(cacheName)
  }
  const storage = {
    async readIndex() {
      try {
        return JSON.parse(localStorage.getItem(indexKey) || 'null') || memoryIndex
      } catch {
        return memoryIndex
      }
    },
    async writeIndex(index) {
      Object.assign(memoryIndex, normalizeIndex(index))
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
    async remove(entry) {
      const cache = await openCache()
      await cache.delete(entry.image_url)
    },
    async clear() {
      if (typeof caches !== 'undefined') await caches.delete(cacheName)
      try {
        localStorage.removeItem(indexKey)
      } catch {
        // Ignore storage cleanup failures.
      }
      Object.assign(memoryIndex, emptyIndex())
    },
  }
  const network = {
    async getKind() {
      const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection || {}
      return classifyNetwork(connection, navigator.onLine !== false)
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
  async function loadImage(entry) {
    const response = await fetch(entry.image_url, { credentials: 'include' })
    if (!response.ok) throw new Error(`标准图下载失败 (${response.status})`)
    return response.blob()
  }
  return createStandardPhotoCache({
    manifestLoader: loadManifest,
    imageLoader: loadImage,
    storage,
    network,
  })
}
