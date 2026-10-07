// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 07 页面那一半的验收：发送记录 tab 的筛选控件、七列、失败行标识与分页。
//
// 断言的是**页面渲染出来的东西**与**页面发出去的请求**：下拉里有哪些选项、表头有哪几
// 列、翻页/改筛选时请求带什么参数。组件怎么组织这些数据可以改，这几条不能改。
//
// 内容类型下拉的来源是 `/meta` 的注册表（票 02 / ADR 0097）——「加一类内容只改后端」
// 这条约束在页面上就落成「选项不写死」。所以夹具里的 `/meta` 故意给出比渠道/订阅
// 数据里更多的一类内容：写死的实现会漏掉它。

const API_VERSION = 'v1'

/** 注册表（`/meta` 的 topics）：页面只认这一份，不自己维护名字表。 */
const REGISTRY_TOPICS = [
  { id: 'sales_report', name: '销售报表' },
  { id: 'unmapped_dish', name: '未映射菜品提醒' },
  { id: 'hygiene_reminder', name: '卫生提醒' },
]

function channel(id, name, overrides = {}) {
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
    ...overrides,
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

function delivery(overrides = {}) {
  return {
    id: 11,
    created_at: '2026-10-05T21:30:12+08:00',
    finished_at: '2026-10-05T21:30:13+08:00',
    channel_id: 1,
    channel_name: '门店群',
    topic_id: 'sales_report',
    topic_name: '销售报表',
    status: 'sent',
    message_bytes: 512,
    attempts: 1,
    last_error: '',
    content_summary: '【销售报表】2026-10-05',
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

/** 记录每一次请求；`deliveriesFor` 让每一档夹具自己决定按什么筛选返回哪几行。 */
function probeFetch({ channels = [], topics = [], groups = [], deliveriesFor = null } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({
        success: true, api_version: API_VERSION, topics: REGISTRY_TOPICS, job_templates: [],
      })
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
      if (deliveriesFor) return jsonResponse(deliveriesFor(new URL(path, 'http://x').searchParams))
      return jsonResponse({
        success: true, rows: [], total: 0, page: 1, page_size: 50, pages: 0, logs: [],
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

/** 发送记录那张表：按表头文字找，免得被订阅矩阵的表格抢了。 */
function deliveryTable(wrapper) {
  return wrapper.findAll('table.data-table').find((table) =>
    table.findAll('thead th').map((cell) => cell.text()).includes('尝试次数'))
}

function lastLogsCall(fetchMock) {
  return [...fetchMock.calls].reverse().find((call) => call.path.includes('/api/wecom-push/logs'))
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('发送记录 tab 的列表', () => {
  it('每行显示时间 / 目标渠道 / 内容类型 / 状态 / 字节数 / 尝试次数 / 最后一次错误', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
      deliveriesFor: () => ({
        success: true,
        rows: [delivery({
          status: 'failed', attempts: 4, message_bytes: 118,
          last_error: '第 1/1 条发送失败：invalid webhook url',
        })],
        total: 1, page: 1, page_size: 50, pages: 1,
      }),
    })

    await openTab(wrapper, '发送记录')

    const table = deliveryTable(wrapper)
    expect(table, '找不到发送记录表').toBeTruthy()
    expect(table.findAll('thead th').map((cell) => cell.text())).toEqual([
      '时间', '目标渠道', '内容类型', '状态', '字节数', '尝试次数', '最后一次错误',
    ])
    const cells = table.findAll('tbody tr')[0].findAll('td').map((cell) => cell.text())
    // 已完成的行显示**完成时间**：入队时间与「到底发出去没有」不是一回事
    expect(cells[0]).toContain('2026-10-05 21:30:13')
    expect(cells[1]).toBe('门店群')
    expect(cells[2]).toBe('销售报表')
    expect(cells[3]).toContain('失败')
    expect(cells[4]).toBe('118')
    expect(cells[5]).toBe('4')
    expect(cells[6]).toContain('invalid webhook url')
  })

  it('还没完成的行显示入队时间并标注「未完成」，不会被读成已发送', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
      deliveriesFor: () => ({
        success: true,
        rows: [delivery({ status: 'pending', finished_at: '', attempts: 0 })],
        total: 1, page: 1, page_size: 50, pages: 1,
      }),
    })

    await openTab(wrapper, '发送记录')

    const row = deliveryTable(wrapper).findAll('tbody tr')[0]
    expect(row.text()).toContain('2026-10-05 21:30:12')
    expect(row.text()).toContain('未完成')
    expect(row.text()).toContain('待发')
  })

  it('失败行整行有明显标识，成功行没有', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
      deliveriesFor: () => ({
        success: true,
        rows: [
          delivery({ id: 2, status: 'failed', last_error: 'boom', attempts: 4 }),
          delivery({ id: 1, status: 'sent' }),
        ],
        total: 2, page: 1, page_size: 50, pages: 1,
      }),
    })

    await openTab(wrapper, '发送记录')

    const rows = deliveryTable(wrapper).findAll('tbody tr')
    expect(rows[0].classes()).toContain('wp-delivery-failed')
    expect(rows[1].classes()).not.toContain('wp-delivery-failed')
    // 「还没发出去」的行也要看得出来：待发不是已发
    expect(rows[1].text()).toContain('已发')
  })
})

describe('发送记录 tab 的筛选', () => {
  const CHANNELS = [channel(1, '门店群'), channel(2, '日报群')]

  it('内容类型下拉来自 /meta 的注册表，不写死', async () => {
    const { wrapper } = await mountView({
      channels: CHANNELS,
      topics: [topic('sales_report', '销售报表', [1])],
    })

    await openTab(wrapper, '发送记录')

    const selects = deliveryTable(wrapper).element.closest('div').parentElement
      .querySelectorAll('select')
    const topicSelect = [...selects].find((node) =>
      [...node.options].some((option) => option.textContent === '卫生提醒'))
    expect(topicSelect, '内容类型下拉没有出现注册表里的「卫生提醒」，选项可能是写死的').toBeTruthy()
    const labels = [...topicSelect.options].map((option) => option.textContent)
    expect(labels[0]).toBe('全部内容类型')
    for (const registered of REGISTRY_TOPICS) {
      expect(labels).toContain(registered.name)
    }
  })

  it('状态下拉给出全部五种出站状态', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: [] })
    await openTab(wrapper, '发送记录')

    const controls = wrapper.text()
    for (const label of ['待发', '发送中', '已发', '失败', '已跳过']) {
      expect(controls).toContain(label)
    }
  })

  it('选好筛选点「筛选」：请求带上三项参数，页码回到 1', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: CHANNELS,
      topics: [],
      deliveriesFor: () => ({
        success: true, rows: [delivery()], total: 1, page: 1, page_size: 50, pages: 1,
      }),
    })
    await openTab(wrapper, '发送记录')
    const selects = wrapper.findAll('select')
    await selects[0].setValue('hygiene_reminder')
    await selects[1].setValue('2')
    await selects[2].setValue('failed')

    const apply = wrapper.findAll('button').find((node) => node.text() === '筛选')
    await apply.trigger('click')
    await flushPromises()

    const call = lastLogsCall(fetchMock)
    const query = new URL(call.path, 'http://x').searchParams
    expect(query.get('topic_id')).toBe('hygiene_reminder')
    expect(query.get('channel_id')).toBe('2')
    expect(query.get('status')).toBe('failed')
    expect(query.get('page')).toBe('1')
  })

  it('筛不到记录时说清是筛选的结果，而不是「暂无发送记录」', async () => {
    const { wrapper } = await mountView({
      channels: CHANNELS,
      topics: [],
      deliveriesFor: () => ({
        success: true, rows: [], total: 0, page: 1, page_size: 50, pages: 0,
      }),
    })
    await openTab(wrapper, '发送记录')
    const selects = wrapper.findAll('select')
    await selects[2].setValue('failed')

    const apply = wrapper.findAll('button').find((node) => node.text() === '筛选')
    await apply.trigger('click')
    await flushPromises()

    const table = deliveryTable(wrapper)
    expect(table.text()).toContain('没有符合条件的记录')
    // 记录本身为空与筛完为空是两件事：第一页那一次老实的空表仍然说「暂无」
    const first = probeFetch({ channels: CHANNELS, topics: [] })
    vi.stubGlobal('fetch', first)
    const pinia = createPinia()
    setActivePinia(pinia)
    const second = mount(WecomPushView, { global: { plugins: [pinia] } })
    await flushPromises()
    await flushPromises()
    await openTab(second, '发送记录')
    expect(deliveryTable(second).text()).toContain('暂无发送记录')
  })
})

describe('发送记录 tab 的分页', () => {
  it('显示第几页 / 共几页与总数，翻页请求目标页且筛选跟着走', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [],
      deliveriesFor: (query) => ({
        success: true,
        rows: [delivery({ id: Number(query.get('page')) || 1 })],
        total: 120,
        page: Number(query.get('page')) || 1,
        page_size: 50,
        pages: 3,
      }),
    })
    await openTab(wrapper, '发送记录')

    expect(wrapper.text()).toContain('共 120 条')
    expect(wrapper.text()).toContain('第 1 / 3 页')

    // 第一页时「上一页」不可点
    const prev = wrapper.findAll('button').find((node) => node.text() === '上一页')
    expect(prev.attributes('disabled')).toBeDefined()

    const next = wrapper.findAll('button').find((node) => node.text() === '下一页')
    await next.trigger('click')
    await flushPromises()

    expect(new URL(lastLogsCall(fetchMock).path, 'http://x').searchParams.get('page')).toBe('2')
    expect(wrapper.text()).toContain('第 2 / 3 页')
  })
})
