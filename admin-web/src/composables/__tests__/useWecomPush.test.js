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

const {
  useWecomPush, sendNowConfirmText, canSendNow,
} = await import('../useWecomPush.js')

const CHANNEL = {
  id: 7,
  name: '门店群',
  enabled: 1,
  notes: '早班',
  webhook_url_masked: 'https://qyapi.weixin.qq.com/***',
}

describe('渠道的启停快捷开关', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
    apiGet.mockResolvedValue({ channels: [CHANNEL], groups: [], topics: [] })
    apiPut.mockResolvedValue({ success: true })
  })

  it('停用时只改 enabled，地址传 null 表示不动原地址', async () => {
    const { toggleChannelEnabled } = useWecomPush()

    await toggleChannelEnabled({ ...CHANNEL, enabled: true })

    expect(apiPut).toHaveBeenCalledTimes(1)
    const [url, payload] = apiPut.mock.calls[0]
    expect(url).toBe('/api/wecom-push/webhooks/7')
    expect(payload.enabled).toBe(false)
    expect(payload.webhook_url).toBeNull()
    expect(payload.name).toBe('门店群')
    expect(payload.notes).toBe('早班')
    // 退役的列不再由页面写（票 06：收件人只由订阅决定）
    expect(payload.hygiene_feed).toBeUndefined()
    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/webhooks')
  })

  it('停用过的渠道再点一次是启用', async () => {
    const { toggleChannelEnabled } = useWecomPush()

    await toggleChannelEnabled({ ...CHANNEL, enabled: false })

    expect(apiPut.mock.calls[0][1].enabled).toBe(true)
  })

  it('后端拒绝时不吞错，交给调用方提示', async () => {
    apiPut.mockRejectedValue(new Error('更新 webhook 失败'))
    const { toggleChannelEnabled } = useWecomPush()

    await expect(toggleChannelEnabled({ ...CHANNEL, enabled: true })).rejects.toThrow(
      '更新 webhook 失败',
    )
  })
})

describe('渠道表单的群组成员关系', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
    apiDelete.mockReset()
    apiPost.mockResolvedValue({ success: true, channel: { id: 7 }, group: { id: 1 } })
    apiDelete.mockResolvedValue({ success: true })
  })

  it('新建渠道后按勾选对齐群组成员（只发差集）', async () => {
    apiGet.mockResolvedValue({
      channels: [],
      groups: [
        { id: 1, name: '日报群组', member_channel_ids: [] },
        { id: 2, name: '门店 A', member_channel_ids: [7] },
      ],
      topics: [],
    })
    const { channelForm, saveChannel, loadChannelGroups } = useWecomPush()
    // 页面上的群组列表先到位：差集要拿「这次勾选之前」的成员关系来算
    await loadChannelGroups()
    Object.assign(channelForm, {
      name: '门店群',
      webhook_url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc',
      group_ids: [1],
    })

    await saveChannel()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/api/wecom-push/webhooks')
    expect(payload.name).toBe('门店群')
    // 加进 1；原来在 2 里，这次没勾 → 移出
    expect(apiPost.mock.calls[1]).toEqual([
      '/api/wecom-push/channel-groups/1/members', { channel_id: 7 },
    ])
    expect(apiDelete).toHaveBeenCalledWith('/api/wecom-push/channel-groups/2/members/7')
  })

  it('新建时不填地址当场报错，不发请求', async () => {
    const { channelForm, saveChannel } = useWecomPush()
    Object.assign(channelForm, { name: '门店群', webhook_url: '' })

    await expect(saveChannel()).rejects.toThrow('必须填写 webhook 地址')
    expect(apiPost).not.toHaveBeenCalled()
  })
})

// 「立即发送」会把消息真的发到门店群、撤不回来。原来的确认文案写死了
// 「确定立即发送当前预览对应的销售报表？」——预览区空着（0 / 2048 字节、
// 「选择任务后点击刷新预览」）时也这么说，用户既不知道发给谁也不知道多少字节。
// 票 08 之后收件人来自**订阅**：文案要说清"将发给几个群"，零订阅时说清会被拒绝。
describe('立即发送的确认文案与可点条件', () => {
  const JOB = { name: '每日销售报表', topic_name: '销售报表', target_count: 2 }

  it('点名内容类型与将发给几个群，而不是只说"当前预览对应的销售报表"', () => {
    const text = sendNowConfirmText({ job: JOB, bytes: 812, content: '今日营业额 ¥14525\n明细…' })

    expect(text).toContain('2 个群')
    expect(text).toContain('销售报表')
    expect(text).toContain('812 / 2048 字节')
    // 外发撤不回来这件事必须写出来
    expect(text).toContain('无法撤回')
    // 回显正文首行，用户能核对"要发出去的是什么"
    expect(text).toContain('今日营业额')
  })

  it('预览为空时明确说"没有预览内容"，不再声称有当前预览', () => {
    const text = sendNowConfirmText({ job: JOB, bytes: 0, content: '' })

    expect(text).toContain('没有预览内容')
    expect(text).toContain('0 / 2048 字节')
    expect(text).not.toContain('当前预览对应的')
  })

  it('别的内容类型用自己的显示名（名字来自注册表，不是页面写死的表）', () => {
    const text = sendNowConfirmText({
      job: { ...JOB, topic_name: '对账差异告警' },
      bytes: 300,
      content: '数据质量摘要',
    })
    expect(text).toContain('对账差异告警')
    expect(text).not.toContain('销售报表')
  })

  it('零订阅时说清这次发送会被拒绝', () => {
    const text = sendNowConfirmText({
      job: { ...JOB, target_count: 0 }, bytes: 10, content: 'x',
    })
    expect(text).toContain('没有任何群订阅')
  })

  it('正文首行过长时截断，不让确认框被一整段顶开', () => {
    const text = sendNowConfirmText({
      job: JOB,
      bytes: 100,
      content: `${'甲'.repeat(80)}\n第二行`,
    })
    expect(text).toContain('…')
    expect(text).not.toContain('甲'.repeat(41))
  })

  it('没有预览内容就不允许点「立即发送」', () => {
    expect(canSendNow({ job: JOB, content: '有内容' })).toBe(true)
    expect(canSendNow({ job: JOB, content: '' })).toBe(false)
    // 只有空白字符同样不算"核对过内容"
    expect(canSendNow({ job: JOB, content: '   \n  ' })).toBe(false)
    expect(canSendNow({ job: null, content: '有内容' })).toBe(false)
  })
})
