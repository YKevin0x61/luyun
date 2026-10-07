// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 这一份钉的是「页面在非正常状态下长什么样」与「控件的程序化名称」：
//   U4 首屏加载态（数据没回来时不能说「0 个 / 暂无渠道」）；
//   U5 加载失败只显示中文人话 + 重试，英文原文折叠在「详情」里；
//   U10 14 个表单控件的 id/for 与筛选下拉的 aria-label。
//
// 三块卡片的加载态都用同一个 `loading`（composable 里已有、模板以前没用），所以这里
// 只钉「挂起时看到的是加载态、不是空态」这一件事，具体骨架长什么样不锁。

const API_VERSION = 'v2'

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function channel(id, name) {
  return {
    id,
    name,
    enabled: true,
    notes: '',
    webhook_url_masked: `https://qyapi.weixin.qq.com/***${id}`,
    job_count: 0,
    last_sent_at: '',
    groups: [],
    topics: [],
  }
}

function topic(id, name, subscribed = []) {
  return {
    id,
    name,
    triggers: ['event'],
    contains_employee_photos: false,
    default_schedule_time: null,
    channels: subscribed.map((channelId) => ({ id: channelId, enabled: true })),
  }
}

/**
 * 可编排的读接口：`failReads` 为真时三个首屏读接口都 500（正文是 FastAPI 的英文 detail）。
 * 返回的对象带 `calls` 与 `setFail()`，测试中途可以把故障摘掉再点「重试」。
 */
function probeFetch({ channels = [], topics = [], groups = [] } = {}) {
  const calls = []
  const state = { failReads: false }
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (state.failReads && !path.includes('/api/staff')) {
      return jsonResponse({ detail: 'Internal Server Error' }, 500)
    }
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({ success: true, api_version: API_VERSION, topics, job_templates: [] })
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({ success: true, api_version: API_VERSION, channels, topics })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({ success: true, webhooks: [], channels, topics, groups })
    }
    if (path.includes('/api/wecom-push/jobs')) return jsonResponse({ success: true, jobs: [] })
    if (path.includes('/api/wecom-push/logs')) {
      return jsonResponse({
        success: true, rows: [], total: 0, page: 1, page_size: 50, pages: 0, logs: [],
      })
    }
    if (path.includes('/api/stations')) return jsonResponse([])
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  fetchMock.state = state
  return fetchMock
}

async function mountView(fetchMock) {
  vi.stubGlobal('fetch', fetchMock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(WecomPushView, { global: { plugins: [pinia] } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

async function openTab(wrapper, name) {
  const tab = wrapper.findAll('.view-tab').find((node) => node.text() === name)
  expect(tab, `tab「${name}」不在页面上`).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// U5：后端 5xx 时页面把 `Internal Server Error` 原样端给店长。
describe('加载失败的中文化与重试（U5）', () => {
  it('5xx 不把英文原文端出来：显示「服务暂时不可用」+ 重试，原文进详情', async () => {
    const fetchMock = probeFetch()
    fetchMock.state.failReads = true
    const wrapper = await mountView(fetchMock)

    const banner = wrapper.find('.wp-load-error')
    expect(banner.exists(), '加载失败时没有提示条').toBeTruthy()
    // 店长读的是这一行：中文、可操作，没有英文原文
    const headline = banner.find('.wp-load-error__text').text()
    expect(headline).toContain('服务暂时不可用')
    expect(headline).not.toContain('Internal Server Error')
    // 原文一个字不丢，折叠在「详情」里
    expect(banner.find('details pre').text()).toContain('Internal Server Error')
    expect(banner.find('details pre').text()).toContain('HTTP 500')
    expect(banner.findAll('button').map((node) => node.text())).toContain('重试')
  })

  it('点「重试」重新拉一次，成功后提示条自己消失、数据上屏', async () => {
    const fetchMock = probeFetch({ channels: [channel(1, '门店群')] })
    fetchMock.state.failReads = true
    const wrapper = await mountView(fetchMock)
    expect(wrapper.find('.wp-load-error').exists()).toBe(true)

    const before = fetchMock.calls.length
    fetchMock.state.failReads = false
    await wrapper.find('.wp-load-error button').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(fetchMock.calls.length).toBeGreaterThan(before)
    expect(wrapper.find('.wp-load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('门店群')
  })
})

// U4：数据没回来时页面渲染的是"空数据"的样子（「渠道 0 个 / 暂无渠道」）。
describe('首屏加载态（U4）', () => {
  it('读接口挂起时显示加载态，不说「0 个 / 暂无渠道」', async () => {
    // /meta 永远不返回：loadAll 卡在第一步，三块卡片都还没有数据
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('/api/wecom-push/meta')) return new Promise(() => {})
      return Promise.resolve(jsonResponse({ success: true, topics: [], channels: [], groups: [] }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(WecomPushView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.find('.wp-skeleton').exists(), '首屏没有加载态节点').toBe(true)
    const text = wrapper.text()
    expect(text).toContain('加载中…')
    expect(text).not.toContain('暂无渠道')
    expect(text).not.toContain('暂无群组')
    expect(text).not.toContain('0 个')
    // 加载态是朗读友好的（读屏能听到"正在加载"，不是一片空白）
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('数据回来之后才是空态（加载态不会一直挂着）', async () => {
    const wrapper = await mountView(probeFetch())
    expect(wrapper.find('.wp-skeleton').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂无渠道')
  })
})

// U10（旧清单 E4 在本页的落点）：14 个表单控件 0 个有程序化名称。
describe('表单控件的程序化名称（U10）', () => {
  it('渠道 / 群组 / 任务表单的每个控件都能被自己的 label 指到', async () => {
    const fetchMock = probeFetch()
    const wrapper = await mountView(fetchMock)

    const pairs = [
      ['名称', 'wp-channel-name'],
      ['Webhook 地址', 'wp-channel-url'],
      ['备注', 'wp-channel-notes'],
      ['群组名称', 'wp-group-name'],
    ]
    for (const [label, id] of pairs) {
      const node = wrapper.findAll('label').find((item) => item.text() === label
        && item.attributes('for') === id)
      expect(node, `label「${label}」没有 for="${id}"`).toBeTruthy()
      expect(wrapper.find(`#${id}`).exists(), `控件 #${id} 不在页面上`).toBe(true)
    }

    await openTab(wrapper, '定时任务')
    for (const [label, id] of [['任务名称', 'wp-job-name'], ['内容类型', 'wp-job-topic']]) {
      const node = wrapper.findAll('label').find((item) => item.text() === label
        && item.attributes('for') === id)
      expect(node, `label「${label}」没有 for="${id}"`).toBeTruthy()
      expect(wrapper.find(`#${id}`).exists()).toBe(true)
    }
    // 预览框没有可见标签，用 aria-label
    expect(wrapper.find('#wp-job-preview').attributes('aria-label')).toBe('消息预览内容')
  })

  it('四个筛选下拉各自带 aria-label（没有可见标签的那一类）', async () => {
    const wrapper = await mountView(probeFetch({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    }))

    await openTab(wrapper, '发送记录')
    for (const label of ['按内容类型筛选', '按目标渠道筛选', '按状态筛选']) {
      expect(wrapper.find(`select[aria-label="${label}"]`).exists(), `缺 ${label}`).toBe(true)
    }

    await openTab(wrapper, '变更历史')
    expect(wrapper.find('select[aria-label="按对象类型筛选"]').exists()).toBe(true)
  })
})
