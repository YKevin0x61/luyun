// @vitest-environment jsdom
/**
 * 损耗两页的页面级测试（从 worktool2 那个小程序搬过来的那一块）。
 *
 * 压三件服务端测试看不到的事：
 *   ① 登记页提交/修改的 body 形状（字段名 / 图片 id 列表 / 数量转数字 / PATCH 不带照片）；
 *   ② 登记页这一轮的交互契约（spec 4.1~4.5）：表单重排后必填在选填之前、选填三项收起、
 *      提交按钮不再 disabled 而是就地校验+聚焦、成功后不滚页面、草稿自动存/恢复/清、
 *      模板删除与记录撤回的二次确认、备货计划带过来的预填（含损耗清单里没有的单位）；
 *   ③ 分析页画的是**服务端给的数字**（总笔数、原因分布、物料排行、明细条数）与三把筛子。
 *
 * 服务端自己的口径（校验、归属、24h 窗口、统计算法）在 tests/test_wastage.py 里压，这里不重复。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
const patch = vi.fn()
const del = vi.fn()
const upload = vi.fn()

vi.mock('../../../api/client.js', () => ({
  api: {
    get: (...args) => get(...args),
    post: (...args) => post(...args),
    patch: (...args) => patch(...args),
    delete: (...args) => del(...args),
    upload: (...args) => upload(...args),
  },
}))

import WastageAnalysisView from '../WastageAnalysisView.vue'
import WastageRegisterView from '../WastageRegisterView.vue'
import { useWorkbenchIdentityStore } from '../../../stores/workbenchIdentity'

const DRAFT_KEY = 'luyun.wastage.draft'

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
    amend_window_hours: 24,
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
      images: [{ capture_id: 'img-2', url: '/api/wastage/images/img-2' }],
      can_amend: true,
      amend_deadline_at: '2026-10-18T09:00:00+08:00',
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
      can_amend: false,
      amend_deadline_at: null,
    },
  ],
}

const TEMPLATES = {
  templates: [
    { id: 1, item_name: '五花肉', unit: 'kg', reason: 'spoiled', reason_text: '变质腐败', is_custom: true },
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

async function mountView(component, path, identity = 'staff', query = null) {
  const router = route()
  await router.push(query ? { path, query } : path)
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
    attachTo: document.body,
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

/** 选填三项收成了一行小按钮（spec 4.1.5）：按文案找那一颗。 */
function moreButton(wrapper, label) {
  return wrapper.findAll('.loss-more-btn').find((node) => node.text().includes(label))
}

/** 单位 chips（没有原生 select 了，spec 4.1.3）。 */
function unitChip(wrapper, label) {
  return wrapper.findAll('.loss-unit-chip').find((node) => node.text() === label)
}

/** 填满三项必填（物料 / 数量+单位 / 原因）。 */
async function fillRequired(wrapper, options = {}) {
  const item = options.item ?? '五花肉'
  const qty = options.qty ?? '1.5'
  const unitLabel = options.unit ?? 'kg'
  const reasonIndex = options.reasonIndex ?? 0
  await wrapper.get('#wastage-item-name').setValue(item)
  await wrapper.get('input[type="number"]').setValue(qty)
  await unitChip(wrapper, unitLabel).trigger('click')
  await wrapper.findAll('.loss-reason')[reasonIndex].trigger('click')
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  patch.mockReset()
  del.mockReset()
  upload.mockReset()
  window.localStorage.clear()
  // jsdom 没有 scrollIntoView；页面里「成功后不滚动」这条要靠它反证。
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('损耗登记页（员工端）', () => {
  beforeEach(() => {
    get.mockImplementation((url) => {
      if (url === '/api/wastage/meta') return Promise.resolve(META)
      if (url === '/api/wastage/templates') return Promise.resolve(TEMPLATES)
      // 带单位：chips 点一下要能把「五花肉 + kg」一起填好（2026-11-07）。
      if (url === '/api/wastage/suggestions') {
        return Promise.resolve({
          suggestions: [
            { item_name: '五花肉', unit: 'kg' },
            { item_name: '豆腐', unit: '盒' },
          ],
        })
      }
      if (url === '/api/wastage/records') return Promise.resolve(RECORDS)
      if (url === '/api/prep-plan/items') {
        return Promise.resolve({
          success: true,
          items: [
            { id: 3, item_name: '五花肉', unit: 'kg', station: 'shulong', kind: 'bought', category: '原料' },
            { id: 4, item_name: '河粉', unit: '斤', station: 'shulong', kind: 'made', category: '半成品' },
          ],
        })
      }
      return Promise.resolve({})
    })
    post.mockResolvedValue({
      record: {
        id: 9,
        item_name: '五花肉',
        quantity: 1.5,
        unit: 'kg',
        reason_text: '变质腐败',
      },
    })
    patch.mockResolvedValue({
      record: {
        id: 2,
        item_name: '五花肉',
        quantity: 2,
        unit: 'kg',
        reason_text: '变质腐败',
      },
    })
    del.mockResolvedValue({ record: { id: 2 } })
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
      // 「对应备货品」的候选项来自备货品主数据（同一道「任一身份」门，不新开权限）。
      '/api/prep-plan/items',
    ])
    // 员工档的页面每个请求都声明 identity=staff：共用电脑上两套 cookie 都在时，
    // 后端默认「管理端优先」会让员工页的归属与可见范围都是错的。
    for (const [, params] of get.mock.calls) {
      expect(params).toMatchObject({ identity: 'staff' })
    }
    // 模板 chips 与「我最近的登记」都摆出来了。
    expect(wrapper.text()).toContain('五花肉')
    expect(wrapper.text()).toContain('我最近的登记')
    expect(wrapper.findAll('.loss-item')).toHaveLength(2)
    // 六个原因与单位都来自服务端的清单，不是页面里写死的。
    expect(wrapper.findAll('.loss-reason')).toHaveLength(6)
    // 顶部一句「今天已记 N 笔」：从已加载的记录算，不额外请求（spec 4.1.1）。
    expect(wrapper.get('.loss-status').text()).toContain('今天已记 1 笔')
    expect(get.mock.calls.map(([url]) => url)).not.toContain('/api/wastage/today-count')
  })

  it('表单重排：必填三项在前，照片/备注/日期收成一行小按钮、点开才展开（4.1.1~4.1.5）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    const form = wrapper.get('form.loss-block')
    const html = form.html()
    // 必填三项：物料 → 数量与单位 → 原因，顺序不能乱。
    expect(html.indexOf('wastage-item-name')).toBeLessThan(html.indexOf('loss-units'))
    expect(html.indexOf('loss-units')).toBeLessThan(html.indexOf('loss-reasons'))
    // 选填三项收在必填之后，一行三颗小按钮。
    expect(html.indexOf('loss-reasons')).toBeLessThan(html.indexOf('loss-more'))
    const moreButtons = wrapper.findAll('.loss-more-btn')
    // 四颗：照片 / 备注 / 日期 / 对应备货品。「对应备货品」原本插在必填三项中间，
    // 70px 会把「原因」挤出首屏 —— 2026-11-07 挪进这一行，一行横滑不换行。
    expect(moreButtons).toHaveLength(4)
    expect(moreButtons.map((node) => node.text())).toEqual([
      '＋现场照片',
      '＋备注',
      '改日期 · 今天',
      '＋对应备货品',
    ])
    // 默认全收着：照片区、备注框、日期快捷都不在 DOM 里（首屏一屏看完）。
    expect(wrapper.find('.loss-photo').exists()).toBe(false)
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.find('.loss-day').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'HygieneLiveCamera' }).exists()).toBe(false)

    // 点开第一颗 → 照片区出现（仍在本表单里，仍是页内相机）。
    await moreButton(wrapper, '现场照片').trigger('click')
    expect(wrapper.get('form.loss-block .loss-photo').exists() || wrapper.find('.loss-photo').exists()).toBe(true)
    // 再点一次收起。
    await moreButton(wrapper, '现场照片').trigger('click')
    expect(wrapper.find('.loss-photo').exists()).toBe(false)

    // 备注：点开才出现。
    await moreButton(wrapper, '备注').trigger('click')
    expect(wrapper.find('textarea').exists()).toBe(true)
    // 日期：点开才出现今天/昨天。
    await moreButton(wrapper, '日期').trigger('click')
    expect(wrapper.findAll('.loss-day')).toHaveLength(2)
    // 原因三列两行：CSS 走 grid repeat(3, minmax(0, 1fr))，这里只确认容器与 6 颗按钮。
    expect(wrapper.findAll('.loss-reason')).toHaveLength(6)
  })

  it('单位是 chips 不是原生 select：选中用 --action 描边（4.1.3）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 单位是 chips，没有原生 select（原生 select 只剩收在选填里的「对应备货品」）。
    expect(wrapper.findAll('.loss-units select')).toHaveLength(0)
    // 只摆前 6 个常用单位 + 「其他…」。
    const chips = wrapper.findAll('.loss-unit-chip')
    expect(chips).toHaveLength(7)
    expect(chips.map((node) => node.text())).toEqual(['份', 'kg', 'g', '个', '盒', '袋', '其他…'])
    await unitChip(wrapper, 'kg').trigger('click')
    expect(unitChip(wrapper, 'kg').classes()).toContain('on')
    // 「其他…」展开自定义单位输入。
    await unitChip(wrapper, '其他…').trigger('click')
    expect(wrapper.find('.loss-unit-custom').exists()).toBe(true)
  })

  it('提交的 body 是服务端要的那几个字段（数量转数字、图片带 capture_id）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await fillRequired(wrapper)
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
    // 没选备货品时显式传 null（复盘侧按品名兜底）。
    expect(body.prep_item_id).toBeNull()
    // 登记的这一天默认是服务端给的今天。
    expect(body.wastage_date).toBe('2026-10-17')
  })

  it('可选「对应备货品」：选了下拉就把 prep_item_id 一起提交', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 原生 select 只剩这一个，且收在选填行里：点开才有（首屏不占位）。
    expect(wrapper.find('select').exists()).toBe(false)
    await moreButton(wrapper, '对应备货品').trigger('click')
    const select = wrapper.get('form select')
    expect(wrapper.text()).toContain('对应备货品')
    expect(select.element.value).toBe('')

    await fillRequired(wrapper)
    await select.setValue('3')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const [, body] = post.mock.calls[0]
    expect(body.prep_item_id).toBe(3)
  })

  it('按钮不再 disabled：只有 busy 时禁用（4.1.6）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    const button = wrapper.get('.loss-actions .btn-primary')
    // 空表单也点得动（上一版这里是 disabled，submit 里的校验成了死代码）。
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.text()).toContain('还差物料')

    // 在飞的请求期间才禁用。
    let resolvePost = null
    post.mockImplementation(
      () => new Promise((resolve) => {
        resolvePost = resolve
      }),
    )
    await fillRequired(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('提交中')

    resolvePost({ record: { id: 9, item_name: '五花肉', quantity: 1.5, unit: 'kg', reason_text: '变质腐败' } })
    await flushPromises()
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('缺项点提交：就地拦下、不发请求、提示在按钮上方、焦点落在缺的那一栏（4.1.6）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(post).not.toHaveBeenCalled()
    const actions = wrapper.get('.loss-actions')
    expect(actions.find('.loss-error').exists()).toBe(true)
    expect(actions.get('.loss-error').text()).toContain('先填物料')
    expect(document.activeElement).toBe(wrapper.get('#wastage-item-name').element)

    // 物料有了，还差数量 —— 按钮文案与提示都跟着走。
    await wrapper.get('#wastage-item-name').setValue('五花肉')
    expect(wrapper.get('.loss-actions .btn-primary').text()).toContain('还差数量')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(post).not.toHaveBeenCalled()
    expect(wrapper.get('.loss-actions .loss-error').text()).toContain('数量要大于 0')
    expect(document.activeElement).toBe(wrapper.get('input[type="number"]').element)
  })

  it('选了「其他」时把备注展开并标成必填（判据仍在服务端）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    expect(wrapper.text()).not.toContain('选了「其他」要说明')
    await wrapper.findAll('.loss-reason')[5].trigger('click')
    await flushPromises()
    // 备注区就地展开（原来它在页底，红字提示被吸底条挡着）。
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.text()).toContain('选了「其他」要说明')
  })

  it('现场照片：「＋现场照片」点开才在表单里，点「拍一张」就地开相机（不跳系统相机）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await moreButton(wrapper, '现场照片').trigger('click')
    // 照片是**这一笔损耗**表单里的一节，不浮在卡片外面。
    const form = wrapper.get('form.loss-block')
    expect(form.find('.loss-photo').exists()).toBe(true)
    expect(form.get('.loss-photo').text()).toContain('现场照片')

    // 没有 file input：这一版不走「系统相机 / 相册」那条路 —— 那条路在 iPhone 上拿到的是
    // HEIC，后端认不出格式（用户真机上撞过）。
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    // 相机默认收着，点一下才展开 —— 页内取景，不跳出去。
    expect(wrapper.find('.stub-camera').exists()).toBe(false)
    await wrapper.get('.loss-shot-add').trigger('click')
    expect(wrapper.find('.stub-camera').exists()).toBe(true)

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

  it('日期快捷：点「昨天」提交的就是昨天（跨零点补记）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await moreButton(wrapper, '日期').trigger('click')
    // 服务端给的今天是 2026-10-17，页面上的「昨天」必须是 2026-10-16。
    const days = wrapper.findAll('.loss-day')
    expect(days).toHaveLength(2)
    await days[1].trigger('click')

    await fillRequired(wrapper, { qty: '1' })
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][1].wastage_date).toBe('2026-10-16')
  })

  it('选了「其他」没写说明：就地拦下，不发请求，焦点在备注框', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await fillRequired(wrapper, { reasonIndex: 5 })
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(post).not.toHaveBeenCalled()
    const error = wrapper.get('.loss-actions .loss-error')
    expect(error.text()).toContain('选了「其他」要写一句说明')
    expect(document.activeElement).toBe(wrapper.get('textarea').element)
  })

  it('物料名可以点「最近记过的」直接填，名字和单位一起好（2026-11-07）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    const chips = wrapper.findAll('.loss-quick-chip')
    expect(chips.length).toBeGreaterThan(0)
    // chip 上带单位：员工看得见「五花肉 kg」，不是填完名字还得再猜一次单位。
    expect(chips[0].text()).toContain('五花肉')
    expect(chips[0].find('.loss-quick-unit').text()).toBe('kg')
    await chips[0].trigger('click')
    expect(wrapper.get('#wastage-item-name').element.value).toBe('五花肉')
    // 一下就把单位也选好了（省掉一次点击，这正是这一页存在的意义）。
    expect(unitChip(wrapper, 'kg').classes()).toContain('on')
  })

  it('「从备货单选」：品名和单位一起填好，还挂上对应备货品', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 默认没有弹层（它挂在 body 层，不在表单里）。
    expect(wrapper.find('.loss-picker').exists()).toBe(false)
    await wrapper.get('.loss-link-btn').trigger('click')
    expect(wrapper.find('.loss-picker').exists()).toBe(true)
    // 备货品主数据来的两项都列着，带单位与分类。
    const rows = wrapper.findAll('.loss-picker-item')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('五花肉')
    expect(rows[0].text()).toContain('kg')

    // 搜「河」只剩河粉（员工记得住名字就行）。
    await wrapper.get('.loss-picker input[type="search"]').setValue('河')
    expect(wrapper.findAll('.loss-picker-item')).toHaveLength(1)
    await wrapper.get('.loss-picker-item').trigger('click')

    // 弹层关了，物料与单位都填好，来源说得清是哪一条。
    expect(wrapper.find('.loss-picker').exists()).toBe(false)
    expect(wrapper.get('#wastage-item-name').element.value).toBe('河粉')
    expect(unitChip(wrapper, '斤').classes()).toContain('on')
    expect(wrapper.get('.loss-source').text()).toContain('河粉 斤')

    // 提交时把对应备货品一起带上（复盘按它对齐数量）。材料齐了就能提交，
    // 数量只有现场知道 —— 所以弹层只填品名与单位。
    await wrapper.get('input[type="number"]').setValue('2')
    await wrapper.findAll('.loss-reason')[0].trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][1].prep_item_id).toBe(4)
  })

  it('提交成功后：不滚页面、提示在吸底条里、表单清空但原因留着、焦点回物料框（4.1.7）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await fillRequired(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledTimes(1)
    // 成功那一声在吸底条里（按钮上方），提交那一刻一定在视野里。
    const actions = wrapper.get('.loss-actions')
    expect(actions.find('.loss-note').exists()).toBe(true)
    expect(actions.get('.loss-note').text()).toContain('已记下')
    // 提交成功后**页面不滚动**（上一版 scrollIntoView 到记录区，连记 5 笔每笔被弹走）。
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
    // 焦点回物料框，方便连着记下一笔。
    expect(document.activeElement).toBe(wrapper.get('#wastage-item-name').element)
    // 表单清空，但原因/单位留着（连着记同一批损耗不用再点一次）。
    expect(wrapper.get('#wastage-item-name').element.value).toBe('')
    expect(wrapper.get('input[type="number"]').element.value).toBe('')
    expect(wrapper.find('.loss-reason.on').exists()).toBe(true)
    expect(unitChip(wrapper, 'kg').classes()).toContain('on')
    // 模板重拉一次（初始 1 次 + 提交后 1 次）：刚记的组合下一笔就能点。
    const templateLoads = get.mock.calls.filter(([url]) => url === '/api/wastage/templates')
    expect(templateLoads).toHaveLength(2)
  })

  it('提交失败：表单留着、错误上方多一句「再点一次就是重试」（4.3.2）', async () => {
    post.mockRejectedValue(Object.assign(new Error('这台机器暂时连不上'), { status: 500 }))
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await fillRequired(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const error = wrapper.get('.loss-actions .loss-error')
    expect(error.text()).toContain('这台机器暂时连不上')
    expect(error.text()).toContain('再点一次就是重试')
    expect(wrapper.get('#wastage-item-name').element.value).toBe('五花肉')
  })

  it('模板删除要二次确认：点 × 先变「确认删」，再点一次才真删（4.4）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 第一次点 × 只把按钮换成「确认删」，不发请求。
    await wrapper.get('.loss-chip-x').trigger('click')
    await nextTick()
    expect(del).not.toHaveBeenCalled()
    expect(wrapper.get('.loss-chip-x-confirm').text()).toBe('确认删')

    // 再点一下才真删。
    await wrapper.get('.loss-chip-x-confirm').trigger('click')
    await flushPromises()
    expect(del).toHaveBeenCalledTimes(1)
    expect(del.mock.calls[0][0]).toBe('/api/wastage/templates/1')
    expect(del.mock.calls[0][1]).toEqual({ identity: 'staff' })
  })

  it('模板删除的「确认删」3 秒不点就复位，不会一直挂着（4.4）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    vi.useFakeTimers()
    await wrapper.get('.loss-chip-x').trigger('click')
    await nextTick()
    expect(wrapper.find('.loss-chip-x-confirm').exists()).toBe(true)
    // 3 秒不点就复位：还是那颗 ×，没发请求。
    vi.advanceTimersByTime(3001)
    await nextTick()
    expect(wrapper.find('.loss-chip-x-confirm').exists()).toBe(false)
    expect(wrapper.find('.loss-chip-x').exists()).toBe(true)
    expect(del).not.toHaveBeenCalled()
  })

  it('只有 can_amend 的行才有「改」「撤回」；撤回要行内二次确认（4.2.1/4.2.3）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    // 两条记录里只有 id=2 那一笔可以更正（24h 内、本人的）。
    expect(wrapper.findAll('.loss-item-acts')).toHaveLength(1)
    const acts = wrapper.get('.loss-item-acts')
    expect(acts.findAll('.loss-item-btn').map((node) => node.text())).toEqual(['改', '撤回'])

    await acts.findAll('.loss-item-btn')[1].trigger('click')
    // 撤回是行内二次确认，不是弹窗。
    expect(wrapper.get('.loss-item-btn.is-danger').text()).toBe('确认撤回')
    await wrapper.get('.loss-item-btn.is-danger').trigger('click')
    await flushPromises()

    expect(del).toHaveBeenCalledTimes(1)
    expect(del.mock.calls[0][0]).toBe('/api/wastage/records/2')
    expect(del.mock.calls[0][1]).toEqual({ identity: 'staff' })
    expect(wrapper.findAll('.loss-item')).toHaveLength(1)
    expect(wrapper.findAll('.loss-note').some((node) => node.text().includes('已撤回'))).toBe(true)
  })

  it('点「改」进编辑态：保存修改走 PATCH、成功后退出编辑并重拉列表（4.2.2）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await wrapper.get('.loss-item-acts .loss-item-btn').trigger('click')
    await flushPromises()
    // 表单切到编辑态：说清正在改哪一笔，主按钮变「保存修改」，多一颗「取消」。
    expect(wrapper.get('.loss-status').text()).toContain('正在改')
    expect(wrapper.get('.loss-actions .btn-primary').text()).toBe('保存修改')
    expect(wrapper.get('.loss-actions').text()).toContain('取消')
    // 照片是现场证据：原记录的留着、不能换（照片入口变成一句静态说明）。
    expect(wrapper.find('.loss-edit-photo').text()).toContain('原记录的 1 张照片保留，不能换')
    expect(wrapper.find('.loss-more-btn.is-static').text()).toBe('照片保留')
    expect(wrapper.find('.loss-shot-add').exists()).toBe(false)
    // 字段回填。
    expect(wrapper.get('#wastage-item-name').element.value).toBe('五花肉')
    expect(wrapper.get('input[type="number"]').element.value).toBe('1.5')
    expect(unitChip(wrapper, 'kg').classes()).toContain('on')

    await wrapper.get('input[type="number"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledTimes(1)
    const [url, body, params] = patch.mock.calls[0]
    expect(url).toBe('/api/wastage/records/2')
    expect(params).toEqual({ identity: 'staff' })
    expect(body.quantity).toBe(2)
    expect(body.item_name).toBe('五花肉')
    expect(body.unit).toBe('kg')
    expect(body.reason).toBe('spoiled')
    expect(body.wastage_date).toBe('2026-10-17')
    // PATCH 不带照片：照片不可改不可增删。
    expect(body.image_ids).toBeUndefined()
    // 成功：退出编辑、提示「已改好」、列表重拉一次。
    expect(wrapper.find('.loss-status-edit').exists()).toBe(false)
    expect(wrapper.get('.loss-actions .loss-note').text()).toContain('已改好')
    const recordLoads = get.mock.calls.filter(([url2]) => url2 === '/api/wastage/records')
    expect(recordLoads).toHaveLength(2)
  })

  it('编辑态点「取消」退回新建，表单清空', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await wrapper.get('.loss-item-acts .loss-item-btn').trigger('click')
    await flushPromises()
    const cancel = wrapper.findAll('.loss-actions .btn').find((node) => node.text() === '取消')
    await cancel.trigger('click')
    await flushPromises()

    expect(wrapper.find('.loss-status-edit').exists()).toBe(false)
    expect(wrapper.get('.loss-actions .btn-primary').text()).toContain('确认登记')
    expect(wrapper.get('#wastage-item-name').element.value).toBe('')
    expect(patch).not.toHaveBeenCalled()
  })

  it('改这一笔被服务端挡下（409 超过 24 小时）：把服务端的话摆出来（4.2.2）', async () => {
    patch.mockRejectedValue(
      Object.assign(new Error('超过 24 小时了，这一笔不能再改；如果记错了，另记一笔说明'), { status: 409 }),
    )
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()

    await wrapper.get('.loss-item-acts .loss-item-btn').trigger('click')
    await flushPromises()
    await wrapper.get('input[type="number"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.loss-actions .loss-error').text()).toContain('超过 24 小时了')
    // 窗口关了说明列表里的 can_amend 过时了：重拉一次。
    const recordLoads = get.mock.calls.filter(([url]) => url === '/api/wastage/records')
    expect(recordLoads).toHaveLength(2)
  })

  it('草稿：填了一半切走再回来会恢复；提交成功后清掉（4.3.1）', async () => {
    const first = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()
    await first.get('#wastage-item-name').setValue('五花肉')
    await first.get('input[type="number"]').setValue('1.5')
    await flushPromises()

    const stored = JSON.parse(window.localStorage.getItem(DRAFT_KEY))
    expect(stored.item_name).toBe('五花肉')
    // input[type=number] 的 v-model 会把值转成数字，草稿里就是数字。
    expect(stored.quantity).toBe(1.5)
    expect(stored.day).toBe('2026-10-17')
    first.unmount()

    const second = await mountView(WastageRegisterView, '/workbench/staff/loss')
    await flushPromises()
    expect(second.get('#wastage-item-name').element.value).toBe('五花肉')
    expect(second.get('input[type="number"]').element.value).toBe('1.5')
    expect(second.find('.loss-draft').exists()).toBe(true)
    expect(second.get('.loss-draft').text()).toContain('上次没提交完的那一笔已恢复')

    await unitChip(second, 'kg').trigger('click')
    await second.findAll('.loss-reason')[0].trigger('click')
    await second.get('form').trigger('submit')
    await flushPromises()
    expect(window.localStorage.getItem(DRAFT_KEY)).toBe(null)
  })

  it('从备货计划带过来：预填物料与单位、显示来源、数量留空（4.5）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'staff', {
      from: 'prep',
      item: '五花肉',
      unit: 'kg',
    })
    await flushPromises()

    expect(wrapper.get('#wastage-item-name').element.value).toBe('五花肉')
    expect(unitChip(wrapper, 'kg').classes()).toContain('on')
    // 数量必须现场确认：带过来的不带数量。
    expect(wrapper.get('input[type="number"]').element.value).toBe('')
    const source = wrapper.get('.loss-source')
    expect(source.text()).toContain('从备货计划带过来：五花肉 kg')
  })

  it('备货计划的单位不在损耗清单里（条/底/板/碗）：落到「其他…」带走，不丢（4.5 + Lead 边界）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'staff', {
      from: 'prep',
      item: '鸭肠',
      unit: '条',
    })
    await flushPromises()

    expect(wrapper.get('#wastage-item-name').element.value).toBe('鸭肠')
    expect(unitChip(wrapper, '其他…').classes()).toContain('on')
    expect(wrapper.get('.loss-unit-custom').element.value).toBe('条')
    expect(wrapper.get('.loss-source').text()).toContain('从备货计划带过来：鸭肠 条')

    await wrapper.get('input[type="number"]').setValue('3')
    await wrapper.findAll('.loss-reason')[0].trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(post.mock.calls[0][1].unit).toBe('条')
  })

  it('备货计划的单位在清单里但不在前六颗（瓶/包/斤）：补一颗并选中，不落到「其他…」（4.5 + Lead 边界）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'staff', {
      from: 'prep',
      item: '冰糖',
      unit: '瓶',
    })
    await flushPromises()

    // 瓶在 units 里、但排在第七，默认六颗里没有它：要补一颗出来，且是选中态。
    const chip = unitChip(wrapper, '瓶')
    expect(chip).toBeTruthy()
    expect(chip.classes()).toContain('on')
    expect(wrapper.find('.loss-unit-custom').exists()).toBe(false)
    expect(wrapper.get('.loss-source').text()).toContain('从备货计划带过来：冰糖 瓶')

    await wrapper.get('input[type="number"]').setValue('2')
    await wrapper.findAll('.loss-reason')[0].trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(post.mock.calls[0][1].unit).toBe('瓶')
  })

  it('别的来源的 query 不预填（只认 from=prep）', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'staff', {
      from: 'hygiene',
      item: '五花肉',
      unit: 'kg',
    })
    await flushPromises()
    expect(wrapper.get('#wastage-item-name').element.value).toBe('')
    expect(wrapper.find('.loss-source').exists()).toBe(false)
  })

  it('同页的店长档（管理端会话）声明 admin，而不是员工', async () => {
    const wrapper = await mountView(WastageRegisterView, '/workbench/staff/loss', 'super')
    await flushPromises()

    expect(get.mock.calls.map(([url]) => url)).toEqual([
      '/api/wastage/meta',
      '/api/wastage/templates',
      '/api/wastage/suggestions',
      '/api/wastage/records',
      // 「对应备货品」的候选项来自备货品主数据（同一道「任一身份」门，不新开权限）。
      '/api/prep-plan/items',
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
      if (url === '/api/prep-plan/items') {
        return Promise.resolve({
          success: true,
          items: [
            { id: 3, item_name: '五花肉', unit: 'kg', station: 'shulong', kind: 'bought', category: '原料' },
            { id: 4, item_name: '河粉', unit: '斤', station: 'shulong', kind: 'made', category: '半成品' },
          ],
        })
      }
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
