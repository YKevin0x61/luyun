// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 06 页面那一半的验收：页内 tab 骨架、渠道卡片上那几样、订阅矩阵的勾选与请求体、
// 渠道超过 8 个时切成「先选内容、再勾群」的多选列表。
//
// 断言的是**渲染出来的东西**与**页面发出去的请求**，不是组件内部状态：组件怎么组织
// 这些数据可以改，勾一下要发什么请求、超阈值之后页面长什么样不能改。
//
// 与 `wecomPushLayout.test.js`（读源码钉窄屏那几条 CSS）分工：那一份挡的是"样式被
// 顺手简化掉"，这一份挡的是"功能没接上"。

const API_VERSION = 'v2'

/** 一条渠道的完整卡片形状（读接口给的就是这个）。 */
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

function topic(id, name, subscribed = [], overrides = {}) {
  return {
    id,
    name,
    triggers: ['event'],
    contains_employee_photos: false,
    default_schedule_time: null,
    channels: subscribed.map((channelId) => ({ id: channelId, enabled: true })),
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

/** 记录每一次请求；`channels` 用闭包给，方便多选列表那一档造 9 个渠道。 */
function probeFetch({ channels = [], topics = [], groups = [] }) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({ success: true, api_version: API_VERSION, job_templates: [] })
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({
        success: true, api_version: API_VERSION, channels, topics,
      })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({ success: true, webhooks: [], channels, topics, groups })
    }
    if (path.includes('/api/wecom-push/jobs')) return jsonResponse({ success: true, jobs: [] })
    if (path.includes('/api/wecom-push/logs')) return jsonResponse({ success: true, logs: [] })
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

/** 页内 tab：按可见文字点。 */
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

describe('企微推送页的 tab 骨架（不改路由）', () => {
  it('四个页内 tab 都在，默认停在「渠道」', async () => {
    const { wrapper } = await mountView({ channels: [channel(1, '门店群')], topics: [topic('sales_report', '销售报表')] })

    expect(wrapper.findAll('.view-tab').map((node) => node.text())).toEqual([
      '渠道', '订阅', '定时任务', '发送记录',
    ])
    expect(wrapper.find('.view-tab.active').text()).toBe('渠道')
    expect(wrapper.text()).toContain('渠道群组')
  })

  it('切到「订阅」看到矩阵，切到「发送记录」看到记录表', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })

    await openTab(wrapper, '订阅')
    expect(wrapper.find('table.wp-matrix').exists()).toBe(true)
    expect(wrapper.find('table.wp-matrix').text()).toContain('销售报表')

    await openTab(wrapper, '发送记录')
    expect(wrapper.text()).toContain('暂无发送记录')

    await openTab(wrapper, '定时任务')
    expect(wrapper.text()).toContain('推送任务')
  })
})

describe('渠道 tab 的卡片', () => {
  it('显示启停 / 备注 / 所属群组 / 订阅内容 / 最近一次发送成功时间', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群', {
        notes: '早班用',
        job_count: 2,
        last_sent_at: '2026-10-05T21:30:12+08:00',
        groups: [{ id: 3, name: '日报群组', enabled: true }],
        topics: [{ id: 'sales_report', name: '销售报表', via_group: true }],
      })],
      topics: [topic('sales_report', '销售报表', [1])],
      groups: [{ id: 3, name: '日报群组', enabled: true, notes: '', member_channel_ids: [1] }],
    })

    const text = wrapper.text()
    expect(text).toContain('启用')
    expect(text).toContain('早班用')
    expect(text).toContain('日报群组')
    expect(text).toContain('销售报表')
    expect(text).toContain('2026-10-05 21:30:12')
    expect(text).toContain('含群组订阅')
  })

  it('删除渠道前先给出后果提示，取消后不发请求', async () => {
    // 任务不再绑定渠道（票 08）：删除不再会被「被任务引用」拦下来，确认框只说清
    // 订阅与群组成员会跟着走。
    const { wrapper, fetchMock } = await mountView({
      channels: [channel(1, '门店群', { job_count: 1, topics: [{ id: 'sales_report', name: '销售报表' }] })],
      topics: [topic('sales_report', '销售报表')],
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    const del = wrapper.findAll('button').find((node) => node.text() === '删除')
    await del.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(String(confirmSpy.mock.calls[0][0])).toContain('订阅')
    expect(String(confirmSpy.mock.calls[0][0])).not.toContain('推送任务')
    // 用户点了「取消」：一条写请求都不该发出去
    expect(fetchMock.calls.filter((call) => call.method !== 'GET')).toEqual([])
  })
})

describe('订阅矩阵', () => {
  const CHANNELS = [channel(1, '门店群'), channel(2, '日报群')]
  const TOPICS = [
    topic('sales_report', '销售报表', [1]),
    topic('hygiene_photo', '验收照片', [], { contains_employee_photos: true }),
  ]

  it('渠道不超过阈值时是勾选矩阵：行=内容类型，列=渠道', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const table = wrapper.find('table.wp-matrix')
    expect(table.exists()).toBe(true)
    const header = table.findAll('thead th').map((cell) => cell.text())
    expect(header[0]).toBe('内容类型')
    expect(header.slice(1).join('|')).toContain('门店群')
    expect(header.slice(1).join('|')).toContain('日报群')
    expect(table.findAll('tbody tr')).toHaveLength(2)
  })

  it('勾选某个内容类型给某个渠道：请求体带 topic / channel / enabled', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    // 第一行（销售报表）里「日报群」那一格的勾选框：当前没勾 → 点一下就是勾上
    const cells = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')
    await cells[2].find('button').trigger('click')
    await flushPromises()

    const post = fetchMock.calls.find((call) => call.method === 'POST')
    expect(post, '勾选没有发出请求').toBeTruthy()
    expect(post.path).toContain('/api/wecom-push/subscriptions')
    expect(JSON.parse(post.body)).toMatchObject({
      topic_id: 'sales_report',
      target_channel_id: 2,
      enabled: true,
    })
  })

  it('零订阅的内容类型：行高亮 + 页顶提示条', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const rows = wrapper.find('table.wp-matrix').findAll('tbody tr')
    expect(rows[1].classes()).toContain('wp-matrix-zero')
    expect(rows[0].classes()).not.toContain('wp-matrix-zero')

    const banner = wrapper.findAll('.dash-error-banner').map((node) => node.text()).join('\n')
    expect(banner).toContain('验收照片')
    expect(banner).toContain('不会发出')
  })

  it('卫生类内容所在行标注「含员工实拍照片」', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const rows = wrapper.find('table.wp-matrix').findAll('tbody tr')
    expect(rows[1].text()).toContain('含员工实拍照片')
    expect(rows[0].text()).not.toContain('含员工实拍照片')
  })

  it('渠道超过 8 个自动切成「先选内容、再勾群」的多选列表', async () => {
    const many = Array.from({ length: 9 }, (_, index) => channel(index + 1, `群 ${index + 1}`))
    const { wrapper, fetchMock } = await mountView({
      channels: many,
      topics: [topic('sales_report', '销售报表', [1])],
    })
    await openTab(wrapper, '订阅')

    expect(wrapper.find('table.wp-matrix').exists()).toBe(false)
    const picks = wrapper.findAll('.wp-pick-row')
    expect(picks).toHaveLength(1)
    expect(picks[0].text()).toContain('销售报表')

    // 先选内容 → 再勾群 → 保存：只发**变化**的那几条（原本只有 1 勾着）
    await picks[0].trigger('click')
    await flushPromises()
    const rows = wrapper.findAll('.luyun-check-row')
    expect(rows.length).toBe(9)
    await rows[1].find('button').trigger('click')
    await flushPromises()

    const save = wrapper.findAll('button').find((node) => node.text() === '保存订阅')
    await save.trigger('click')
    await flushPromises()

    const posts = fetchMock.calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0].body)).toMatchObject({
      topic_id: 'sales_report', target_channel_id: 2, enabled: true,
    })
  })
})

describe('接口版本提示条', () => {
  it('对得上就不出现；对不上则出现且不阻断操作', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表')],
    })
    expect(wrapper.text()).not.toContain('接口版本不一致')

    const stale = probeFetch({ channels: [], topics: [] })
    // 这一档让 /meta 报一个与前端常量不同的版本
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (String(url).includes('/api/wecom-push/meta')) {
        return jsonResponse({ success: true, api_version: 'v0', job_templates: [] })
      }
      return stale(url, options)
    }))
    const pinia = createPinia()
    setActivePinia(pinia)
    const second = mount(WecomPushView, { global: { plugins: [pinia] } })
    await flushPromises()
    await flushPromises()

    expect(second.text()).toContain('接口版本不一致')
    // 不阻断：表单与按钮照旧在
    expect(second.find('button[type="submit"]').exists()).toBe(true)
  })
})
