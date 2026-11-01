// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import WecomPushView from '../WecomPushView.vue'

// 票 02 页面那一半的验收：
//   - 默认落点随状态走（没渠道 → 渠道；有渠道没任务 → 定时任务；常规 → 发送记录）；
//   - `?tab=logs` / `?tab=subscriptions` 直达，切 tab 后 URL 跟着变（刷新不丢）；
//   - 页顶零订阅黄条可点，直达订阅 tab。
//
// 路径仍是 `/wecom-push`：`pageRoutes.json` / `main.py` / 反代一个字没动，所以这里也只验
// query。装了 router 的用例才验 URL（没装 router 时 tab 只存在内存里，见视图里的注释）。

const API_VERSION = 'v5'

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
    triggers: ['cron'],
    contains_employee_photos: false,
    default_schedule_time: '09:00',
    channels: subscribed.map((channelId) => ({ id: channelId, enabled: true })),
  }
}

function job(id, name, topicId) {
  return {
    id,
    name,
    topic_id: topicId,
    enabled: true,
    notes: '',
    params: { schedule_time: '09:00' },
    schedule_text: '每天 09:00',
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

function probeFetch({ channels = [], topics = [], jobs = [] } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({
        success: true, api_version: API_VERSION, topics, job_templates: [],
      })
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({ success: true, api_version: API_VERSION, channels, topics })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups: [] })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({
        success: true, webhooks: [], channels, topics, groups: [],
      })
    }
    if (path.includes('/api/wecom-push/jobs')) {
      return jsonResponse({ success: true, api_version: API_VERSION, jobs })
    }
    if (path.includes('/api/wecom-push/logs')) {
      return jsonResponse({
        success: true, rows: [], total: 0, page: 1, page_size: 50, pages: 0,
      })
    }
    if (path.includes('/api/stations')) return jsonResponse([])
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView(options, { router = null } = {}) {
  const fetchMock = probeFetch(options)
  vi.stubGlobal('fetch', fetchMock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const plugins = router ? [pinia, router] : [pinia]
  const wrapper = mount(WecomPushView, { global: { plugins } })
  await flushPromises()
  await flushPromises()
  return { wrapper, fetchMock }
}

function newRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/wecom-push', name: 'wecom-push', component: { template: '<div />' } }],
  })
}

function activeTab(wrapper) {
  return wrapper.find('.view-tab.active').text()
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('默认落点随状态走（票 02）', () => {
  it('一个渠道都没有：首屏还是渠道引导（新装先配渠道）', async () => {
    const { wrapper } = await mountView({ channels: [], topics: [topic('sales_report', '销售报表')] })
    expect(activeTab(wrapper)).toBe('渠道')
    expect(wrapper.text()).toContain('新增渠道')
  })

  it('有渠道但没任务：首屏落在定时任务', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })
    expect(activeTab(wrapper)).toBe('定时任务')
  })

  it('有渠道有任务：首屏落在发送记录（每天打开这一页要看的就是它）', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
      jobs: [job(7, '每日销售报表', 'sales_report')],
    })
    expect(activeTab(wrapper)).toBe('发送记录')
    expect(wrapper.text()).toContain('暂无发送记录')
  })
})

describe('?tab= 深链与刷新（票 02）', () => {
  it('?tab=logs 直达发送记录', async () => {
    const router = newRouter()
    await router.push('/wecom-push?tab=logs')
    await router.isReady()
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    }, { router })
    expect(activeTab(wrapper)).toBe('发送记录')
  })

  it('?tab=subscriptions 直达订阅', async () => {
    const router = newRouter()
    await router.push('/wecom-push?tab=subscriptions')
    await router.isReady()
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    }, { router })
    expect(activeTab(wrapper)).toBe('订阅')
    expect(wrapper.find('table.wp-matrix').exists()).toBe(true)
  })

  it('不认识的 tab 名字当没写，按状态落默认', async () => {
    const router = newRouter()
    await router.push('/wecom-push?tab=whatever')
    await router.isReady()
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
      jobs: [job(7, '每日销售报表', 'sales_report')],
    }, { router })
    expect(activeTab(wrapper)).toBe('发送记录')
  })

  it('切 tab 会把 tab 写进 URL（刷新后不丢），路径不变', async () => {
    const router = newRouter()
    await router.push('/wecom-push')
    await router.isReady()
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    }, { router })

    const auditTab = wrapper.findAll('.view-tab').find((node) => node.text() === '变更历史')
    await auditTab.trigger('click')
    await flushPromises()

    expect(activeTab(wrapper)).toBe('变更历史')
    expect(router.currentRoute.value.path).toBe('/wecom-push')
    expect(router.currentRoute.value.query.tab).toBe('audit')
  })
})

describe('页顶零订阅黄条可点（票 02）', () => {
  it('点黄条上的按钮直达订阅 tab', async () => {
    const { wrapper } = await mountView({
      // 有渠道，但这类内容一个渠道都没订 → 页顶黄条出现
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [])],
      jobs: [job(7, '每日销售报表', 'sales_report')],
    })

    const banner = wrapper.find('.dash-error-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('请在「订阅」里勾选收件渠道')

    const button = banner.findAll('button').find((node) => node.text().includes('订阅'))
    expect(button, '黄条上没有可点的入口').toBeTruthy()
    await button.trigger('click')
    await flushPromises()

    expect(activeTab(wrapper)).toBe('订阅')
  })
})
