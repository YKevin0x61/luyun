import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { api } from '../client.js'

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

  it('401 且非独立鉴权路由时跳转登录并 reject', async () => {
    const promise = api.upload('/api/backup/import/apply', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(401, '')
    await expect(promise).rejects.toThrow('未登录，正在跳转登录页')
    expect(global.window.location.href).toContain('/login?next=')
  })

  it('401 且处于 /setup 独立鉴权路由时不跳转，按错误处理', async () => {
    global.window.location.pathname = '/setup'
    const promise = api.upload('/api/backup/import/apply', new FormData())
    const xhr = FakeXHR.instances[0]
    xhr.emitLoad(401, JSON.stringify({ detail: '会话已过期' }))
    await expect(promise).rejects.toMatchObject({ message: '会话已过期', status: 401 })
    expect(global.window.location.href).toBe('')
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
