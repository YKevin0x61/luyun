// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import PushParamsForm from '../PushParamsForm.vue'

// 票面验收项：**注册表新增一类内容类型时前端零改动**。
//
// 所以这里的夹具是一个前端代码里从未出现过的内容类型（id、字段名、下拉选项都是新的），
// 唯一来源是后端 `/meta` 会给的那份 schema + uischema。断言的是渲染出来的控件与
// emit 出去的参数对象 —— 只要参数区是照 schema 渲染的，这个用例就成立；哪天有人把
// 「销售报表有哪几个字段」写进前端，它立刻红。

/** 一个假的内容类型：模拟后端 `/meta` 里 topics[0] 的形状。 */
const FAKE_TOPIC = {
  id: 'fake_ops_watermark',
  name: '夹具内容类型',
  triggers: ['scheduled'],
  default_schedule_time: '07:15',
  contains_employee_photos: false,
  params_schema: {
    type: 'object',
    additionalProperties: false,
    title: 'FakeOpsParams',
    properties: {
      schedule_time: {
        type: 'string',
        default: '07:15',
        pattern: '^([01]\\d|2[0-3]):[0-5]\\d$',
        title: '推送时间',
        description: '每天在这个时间推送（营业日口径）。',
      },
      level: {
        type: 'string',
        default: 'warn',
        title: '水位等级',
        oneOf: [
          { const: 'warn', title: '告警' },
          { const: 'recover', title: '恢复' },
        ],
      },
      note: { type: 'string', default: '', title: '备注' },
    },
  },
  uischema: {
    type: 'VerticalLayout',
    elements: [
      {
        type: 'Control',
        scope: '#/properties/schedule_time',
        label: '推送时间',
        options: { control: 'time' },
      },
      {
        type: 'Control',
        scope: '#/properties/level',
        label: '水位等级',
        options: { control: 'select' },
      },
      {
        type: 'Control',
        scope: '#/properties/note',
        label: '备注',
        options: { control: 'text' },
      },
    ],
  },
}

function mountForm(overrides = {}) {
  return mount(PushParamsForm, {
    props: {
      schema: FAKE_TOPIC.params_schema,
      uischema: FAKE_TOPIC.uischema,
      data: { schedule_time: '07:15', level: 'warn', note: '' },
      ...overrides,
    },
  })
}

describe('参数区由注册表的 schema + uischema 渲染', () => {
  it('前端从未见过的内容类型也能渲染出它的每个参数', () => {
    const wrapper = mountForm()

    expect(wrapper.text()).toContain('推送时间')
    expect(wrapper.text()).toContain('水位等级')
    expect(wrapper.text()).toContain('备注')
  })

  it('时间字段是 time 控件（后端说「时间用 time 控件」）', () => {
    const wrapper = mountForm()

    expect(wrapper.find('input[type="time"]').exists()).toBe(true)
  })

  it('下拉的选项来自 schema 的 oneOf，不是页面上写死的', () => {
    const wrapper = mountForm()
    const select = wrapper.find('select')

    expect(select.exists()).toBe(true)
    // vanilla 渲染器把标签放在 option 的 label 属性上（不是文本节点）
    const labels = wrapper.findAll('option')
      .map((option) => option.attributes('label'))
      .filter(Boolean)
    expect(labels).toEqual(['告警', '恢复'])
  })

  it('说明文字也一起渲染（description 属于注册表）', () => {
    const wrapper = mountForm()

    expect(wrapper.text()).toContain('每天在这个时间推送')
  })

  it('空 schema / 空 uischema 不炸（页面加载中途也能挂）', () => {
    const wrapper = mount(PushParamsForm, { props: {} })

    expect(wrapper.find('.wp-params-form').exists()).toBe(true)
  })
})

describe('参数变化回给调用方', () => {
  it('改一个字段就把整个参数对象 emit 出去（其余字段带着）', async () => {
    const wrapper = mountForm()

    await wrapper.find('input[type="time"]').setValue('08:30')

    const events = wrapper.emitted('update:data')
    expect(events, '改一个字段要回一个 update:data').toBeTruthy()
    expect(events.at(-1)[0]).toMatchObject({ schedule_time: '08:30', level: 'warn' })
  })

  it('选下拉也回一个完整的参数对象', async () => {
    const wrapper = mountForm()

    await wrapper.find('select').setValue('recover')

    const events = wrapper.emitted('update:data')
    expect(events.at(-1)[0]).toMatchObject({ level: 'recover', schedule_time: '07:15' })
  })

  it('父组件换一份参数（切内容类型 / 编辑任务）时表单跟着变', async () => {
    const wrapper = mountForm()

    await wrapper.setProps({ data: { schedule_time: '09:45', level: 'recover', note: 'x' } })

    // jsdom 的原生 time 控件会把值补成 HH:MM:SS（组件在 change 那一步会修剪回 HH:MM）
    expect(wrapper.find('input[type="time"]').element.value).toMatch(/^09:45/)
    expect(wrapper.find('select').element.value).toBe('recover')
  })
})
