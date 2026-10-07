// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 11 页面那一半：第五个 tab「变更历史」——按时间倒序看配置变更，可按对象类型筛。
//
// 关键的一条是**筛选项来自读接口**（`object_types`）：夹具里那个假对象类型在页面代码里
// 从未出现过，只要它出现在响应里，下拉里就有它。哪天有人把四种对象类型写死在模板里，
// 这条就会红。

const API_VERSION = 'v2'

/** 前端从未见过的对象类型：名字与 id 都只有接口知道。 */
const FAKE_OBJECT_TYPE = { id: 'wecom_fake_ops', name: '夹具对象' }

const AUDIT_ROWS = [
  {
    id: 21,
    created_at: '2026-10-05T21:30:12+08:00',
    actor: 'admin',
    action: 'disable',
    object_type: 'wecom_push_webhooks',
    object_id: 3,
    object_name: '门店群',
    before: { name: '门店群', enabled: true, url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=693a...5aaa' },
    after: { name: '门店群', enabled: false, url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=693a...5aaa' },
  },
  {
    id: 20,
    created_at: '2026-10-05T21:20:00+08:00',
    actor: 'admin',
    action: 'create',
    object_type: 'wecom_push_subscriptions',
    object_id: 4,
    object_name: '销售报表 → 门店群',
    before: {},
    after: { topic_id: 'sales_report', target_name: '门店群', enabled: true },
  },
  {
    id: 19,
    created_at: '2026-10-05T21:10:00+08:00',
    actor: 'admin',
    action: 'delete',
    object_type: 'wecom_push_jobs',
    object_id: 7,
    object_name: '旧日报',
    before: { name: '旧日报', schedule_time: '21:30', enabled: true },
    after: {},
  },
]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function auditPayload(overrides = {}) {
  return {
    success: true,
    api_version: API_VERSION,
    rows: AUDIT_ROWS,
    total: 3,
    page: 1,
    page_size: 50,
    pages: 1,
    object_types: [
      { id: 'wecom_push_webhooks', name: '推送渠道' },
      FAKE_OBJECT_TYPE,
    ],
    actions: [
      { id: 'create', name: '新增' },
      { id: 'update', name: '修改' },
      { id: 'enable', name: '启用' },
      { id: 'disable', name: '停用' },
      { id: 'delete', name: '删除' },
    ],
    ...overrides,
  }
}

function probeFetch(auditOverrides = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({ success: true, api_version: API_VERSION, topics: [], job_templates: [] })
    }
    if (path.includes('/api/wecom-push/audit-log')) {
      // 回显请求的页码 / 筛选，页面据此更新「第 N / M 页」与行数据。
      const query = new URLSearchParams(path.split('?')[1] || '')
      return jsonResponse(auditPayload({
        page: Number(query.get('page')) || 1,
        ...auditOverrides,
      }))
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({ success: true, api_version: API_VERSION, topics: [], channels: [] })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({ success: true, webhooks: [], channels: [], topics: [], groups: [] })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups: [] })
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
  return fetchMock
}

async function mountView(auditOverrides = {}) {
  const fetchMock = probeFetch(auditOverrides)
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

/** 变更历史那一张表的每一行文本。 */
function auditTableRows(wrapper) {
  return wrapper.findAll('.wp-audit-table tbody tr').map((row) => row.text())
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('变更历史 tab', () => {
  it('作为第五个 tab 与其余四个并列（页内 tab，不改路由）', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.findAll('.view-tab').map((node) => node.text())).toEqual([
      '渠道', '订阅', '定时任务', '发送记录', '变更历史',
    ])
  })

  it('打开就按时间倒序显示记录：时间 / 操作人 / 操作 / 对象 / 变更内容', async () => {
    const { wrapper } = await mountView()

    await openTab(wrapper, '变更历史')

    const rows = auditTableRows(wrapper)
    expect(rows).toHaveLength(3)
    expect(wrapper.text()).toContain('共 3 条')
    // 倒序：接口给的就是这个顺序，页面照单渲染
    expect(rows[0]).toContain('2026-10-05 21:30:12')
    expect(rows[0]).toContain('admin')
    expect(rows[0]).toContain('停用')
    expect(rows[0]).toContain('推送渠道')
    expect(rows[0]).toContain('门店群')
    // 变更内容算的是前后差异：只切了启停，就不显示地址那一项
    expect(rows[0]).toContain('启用：启用 → 停用')
    expect(rows[0]).not.toContain('Webhook 地址')
  })

  it('新建与删除整份快照都看得见（不说成「→（空）」）', async () => {
    const { wrapper } = await mountView()

    await openTab(wrapper, '变更历史')

    const rows = auditTableRows(wrapper)
    expect(rows[1]).toContain('新增')
    expect(rows[1]).toContain('推送订阅')
    expect(rows[1]).toContain('销售报表 → 门店群')
    expect(rows[1]).toContain('目标：门店群')
    expect(rows[2]).toContain('删除')
    expect(rows[2]).toContain('推送任务')
    expect(rows[2]).toContain('旧日报')
    expect(rows[2]).toContain('推送时间：21:30（已删除）')
  })

  it('不显示行内 id 这类内部字段（变更内容只给人看的东西）', async () => {
    const { wrapper } = await mountView()

    await openTab(wrapper, '变更历史')

    // 目标已经用名字显示（「目标：门店群」），再列 target_channel_id: 2 只是噪音；
    // `topic_id` 也已经进了对象名（「销售报表 → 门店群」）。
    const text = auditTableRows(wrapper).join('\n')
    expect(text).not.toContain('目标渠道')
    expect(text).not.toContain('目标群组')
    expect(text).not.toContain('内容类型：sales_report')
  })

  it('对象类型筛选项来自读接口 —— 夹具里的假类型也在下拉里', async () => {
    const { wrapper } = await mountView()

    await openTab(wrapper, '变更历史')

    const options = wrapper.findAll('.wp-audit-filters option').map((node) => node.text())
    expect(options).toContain('全部对象类型')
    expect(options).toContain('推送渠道')
    expect(options).toContain('夹具对象')
  })

  it('选对象类型点筛选：请求带上 object_type，并回到第一页', async () => {
    const { wrapper, fetchMock } = await mountView()

    await openTab(wrapper, '变更历史')
    await wrapper.find('.wp-audit-filters select').setValue('wecom_push_webhooks')
    const button = wrapper.findAll('.wp-audit-filters button').find((n) => n.text() === '筛选')
    await button.trigger('click')
    await flushPromises()

    const calls = fetchMock.calls.filter((call) => call.path.includes('/audit-log'))
    expect(calls.at(-1).path).toContain('object_type=wecom_push_webhooks')
    expect(calls.at(-1).path).toContain('page=1')
  })

  it('筛选之后没有记录时说「没有符合条件的记录」，而不是「暂无变更记录」', async () => {
    const { wrapper } = await mountView({ rows: [], total: 0, pages: 0 })

    await openTab(wrapper, '变更历史')

    expect(wrapper.find('.wp-audit-table').text()).toContain('暂无变更记录')
    await wrapper.find('.wp-audit-filters select').setValue('wecom_push_webhooks')
    const button = wrapper.findAll('.wp-audit-filters button').find((n) => n.text() === '筛选')
    await button.trigger('click')
    await flushPromises()

    expect(wrapper.find('.wp-audit-table').text()).toContain('没有符合条件的记录')
  })

  it('翻页请求目标页（记录长期保留，一页装不下）', async () => {
    const { wrapper, fetchMock } = await mountView({ total: 120, pages: 3 })

    await openTab(wrapper, '变更历史')
    const next = wrapper.findAll('.wp-audit-pager button').find((n) => n.text() === '下一页')
    expect(next.attributes('disabled')).toBeUndefined()
    await next.trigger('click')
    await flushPromises()

    const calls = fetchMock.calls.filter((call) => call.path.includes('/audit-log'))
    expect(calls.at(-1).path).toContain('page=2')
    expect(wrapper.find('.wp-audit-pager').text()).toContain('第 2 / 3 页')
  })

  it('说明保留口径：配置变更长期保留，不随发送记录的清理走', async () => {
    const { wrapper } = await mountView()

    await openTab(wrapper, '变更历史')

    const text = wrapper.find('.wp-audit-pager').text()
    expect(text).toContain('长期保留')
    expect(text).toContain('不随发送记录')
    // 发送记录那一页才说「超过保留天数的记录会被自动清理」——两边口径不同，别串了
    expect(text).not.toContain('自动清理')
  })

  it('页面加载时不请求变更历史，切到那个 tab 才拉', async () => {
    const { wrapper, fetchMock } = await mountView()

    expect(fetchMock.calls.filter((call) => call.path.includes('/audit-log'))).toHaveLength(0)

    await openTab(wrapper, '变更历史')

    expect(fetchMock.calls.filter((call) => call.path.includes('/audit-log'))).toHaveLength(1)
  })
})
