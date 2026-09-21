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
    _applySnapshot(snapshot) {
      this.missingStandardIds = snapshot
        ? snapshot.state.missing.map((entry) => String(entry.standard_id))
        : []
      this.stats = snapshot ? snapshot.stats : this.stats
      return snapshot ? snapshot.state : null
    },
    _publishNotice(notice) {
      if (this.taskSheetOpen) this.queuedNotice = notice
      else this.notice = notice
    },
    setTaskSheetOpen(open) {
      this.taskSheetOpen = Boolean(open)
      if (this.taskSheetOpen || !this.queuedNotice) return
      const queued = this.queuedNotice
      this.queuedNotice = null
      // 首次下载失败：重新把首次下载弹窗打开（弹窗里会显示 errorText），而不是把它
      // 当成一条普通 notice——普通 notice 在拍摄弹层关闭后弹出，反而会再挡一次画面。
      if (queued.reopenFirstPrompt) this.firstPromptOpen = true
      else this.notice = queued
    },
    dismissNotice() {
      this.notice = null
    },
    async refreshStats() {
      const snapshot = this.manifest
        ? await getBrowserCache().snapshot(this.manifest)
        : null
      this._applySnapshot(snapshot)
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
        const state = this._applySnapshot(
          await getBrowserCache().snapshot(this.manifest),
        )
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
        const state = this._applySnapshot(
          await getBrowserCache().snapshot(this.manifest),
        )
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
        // 走 _publishNotice 而不是直接 firstPromptOpen = true：拍摄弹层开着时它会把
        // 提示排进 queuedNotice，等弹层关掉再弹。原来的写法绕过了这套排队机制，
        // 而弹窗的 z-index 又高于拍摄弹层，下载一失败就压在相机画面上。
        if (this.taskSheetOpen) {
          this.queuedNotice = {
            type: 'warning',
            message: this.errorText,
            reopenFirstPrompt: true,
          }
        } else {
          this.firstPromptOpen = true
        }
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
