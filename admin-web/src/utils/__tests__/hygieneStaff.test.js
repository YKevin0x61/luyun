import { afterEach, describe, expect, it, vi } from 'vitest'
import { staffUpload } from '../hygieneStaff'

class FakeUpload {
  onprogress = null
  onload = null
}

class FakeXhr {
  constructor() {
    this.upload = new FakeUpload()
    this.withCredentials = false
    this.status = FakeXhr.prototype.status
    this.responseText = FakeXhr.prototype.responseText
  }

  open(_method, _path) {}

  send() {
    queueMicrotask(() => {
      this.upload.onprogress?.({ lengthComputable: true, total: 100, loaded: 42 })
      this.upload.onload?.()
      this.onload?.()
    })
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('staffUpload', () => {
  it('reports byte progress and resolves JSON', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    FakeXhr.prototype.status = 200
    FakeXhr.prototype.responseText = JSON.stringify({ ok: true })
    const progress = []
    const result = await staffUpload('/upload', {}, (next) => progress.push(next))

    expect(result).toEqual({ ok: true })
    expect(progress).toEqual([
      { percent: 42, done: false },
      { percent: 100, done: true },
    ])
  })

  it('keeps error detail from JSON failures', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    FakeXhr.prototype.status = 400
    FakeXhr.prototype.responseText = JSON.stringify({ detail: '文件无效' })

    await expect(staffUpload('/upload', {})).rejects.toThrow('文件无效')
  })
})
