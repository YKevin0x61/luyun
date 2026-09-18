import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// upload() 用 XMLHttpRequest 取真实上传进度，这里用最小 stub 固定响应，
// 校验两条客户端路径（JSON 的 request 与 multipart 的 upload）对 detail 的语义一致。
class FakeXHR {
  constructor() {
    FakeXHR.last = this
    this.upload = {}
    this.status = 0
    this.responseText = ''
  }

  open(method, url) {
    this.method = method
    this.url = url
  }

  send() {
    FakeXHR.sent = true
  }

  respond(status, body) {
    this.status = status
    this.responseText = body === undefined ? '' : JSON.stringify(body)
    this.onload()
  }
}

const { api } = await import('../client.js')

async function uploadAndFail(status, body) {
  const pending = api.upload('/api/backup/import/apply', new FormData())
  FakeXHR.last.respond(status, body)
  return pending.catch((err) => err)
}

describe('api.upload 的错误语义', () => {
  beforeEach(() => {
    FakeXHR.last = null
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('detail 是对象时取 message，并把结构化 detail 原样带出', async () => {
    const err = await uploadAndFail(409, {
      detail: {
        reason: 'photo_mismatch',
        message: '当前数据引用的照片不在这份备份里：标准图；请确认后强制继续',
        missing: { standard: 2, other: 0 },
      },
    })
    expect(err.message).toBe('当前数据引用的照片不在这份备份里：标准图；请确认后强制继续')
    expect(err.status).toBe(409)
    // 调用方按 reason 分派分支，需要能读到完整 detail
    expect(err.detail.reason).toBe('photo_mismatch')
    expect(err.detail.missing.standard).toBe(2)
  })

  it('detail 是字符串时直接用作文案', async () => {
    const err = await uploadAndFail(400, { detail: 'mode 必须是 merge 或 overwrite' })
    expect(err.message).toBe('mode 必须是 merge 或 overwrite')
    expect(err.detail).toBe('mode 必须是 merge 或 overwrite')
    expect(err.status).toBe(400)
  })

  it('响应体不是 JSON（如反向代理的 413）时给出可操作的提示', async () => {
    const err = await uploadAndFail(413, undefined)
    expect(err.status).toBe(413)
    expect(err.message).toContain('文件过大')
  })

  it('2xx 解析 JSON 并 resolve', async () => {
    const pending = api.upload('/api/backup/import/preview', new FormData())
    FakeXHR.last.respond(200, { success: true, import_token: 'tok-1' })
    await expect(pending).resolves.toEqual({ success: true, import_token: 'tok-1' })
  })

  it('上传到一半的进度回调只给百分比，不改写服务端结论', async () => {
    const onProgress = vi.fn()
    const pending = api.upload('/api/backup/import/preview', new FormData(), onProgress)
    FakeXHR.last.upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 })
    FakeXHR.last.upload.onload()
    FakeXHR.last.respond(200, { success: true })
    await pending
    expect(onProgress).toHaveBeenCalledWith({ loaded: 50, total: 100, percent: 50, done: false })
    expect(onProgress).toHaveBeenCalledWith({ loaded: 1, total: 1, percent: 100, done: true })
  })
})
