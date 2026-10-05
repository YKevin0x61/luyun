// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// B4：「先下载标准图」那张**阻塞式**弹窗原来每次进页都弹。现场七页各自挂一份面板、
// 每次进页都 `initialize()` 一次，而 `stats.baselineReady` 仍是 false —— 于是同一个浏览器
// 上下文里 zones → daily → data 连进三页弹三次，刚点过「稍后下载」照弹。
// 这里断的是「记住那个选择」这件事本身：同一份存储里只弹一次，真正动手之后才重新问。
const mocks = vi.hoisted(() => ({ cache: null }))

vi.mock('../../utils/standardPhotoCache', () => ({
  createBrowserStandardPhotoCache: () => mocks.cache,
}))

import { PROMPT_DEFERRED_KEY, useStandardPhotoCacheStore } from '../standardPhotoCache.js'

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
    clear: vi.fn(async () => {}),
    resolve: vi.fn(),
    release: vi.fn(),
    currentStandardId: vi.fn(),
    ...overrides,
  }
  cache.snapshot = vi.fn(async () => ({
    state: await cache.inspect(),
    stats: await cache.stats(),
  }))
  return cache
}

/** 换一页 = 面板重新挂载一次（`onMounted` 里都调 `initialize()`），store 与存储都还在。 */
async function enterPage(store) {
  await store.initialize()
  return store.firstPromptOpen
}

describe('「先下载标准图」那张弹窗只弹一次（B4）', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    mocks.cache = fakeCache()
  })

  it('第一次进页照弹；点「稍后下载」之后再进三页都不弹（记在 localStorage 里）', async () => {
    const store = useStandardPhotoCacheStore()

    expect(await enterPage(store)).toBe(true)

    store.skipFirstRun()
    expect(store.firstPromptOpen).toBe(false)
    expect(localStorage.getItem(PROMPT_DEFERRED_KEY)).toBe('1')

    // 连进三页（zones → daily → data 那一次实测的形状）。
    expect(await enterPage(store)).toBe(false)
    expect(await enterPage(store)).toBe(false)
    expect(await enterPage(store)).toBe(false)
  })

  it('换一台设备（没有那份记忆）照弹一次：不是把提示整个关掉了', async () => {
    const store = useStandardPhotoCacheStore()
    expect(await enterPage(store)).toBe(true)
    store.skipFirstRun()

    localStorage.clear()
    expect(await enterPage(store)).toBe(true)
  })

  it('存储写不进去时退回"这一次不弹"，不把面板弄崩', async () => {
    const store = useStandardPhotoCacheStore()
    await enterPage(store)

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => store.skipFirstRun()).not.toThrow()
    expect(store.firstPromptOpen).toBe(false)
    setItem.mockRestore()
  })

  it('真去下载之后那个「稍后」就花掉了：下一次缺图重新问一遍', async () => {
    const store = useStandardPhotoCacheStore()
    await enterPage(store)
    store.skipFirstRun()

    await store.startFirstDownload()
    expect(localStorage.getItem(PROMPT_DEFERRED_KEY)).toBeNull()
    expect(await enterPage(store)).toBe(true)
  })

  it('清掉缓存 = 回到没有基线：提示重新有机会出现', async () => {
    const store = useStandardPhotoCacheStore()
    await enterPage(store)
    store.skipFirstRun()

    await store.clearCache()
    expect(localStorage.getItem(PROMPT_DEFERRED_KEY)).toBeNull()
    expect(await enterPage(store)).toBe(true)
  })
})
