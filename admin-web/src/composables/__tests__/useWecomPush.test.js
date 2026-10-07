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
  useWecomPush, sendNowConfirmText, sendNowTargetNames, canSendNow,
  loadErrorMessage, loadErrorDetail, testChannelConfirmText,
  deliveryErrorSummary, wecomErrcode,
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
// 「点『刷新预览』生成内容」）时也这么说，用户既不知道发给谁也不知道多少字节。
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

// U7：确认框原来只说「将发给 3 个群」——店长看不出里面有没有那个不该收的群，而消息发出
// 去撤不回来。群名从页面已经加载的订阅矩阵里取（与后端 resolve_targets 同一口径）。
describe('立即发送的收件群名单（U7）', () => {
  const JOB = {
    id: 1, name: '每日销售报表', topic_id: 'sales_report', topic_name: '销售报表', target_count: 2,
  }
  const MATRIX = {
    topics: [
      { id: 'sales_report', name: '销售报表', channels: [{ id: 1, enabled: true }, { id: 2, enabled: true }] },
      { id: 'hygiene_reminder', name: '卫生提醒', channels: [{ id: 1, enabled: true }] },
    ],
    channels: [{ id: 1, name: '一楼前厅群' }, { id: 2, name: '店长日报群' }, { id: 3, name: '卫生群' }],
  }

  it('从订阅矩阵里取出这一类内容的收件群名（顺序跟矩阵的列序）', () => {
    expect(sendNowTargetNames({ job: JOB, matrix: MATRIX })).toEqual(['一楼前厅群', '店长日报群'])
  })

  it('名字数与 target_count 对不上（矩阵没加载 / 已在别处改过）时给空数组，不列一份可能错的名单', () => {
    expect(sendNowTargetNames({ job: { ...JOB, target_count: 3 }, matrix: MATRIX })).toEqual([])
    expect(sendNowTargetNames({ job: JOB, matrix: null })).toEqual([])
    expect(sendNowTargetNames({ job: { ...JOB, target_count: 0 }, matrix: MATRIX })).toEqual([])
    expect(sendNowTargetNames({ job: { ...JOB, topic_id: '' }, matrix: MATRIX })).toEqual([])
  })

  it('≤3 个群时逐个点名', () => {
    const text = sendNowConfirmText({
      job: JOB, bytes: 10, content: 'x', targetNames: ['一楼前厅群', '店长日报群'],
    })
    expect(text).toContain('将发给 2 个群：一楼前厅群、店长日报群。')
  })

  it('多于 3 个群时只列前三个 + 总数，不把一整页群名塞进确认框', () => {
    const text = sendNowConfirmText({
      job: { ...JOB, target_count: 5 },
      bytes: 10,
      content: 'x',
      targetNames: ['A群', 'B群', 'C群', 'D群', 'E群'],
    })
    expect(text).toContain('将发给 A群、B群、C群 等 5 个群。')
    expect(text).not.toContain('D群')
  })

  it('没有名单时退回只说数量（矩阵还没回来也不会说不出话）', () => {
    expect(sendNowConfirmText({ job: JOB, bytes: 10, content: 'x' })).toContain('将发给 2 个群。')
  })
})

// U5（UI 走查 medium）：后端 5xx 时页面把 `Internal Server Error` 原样端给店长。
describe('加载失败的两套文案（U5）', () => {
  it('5xx 翻成「服务暂时不可用」，英文原文只进"详情"', () => {
    const error = Object.assign(new Error('Internal Server Error'), {
      status: 500, detail: 'Internal Server Error',
    })

    expect(loadErrorMessage(error)).toBe('服务暂时不可用，请稍后重试')
    expect(loadErrorMessage(error)).not.toContain('Internal Server Error')
    expect(loadErrorDetail(error)).toBe('HTTP 500 · Internal Server Error')
  })

  it('4xx 用后端写好的中文 detail（那是给人看的），网络层用客户端已经翻过的那句', () => {
    const stale = Object.assign(new Error('页面已更新，请刷新后重试'), { status: 409 })
    expect(loadErrorMessage(stale)).toBe('页面已更新，请刷新后重试')

    const offline = new Error('网络连不上，请检查网络后重试')
    expect(loadErrorMessage(offline)).toBe('网络连不上，请检查网络后重试')
    // 断网时没有状态码：详情与技术原文是同一句，页面据此不重复渲染「详情」
    expect(loadErrorDetail(offline)).toBe('网络连不上，请检查网络后重试')
  })

  it('兜底：认不出的失败也给一句中文，不把空字符串糊到页面上', () => {
    expect(loadErrorMessage(null)).toBe('加载失败，请重试')
    expect(loadErrorDetail(null)).toBe('')
  })
})

// U6（旧清单 A29）：「测试」一点就真外发，走查时真的发进了门店群。
describe('「测试」按钮的确认文案（U6）', () => {
  it('点名目标群，并说清撤不回来', () => {
    const text = testChannelConfirmText({ id: 3, name: '门店群' })

    expect(text).toContain('门店群')
    expect(text).toContain('无法撤回')
    // 括号里那句话是给读屏与确认框一起读的：写的是"真的发到这个群"
    expect(text).toContain('真的发到')
  })

  it('没有名字时退回 id / 兜底文案，不生成一句没有主语的提示', () => {
    expect(testChannelConfirmText({ id: 9 })).toContain('9')
    expect(testChannelConfirmText(null)).toContain('该渠道')
  })
})

// U16（UI 走查 medium）：第 7 列显示的是英文原文或 Python 异常栈的一行。
describe('发送记录错误的中文化（U16）', () => {
  it('errcode 映射成人话（企微限流这类最常见的失败）', () => {
    expect(deliveryErrorSummary('企业微信接口返回 errcode=45009：api freq out of limit'))
      .toContain('调用频率上限')
    expect(deliveryErrorSummary('上传图片素材失败：errcode=40001 invalid credential'))
      .toContain('key 无效')
    expect(deliveryErrorSummary('errcode=93000 invalid webhook url'))
      .toContain('已被移出这个群')
  })

  it('网络层的几种失败也认得出', () => {
    expect(deliveryErrorSummary(
      "HTTPSConnectionPool(host='qyapi.weixin.qq.com', port=443): Read timed out. (read timeout=10)",
    )).toContain('超时')
    expect(deliveryErrorSummary('第 1/1 条发送失败：invalid webhook url'))
      .toContain('webhook 地址无效')
  })

  it('errcode 认不出来就原样显示，不吞掉失败原因', () => {
    const raw = '第 1/1 条发送失败：某种没见过的错'
    expect(deliveryErrorSummary(raw)).toBe(raw)
    expect(deliveryErrorSummary('')).toBe('')
    expect(wecomErrcode('errcode=40001')).toBe(40001)
    expect(wecomErrcode('没有码')).toBe(0)
  })
})
