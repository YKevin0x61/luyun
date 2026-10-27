// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../../api/client', () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

const { api } = await import('../../../api/client')
const { default: PrepItemsView } = await import('../PrepItemsView.vue')

const ITEMS = [
  {
    id: 41, item_name: '虾饺', unit: '个', position: '案板', station: 'shulong',
    kind: 'made', category: '半成品', active: true, notes: '', rule_count: 6,
  },
  {
    id: 50, item_name: '香茜饺', unit: '份', position: '案板', station: 'shulong',
    kind: 'made', category: '半成品', active: true, notes: '', rule_count: 0,
  },
  {
    id: 58, item_name: '黄网皮', unit: '包', position: '肠粉', station: 'changfen',
    kind: 'bought', category: '原料', active: false, notes: '', rule_count: 3,
  },
]

beforeEach(() => {
  api.get.mockReset()
  api.patch.mockReset()
  api.delete.mockReset()
  api.get.mockResolvedValue({ success: true, items: ITEMS.map((row) => ({ ...row })) })
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function mountView() {
  const wrapper = mount(PrepItemsView)
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('备货品清单', () => {
  it('列出全部备货品（含停用），带单位、岗位、归属与规则数', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('备货品清单')
    const rows = wrapper.findAll('.pi-row')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('虾饺')
    expect(rows[0].text()).toContain('案板')
    expect(rows[0].text()).toContain('半成品')
    expect(rows[0].text()).toContain('6 条规则')
    // 停用的行照常列出（管理面要能重新启用）
    expect(rows[2].text()).toContain('已停用')
    expect(rows[1].text()).toContain('没有规则')
  })

  it('按名字搜索与按归属筛选', async () => {
    const wrapper = await mountView()

    await wrapper.get('.pi-search').setValue('网皮')
    expect(wrapper.findAll('.pi-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('黄网皮')

    await wrapper.get('.pi-search').setValue('')
    const selects = wrapper.findAll('.pi-filter')
    await selects[1].setValue('made')
    expect(wrapper.findAll('.pi-row')).toHaveLength(2)
  })
})

describe('编辑', () => {
  it('点编辑打开表单，保存时提交名称/单位/岗位/归属/启用/备注', async () => {
    api.patch.mockResolvedValue({
      success: true,
      item: { ...ITEMS[1], item_name: '香茜饺（改名）', kind: 'bought', category: '原料' },
    })
    const wrapper = await mountView()

    await wrapper.findAll('.pi-edit')[1].trigger('click')
    expect(wrapper.find('.pi-sheet').exists()).toBe(true)

    const inputs = wrapper.findAll('.pi-input')
    await inputs[0].setValue('香茜饺（改名）')
    const selects = wrapper.findAll('.pi-sheet select')
    await selects[1].setValue('bought')
    await wrapper.get('.pi-sheet form, .pi-sheet').trigger('submit')

    expect(api.patch).toHaveBeenCalledTimes(1)
    const [url, payload] = api.patch.mock.calls[0]
    expect(url).toBe('/api/semi-rules/prep-items/50')
    expect(payload).toMatchObject({ item_name: '香茜饺（改名）', kind: 'bought' })
    // 保存成功后行内跟着变，不用整页刷新
    expect(wrapper.text()).toContain('香茜饺（改名）')
  })

  it('重名被后端拒绝时把错误显示在表单里，不关面板', async () => {
    api.patch.mockRejectedValue(new Error('清单里已经有「虾饺」了'))
    const wrapper = await mountView()

    await wrapper.findAll('.pi-edit')[1].trigger('click')
    await wrapper.findAll('.pi-input')[0].setValue('虾饺')
    await wrapper.get('.pi-sheet form, .pi-sheet').trigger('submit')

    expect(wrapper.find('.pi-sheet').exists()).toBe(true)
    expect(wrapper.text()).toContain('清单里已经有「虾饺」了')
  })
})

describe('删除', () => {
  it('确认后删除：调 DELETE、行从列表消失、面板关掉', async () => {
    api.delete.mockResolvedValue({ success: true, id: 50, rules_detached: 0 })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountView()

    await wrapper.findAll('.pi-edit')[1].trigger('click')
    await wrapper.get('.pi-delete').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(api.delete).toHaveBeenCalledWith('/api/semi-rules/prep-items/50')
    expect(wrapper.find('.pi-sheet').exists()).toBe(false)
    expect(wrapper.findAll('.pi-row')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('香茜饺')
  })

  it('确认框取消时不发请求', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = await mountView()

    await wrapper.findAll('.pi-edit')[1].trigger('click')
    await wrapper.get('.pi-delete').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(api.delete).not.toHaveBeenCalled()
    expect(wrapper.find('.pi-sheet').exists()).toBe(true)
  })
})
