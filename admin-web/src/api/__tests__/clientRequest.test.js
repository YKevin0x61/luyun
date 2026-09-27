import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../client.js'

// request()（JSON 那一路）的网络层口径：fetch 只在断网 / DNS 失败 / 商场 AP 假死时
// reject。以前外面没有 try/catch，冒泡出去的是 TypeError('Failed to fetch')
// （Safari 是 'Load failed'），而三个页面都拿 `err.message` 显示 —— 店长看到的是英文
// 原文，跟服务端 400 的中文 `detail` 长得一样（后者带 `status`，本可以分开）。
afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

describe('api.request 的网络失败口径', () => {
  it('断网 / DNS 失败给中文，且不带 status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const err = await api.get('/api/scheduling/inbox').catch((e) => e)
    expect(err.message).toBe('网络连不上，请检查网络后重试')
    // 不带 status 是契约的一半：调用方（页面、上传队列）据此区分「没连上，可以重试」
    // 与「服务端拒绝了这次操作」——后者一定有 status。
    expect(err.status).toBeUndefined()
  })

  it('Safari 的 "Load failed" 也翻成中文', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Load failed')))

    await expect(api.post('/api/scheduling/inbox/1/approve', {})).rejects.toThrow(
      '网络连不上，请检查网络后重试',
    )
  })

  it('服务端 400 的中文 detail 照旧带 status，跟断网分得开', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(400, { detail: '这条申请已经处理过了' }),
    ))

    const err = await api.post('/api/scheduling/inbox/1/approve', {}).catch((e) => e)
    expect(err.message).toBe('这条申请已经处理过了')
    expect(err.status).toBe(400)
  })

  it('2xx 照旧 resolve 解析后的 JSON（包装没把成功路径吞掉）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse(200, { today: '2026-07-20', requests: [] }),
    ))

    await expect(api.get('/api/scheduling/inbox')).resolves.toEqual({
      today: '2026-07-20',
      requests: [],
    })
  })
})
