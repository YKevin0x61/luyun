import { describe, expect, it } from 'vitest'

import {
  normalizeControlValues, splitEmptySelectOption, toJsonFormsUiSchema,
} from '../jsonFormsControls.js'

// 后端给的 uischema 用 `options.control` 声明控件类型（ADR 0097：「时间用 time 控件、
// 档口用下拉」属于后端知识）。vanilla 渲染器不认这个名字，这里翻成它认的形状
// （`options.format`）。断言的是**翻译结果**，不是某个内容类型的字段。

const UISCHEMA = {
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
      label: '等级',
      options: { control: 'select' },
    },
    {
      type: 'Control',
      scope: '#/properties/note',
      label: '备注',
      options: { control: 'text' },
    },
  ],
}

describe('控件类型到 JSON Forms 的映射', () => {
  it('time 控件翻成 options.format=time（渲染器认这个）', () => {
    const elements = toJsonFormsUiSchema(UISCHEMA).elements

    expect(elements[0].options).toEqual({ format: 'time' })
    expect(elements[0].label).toBe('推送时间')
    expect(elements[0].scope).toBe('#/properties/schedule_time')
  })

  it('select / text 只去掉后端自己的 control 键，不塞别的选项', () => {
    const elements = toJsonFormsUiSchema(UISCHEMA).elements

    expect(elements[1].options).toEqual({})
    expect(elements[2].options).toEqual({})
  })

  it('没有 options 的元素与没有 uischema 的情况都原样返回', () => {
    const bare = { type: 'VerticalLayout', elements: [{ type: 'Control', scope: '#/properties/a' }] }

    expect(toJsonFormsUiSchema(bare).elements[0]).toEqual({
      type: 'Control', scope: '#/properties/a',
    })
    expect(toJsonFormsUiSchema(null)).toBeNull()
    expect(toJsonFormsUiSchema({ type: 'VerticalLayout' })).toEqual({ type: 'VerticalLayout' })
  })

  it('不改动传进来的那份 uischema（后端给的注册表数据不能被就地改掉）', () => {
    const source = JSON.parse(JSON.stringify(UISCHEMA))

    toJsonFormsUiSchema(UISCHEMA)

    expect(UISCHEMA).toEqual(source)
  })
})

// 参数从 JSON Forms 回到调用方再传回去，中间经过这里。**它必须保持引用相等**：
// 每次都返回新对象的话，父组件写回参数 → prop 变化 → JSON Forms 再 dispatch 一轮
// → 又回调 change → 再复制，Vue 最终报 `Maximum recursive updates exceeded`
// （页面表现是选中内容类型后参数区把整页卡死）。
describe('参数修剪与对象引用', () => {
  const TIME_ONLY = {
    type: 'VerticalLayout',
    elements: [
      {
        type: 'Control',
        scope: '#/properties/schedule_time',
        label: '推送时间',
        options: { control: 'time' },
      },
    ],
  }

  it('time 控件补出来的秒（HH:MM:SS）修剪回后端要的 HH:MM', () => {
    expect(normalizeControlValues({ schedule_time: '21:30:00' }, TIME_ONLY))
      .toEqual({ schedule_time: '21:30' })
  })

  it('一个字段都不用修剪时返回同一个对象，不是复制一份', () => {
    const data = { schedule_time: '21:30', date_range_mode: 'today' }

    expect(normalizeControlValues(data, TIME_ONLY)).toBe(data)
  })

  it('只修剪后端声明为 time 的字段，别的字段一个字不动', () => {
    const data = { schedule_time: '21:30:00', date_range_mode: '09:00:00' }

    const result = normalizeControlValues(data, TIME_ONLY)

    expect(result.schedule_time).toBe('21:30')
    // 没声明成 time 的字段即便长成 HH:MM:SS 也不碰（那是它的业务值）
    expect(result.date_range_mode).toBe('09:00:00')
    expect(result).not.toBe(data)
  })

  it('空参数不炸', () => {
    expect(normalizeControlValues(null, TIME_ONLY)).toEqual({})
    expect(normalizeControlValues(undefined, null)).toEqual({})
  })
})

// D3：vanilla 渲染器给每个 select 前面硬编码了一个没有文案的 `<option value="">`，
// 而档口的「全部（排除楼面）」const 就是空串 —— 同一个下拉里两个 `value=""`，浏览器
// 选中的是前面那个空文案的，于是默认显示空白、挑「全部（排除楼面）」也还是空白。
// 这里把 schema 里那个空值选项拎出来交给渲染器（用它的 title 渲染第一个选项），
// 空串仍然表示「全部」—— 只是看得见了。
describe('下拉的空值选项', () => {
  it('schema 里空值选项被单独拎出来，带上它的 title', () => {
    const result = splitEmptySelectOption([
      { label: '全部（排除楼面）', value: '' },
      { label: '传菜', value: 'chuanqi' },
    ])

    expect(result).toEqual({
      hasEmptyOption: true,
      emptyLabel: '全部（排除楼面）',
      options: [{ label: '传菜', value: 'chuanqi' }],
    })
  })

  it('没有空值选项时原样返回（「清空」语义留给渲染器）', () => {
    const list = [{ label: '当天', value: 'today' }, { label: '昨天', value: 'yesterday' }]

    expect(splitEmptySelectOption(list)).toEqual({
      hasEmptyOption: false, emptyLabel: '', options: list,
    })
  })

  it('空数组 / 拿不到选项都不炸', () => {
    for (const input of [[], undefined, null, {}]) {
      expect(splitEmptySelectOption(input)).toEqual({
        hasEmptyOption: false, emptyLabel: '', options: [],
      })
    }
  })

  it('值是 0 / false 的选项不算空值（只认空串）', () => {
    const result = splitEmptySelectOption([
      { label: '关', value: 0 }, { label: '开', value: 1 },
    ])

    expect(result.hasEmptyOption).toBe(false)
    expect(result.options).toHaveLength(2)
  })
})
