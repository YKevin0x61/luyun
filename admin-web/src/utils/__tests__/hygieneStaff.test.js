import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { staffUpload } from '../hygieneStaff'

const here = dirname(fileURLToPath(import.meta.url))

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

  it('gives up on a hung upload instead of parking the queue forever', async () => {
    // 假死网络（连得上但不回包）下没有超时的话，任务永远停在 uploading、占满并发槽，
    // 后面的照片全排队不动；而它又被"已入队"标记从待办里拿掉了。
    class TimeoutXhr extends FakeXhr {
      send() {
        queueMicrotask(() => this.ontimeout?.())
      }
    }
    vi.stubGlobal('XMLHttpRequest', TimeoutXhr)

    await expect(staffUpload('/upload', {})).rejects.toThrow('上传超时')

    const source = readFileSync(
      join(here, '../hygieneStaff.js'),
      'utf8',
    )
    expect(source).toMatch(/xhr\.timeout = STAFF_UPLOAD_TIMEOUT_MS/)
    expect(source).toMatch(/xhr\.ontimeout = \(\) => reject\(new Error\('上传超时/)
  })
})
