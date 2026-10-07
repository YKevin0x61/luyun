import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
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
  useWecomPush, WECOM_PUSH_API_VERSION, MATRIX_CHANNEL_LIMIT, MATRIX_VIEW_MULTI_SELECT,
  matrixViewMode, matrixZeroTopicNames, zeroSubscriptionWarning, contractMismatchWarning,
  channelDeleteConfirmText, channelGroupNames, channelTopicNames, formatSentAt,
} = await import('../useWecomPush.js')

const CONTRACT_SOURCE = join(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../services/wecom_push_topics.py',
)

const CHANNEL_A = { id: 1, name: '门店群', enabled: true }
const CHANNEL_B = { id: 2, name: '日报群', enabled: true }

function channels(count) {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    name: `群 ${index + 1}`,
    enabled: true,
  }))
}

function matrixPayload(channelList = [CHANNEL_A, CHANNEL_B]) {
  return {
    success: true,
    api_version: 'test',
    channels: channelList,
    topics: [
      {
        id: 'sales_report',
        name: '销售报表',
        triggers: ['scheduled'],
        contains_employee_photos: false,
        channels: channelList.map((channel) => ({ id: channel.id, enabled: channel.id === CHANNEL_A.id })),
      },
      {
        id: 'hygiene_photo',
        name: '验收照片',
        triggers: ['event'],
        contains_employee_photos: true,
        channels: [],
      },
    ],
  }
}

describe('企微推送接口契约版本', () => {
  it('前端常量与后端注册表里的版本一致（漂移就是提示条永远亮着）', () => {
    const source = readFileSync(CONTRACT_SOURCE, 'utf8')
    const matched = /^WECOM_PUSH_API_VERSION\s*=\s*"([^"]+)"/m.exec(source)

    expect(matched, 'services/wecom_push_topics.py 里找不到 WECOM_PUSH_API_VERSION').toBeTruthy()
    expect(WECOM_PUSH_API_VERSION).toBe(matched[1])
  })

  it('对得上就不提示，对不上给出顶部提示（不阻断操作）', () => {
    expect(contractMismatchWarning({ api_version: WECOM_PUSH_API_VERSION })).toBe('')
    expect(contractMismatchWarning({})).toBe('')

    const stale = contractMismatchWarning({ api_version: 'v0.0.1' })
    expect(stale).toContain('刷新')
    expect(stale).toContain('v0.0.1')
  })
})

describe('订阅矩阵的视图切换阈值', () => {
  it(`渠道数超过 ${8} 个才切多选列表，正好 8 个仍是矩阵`, () => {
    expect(MATRIX_CHANNEL_LIMIT).toBe(8)
    expect(matrixViewMode(channels(1))).toBe('matrix')
    expect(matrixViewMode(channels(8))).toBe('matrix')
    expect(matrixViewMode(channels(9))).toBe(MATRIX_VIEW_MULTI_SELECT)
    expect(matrixViewMode([])).toBe('matrix')
  })
})

describe('零订阅内容类型的提示', () => {
  const matrix = {
    topics: [
      { id: 'sales_report', name: '销售报表', channels: [{ id: 1, enabled: true }] },
      { id: 'hygiene_photo', name: '验收照片', channels: [] },
      { id: 'hygiene_reminder', name: '卫生提醒', channels: [{ id: 1, enabled: false }] },
    ],
  }

  it('订阅了但没有启用中的渠道也算零订阅', () => {
    expect(matrixZeroTopicNames(matrix)).toEqual(['验收照片', '卫生提醒'])
  })

  it('一处零订阅就在页顶点名，全都有订阅时不提示', () => {
    const warning = zeroSubscriptionWarning(matrix)
    expect(warning).toContain('验收照片')
    expect(warning).toContain('卫生提醒')
    expect(warning).toContain('不会发出')

    expect(zeroSubscriptionWarning({ topics: [{ name: '销售报表', channels: [{ id: 1, enabled: true }] }] })).toBe('')
    expect(zeroSubscriptionWarning({ topics: [] })).toBe('')
  })
})

describe('渠道卡片的展示字段', () => {
  it('所属群组与订阅内容都来自后端，不在前端重新推导', () => {
    const channel = {
      groups: [{ id: 3, name: '日报群组', enabled: true }],
      topics: [{ id: 'sales_report', name: '销售报表', via_group: true }],
    }

    expect(channelGroupNames(channel)).toBe('日报群组')
    // 名字里不带来路：那层是页面上单独一枚徽章（channel.topics[].via_group）
    expect(channelTopicNames(channel)).toBe('销售报表')
    expect(channel.topics[0].via_group).toBe(true)
    expect(channelGroupNames({ groups: [] })).toBe('')
    expect(channelTopicNames({})).toBe('')
  })

  it('最近一次发送成功的时间取后端给的展示串，没有就写「从未发送」', () => {
    expect(formatSentAt({ last_sent_at: '2026-10-05T21:30:12+08:00' })).toBe('2026-10-05 21:30:12')
    expect(formatSentAt({ last_sent_at: '' })).toBe('从未发送')
    expect(formatSentAt({})).toBe('从未发送')
  })
})

describe('删除渠道的确认文案', () => {
  it('被订阅时说清订阅会跟着取消，没有被订阅就只说后果', () => {
    // 任务不再绑定渠道（票 08）：删渠道不会让任何任务失效，所以文案里不再有「被任务引用」。
    expect(channelDeleteConfirmText({ name: '门店群', job_count: 1 })).not.toContain('推送任务')
    expect(
      channelDeleteConfirmText({ name: '门店群', topics: [{ id: 'sales_report' }] }),
    ).toContain('订阅')
    expect(channelDeleteConfirmText({ name: '门店群' })).toContain('门店群')
    expect(channelDeleteConfirmText(null)).toContain('删除')
  })
})

describe('订阅的勾选与取消', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
    apiPost.mockReset()
    apiDelete.mockReset()
    apiPost.mockResolvedValue({ success: true })
    apiPut.mockResolvedValue({ success: true })
    apiGet.mockImplementation(async (path) => {
      if (path === '/api/wecom-push/subscriptions') return matrixPayload()
      return {}
    })
  })

  it('勾选某类内容给某个渠道：请求体带 topic / channel / enabled', async () => {
    const { toggleSubscription } = useWecomPush()

    await toggleSubscription({ topicId: 'sales_report', channelId: 2, enabled: true })

    expect(apiPost).toHaveBeenCalledTimes(1)
    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/api/wecom-push/subscriptions')
    expect(payload).toMatchObject({
      topic_id: 'sales_report',
      target_channel_id: 2,
      enabled: true,
    })
    // 改完要重新拉矩阵，页面状态不会自己变
    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/subscriptions')
  })

  it('取消勾选传 enabled: false（停用而不是删行）', async () => {
    const { toggleSubscription } = useWecomPush()

    await toggleSubscription({ topicId: 'sales_report', channelId: 1, enabled: false })

    expect(apiPost.mock.calls[0][1]).toMatchObject({ enabled: false })
  })

  it('后端拒绝时不吞错，交给调用方提示', async () => {
    apiPost.mockRejectedValue(new Error('未知的推送内容类型: nope'))
    const { toggleSubscription } = useWecomPush()

    await expect(
      toggleSubscription({ topicId: 'nope', channelId: 1, enabled: true }),
    ).rejects.toThrow('未知的推送内容类型')
  })

  it('多选列表模式：一次保存把选中的渠道逐个勾上', async () => {
    const { saveMultiSelectTopic, multiSelect } = useWecomPush()

    multiSelect.topicId = 'sales_report'
    multiSelect.channelIds = [1, 2]
    await saveMultiSelectTopic()

    expect(apiPost).toHaveBeenCalledTimes(2)
    expect(apiPost.mock.calls.map((call) => call[1])).toEqual([
      expect.objectContaining({ topic_id: 'sales_report', target_channel_id: 1, enabled: true }),
      expect.objectContaining({ topic_id: 'sales_report', target_channel_id: 2, enabled: true }),
    ])
  })

  it('加载矩阵时把接口版本、内容类型与渠道一起收下', async () => {
    const { loadSubscriptions, matrix } = useWecomPush()

    await loadSubscriptions()

    expect(matrix.value.topics.map((topic) => topic.id)).toEqual(['sales_report', 'hygiene_photo'])
    expect(matrix.value.topics[1].contains_employee_photos).toBe(true)
    expect(matrix.value.channels.map((channel) => channel.id)).toEqual([1, 2])
  })
})

describe('群组与成员管理', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
    apiPut.mockReset()
    apiDelete.mockReset()
    apiPost.mockResolvedValue({ success: true, group: { id: 3, member_channel_ids: [] } })
    apiPut.mockResolvedValue({ success: true, group: { id: 5 } })
    apiDelete.mockResolvedValue({ success: true, group: { id: 3, member_channel_ids: [] } })
    apiGet.mockResolvedValue({ channels: [], groups: [] })
  })

  it('一个渠道可以同时加进多个群组：每次加成员都是一条独立请求', async () => {
    const { addGroupMember, removeGroupMember } = useWecomPush()

    await addGroupMember({ groupId: 3, channelId: 7 })
    await addGroupMember({ groupId: 4, channelId: 7 })

    expect(apiPost.mock.calls.map((call) => call[0])).toEqual([
      '/api/wecom-push/channel-groups/3/members',
      '/api/wecom-push/channel-groups/4/members',
    ])
    expect(apiPost.mock.calls[0][1]).toEqual({ channel_id: 7 })

    await removeGroupMember({ groupId: 3, channelId: 7 })
    expect(apiDelete).toHaveBeenCalledWith('/api/wecom-push/channel-groups/3/members/7')
  })

  it('新建群组与改名都走同一条群组接口', async () => {
    const { channelGroupForm, saveChannelGroup } = useWecomPush()
    Object.assign(channelGroupForm, { name: '日报群组', notes: '早班' })

    await saveChannelGroup()
    expect(apiPost.mock.calls[0][0]).toBe('/api/wecom-push/channel-groups')

    Object.assign(channelGroupForm, { id: 5, name: '日报群组 A' })
    await saveChannelGroup()
    expect(apiPut.mock.calls[0][0]).toBe('/api/wecom-push/channel-groups/5')
  })
})
