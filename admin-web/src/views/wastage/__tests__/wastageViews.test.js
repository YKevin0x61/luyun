// @vitest-environment jsdom
/**
 * 损耗两页的页面级测试（从 worktool2 那个小程序搬过来的那一块）。
 *
 * 压三件服务端测试看不到的事：
 *   ① 登记页提交的 body 形状（字段名 / 图片 id 列表 / 数量转数字）；
 *   ② 分析页画的是**服务端给的数字**（总笔数、原因分布、物料排行、明细条数）；
 *   ③ 「明细」的三把筛子与范围快捷项真的换了请求参数。
 *
 * 服务端自己的口径（校验、归属、统计算法）在 `tests/test_wastage.py` 里压，这里不重复。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
const del = vi.fn()

vi.mock('../../../api/client.js', () => ({
  api: {
    get: (...args) => get(...args),
    post: (...args) => post(...args),
    delete: (...args) => del(...args),
    upload: vi.fn(),
  },
}))

import WastageAnalysisView from '../WastageAnalysisView.vue'
import WastageRegisterView from '../WastageRegisterView.vue'

const META = {
  today: '2026-10-17',
  meta: {
    reasons: [
      { value: 'spoiled', label: '变质腐败' },
      { value: 'expired', label: '过期丢弃' },
      { value: 'damaged', label: '破损/污染' },
      { value: 'production_fail', label: '制作失败' },
      { value: 'excess', label: '备货过量' },
      { value: 'other', label: '其他' },
    ],
    units: ['份', 'kg', 'g', '个', '盒', '袋', '瓶', '包', '斤'],
    max_images: 3,
    max_item_name: 50,
    max_unit: 10,
    max_remark: 200,
    max_quantity: 99999,
    max_templates: 20,
    max_image_bytes: 20971520,
  },
}

const STATS = {
  start_date: '2026-10-01',
  end_date: '2026-10-31',
  total_count: 7,
  item_type_count: 3,
  by_reason: [
    { reason: 'spoiled', reason_text: '变质腐败', count: 4, percentage: 57 },
    { reason: 'expired', reason_text: '过期丢弃', count: 3, percentage: 43 },
  ],
  top_items: [
    { item_name: '五花肉', unit: 'kg', count: 2, total_quantity: 2.5 },
    { item_name: '豆腐', unit: '盒', count: 1, total_quantity: 3 },
  ],
  daily_stats: [{ date: '2026-10-05', count: 2 }],
}

const RECORDS = {
  total: 2,
  records: [
    {
      id: 2,
      item_name: '五花肉',
      quantity: 1.5,
      unit: 'kg',
      reason: 'spoiled',
      reason_text: '变质腐败',
      remark: '没放住',
      wastage_date: '2026-10-17',
      operator_name: '张伟',
      images: [],
    },
    {
      id: 1,
      item_name: '豆腐',
      quantity: 2,
      unit: '盒',
      reason: 'damaged',
      reason_text: '破损/污染',
      remark: '',
      wastage_date: '2026-10-16',
      operator_name: '管理端',
      images: [],
    },
  ],
}

function route() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/workbench/me/loss', component: { template: '<div />' } },
      { path: '/workbench/kitchen/loss', component: { template: '<div />' } },
    ],
  })
  return router
}

async function mountView(component, path) {
  const router = route()
  await router.push(path)
  await router.isReady()
  return mount(component, {
    global: {
      plugins: [router],
      stubs: { LuyunDatePicker: { template: '<input />' } },
    },
  })
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  del.mockReset()
})

describe('损耗登记页（员工端）', () => {
  beforeEach(() => {
    get.mockImplementation((url) => {
      if (url === '/api/wastage/meta') return Promise.resolve(META)
      if (url === '/api/wastage/templates') {
        return Promise.resolve({
          templates: [
            { id: 1, item_name: '五花肉', unit: 'kg', reason: 'spoiled', reason_text: '变质腐败', is_custom: true },
          ],
        })
      }
      if (url === '/api/wastage/suggestions') return Promise.resolve({ suggestions: ['五花肉', '豆腐'] })
      if (url === '/api/wastage/records') return Promise.resolve(RECORDS)
      return Promise.resolve({})
    })
    post.mockResolvedValue({
      record: { item_name: '五花肉', quantity: 1.5, unit: 'kg', reason_text: '变质腐败' },
    })
  })

  it('读四样东西：清单与上限、模板、名字补全、我最近的登记', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/me/loss')
    await flushPromises()

    const calls = get.mock.calls.map(([url]) => url)
    expect(calls).toEqual([
      '/api/wastage/meta',
      '/api/wastage/templates',
      '/api/wastage/suggestions',
      '/api/wastage/records',
    ])
    // 模板 chips 与「我最近的登记」都摆出来了。
    expect(wrapper.text()).toContain('五花肉')
    expect(wrapper.text()).toContain('我最近的登记')
    expect(wrapper.findAll('.loss-item')).toHaveLength(2)
    // 六个原因与单位都来自服务端的清单，不是页面里写死的。
    expect(wrapper.findAll('.loss-reason')).toHaveLength(6)
  })

  it('提交的 body 是服务端要的那几个字段（数量转数字、图片带 capture_id）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/me/loss')
    await flushPromises()

    await wrapper.get('input[type="text"]').setValue('五花肉')
    await wrapper.get('input[type="number"]').setValue('1.5')
    await wrapper.get('select').setValue('kg')
    await wrapper.findAll('.loss-reason')[0].trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledTimes(1)
    const [url, body] = post.mock.calls[0]
    expect(url).toBe('/api/wastage/records')
    expect(body.item_name).toBe('五花肉')
    expect(body.quantity).toBe(1.5)
    expect(body.unit).toBe('kg')
    expect(body.reason).toBe('spoiled')
    expect(body.image_ids).toEqual([])
    // 登记的这一天默认是服务端给的今天。
    expect(body.wastage_date).toBe('2026-10-17')
  })

  it('选了「其他」时把备注标成必填（判据仍在服务端）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/me/loss')
    await flushPromises()

    const reasons = wrapper.findAll('.loss-reason')
    expect(wrapper.text()).not.toContain('选了「其他」要说明')
    await reasons[5].trigger('click')
    expect(wrapper.text()).toContain('选了「其他」要说明')
  })

  it('现场照片排在登记页最前，且只走系统相机（没有相册这条路）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/me/loss')
    await flushPromises()

    // 第一块就是照片（2026-10-17 用户裁定：先拍现场、再填物料）。
    expect(wrapper.findAll('.loss-block')[0].text()).toContain('现场照片')

    // `capture="environment"` 让手机浏览器直接开系统相机；没有 `multiple` —— 相机一张一交。
    // 这两条一起才是「不用相册」：去掉 capture 或加回 multiple，手机就会弹「拍照 / 相册」。
    const file = wrapper.get('input[type="file"]')
    expect(file.attributes('capture')).toBe('environment')
    expect(file.attributes('multiple')).toBeUndefined()
    expect(file.attributes('accept')).toBe('image/*')
  })
})

describe('损耗分析页（管理端）', () => {
  beforeEach(() => {
    get.mockImplementation((url) => {
      if (url === '/api/wastage/meta') return Promise.resolve(META)
      if (url === '/api/wastage/statistics') return Promise.resolve(STATS)
      if (url === '/api/wastage/records') return Promise.resolve(RECORDS)
      return Promise.resolve({})
    })
  })

  it('画的是服务端给的数字：总笔数 / 物料种类 / 原因分布 / 物料排行 / 明细', async () => {
    const wrapper = await mountView(WastageAnalysisView, '/workbench/kitchen/loss')
    await flushPromises()

    expect(get.mock.calls[0][0]).toBe('/api/wastage/meta')
    const statsCall = get.mock.calls.find(([url]) => url === '/api/wastage/statistics')
    expect(statsCall[1]).toEqual({ month: '2026-10' })

    const text = wrapper.text()
    expect(text).toContain('7')
    expect(text).toContain('3')
    expect(text).toContain('变质腐败')
    expect(text).toContain('五花肉')
    expect(wrapper.findAll('.loss-item')).toHaveLength(2)

    // 原因分布条是 SVG 的属性（不是内联样式）：宽度直接是服务端给的百分比。
    const fills = wrapper.findAll('.loss-bar-fill')
    expect(fills.map((node) => node.attributes('width'))).toEqual(['57', '43'])
  })

  it('明细的范围快捷项换的是请求参数（这个月 / 近30天 / 全部）', async () => {
    const wrapper = await mountView(WastageAnalysisView, '/workbench/kitchen/loss')
    await flushPromises()

    const detailCalls = () => get.mock.calls.filter(([url]) => url === '/api/wastage/records')
    expect(detailCalls().at(-1)[1].start_date).toBe('2026-10-01')
    expect(detailCalls().at(-1)[1].end_date).toBe('2026-10-31')

    await wrapper.findAll('.loss-mode')[1].trigger('click')
    await flushPromises()
    expect(detailCalls().at(-1)[1].start_date).toBe('2026-09-18')
    expect(detailCalls().at(-1)[1].end_date).toBe('2026-10-17')

    await wrapper.findAll('.loss-mode')[3].trigger('click')
    await flushPromises()
    // 「全部」不带日期：服务端那一侧不加范围条件。
    expect(detailCalls().at(-1)[1].start_date).toBe('')
    expect(detailCalls().at(-1)[1].end_date).toBe('')
  })

  it('页头那颗「去登记一笔」指着员工端那一页', async () => {
    const wrapper = await mountView(WastageAnalysisView, '/workbench/kitchen/loss')
    await flushPromises()
    expect(wrapper.get('.loss-head a').attributes('href')).toBe('/workbench/me/loss')
  })
})
