// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 08 页面那一半的接线：定时任务 tab 的内容类型下拉与参数区都来自 `/meta` 的注册表
// （内容类型下拉只列支持定时触发的），任务卡片显示的是订阅目标数而不是单个群名。
//
// 关键的一条是「夹具里的假内容类型」：它在页面代码里从未出现过，只要它出现在 `/meta`
// 里，下拉里就有它、选中之后参数区就渲染出它自己的字段。哪天有人把某一类内容的字段名
// 写进前端，这条就会红。

const API_VERSION = 'v2'

const SALES_TOPIC = {
  id: 'sales_report',
  name: '销售报表',
  triggers: ['scheduled'],
  contains_employee_photos: false,
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
  uischema: {
    type: 'VerticalLayout',
    elements: [
      { type: 'Control', scope: '#/properties/schedule_time', label: '推送时间', options: { control: 'time' } },
      { type: 'Control', scope: '#/properties/date_range_mode', label: '日期口径', options: { control: 'select' } },
      { type: 'Control', scope: '#/properties/station', label: '档口', options: { control: 'select' } },
    ],
  },
}

/** 事件类内容类型：没有定时侧，不能建成任务，因此不该出现在下拉里。 */
const PHOTO_TOPIC = {
  id: 'hygiene_photo',
  name: '验收照片',
  triggers: ['event'],
  contains_employee_photos: true,
  default_schedule_time: null,
  params_schema: { type: 'object', properties: {} },
  uischema: { type: 'VerticalLayout', elements: [] },
}

/** 前端从未见过的内容类型：参数名与标签都是新的（只有注册表知道它们）。 */
const FAKE_TOPIC = {
  id: 'fake_ops_watermark',
  name: '夹具水位提醒',
  triggers: ['scheduled'],
  contains_employee_photos: false,
  default_schedule_time: '07:15',
  params_schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      schedule_time: { type: 'string', default: '07:15', title: '推送时间' },
      watermark_level: {
        type: 'string',
        default: 'warn',
        title: '水位等级',
        oneOf: [{ const: 'warn', title: '告警' }, { const: 'recover', title: '恢复' }],
      },
      note_text: { type: 'string', default: '', title: '附加说明' },
    },
  },
  uischema: {
    type: 'VerticalLayout',
    elements: [
      { type: 'Control', scope: '#/properties/schedule_time', label: '推送时间', options: { control: 'time' } },
      { type: 'Control', scope: '#/properties/watermark_level', label: '水位等级', options: { control: 'select' } },
      { type: 'Control', scope: '#/properties/note_text', label: '附加说明', options: { control: 'text' } },
    ],
  },
}

const JOBS = [
  {
    id: 3,
    name: '每日销售报表',
    topic_id: 'sales_report',
    topic_name: '销售报表',
    params: { schedule_time: '21:30', date_range_mode: 'today', station: '' },
    schedule_time: '21:30',
    enabled: true,
    last_sent_date: '2026-10-06',
    notes: '',
    target_count: 2,
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

function probeFetch({ topics = [] } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET', body: options.body })
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({ success: true, api_version: API_VERSION, topics, job_templates: [] })
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
    if (path.includes('/api/wecom-push/jobs')) return jsonResponse({ success: true, jobs: JOBS })
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

async function mountView(options = {}) {
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

/** 页面上所有下拉的选项文字（去重前）。 */
function optionTexts(wrapper) {
  return wrapper.findAll('option').map((option) => option.text())
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('定时任务 tab 的表单', () => {
  it('内容类型下拉来自 /meta，只列支持定时触发的', async () => {
    const { wrapper } = await mountView({ topics: [SALES_TOPIC, PHOTO_TOPIC] })
    await openTab(wrapper, '定时任务')

    const options = optionTexts(wrapper)
    expect(options).toContain('销售报表')
    expect(options).not.toContain('验收照片')
    // 收件人不再由任务指定
    expect(wrapper.text()).not.toContain('目标渠道')
    expect(wrapper.text()).not.toContain('目标 Webhook')
  })

  it('选中一个内容类型后，参数区按它的 schema + uischema 渲染', async () => {
    const { wrapper } = await mountView({ topics: [SALES_TOPIC, PHOTO_TOPIC] })
    await openTab(wrapper, '定时任务')

    const select = wrapper.find('select')
    await select.setValue('sales_report')
    await flushPromises()

    expect(wrapper.find('.wp-params-form').exists()).toBe(true)
    expect(wrapper.find('input[type="time"]').exists()).toBe(true)
    const text = wrapper.find('.wp-params-form').text()
    expect(text).toContain('推送时间')
    expect(text).toContain('日期口径')
    expect(text).toContain('档口')
  })

  it('夹具里的假内容类型：前端零改动就能渲染它的参数区', async () => {
    const { wrapper } = await mountView({ topics: [SALES_TOPIC, FAKE_TOPIC] })
    await openTab(wrapper, '定时任务')

    const options = optionTexts(wrapper)
    expect(options).toContain('夹具水位提醒')

    await wrapper.find('select').setValue('fake_ops_watermark')
    await flushPromises()

    const text = wrapper.find('.wp-params-form').text()
    expect(text).toContain('水位等级')
    expect(text).toContain('附加说明')
    // 下拉的选项也是它的 schema 给的（页面上没有写死过「告警 / 恢复」）
    const labels = wrapper.findAll('.wp-params-form option')
      .map((option) => option.attributes('label'))
      .filter(Boolean)
    expect(labels).toContain('告警')
    expect(labels).toContain('恢复')
    // 而且换类型之后不再是上一个类型的字段
    expect(text).not.toContain('档口')
  })

  it('保存任务时发出去的请求体是新形状（内容类型 + 参数 + 时间，没有收件人）', async () => {
    const { wrapper, fetchMock } = await mountView({ topics: [SALES_TOPIC, PHOTO_TOPIC] })
    await openTab(wrapper, '定时任务')

    await wrapper.find('select').setValue('sales_report')
    await flushPromises()
    const nameInput = wrapper.find('input.input[maxlength="60"]')
    await nameInput.setValue('每日销售报表')
    const submit = wrapper.findAll('button').find((node) => node.text() === '保存任务')
    await submit.trigger('click')
    await flushPromises()

    const posts = fetchMock.calls.filter((call) => call.method === 'POST' && call.path.includes('/jobs'))
    expect(posts).toHaveLength(1)
    const payload = JSON.parse(posts[0].body)
    expect(payload).toMatchObject({
      name: '每日销售报表',
      topic_id: 'sales_report',
      schedule_time: '21:30',
      enabled: true,
    })
    expect(payload.webhook_id).toBeUndefined()
    expect(payload.push_type).toBeUndefined()
    expect(payload.params.schedule_time).toBe('21:30')
  })
})

describe('任务卡片', () => {
  it('显示内容类型与订阅目标数，而不是单个群名', async () => {
    const { wrapper } = await mountView({ topics: [SALES_TOPIC] })
    await openTab(wrapper, '定时任务')

    const text = wrapper.text()
    expect(text).toContain('每日销售报表')
    expect(text).toContain('销售报表')
    expect(text).toContain('订阅目标')
    expect(text).toContain('2 个群')
    expect(text).toContain('2026-10-06')
  })
})

describe('参数表单的样式映射', () => {
  it('vanilla 渲染器的结构映射到了页面既有的输入控件样式（ADR 0097）', () => {
    const cssPath = join(
      dirname(fileURLToPath(import.meta.url)), '..', '..', 'styles', 'jsonforms.css',
    )
    const css = readFileSync(cssPath, 'utf8')
    const mainSource = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'main.js'), 'utf8',
    )

    // 容器类名与组件绑定的一致，且映射到既有的 .input / .select 上
    expect(css).toContain('.wp-params-form')
    expect(css).toContain('.control')
    expect(css).toContain('.input')
    expect(css).toContain('.select')
    // 这份样式得真的被页面加载（忘了 import 就是一个没有样式的参数区）
    expect(mainSource).toContain('jsonforms.css')
  })
})
