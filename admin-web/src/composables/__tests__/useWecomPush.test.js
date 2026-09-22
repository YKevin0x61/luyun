import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPut = vi.fn()
const apiPost = vi.fn()
const apiDelete = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    put: (...args) => apiPut(...args),
    post: (...args) => apiPost(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

const { useWecomPush } = await import('../useWecomPush.js')

const WEBHOOK = {
  id: 7,
  name: '门店群',
  enabled: 1,
  notes: '早班',
  webhook_url_masked: 'https://qyapi.weixin.qq.com/***',
}

describe('useWecomPush 的 webhook 启停', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
    apiGet.mockResolvedValue({ webhooks: [WEBHOOK], jobs: [], logs: [], meta: {} })
    apiPut.mockResolvedValue({ success: true })
  })

  it('停用时只改 enabled，地址传 null 表示不动原地址', async () => {
    const { toggleWebhookEnabled } = useWecomPush()

    await toggleWebhookEnabled({ ...WEBHOOK, enabled: true })

    expect(apiPut).toHaveBeenCalledTimes(1)
    const [url, payload] = apiPut.mock.calls[0]
    expect(url).toBe('/api/wecom-push/webhooks/7')
    expect(payload.enabled).toBe(false)
    expect(payload.webhook_url).toBeNull()
    expect(payload.name).toBe('门店群')
    expect(payload.notes).toBe('早班')
    // 改完要重新拉列表，页面状态不会自己变
    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/webhooks')
  })

  it('停用过的 webhook 再点一次是启用', async () => {
    const { toggleWebhookEnabled } = useWecomPush()

    await toggleWebhookEnabled({ ...WEBHOOK, enabled: false })

    expect(apiPut.mock.calls[0][1].enabled).toBe(true)
  })

  it('后端拒绝时不吞错，交给调用方提示', async () => {
    apiPut.mockRejectedValue(new Error('更新 webhook 失败'))
    const { toggleWebhookEnabled } = useWecomPush()

    await expect(toggleWebhookEnabled({ ...WEBHOOK, enabled: true })).rejects.toThrow(
      '更新 webhook 失败',
    )
  })
})
