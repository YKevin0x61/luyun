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

/** 一条推送任务的完整形状（读接口给的就是这个）。 */
function job(id, name, scheduleTime, overrides = {}) {
  return {
    id,
    name,
    topic_id: 'sales_report',
    topic_name: '销售报表',
    params: { schedule_time: scheduleTime, date_range_mode: 'today', station: '' },
    schedule_time: scheduleTime,
    enabled: true,
    last_sent_date: '2026-10-06',
    notes: '',
    target_count: 2,
    ...overrides,
  }
}

const JOBS = [job(3, '每日销售报表', '21:30')]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function probeFetch({ topics = [], jobs = JOBS } = {}) {
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
    if (path.includes('/api/wecom-push/jobs')) {
      if ((options.method || 'GET') === 'POST') {
        // 保存成功：后端回一条带 id 的任务（页面据此高亮"刚改的这一张"）
        return jsonResponse({ success: true, job: { ...jobs[0], id: 3 } })
      }
      return jsonResponse({ success: true, jobs })
    }
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

/**
 * U1（UI 走查 high）：任务卡原来按 `schedule_time` 升序排，改完时间一保存这张卡就换位；
 * 按位置连点「编辑」会把时间写进另一条任务（走查里真的发生过）。现在后端按 id 稳定排、
 * 页面按接口给的顺序渲染，保存后把刚改的那张卡短时高亮并滚进视野。
 */
describe('任务卡的排序与保存反馈（U1）', () => {
  /** 页面上任务卡的名字，按渲染顺序。 */
  function cardNames(wrapper) {
    return wrapper.findAll('.wp-job-card strong').map((node) => node.text())
  }

  it('卡片顺序 = 接口给的顺序：时间只显示，不参与排序', async () => {
    // 接口给的是 id 序（22:10 排在 21:30 前面）。页面若自己按时间重排，这里就会翻过来 ——
    // 那正是"改完时间卡片跳位"的来源。
    const { wrapper } = await mountView({
      topics: [SALES_TOPIC],
      jobs: [job(5, '日终对账差异', '22:10'), job(3, '每日销售报表', '21:30')],
    })
    await openTab(wrapper, '定时任务')

    expect(cardNames(wrapper)).toEqual(['日终对账差异', '每日销售报表'])
    // 时间照旧显示在卡上
    expect(wrapper.text()).toContain('22:10')
    expect(wrapper.text()).toContain('21:30')
  })

  it('保存成功后把那张卡标成 is-saved 并滚进视野', async () => {
    const scrollSpy = vi.fn()
    // jsdom 没有 scrollIntoView；存根一个，用来确认真的调了
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = scrollSpy
    try {
      const { wrapper } = await mountView({
        topics: [SALES_TOPIC],
        jobs: [job(5, '日终对账差异', '22:10'), job(3, '每日销售报表', '21:30')],
      })
      await openTab(wrapper, '定时任务')

      // 编辑第二条（按位置点，正是走查里踩雷的那一步）
      const cards = wrapper.findAll('.wp-job-card')
      await cards[1].findAll('button').find((node) => node.text() === '编辑').trigger('click')
      await flushPromises()

      await wrapper.findAll('button').find((node) => node.text() === '保存任务').trigger('click')
      await flushPromises()

      const saved = wrapper.findAll('.wp-job-card').find(
        (node) => node.attributes('data-job-id') === '3',
      )
      expect(saved, '找不到被保存的那张卡').toBeTruthy()
      expect(saved.classes()).toContain('is-saved')
      expect(scrollSpy).toHaveBeenCalled()
      // 顺序没变：卡片没有因为时间被改而换位
      expect(cardNames(wrapper)).toEqual(['日终对账差异', '每日销售报表'])
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })
})

describe('「立即发送」的禁用态（U8）', () => {
  it('预览为空时禁用，title 说的是"点刷新预览"，与卡片右上角的当前任务一致', async () => {
    const { wrapper } = await mountView({ topics: [SALES_TOPIC] })
    await openTab(wrapper, '定时任务')

    const send = wrapper.findAll('button').find((node) => node.text() === '立即发送')
    expect(send.attributes('disabled')).toBeDefined()
    expect(send.attributes('title')).toBe('点「刷新预览」后可发送')
    expect(send.classes()).toContain('wp-send-now')
    // 页面已经显示"当前任务：…"，禁用理由不能再让人去"先选择任务"
    expect(wrapper.text()).toContain('当前任务：每日销售报表')
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
