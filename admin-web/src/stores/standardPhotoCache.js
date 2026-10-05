import { defineStore } from 'pinia'
import { createBrowserStandardPhotoCache } from '../utils/standardPhotoCache'

let browserCache = null
let firstDownloadController = null

/** 「稍后下载」的记忆键（B4）。
 *
 *  为什么要有它：首次下载那张**阻塞式**弹窗原来是"每次进页都弹"—— 绕开的方式只有
 *  「开始下载」，点「稍后下载」只是把 `firstPromptOpen` 在内存里按下去，而现场七页
 *  各自挂一份面板、每次进页都 `initialize()` 一次，`stats.baselineReady` 仍是 false，
 *  于是又弹。实测同一会话里 zones → daily → data 连进三页弹三次，刚点过「稍后下载」照弹。
 *  记在 localStorage 里（不是内存里）才算"记住"：员工关掉页面、换一页再进来也不该再被
 *  拦一次。缺图的提醒还在 —— 外壳上那颗浮标「标准图缓存 N」与缓存管理面板都照旧。 */
export const PROMPT_DEFERRED_KEY = 'luyun.hygiene.standardPhotoCache.promptDeferred'

/** 读 / 写那一个标记。存储被禁（隐私模式、配额满）时**一律当作没记住**：
 *  读不出来就是"还没选过"，写不进去最多退回原来的行为，不该连带把面板弄崩。 */
function readPromptDeferred() {
  try {
    return globalThis.localStorage?.getItem(PROMPT_DEFERRED_KEY) === '1'
  } catch {
    return false
  }
}

function writePromptDeferred(deferred) {
  try {
    if (deferred) globalThis.localStorage?.setItem(PROMPT_DEFERRED_KEY, '1')
    else globalThis.localStorage?.removeItem(PROMPT_DEFERRED_KEY)
  } catch {
    // 记不住就算了：这一次仍然按调用方的意思收起弹窗（见 `skipFirstRun`）。
  }
}

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
    // 「稍后下载」选过没有（B4）：`initialize()` 里先从 localStorage 读回来，
    // 选过之后**不再自动弹**那张阻塞遮罩。
    promptDeferred: false,
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
          // 还没下过基线：只在**没记过「稍后下载」**时弹那张阻塞遮罩（B4）。
          this.promptDeferred = readPromptDeferred()
          this.firstPromptOpen = Boolean(
            prompt
            && !this.promptDeferred
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
    /** 「稍后下载」：收起弹窗，并且**记住**这个选择（B4）。
     *
     *  记的是"这台设备上这个人不想现在下"：换页、刷新、明天再进来都不再拦他一次。
     *  真要下的时候入口还在两处 —— 浮标「标准图缓存 N」与缓存管理面板里的「下载 N 张」。 */
    skipFirstRun() {
      this.firstPromptOpen = false
      this.promptDeferred = true
      writePromptDeferred(true)
    },
    /** 那个"稍后"的选择在真正动手之后就算花掉了：他开始下载、或者清了缓存从头来过，
     *  下一次缺图时该重新问一遍（否则清完缓存就再也看不到那张提示了）。 */
    _forgetPromptDeferred() {
      this.promptDeferred = false
      writePromptDeferred(false)
    },
    async startFirstDownload() {
      if (this.busy || !this.manifest) return null
      this.busy = true
      this.firstPromptOpen = false
      this.errorText = ''
      this._forgetPromptDeferred()
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
    /**
     * 核对标准图有没有新版本。
     *
     * 默认**只核对、不下载**：拉回清单、比对本机缓存，缺哪几张记进 `deferred`
     * 让界面提示「有 N 张待更新 · 立即更新」。真正拉图要显式 `download: true`
     * （由员工点按钮触发）——自动更新会在员工不知情时吃流量，也让他看不出
     * 「标准图换版了」。首次下载仍走 firstPromptOpen 弹窗，也是员工点的。
     */
    async checkForUpdates({ download = false, silent = false, manifest = null } = {}) {
      if (this.busy) return null
      if (!this.stats.totalCount) return null
      if (!this.stats.baselineReady && !download) return null
      this.busy = true
      this.errorText = ''
      try {
        this.manifest = manifest || await getBrowserCache().loadManifest()
        const result = await getBrowserCache().sync({
          manifest: this.manifest,
          download,
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
        // 清掉缓存 = 回到"这台设备没有基线"，那张一次性提示也该重新有机会出现（B4）。
        this._forgetPromptDeferred()
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
