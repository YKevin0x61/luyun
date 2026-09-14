import { defineStore } from 'pinia'
import { createBrowserStandardPhotoCache } from '../utils/standardPhotoCache'

let browserCache = null
let firstDownloadController = null

function getBrowserCache() {
  if (!browserCache) browserCache = createBrowserStandardPhotoCache()
  return browserCache
}

export const useStandardPhotoCacheStore = defineStore('standardPhotoCache', {
  state: () => ({
    initialized: false,
    initializing: false,
    manifest: null,
    stats: {
      entryCount: 0,
      byteSize: 0,
      failureCount: 0,
      baselineReady: false,
      totalCount: 0,
      missingCount: 0,
      missingBytes: 0,
      lastSyncAt: '',
    },
    firstPromptOpen: false,
    progress: null,
    deferred: null,
    notice: null,
    queuedNotice: null,
    taskSheetOpen: false,
    managementOpen: false,
    busy: false,
    errorText: '',
    online: typeof navigator === 'undefined' ? true : navigator.onLine !== false,
    missingStandardIds: [],
    cacheGeneration: 0,
  }),
  getters: {
    complete(state) {
      return Number(state.stats.missingCount || 0) === 0
    },
    isMissing(state) {
      return (standardId) => state.missingStandardIds.includes(String(standardId))
    },
  },
  actions: {
    _publishNotice(notice) {
      if (this.taskSheetOpen) this.queuedNotice = notice
      else this.notice = notice
    },
    setTaskSheetOpen(open) {
      this.taskSheetOpen = Boolean(open)
      if (!this.taskSheetOpen && this.queuedNotice) {
        this.notice = this.queuedNotice
        this.queuedNotice = null
      }
    },
    dismissNotice() {
      this.notice = null
    },
    async refreshStats() {
      const state = this.manifest ? await getBrowserCache().inspect(this.manifest) : null
      this.missingStandardIds = state ? state.missing.map((entry) => String(entry.standard_id)) : []
      this.stats = await getBrowserCache().stats(this.manifest)
      return this.stats
    },
    setOnline(value) {
      this.online = Boolean(value)
    },
    async initialize({ prompt = true } = {}) {
      if (this.initializing) return
      this.initializing = true
      this.errorText = ''
      try {
        this.manifest = await getBrowserCache().loadManifest()
        const state = await getBrowserCache().inspect(this.manifest)
        await this.refreshStats()
        this.initialized = true
        if (state.complete) {
          await this.checkForUpdates({ manifest: this.manifest })
        } else if (this.stats.baselineReady) {
          await this.checkForUpdates({ manifest: this.manifest })
        } else {
          this.firstPromptOpen = Boolean(
            prompt
            && this.stats.totalCount > 0
            && state.missing.length > 0,
          )
        }
      } catch (error) {
        this.errorText = error && error.message ? error.message : '无法读取标准图缓存'
      } finally {
        this.initializing = false
      }
    },
    skipFirstRun() {
      this.firstPromptOpen = false
    },
    async startFirstDownload() {
      if (this.busy || !this.manifest) return null
      this.busy = true
      this.firstPromptOpen = false
      this.errorText = ''
      try {
        const state = await getBrowserCache().inspect(this.manifest)
        this.progress = {
          completed: 0,
          total: state.missing.length,
          downloadedBytes: 0,
          current: null,
        }
        firstDownloadController = new AbortController()
        const result = await getBrowserCache().downloadAll(this.manifest, {
          onProgress: (progress) => { this.progress = progress },
          signal: firstDownloadController.signal,
        })
        await this.refreshStats()
        this.cacheGeneration += 1
        if (result.aborted) {
          return result
        }
        if (result.failures.length) {
          this._publishNotice({
            type: 'warning',
            message: `已缓存 ${result.updated} 张，${result.failures.length} 张暂未完成`,
          })
        } else {
          this._publishNotice({ type: 'success', message: `已缓存全部 ${result.updated} 张标准图` })
        }
        return result
      } catch (error) {
        this.errorText = error && error.message ? error.message : '标准图下载失败'
        this.firstPromptOpen = true
        return null
      } finally {
        firstDownloadController = null
        this.progress = null
        this.busy = false
      }
    },
    cancelDownload() {
      if (firstDownloadController) firstDownloadController.abort()
    },
    async checkForUpdates({ force = false, silent = false, manifest = null } = {}) {
      if (this.busy) return null
      if (!this.stats.totalCount) return null
      if (!this.stats.baselineReady && !force) return null
      this.busy = true
      this.errorText = ''
      try {
        this.manifest = manifest || await getBrowserCache().loadManifest()
        const result = await getBrowserCache().sync({
          manifest: this.manifest,
          force,
        })
        if (result.status === 'deferred') {
          this.deferred = result
        } else {
          this.deferred = null
          if (result.updated > 0 && !silent) {
            this._publishNotice(
              result.failures.length
                ? {
                    type: 'warning',
                    message: `已更新 ${result.updated} 张，${result.failures.length} 张暂未更新`,
                  }
                : { type: 'success', message: `已更新 ${result.updated} 张标准图` },
            )
          } else if (result.failures.length) {
            this._publishNotice({
              type: 'warning',
              message: `${result.failures.length} 张标准图暂未更新`,
            })
          }
        }
        await this.refreshStats()
        this.cacheGeneration += 1
        return result
      } catch (error) {
        this.errorText = error && error.message ? error.message : '标准图更新失败'
        return null
      } finally {
        this.busy = false
      }
    },
    async retryFailed() {
      if (this.busy) return null
      this.busy = true
      try {
        const result = await getBrowserCache().retryFailed()
        await this.refreshStats()
        this.cacheGeneration += 1
        if (result.updated > 0) {
          this._publishNotice({ type: 'success', message: `已重试完成 ${result.updated} 张标准图` })
        }
        return result
      } catch (error) {
        this.errorText = error && error.message ? error.message : '重试标准图缓存失败'
        return null
      } finally {
        this.busy = false
      }
    },
    async clearCache() {
      if (this.busy) return
      this.busy = true
      try {
        await getBrowserCache().clear()
        this.manifest = await getBrowserCache().loadManifest()
        await this.refreshStats()
        this.cacheGeneration += 1
        this.deferred = null
        this.notice = null
        this.queuedNotice = null
        this.firstPromptOpen = false
      } catch (error) {
        this.errorText = error && error.message ? error.message : '清理缓存失败'
      } finally {
        this.busy = false
      }
    },
    async resolveImage(standardId) {
      if (!standardId) return null
      return getBrowserCache().resolve(standardId)
    },
    releaseImage(url) {
      getBrowserCache().release(url)
    },
    currentStandardId(itemId) {
      return getBrowserCache().currentStandardId(itemId, this.manifest)
    },
  },
})
