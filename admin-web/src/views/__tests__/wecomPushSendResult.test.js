// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 05 页面那一半的验收：点「立即发送」之后，不离开「定时任务」tab 就能看到这次发送
// 的逐条状态；文案只能承诺「约半分钟内出结果」（`send-now` 只是入队，出站 30 秒一轮）；
// 轮询有上限，到点停下并给出去处。
//
// 断言的是**渲染出来的东西**与**发出去的请求**：
//   - 结果卡在预览卡下面、列出本次任务的投递行；
//   - 「查看全部 →」带 topic_id 落到发送记录（不是全表）；
//   - 失败原因在原地可见；
//   - 12 轮之后不再发请求。
//
// 「是否真的发出去了」不在这一层验证：这里连的都是假 fetch，不碰任何企微接口。

const API_VERSION = 'v5'

const JOBS = [{
  id: 7,
  name: '每日销售报表',
  topic_id: 'sales_report',
  enabled: true,
  notes: '',
  params: { schedule_time: '09:00' },
  schedule_text: '每天 09:00',
}]

const TOPICS = [{
  id: 'sales_report',
  name: '销售报表',
  triggers: ['scheduled'],
  contains_employee_photos: false,
  default_schedule_time: '09:00',
  channels: [{ id: 1, enabled: true }],
}]

const CHANNELS = [{
  id: 1,
  name: '门店群',
  enabled: true,
  notes: '',
  webhook_url_masked: 'https://qyapi.weixin.qq.com/***1',
  job_count: 1,
  last_sent_at: '',
  groups: [],
  topics: ['sales_report'],
}]

function delivery(overrides = {}) {
  return {
    id: 101,
    created_at: '2026-11-02T09:00:00+08:00',
    finished_at: '',
    channel_id: 1,
    channel_name: '门店群',
    topic_id: 'sales_report',
    topic_name: '销售报表',
    status: 'pending',
    message_bytes: 512,
    attempts: 0,
    last_error: '',
    content_summary: '【销售报表】2026-11-02',
    ...overrides,
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/**
 * 假后端。`logRows` / `sendResult` 由每个用例决定 —— 尤其是 `logRows` 可以随轮询次数
 * 改返回值（第二次轮询起变成「已发」）。
 */
function probeFetch({ logRows = () => [], sendResult = { success: true, target_count: 1 } } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({
        success: true, api_version: API_VERSION, topics: TOPICS, job_templates: [],
      })
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({ success: true, api_version: API_VERSION, channels: CHANNELS, topics: TOPICS })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups: [] })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({
        success: true, webhooks: [], channels: CHANNELS, topics: TOPICS, groups: [],
      })
    }
    // 先判更具体的两条：它们也含 `/jobs`
    if (path.includes('/send-now')) {
      return jsonResponse({ success: sendResult.success, api_version: API_VERSION, ...sendResult })
    }
    if (path.includes('/preview')) {
      return jsonResponse({
        success: true,
        api_version: API_VERSION,
        kind: 'text',
        content: '【销售报表】2026-11-02',
        byte_length: 64,
        chunk_count: 1,
        limit: 2048,
      })
    }
    if (path.includes('/api/wecom-push/jobs')) {
      return jsonResponse({ success: true, api_version: API_VERSION, jobs: JOBS })
    }
    if (path.includes('/api/wecom-push/logs')) {
      const params = new URL(path, 'http://x').searchParams
      return jsonResponse({
        success: true, rows: logRows(params), total: 2, page: 1, page_size: 50, pages: 1,
      })
    }
    if (path.includes('/api/stations')) return jsonResponse([])
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView(options) {
  const fetchMock = probeFetch(options)
  vi.stubGlobal('fetch', fetchMock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(WecomPushView, { global: { plugins: [pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, fetchMock }
}

async function openTab(wrapper, name) {
  const tab = wrapper.findAll('.view-tab').find((node) => node.text() === name)
  expect(tab, `tab「${name}」不在页面上`).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

/** 走到「有预览、可以点立即发送」：进定时任务 tab，点任务卡上的「预览」。 */
async function openJobsWithPreview(wrapper) {
  await openTab(wrapper, '定时任务')
  const previewButton = wrapper.findAll('.wp-job-card .btn').find((node) => node.text() === '预览')
  expect(previewButton, '任务卡上没有「预览」按钮').toBeTruthy()
  await previewButton.trigger('click')
  await flushPromises()
  await flushPromises()
}

function logsCalls(fetchMock) {
  return fetchMock.calls.filter((call) => call.path.includes('/api/wecom-push/logs'))
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  // 定时器一定要真实回来：假定时器漏到别的文件里会让整个 worker 卡住。
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('点「立即发送」之后的结果卡（票 05）', () => {
  it('不离开当前 tab 就能看到本次发送的逐条状态，且「查看全部」带 topic 过滤', async () => {
    const { wrapper, fetchMock } = await mountView({
      // 结果卡按任务 id 取行（带 job_id），只该拿到本次这条；发送记录那条全表查询里混着
      // 别的内容类型 —— 结果卡若借用那边的一页，这行就会漏进来。
      logRows: (params) => (params.get('job_id')
        ? [delivery({ id: 101, status: 'pending' })]
        : [
          delivery({ id: 101, status: 'pending' }),
          // 另一个内容类型的投递：结果卡只认本次这条任务，不能把它算进来
          delivery({ id: 102, topic_id: 'unmapped_dish', channel_name: '后厨群', status: 'sent' }),
        ]),
    })
    await openJobsWithPreview(wrapper)

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const sendButton = wrapper.find('.wp-send-now')
    expect(sendButton.attributes('disabled'), '有预览时「立即发送」不该是灰的').toBeUndefined()
    await sendButton.trigger('click')
    await flushPromises()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('本次发送')
    expect(text).toContain('已加入发送队列：将发给 1 个群，约半分钟内出结果。')
    // 逐条状态：待发（不是「已发送」）
    expect(text).toContain('待发')
    expect(text).toContain('门店群')
    expect(text).not.toContain('后厨群')
    // 不承诺即时送达
    expect(text).not.toContain('已成功发送')
    expect(text).not.toContain('已发送给')

    // toast 降成辅助，但读屏要能听到
    const live = wrapper.findAll('[role="status"]')
    expect(live.some((node) => node.attributes('aria-live') === 'polite')).toBe(true)

    // 「查看全部 →」落在**已按这条任务筛过**的发送记录上
    const viewAll = wrapper.findAll('button').find((node) => node.text().includes('查看全部'))
    expect(viewAll, '结果卡上没有「查看全部」').toBeTruthy()
    await viewAll.trigger('click')
    await flushPromises()
    await flushPromises()

    const last = logsCalls(fetchMock).at(-1)
    expect(last.path).toContain('topic_id=sales_report')
    const activeTab = wrapper.find('.view-tab.active')
    expect(activeTab.text()).toBe('发送记录')

    wrapper.unmount()
  })

  it('发送失败时错误原因在原地展开，不轮询', async () => {
    const { wrapper, fetchMock } = await mountView({
      sendResult: { success: false, error: '请先完成预览，再发送' },
    })
    await openJobsWithPreview(wrapper)
    vi.useFakeTimers()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.find('.wp-send-now').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(wrapper.text()).toContain('发送失败：请先完成预览，再发送')

    // 失败不轮询：推 2 分钟也不该再打发送记录
    const before = logsCalls(fetchMock).length
    await vi.advanceTimersByTimeAsync(120000)
    expect(logsCalls(fetchMock).length).toBe(before)

    wrapper.unmount()
  })

  it('轮询 12 轮后自己停下，并提示去发送记录看结果', async () => {
    const { wrapper, fetchMock } = await mountView({
      logRows: () => [delivery({ id: 101, status: 'pending' })],
    })
    await openJobsWithPreview(wrapper)
    vi.useFakeTimers()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.find('.wp-send-now').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    const before = logsCalls(fetchMock).length
    // 每 10 秒一轮，最多 12 轮（约 2 分钟）
    await vi.advanceTimersByTimeAsync(10 * 1000 * 12)
    expect(logsCalls(fetchMock).length - before).toBe(12)
    expect(wrapper.text()).toContain('轮询已停止：可稍后在「发送记录」查看结果。')

    // 到点之后不再有请求：不无限轮询
    await vi.advanceTimersByTimeAsync(60 * 1000)
    expect(logsCalls(fetchMock).length - before).toBe(12)

    wrapper.unmount()
  })

  it('发送记录那边筛空、翻页，不影响本次发送的结果卡', async () => {
    // 结果卡曾经复用「发送记录」当前那一页再按内容类型过滤 —— 用户在那边筛了「只看失败」
    // 或翻到第 2 页，卡上就空了、或者列出上周的行。这里让「不带 job_id」的查询返回空页：
    // 一旦实现退回旧写法，卡上什么都看不到。
    const { wrapper, fetchMock } = await mountView({
      logRows: (params) => (params.get('job_id') ? [delivery({ id: 201, status: 'pending' })] : []),
    })
    await openJobsWithPreview(wrapper)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.find('.wp-send-now').trigger('click')
    await flushPromises()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('本次发送')
    expect(text).toContain('待发')
    expect(text).toContain('门店群')

    // 取行要按**这条任务**并且只要 3 行，与发送记录那边的筛选、页码无关。
    const scoped = logsCalls(fetchMock).filter((call) => call.path.includes('job_id=7'))
    expect(scoped.length, '结果卡没有按任务 id 取行').toBeGreaterThan(0)
    expect(scoped.at(-1).path).toContain('page_size=3')

    wrapper.unmount()
  })

  it('轮询把「待发」刷新成「已发」', async () => {
    // 入队那一刻是「待发」；出站发完之后再拉就是「已发」——页面要能自己跟过去。
    let finished = false
    const { wrapper } = await mountView({
      logRows: () => [delivery({ id: 101, status: finished ? 'sent' : 'pending' })],
    })
    await openJobsWithPreview(wrapper)
    vi.useFakeTimers()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.find('.wp-send-now').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('待发')

    finished = true
    await vi.advanceTimersByTimeAsync(10 * 1000)
    expect(wrapper.text()).toContain('已发')

    wrapper.unmount()
  })
})
