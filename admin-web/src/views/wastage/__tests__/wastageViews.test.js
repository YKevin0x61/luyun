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
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
const del = vi.fn()
const upload = vi.fn()

vi.mock('../../../api/client.js', () => ({
  api: {
    get: (...args) => get(...args),
    post: (...args) => post(...args),
    delete: (...args) => del(...args),
    upload: (...args) => upload(...args),
  },
}))

import WastageAnalysisView from '../WastageAnalysisView.vue'
import WastageRegisterView from '../WastageRegisterView.vue'
import { useWorkbenchIdentityStore } from '../../../stores/workbenchIdentity'

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
      { path: '/workbench/staff/loss', component: { template: '<div />' } },
      { path: '/workbench/kitchen/loss', component: { template: '<div />' } },
    ],
  })
  return router
}

async function mountView(component, path, identity = 'staff') {
  const router = route()
  await router.push(path)
  await router.isReady()
  // 工作台身份由外壳的探针定（真机上是 `stores/workbenchIdentity`）。这一页按它给请求
  // 声明 `?identity=staff|admin`（双 cookie 的共用电脑上必须显式声明），测试里直接预置
  // 「已探过」的那一档，不去打真实的会话探针接口。
  const pinia = createPinia()
  const identityStore = useWorkbenchIdentityStore(pinia)
  if (identity) {
    identityStore.identity = identity
    identityStore.available = [identity]
    identityStore.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  }
  return mount(component, {
    global: {
      plugins: [router, pinia],
      stubs: {
        LuyunDatePicker: { template: '<input />' },
        // 页内相机挂的是真组件（`getUserMedia` 在 jsdom 里没有）：这里替身只负责
        // 「在不在、交不交得回一张图」。
        HygieneLiveCamera: { name: 'HygieneLiveCamera', template: '<div class="stub-camera" />' },
      },
    },
  })
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  del.mockReset()
  upload.mockReset()
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
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    const calls = get.mock.calls.map(([url]) => url)
    expect(calls).toEqual([
      '/api/wastage/meta',
      '/api/wastage/templates',
      '/api/wastage/suggestions',
      '/api/wastage/records',
    ])
    // 员工档的页面每个请求都声明 `?identity=staff`：共用电脑上两套 cookie 都在时，
    // 后端默认「管理端优先」会让员工页的归属与可见范围都是错的（Slice A 修复）。
    for (const [, params] of get.mock.calls) {
      expect(params).toMatchObject({ identity: 'staff' })
    }
    // 模板 chips 与「我最近的登记」都摆出来了。
    expect(wrapper.text()).toContain('五花肉')
    expect(wrapper.text()).toContain('我最近的登记')
    expect(wrapper.findAll('.loss-item')).toHaveLength(2)
    // 六个原因与单位都来自服务端的清单，不是页面里写死的。
    expect(wrapper.findAll('.loss-reason')).toHaveLength(6)
  })

  it('提交的 body 是服务端要的那几个字段（数量转数字、图片带 capture_id）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await wrapper.get('input[type="text"]').setValue('五花肉')
    await wrapper.get('input[type="number"]').setValue('1.5')
    await wrapper.get('select').setValue('kg')
    await wrapper.findAll('.loss-reason')[0].trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledTimes(1)
    const [url, body, params] = post.mock.calls[0]
    expect(url).toBe('/api/wastage/records')
    // 写也要声明：归属（谁记的）由这一支决定。
    expect(params).toEqual({ identity: 'staff' })
    expect(body.item_name).toBe('五花肉')
    expect(body.quantity).toBe(1.5)
    expect(body.unit).toBe('kg')
    expect(body.reason).toBe('spoiled')
    expect(body.image_ids).toEqual([])
    // 登记的这一天默认是服务端给的今天。
    expect(body.wastage_date).toBe('2026-10-17')
  })

  it('选了「其他」时把备注标成必填（判据仍在服务端）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    const reasons = wrapper.findAll('.loss-reason')
    expect(wrapper.text()).not.toContain('选了「其他」要说明')
    await reasons[5].trigger('click')
    expect(wrapper.text()).toContain('选了「其他」要说明')
  })

  it('现场照片排在登记页最前，点「拍一张」就地开相机（不跳系统相机）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 第一块就是照片（2026-10-17 用户裁定：先拍现场、再填物料）。
    expect(wrapper.findAll('.loss-block')[0].text()).toContain('现场照片')

    // 没有 file input：这一版不走「系统相机 / 相册」那条路 —— 那条路在 iPhone 上拿到的是
    // HEIC，后端认不出格式（用户真机上撞过）。（相机组件内部那个降级入口不算这一页的）
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    // 相机默认收着，点一下才展开 —— 页内取景，不跳出去。
    expect(wrapper.find('.stub-camera').exists()).toBe(false)
    await wrapper.get('.loss-shot-add').trigger('click')
    expect(wrapper.find('.stub-camera').exists()).toBe(true)

    // 相机交回一张 JPEG → 立刻上传，缩略图落进首位那一块。
    upload.mockResolvedValue({
      image: { capture_id: 'abc', url: '/api/wastage/images/abc' },
    })
    wrapper
      .findComponent({ name: 'HygieneLiveCamera' })
      .vm.$emit('captured', new Blob(['jpeg'], { type: 'image/jpeg' }))
    await flushPromises()

    expect(upload).toHaveBeenCalledTimes(1)
    // 上传也带声明：图归属决定提交时能不能绑进这一笔。
    expect(upload.mock.calls[0][0]).toBe('/api/wastage/images?identity=staff')
    expect(wrapper.findAll('.loss-shot')).toHaveLength(1)
  })

  it('同页的店长档（管理端会话）声明 admin，而不是员工', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'super')
    await flushPromises()

    expect(get.mock.calls.map(([url]) => url)).toEqual([
      '/api/wastage/meta',
      '/api/wastage/templates',
      '/api/wastage/suggestions',
      '/api/wastage/records',
    ])
    for (const [, params] of get.mock.calls) {
      expect(params).toMatchObject({ identity: 'admin' })
    }
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
    expect(wrapper.get('.loss-head a').attributes('href')).toBe('/workbench/staff/loss')
  })
})
