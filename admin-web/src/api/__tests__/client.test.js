import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, setUnauthorizedHandler } from '../client.js'

// 轻量 FakeXHR：记录回调，由测试同步驱动 upload / onload 等事件，
// 用来验证 client.js 中 upload() 的 XHR 行为（进度回调、401、错误 detail 等）。
class FakeXHR {
  constructor() {
    this.upload = {}
    this.status = 0
    this.responseText = ''
    this.withCredentials = false
    FakeXHR.instances.push(this)
  }
  open(method, url) {
    this.method = method
    this.url = url
  }
  send(body) {
    this.body = body
  }
  emitUploadProgress(loaded, total) {
    if (this.upload.onprogress) {
      this.upload.onprogress({ lengthComputable: true, loaded, total })
    }
  }
  emitUploadDone() {
    if (this.upload.onload) this.upload.onload()
  }
  emitLoad(status, responseText) {
    this.status = status
    this.responseText = responseText
    this.onload()
  }
}
FakeXHR.instances = []

beforeEach(() => {
  FakeXHR.instances = []
  global.XMLHttpRequest = FakeXHR
  global.window = { location: { pathname: '/admin', search: '', href: '' } }
})

afterEach(() => {
  setUnauthorizedHandler(null)
  delete global.XMLHttpRequest
  delete global.window
})

describe('api.upload', () => {
  it('通过 onProgress 依次上报百分比，并在字节传完后 done=true', async () => {
    const events = []
    const promise = api.upload('/api/backup/import/preview', new FormData(), (e) => events.push(e))
    const xhr = FakeXHR.instances[0]

    xhr.emitUploadProgress(25, 100)
    xhr.emitUploadProgress(80, 100)
    xhr.emitUploadDone()
    xhr.emitLoad(200, JSON.stringify({ success: true }))

    await expect(promise).resolves.toEqual({ success: true })
    expect(events).toEqual([
      { loaded: 25, total: 100, percent: 25, done: false },
      { loaded: 80, total: 100, percent: 80, done: false },
      { loaded: 1, total: 1, percent: 100, done: true },
    ])
  })

  it('2xx 时 resolve 解析后的 JSON', async () => {
    const promise = api.upload('/api/backup/import/apply', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(200, JSON.stringify({ success: true, snapshot_ts: '20260720_130000' }))
    await expect(promise).resolves.toEqual({ success: true, snapshot_ts: '20260720_130000' })
  })

  it('非 2xx 时抛出带 detail 的 Error 并携带 status', async () => {
    const promise = api.upload('/api/backup/import/preview', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(400, JSON.stringify({ detail: '口令错误或文件损坏' }))
    await expect(promise).rejects.toMatchObject({ message: '口令错误或文件损坏', status: 400 })
  })

  it('413（代理返回 HTML 非 JSON）时给出可操作的文件过大提示', async () => {
    const promise = api.upload('/api/backup/import/preview', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(413, '<html><body><h1>413 Request Entity Too Large</h1></body></html>')
    await expect(promise).rejects.toMatchObject({ status: 413 })
    await expect(promise).rejects.toThrow(/文件过大/)
  })

  it('401 且非豁免路由时走客户端导航（不再整页重载）并 reject', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    const promise = api.upload('/api/backup/import/apply', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(401, '')
    await expect(promise).rejects.toThrow('未登录，正在跳转登录页')
    expect(handler).toHaveBeenCalledTimes(1)
    expect(global.window.location.href).toBe('')
  })

  it('401 且处于 /settings 独立鉴权路由时不跳转，按错误处理', async () => {
    global.window.location.pathname = '/settings'
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    const promise = api.upload('/api/backup/import/apply', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(401, JSON.stringify({ detail: '会话已过期' }))
    await expect(promise).rejects.toMatchObject({ message: '会话已过期', status: 401 })
    expect(handler).not.toHaveBeenCalled()
  })

  it('401 且处于配方阅读面时不跳登录', async () => {
    global.window.location.pathname = '/workbench/kitchen/recipe/detail'
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    const promise = api.upload('/api/recipes/stations/x/import', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(401, JSON.stringify({ detail: '需要登录' }))
    await expect(promise).rejects.toMatchObject({ message: '需要登录', status: 401 })
    expect(handler).not.toHaveBeenCalled()
  })

  it('不传 onProgress 时兼容旧行为（不注册 upload 回调）', async () => {
    const promise = api.upload('/api/recipes/stations/x/import', new FormData())
    const xhr = FakeXHR.instances[0]
    expect(xhr.upload.onprogress).toBeUndefined()
    expect(xhr.upload.onload).toBeUndefined()
    xhr.emitLoad(200, JSON.stringify({ ok: 1 }))
    await expect(promise).resolves.toEqual({ ok: 1 })
  })

  it('网络错误时 reject', async () => {
    const promise = api.upload('/api/backup/import/preview', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.onerror()
    await expect(promise).rejects.toThrow('网络错误，上传失败')
  })
})

// ---- request 超时（全局网络层加固）--------------------------------------------
// 用 fake timers 手动推进时间，不真的等 20 秒；fetch 用「永不 resolve」的 Promise 模拟
// 商场 AP 假死（连得上、不回包），并在 signal abort 时按浏览器行为 reject AbortError。
// 注意：上面的 beforeEach 还会装 FakeXHR / window，这里只在 describe 内叠加 fake timers，
// 结束时恢复真实计时器，避免影响文件里已有的 upload 用例。

function hangingFetch() {
  return vi.fn(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      }),
  )
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

// 把 promise 的落定结果收成一个变量，便于断言「到点前还在 loading」。
function track(promise) {
  const state = { settled: false, value: undefined, error: undefined }
  promise.then(
    (value) => {
      state.settled = true
      state.value = value
    },
    (error) => {
      state.settled = true
      state.error = error
    },
  )
  return state
}

describe('api 请求超时', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('默认 20000ms 没响应时抛 code=timeout，文案与网络错误分开', async () => {
    const fetchMock = hangingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const state = track(api.get('/api/orders'))
    await vi.advanceTimersByTimeAsync(19999)
    // 还没到 20 秒：仍在 loading，不能提前把慢请求误杀。
    expect(state.settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)

    expect(state.settled).toBe(true)
    expect(state.error.message).toBe('请求超时，请检查网络后重试')
    expect(state.error.code).toBe('timeout')
    // 到点确实 abort 了底层 fetch，而不是只报错不停请求。
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
    // 完成后要清掉定时器，不能留下悬挂的 setTimeout。
    expect(vi.getTimerCount()).toBe(0)
  })

  it('调用方可用 options.timeoutMs 覆盖默认超时（api.get 第 5 个参数）', async () => {
    const fetchMock = hangingFetch()
    vi.stubGlobal('fetch', fetchMock)

    const state = track(api.get('/api/orders', null, null, null, { timeoutMs: 5000 }))
    await vi.advanceTimersByTimeAsync(4999)
    expect(state.settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)

    expect(state.error.message).toBe('请求超时，请检查网络后重试')
    expect(state.error.code).toBe('timeout')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('调用方自带 signal 取消时按网络错误处理，不被误判成超时', async () => {
    vi.stubGlobal('fetch', hangingFetch())

    const controller = new AbortController()
    const state = track(api.get('/api/orders', null, controller.signal))
    controller.abort()
    // abort 是同步触发的，落到 request 的 catch / finally 还要走一轮微任务。
    await vi.advanceTimersByTimeAsync(0)

    expect(state.settled).toBe(true)
    expect(state.error.message).toBe('网络连不上，请检查网络后重试')
    expect(state.error.code).toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('正常 200 不受影响：照旧 resolve 解析后的 JSON 并清掉定时器', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { items: [1, 2] })))

    await expect(api.get('/api/orders')).resolves.toEqual({ items: [1, 2] })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('网络错误立即拒绝，仍抛「网络连不上」且不带 code=timeout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('fetch failed')),
    )

    await expect(api.get('/api/orders')).rejects.toMatchObject({
      message: '网络连不上，请检查网络后重试',
    })
    // 不能把「立刻连不上」标成超时，否则调用方会误以为可以无脑重试。
    const err = await api.get('/api/orders').catch((e) => e)
    expect(err.code).toBeUndefined()
  })

  it('401 保持既有语义：调用注入的 unauthorizedHandler 并抛「未登录」', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        headers: new Headers(),
        json: async () => ({}),
        text: async () => '',
      }),
    )

    await expect(api.get('/api/orders')).rejects.toThrow('未登录，正在跳转登录页')
    expect(handler).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('上传默认 120000ms 超时，可用 api.upload 第 4 个参数覆盖；超时抛 code=timeout', async () => {
    const promise = api.upload('/api/recipes/stations/x/import', new FormData())
    const xhr = FakeXHR.instances[0]
    expect(xhr.timeout).toBe(120000)
    xhr.ontimeout()
    await expect(promise).rejects.toMatchObject({
      code: 'timeout',
      message: '上传超时，请检查网络后重试',
    })

    // 大文件上传可以显式放宽：传了就用传的值，不再吃默认的 120 秒。
    const custom = api.upload('/api/recipes/stations/x/import', new FormData(), null, {
      timeoutMs: 600000,
    })
    const customXhr = FakeXHR.instances[1]
    expect(customXhr.timeout).toBe(600000)
    customXhr.emitLoad(200, JSON.stringify({ ok: true }))
    await expect(custom).resolves.toEqual({ ok: true })
  })
})

