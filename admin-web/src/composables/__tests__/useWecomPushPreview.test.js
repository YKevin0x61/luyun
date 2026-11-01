import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

// 票 01：预览与「选中的任务 + 表单里这份参数」严格绑定。
//
// 断言的是**状态机**：预览生成之后，换任务 / 改参数 / 保存任务，三样预览数据必须一起
// 清空并给出原因（页面据此把「立即发送」置灰）；重新预览后失效标记要退掉。确认框那一
// 侧断言的是**它不说什么**：预览失效时不能出现正文首行。

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiDelete = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    put: (...args) => apiPut(...args),
    post: (...args) => apiPost(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

const { useWecomPush, sendNowConfirmText } = await import('../useWecomPush.js')

const TOPICS = [
  {
    id: 'sales_report',
    name: '销售报表',
    triggers: ['scheduled'],
    default_schedule_time: '21:30',
    params_schema: {
      type: 'object',
      properties: { schedule_time: { type: 'string', default: '21:30' } },
    },
    uischema: {},
  },
]

const JOBS = [
  { id: 1, name: '早报', topic_id: 'sales_report', params: { schedule_time: '21:30' }, enabled: true, target_count: 2 },
  { id: 2, name: '晚报', topic_id: 'sales_report', params: { schedule_time: '22:30' }, enabled: true, target_count: 2 },
]

function previewPayload(content = '早报正文第一行\n第二行') {
  return { success: true, content, byte_length: content.length, chunk_count: 1, kind: 'text' }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  apiDelete.mockReset()
  apiGet.mockImplementation(async (path) => {
    if (path === '/api/wecom-push/meta') {
      return { success: true, topics: TOPICS, job_templates: [] }
    }
    if (path === '/api/wecom-push/jobs') return { success: true, jobs: JOBS }
    return { success: true, rows: [], total: 0, page: 1, pages: 0 }
  })
})

describe('预览失效（票 01）', () => {
  it('换任务：正文 / 图片 / 字节数一起清空，并给出「任务已切换」的理由', async () => {
    const state = useWecomPush()
    await state.loadJobs()
    expect(state.selectedJobId.value).toBe(1)

    apiPost.mockResolvedValueOnce(previewPayload())
    await state.previewSelectedJob()
    expect(state.previewContent.value).toBe('早报正文第一行\n第二行')
    expect(state.previewStaleReason.value).toBe('')

    state.selectedJobId.value = 2
    await nextTick()

    expect(state.previewContent.value).toBe('')
    expect(state.previewImage.value).toBe('')
    expect(state.previewMeta.value.bytes).toBe(0)
    expect(state.previewStaleReason.value).toBe('任务已切换，请重新预览')
  })

  it('改任务参数：预览作废（按键、备注这类参数也算）', async () => {
    const state = useWecomPush()
    await state.loadJobs()
    apiPost.mockResolvedValueOnce(previewPayload())
    await state.previewSelectedJob()

    state.jobForm.notes = '早班'
    await nextTick()

    expect(state.previewContent.value).toBe('')
    expect(state.previewMeta.value.bytes).toBe(0)
    expect(state.previewStaleReason.value).toBe('任务参数已修改，请重新预览')
  })

  it('保存任务成功：预览清空，理由是「任务已保存」', async () => {
    const state = useWecomPush()
    await state.loadJobs()
    apiPost.mockResolvedValueOnce(previewPayload())
    await state.previewSelectedJob()

    Object.assign(state.jobForm, {
      id: 1, name: '早报', topic_id: 'sales_report', params: { schedule_time: '21:30' },
    })
    apiPut.mockResolvedValueOnce({ success: true, job: { id: 1 } })
    await state.saveJob()

    expect(apiPut).toHaveBeenCalled()
    expect(state.previewContent.value).toBe('')
    expect(state.previewStaleReason.value).toBe('任务已保存，请重新预览')
  })

  it('重新预览之后失效标记退掉（正常路径不回退）', async () => {
    const state = useWecomPush()
    await state.loadJobs()
    state.selectedJobId.value = 2
    await nextTick()
    expect(state.previewStaleReason.value).toBe('任务已切换，请重新预览')

    apiPost.mockResolvedValueOnce(previewPayload('晚报正文'))
    await state.previewSelectedJob()

    expect(state.previewContent.value).toBe('晚报正文')
    expect(state.previewStaleReason.value).toBe('')
  })
})

describe('预览失效时的确认文案', () => {
  it('不出现正文首行，也不出现旧正文', () => {
    const text = sendNowConfirmText({
      job: { name: '早报', topic_name: '销售报表', target_count: 2 },
      bytes: 0,
      content: '早报正文第一行\n第二行',
      targetNames: ['门店群', '日报群'],
      staleReason: '任务已切换，请重新预览',
    })

    expect(text).not.toContain('正文首行')
    expect(text).not.toContain('早报正文第一行')
    // 收件群与字节数照常给出：这两项与预览无关，仍然能核对。
    expect(text).toContain('2 个群')
    expect(text).toContain('0 / 2048 字节')
    expect(text).toContain('任务已切换，请重新预览')
  })

  it('没有失效标记时仍然给正文首行（既有行为不回退）', () => {
    const text = sendNowConfirmText({
      job: { name: '早报', topic_name: '销售报表', target_count: 2 },
      bytes: 12,
      content: '早报正文第一行\n第二行',
      targetNames: ['门店群', '日报群'],
    })

    expect(text).toContain('正文首行：早报正文第一行')
    expect(text).toContain('12 / 2048 字节')
  })
})
