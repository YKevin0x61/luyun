import { beforeEach, describe, expect, it, vi } from 'vitest'

// 票 08 页面那一半的验收：定时任务表单的数据来源是 `/meta` 的注册表（内容类型下拉 +
// 参数区），请求体是新形状（内容类型 + 参数 + 时间，没有收件人），任务卡片显示的是
// 「订阅目标数」而不是某个群名。
//
// 断言的是**页面发出去的请求体**与**纯函数的输出**：选项从哪来、参数怎么初始化、确认框
// 说了什么。组件怎么组织这些数据可以改，这几条不能改。

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiDelete = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

const {
  useWecomPush, scheduledTopics, defaultJobParams, jobPayload,
  sendNowConfirmText, canSendNow,
} = await import('../useWecomPush.js')

/** 注册表（`/meta` 的 topics）：页面只认这一份，不自己维护名字表与参数形状。 */
const TOPICS = [
  {
    id: 'sales_report',
    name: '销售报表',
    triggers: ['scheduled'],
    default_schedule_time: '21:30',
    params_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        schedule_time: { type: 'string', default: '21:30', title: '推送时间' },
        date_range_mode: {
          type: 'string',
          default: 'today',
          title: '日期口径',
          oneOf: [{ const: 'today', title: '当天' }, { const: 'yesterday', title: '昨天' }],
        },
        station: {
          type: 'string',
          default: '',
          title: '档口',
          oneOf: [{ const: '', title: '全部（排除楼面）' }, { const: 'shulong', title: '熟笼档' }],
        },
      },
    },
    uischema: { type: 'VerticalLayout', elements: [] },
  },
  {
    id: 'reconcile_diff',
    name: '对账差异告警',
    triggers: ['scheduled', 'event'],
    default_schedule_time: '22:10',
    params_schema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        schedule_time: { type: 'string', default: '22:10', title: '推送时间' },
        date_range_mode: {
          type: 'string',
          default: 'today',
          title: '日期口径',
          oneOf: [{ const: 'today', title: '当天' }, { const: 'yesterday', title: '昨天' }],
        },
      },
    },
    uischema: { type: 'VerticalLayout', elements: [] },
  },
  {
    id: 'hygiene_photo',
    name: '验收照片',
    triggers: ['event'],
    default_schedule_time: null,
    params_schema: { type: 'object', properties: {} },
    uischema: { type: 'VerticalLayout', elements: [] },
  },
]

function jobFixture(overrides = {}) {
  return {
    id: 3,
    name: '每日销售报表',
    topic_id: 'sales_report',
    topic_name: '销售报表',
    params: { schedule_time: '21:30', date_range_mode: 'today', station: '' },
    schedule_time: '21:30',
    enabled: true,
    last_sent_date: '',
    notes: '',
    target_count: 2,
    ...overrides,
  }
}

function metaPayload() {
  return { success: true, api_version: 'v2', topics: TOPICS, job_templates: [] }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  apiDelete.mockReset()
  apiGet.mockImplementation(async (path) => {
    if (path === '/api/wecom-push/meta') return metaPayload()
    if (path === '/api/wecom-push/jobs') return { success: true, jobs: [] }
    return {}
  })
  apiPost.mockResolvedValue({ success: true, job: jobFixture() })
  apiPut.mockResolvedValue({ success: true, job: jobFixture() })
})

describe('定时任务表单的内容类型下拉', () => {
  it('只列支持定时触发的内容类型（事件类不进下拉）', () => {
    expect(scheduledTopics({ topics: TOPICS }).map((topic) => topic.id))
      .toEqual(['sales_report', 'reconcile_diff'])
    expect(scheduledTopics({})).toEqual([])
  })
})

describe('任务的请求体', () => {
  it('新形状：内容类型 + 参数 + 时间，没有任何收件人字段', () => {
    const payload = jobPayload({
      name: ' 每日销售报表 ',
      topicId: 'sales_report',
      params: { schedule_time: '21:30', date_range_mode: 'today', station: 'shulong' },
      notes: ' 早班 ',
      enabled: true,
    }, TOPICS)

    expect(payload).toEqual({
      name: '每日销售报表',
      topic_id: 'sales_report',
      params: { schedule_time: '21:30', date_range_mode: 'today', station: 'shulong' },
      schedule_time: '21:30',
      enabled: true,
      notes: '早班',
    })
    // 旧的收件人字段一个都不能带：带上就会被后端明确拒绝
    expect(payload.webhook_id).toBeUndefined()
    expect(payload.push_type).toBeUndefined()
    expect(payload.date_range_mode).toBeUndefined()
  })

  it('顶层时间取自参数区那个时间控件（页面上只有这一个时间入口）', () => {
    const payload = jobPayload({ topicId: 'sales_report', params: { schedule_time: '09:05' } }, TOPICS)

    expect(payload.schedule_time).toBe('09:05')
    expect(payload.params.schedule_time).toBe('09:05')
  })

  it('参数里没有时间时退回注册表的默认时间', () => {
    expect(jobPayload({ topicId: 'reconcile_diff', params: {} }, TOPICS).schedule_time).toBe('22:10')
    expect(jobPayload({ topicId: 'sales_report', params: {} }, TOPICS).schedule_time).toBe('21:30')
  })
})

describe('切换内容类型时的参数区', () => {
  it('参数按该类型的 schema 默认值初始化（前端不写死字段名）', () => {
    expect(defaultJobParams(TOPICS[0])).toEqual({
      schedule_time: '21:30', date_range_mode: 'today', station: '',
    })
    expect(defaultJobParams(TOPICS[1])).toEqual({
      schedule_time: '22:10', date_range_mode: 'today',
    })
    expect(defaultJobParams(undefined)).toEqual({})
  })

  it('选一个内容类型就把参数换成它的默认值', async () => {
    const { loadMeta, pickJobTopic, jobForm } = useWecomPush()
    await loadMeta()

    pickJobTopic('reconcile_diff')

    expect(jobForm.topic_id).toBe('reconcile_diff')
    expect(jobForm.params).toEqual({ schedule_time: '22:10', date_range_mode: 'today' })
  })
})

describe('保存任务', () => {
  it('新建走 POST /jobs，请求体是新形状', async () => {
    const { loadMeta, pickJobTopic, jobForm, saveJob } = useWecomPush()
    await loadMeta()
    pickJobTopic('sales_report')
    Object.assign(jobForm, { name: '每日销售报表', notes: '早班' })

    await saveJob()

    expect(apiPost).toHaveBeenCalledTimes(1)
    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/api/wecom-push/jobs')
    expect(payload).toMatchObject({
      name: '每日销售报表',
      topic_id: 'sales_report',
      schedule_time: '21:30',
      notes: '早班',
      enabled: true,
    })
    expect(payload.params.schedule_time).toBe('21:30')
    expect(payload.webhook_id).toBeUndefined()
    // 保存后重新拉列表（卡片上的订阅目标数要跟着刷）
    expect(apiGet).toHaveBeenCalledWith('/api/wecom-push/jobs')
  })

  it('编辑既有任务：填回它的内容类型与参数，保存走 PUT', async () => {
    const { loadMeta, editJob, jobForm, saveJob } = useWecomPush()
    await loadMeta()
    editJob(jobFixture({
      id: 9,
      topic_id: 'reconcile_diff',
      topic_name: '对账差异告警',
      params: { schedule_time: '22:10', date_range_mode: 'yesterday' },
      name: '对账差异日报',
    }))

    expect(jobForm.params).toEqual({ schedule_time: '22:10', date_range_mode: 'yesterday' })

    await saveJob()

    const [url, payload] = apiPut.mock.calls[0]
    expect(url).toBe('/api/wecom-push/jobs/9')
    expect(payload.topic_id).toBe('reconcile_diff')
    expect(payload.params.date_range_mode).toBe('yesterday')
    expect(payload.schedule_time).toBe('22:10')
  })

  it('没选内容类型时不发请求，当场报错', async () => {
    const { jobForm, saveJob } = useWecomPush()
    Object.assign(jobForm, { name: '没类型', topic_id: '', params: {} })

    await expect(saveJob()).rejects.toThrow('内容类型')
    expect(apiPost).not.toHaveBeenCalled()
  })
})

describe('任务卡片的订阅目标数', () => {
  it('列表里的任务是后端给的目标数，页面不自己推', async () => {
    apiGet.mockImplementation(async (path) => {
      if (path === '/api/wecom-push/meta') return metaPayload()
      if (path === '/api/wecom-push/jobs') {
        return { success: true, jobs: [jobFixture({ target_count: 3 })] }
      }
      return {}
    })
    const { loadJobs, jobs } = useWecomPush()

    await loadJobs()

    expect(jobs.value[0].target_count).toBe(3)
    expect(jobs.value[0].topic_name).toBe('销售报表')
  })
})

// 「立即发送」会把消息真的发到门店群、撤不回来：确认框必须说清"发给几个群"与字节数。
describe('立即发送的确认文案', () => {
  const JOB = { name: '每日销售报表', topic_name: '销售报表', target_count: 3 }

  it('写明将发给几个群、消息大小与正文首行', () => {
    const text = sendNowConfirmText({ job: JOB, bytes: 812, content: '今日营业额 ¥14525\n明细…' })

    expect(text).toContain('3 个群')
    expect(text).toContain('销售报表')
    expect(text).toContain('812 / 2048 字节')
    expect(text).toContain('无法撤回')
    expect(text).toContain('今日营业额')
  })

  it('预览为空时明确说"没有预览内容"，不再声称有当前预览', () => {
    const text = sendNowConfirmText({ job: JOB, bytes: 0, content: '' })

    expect(text).toContain('没有预览内容')
    expect(text).toContain('0 / 2048 字节')
    expect(text).not.toContain('当前预览对应的')
  })

  it('零订阅时说清这次发送会被拒绝，而不是假装发得出去', () => {
    const text = sendNowConfirmText({
      job: { ...JOB, target_count: 0 }, bytes: 10, content: 'x',
    })

    expect(text).toContain('没有任何群订阅')
    expect(text).toContain('订阅')
  })

  it('正文首行过长时截断，不让确认框被一整段顶开', () => {
    const text = sendNowConfirmText({ job: JOB, bytes: 100, content: `${'甲'.repeat(80)}\n第二行` })

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
