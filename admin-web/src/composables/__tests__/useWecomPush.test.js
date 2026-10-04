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

const { useWecomPush, webhookDeleteConfirmText, hygieneFeedWarning } = await import('../useWecomPush.js')

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

describe('useWecomPush 的卫生群标记', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
    apiPost.mockReset()
    apiGet.mockResolvedValue({ webhooks: [], jobs: [], logs: [], meta: {} })
    apiPut.mockResolvedValue({ success: true })
    apiPost.mockResolvedValue({ success: true })
  })

  it('快捷开关把标记原样回传，停用不会顺手清掉它', async () => {
    const { toggleWebhookEnabled } = useWecomPush()

    await toggleWebhookEnabled({ ...WEBHOOK, enabled: true, hygiene_feed: true })

    const [, payload] = apiPut.mock.calls[0]
    expect(payload.enabled).toBe(false)
    expect(payload.hygiene_feed).toBe(true)
  })

  it('保存表单时带着勾选状态', async () => {
    const { webhookForm, saveWebhook } = useWecomPush()
    Object.assign(webhookForm, {
      name: '卫生群',
      webhook_url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc',
      hygiene_feed: true,
    })

    await saveWebhook()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/api/wecom-push/webhooks')
    expect(payload.hygiene_feed).toBe(true)
  })

  it('编辑回填把库里的 0/1 变成勾选状态', () => {
    const { webhookForm, editWebhook } = useWecomPush()

    editWebhook({ ...WEBHOOK, hygiene_feed: 1 })

    expect(webhookForm.hygiene_feed).toBe(true)
  })

  it('删除确认：卫生群点名，普通群仍是原来那句', () => {
    expect(webhookDeleteConfirmText({ name: '卫生群', hygiene_feed: true })).toContain('是卫生群')
    expect(webhookDeleteConfirmText({ name: '日报群', hygiene_feed: false })).toBe('确定删除该 webhook？')
    expect(webhookDeleteConfirmText(undefined)).toBe('确定删除该 webhook？')
  })
})

describe('卫生群空态提示', () => {
  it('一个卫生群都没勾时给出提示', () => {
    expect(hygieneFeedWarning([])).toContain('还没有指定卫生群')
    expect(hygieneFeedWarning([{ hygiene_feed: false, enabled: true }])).toContain('还没有指定卫生群')
  })

  it('勾了但已停用，仍算"没有可用的卫生群"', () => {
    expect(hygieneFeedWarning([{ hygiene_feed: true, enabled: false }])).toContain('还没有指定卫生群')
  })

  it('有启用中的卫生群就不提示', () => {
    const webhooks = [
      { hygiene_feed: false, enabled: true },
      { hygiene_feed: true, enabled: true },
    ]

    expect(hygieneFeedWarning(webhooks)).toBe('')
  })
})
