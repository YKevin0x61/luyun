// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

vi.mock('../../../api/client.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

import { api } from '../../../api/client.js'
import { useWorkbenchIdentityStore } from '../../../stores/workbenchIdentity'
import WastageAnalysisView from '../WastageAnalysisView.vue'

const get = api.get

const META = {
  today: '2026-10-17',
  meta: {
    reasons: [
      { value: 'spoiled', label: '变质腐败' },
      { value: 'expired', label: '过期丢弃' },
      { value: 'damaged', label: '破损/污染' },
      { value: 'production_fail', label: '制作失败' },
      { value: 'excess', label: '备多了' },
      { value: 'other', label: '其它' },
    ],
  },
}

const STATS = {
  start_date: '2026-10-01',
  end_date: '2026-10-31',
  total_count: 7,
  item_type_count: 3,
  revoked_count: 2,
  edited_count: 3,
  by_reason: [
    { reason: 'spoiled', reason_text: '变质腐败', count: 4, percentage: 57 },
    { reason: 'expired', reason_text: '过期丢弃', count: 3, percentage: 43 },
  ],
  top_items: [
    { item_name: '五花肉', unit: 'kg', count: 2, total_quantity: 3.5 },
    { item_name: '豆腐', unit: '盒', count: 1, total_quantity: 2 },
  ],
  daily_stats: [
    { date: '2026-10-05', count: 2 },
    { date: '2026-10-17', count: 1 },
  ],
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
      wastage_date: '2026-10-17',
      operator_name: '张伟',
      remark: '解冻后忘了收',
      edit_count: 2,
      images: [{ capture_id: 'img-2', url: '/api/wastage/images/img-2' }],
    },
    {
      id: 1,
      item_name: '豆腐',
      quantity: 2,
      unit: '盒',
      reason: 'damaged',
      reason_text: '破损/污染',
      wastage_date: '2026-10-16',
      operator_name: '李敏',
      remark: '',
      edit_count: 0,
      images: [],
    },
  ],
}

const statsCalls = () => get.mock.calls.filter(([path]) => path === '/api/wastage/statistics')
const recordCalls = () => get.mock.calls.filter(([path]) => path === '/api/wastage/records')
const lastRecordParams = () => recordCalls().at(-1)[1]
const lastStatsParams = () => statsCalls().at(-1)[1]

/** 按路径分发 mock；传 Error 就抛，用来演「读失败」。 */
function stubApi({ meta = META, stats = STATS, records = RECORDS } = {}) {
  get.mockImplementation(async (path) => {
    if (path === '/api/wastage/meta') {
      if (meta instanceof Error) throw meta
      return meta
    }
    if (path === '/api/wastage/statistics') {
      if (stats instanceof Error) throw stats
      return stats
    }
    if (path === '/api/wastage/records') {
      if (records instanceof Error) throw records
      return records
    }
    throw new Error('unexpected path: ' + path)
  })
}

const flush = async () => {
  await flushPromises()
  await nextTick()
}

let router
let wrapper = null

async function mountPage(identity = null, staffName = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const identityStore = useWorkbenchIdentityStore()
  identityStore.identity = identity
  identityStore.staffName = staffName
  wrapper = mount(WastageAnalysisView, {
    attachTo: document.body,
    global: {
      plugins: [router, pinia],
      stubs: { LuyunDatePicker: { template: '<input />' } },
    },
  })
  await flush()
  await flush()
  return wrapper
}

beforeEach(async () => {
  get.mockReset()
  stubApi()
  window.localStorage.clear()
  Element.prototype.scrollIntoView = vi.fn()
  document.body.style.overflow = ''
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/workbench/staff/loss', component: { template: '<div />' } },
      { path: '/workbench/kitchen/loss', component: { template: '<div />' } },
    ],
  })
  await router.push('/workbench/kitchen/loss')
  await router.isReady()
})

afterEach(() => {
  if (wrapper) {
    wrapper.unmount()
    wrapper = null
  }
  delete document.visibilityState
  document.body.style.overflow = ''
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('损耗分析页 A 批', () => {
  it('A3 页头有「刚更新」与刷新按钮，点刷新重新拉统计', async () => {
    const page = await mountPage()
    expect(get.mock.calls[0][0]).toBe('/api/wastage/meta')
    expect(page.find('.loss-updated').text()).toBe('刚更新')
    const before = statsCalls().length
    await page.find('.loss-refresh').trigger('click')
    await flush()
    expect(statsCalls().length).toBe(before + 1)
    expect(page.find('.loss-updated').text()).toBe('刚更新')
  })

  it('A3 每 30 秒轮询；标签页隐藏停表、回来立即刷；卸载清定时器', async () => {
    const setSpy = vi.spyOn(globalThis, 'setInterval')
    const clearSpy = vi.spyOn(globalThis, 'clearInterval')
    const page = await mountPage()
    const pollCall = setSpy.mock.calls.find(([, delay]) => delay === 30000)
    expect(pollCall).toBeTruthy()
    const timerId = setSpy.mock.results[setSpy.mock.calls.indexOf(pollCall)].value
    const before = statsCalls().length

    pollCall[0]()
    await flush()
    expect(statsCalls().length).toBe(before + 1)

    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(clearSpy.mock.calls.some(([id]) => id === timerId)).toBe(true)

    const cleared = statsCalls().length
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(statsCalls().length).toBe(cleared + 1)
    const restartCall = setSpy.mock.calls.at(-1)
    expect(restartCall[1]).toBe(30000)
    const restartedId = setSpy.mock.results.at(-1).value
    page.unmount()
    wrapper = null
    expect(clearSpy.mock.calls.some(([id]) => id === restartedId)).toBe(true)
  })

  it('A3 刷新失败不清旧数字，顶错误条', async () => {
    const page = await mountPage()
    expect(page.findAll('.loss-stat-value').map((node) => node.text().trim())).toEqual(['7次', '3种'])
    stubApi({ stats: new Error('统计服务打盹了') })
    await page.find('.loss-refresh').trigger('click')
    await flush()
    expect(page.find('.loss-error-bar').text()).toContain('统计服务打盹了')
    expect(page.findAll('.loss-stat-value').map((node) => node.text().trim())).toEqual(['7次', '3种'])
  })

  it('A4 改过的明细行挂「改」角标，抬头写改过/撤回笔数', async () => {
    stubApi({ stats: { ...STATS, edited_count: 2, revoked_count: 1 } })
    const page = await mountPage()
    const flags = page.findAll('.loss-edit-flag')
    expect(flags.length).toBe(1)
    expect(flags[0].text()).toBe('改')
    expect(flags[0].attributes('title')).toBe('改过 2 次')
    expect(page.find('.loss-amend').text()).toBe('改过 2 笔 · 撤回 1 笔')
  })

  it('A4 后端缺 edit_count/edited_count/revoked_count 时不崩、不显示', async () => {
    const stats = { ...STATS }
    delete stats.edited_count
    delete stats.revoked_count
    const records = {
      total: RECORDS.total,
      records: RECORDS.records.map((row) => {
        const next = { ...row }
        delete next.edit_count
        return next
      }),
    }
    stubApi({ stats, records })
    const page = await mountPage()
    expect(page.find('.loss-page').exists()).toBe(true)
    expect(page.findAll('.loss-edit-flag').length).toBe(0)
    expect(page.find('.loss-amend').exists()).toBe(false)
    expect(page.findAll('.loss-stat-value').map((node) => node.text().trim())).toEqual(['7次', '3种'])
  })

  it('A5 本月没有记录时只摆空态卡，原因/排行/趋势都不画', async () => {
    stubApi({ stats: { ...STATS, total_count: 0, item_type_count: 0, by_reason: [], top_items: [], daily_stats: [] } })
    const page = await mountPage()
    expect(page.find('.loss-empty-title').text()).toBe('这个月还没有损耗记录')
    expect(page.findAll('.loss-stat').length).toBe(0)
    expect(page.find('.loss-bars').exists()).toBe(false)
    expect(page.find('.loss-rank').exists()).toBe(false)
    expect(page.find('.loss-trend').exists()).toBe(false)
    expect(page.findAll('.loss-item').length).toBe(2)
    await page.find('.loss-empty-prev').trigger('click')
    await flush()
    expect(lastStatsParams()).toEqual({ month: '2026-09' })
    expect(lastRecordParams().start_date).toBe('2026-09-01')
  })

  it('A6 统计读失败：顶部错误条 + 服务端原话 + 卡片显示「—」而不是 0', async () => {
    stubApi({ stats: new Error('statistics 500：数据库刚重连') })
    const page = await mountPage()
    expect(page.find('.loss-error-title').text()).toBe('统计没读出来')
    expect(page.find('.loss-error-detail').text()).toContain('statistics 500：数据库刚重连')
    expect(page.findAll('.loss-stat-value').map((node) => node.text().trim())).toEqual(['—', '—'])
    expect(page.findAll('.loss-item').length).toBe(2)
    expect(page.find('.loss-bars').exists()).toBe(false)
  })

  it('A6 错误条上的重试成功后会恢复数字并收起错误条', async () => {
    stubApi({ stats: new Error('统计服务打盹了') })
    const page = await mountPage()
    expect(page.find('.loss-error-bar').exists()).toBe(true)
    stubApi()
    await page.find('.loss-error-bar button').trigger('click')
    await flush()
    expect(page.find('.loss-error-bar').exists()).toBe(false)
    expect(page.findAll('.loss-stat-value').map((node) => node.text().trim())).toEqual(['7次', '3种'])
    expect(page.find('.loss-bars').exists()).toBe(true)
  })

  it('A8 汇总只剩两张卡，「占最多的是」并进原因卡标题右侧', async () => {
    const page = await mountPage()
    expect(page.findAll('.loss-stat').length).toBe(2)
    expect(page.findAll('.loss-stat-label').map((node) => node.text())).toEqual(['本月总损耗', '涉及物料'])
    expect(page.find('.loss-top-reason').text()).toBe('最多：变质腐败 4 次 · 57%')
  })

  it('A9 趋势卡补 y 轴刻度与峰值文案', async () => {
    const page = await mountPage()
    expect(page.findAll('.loss-trend-scale-tick').map((node) => node.text())).toEqual(['2', '1', '0'])
    expect(page.find('.loss-peak-label').text()).toBe('峰值 2 笔 · 10-05')
  })

  it('A11 换月份后明细范围回「这个月」；范围不同时抬头写明两个口径', async () => {
    const page = await mountPage()
    await page.find('[aria-label="上一个月"]').trigger('click')
    await flush()
    expect(lastStatsParams()).toEqual({ month: '2026-09' })
    expect(lastRecordParams().start_date).toBe('2026-09-01')
    expect(lastRecordParams().end_date).toBe('2026-09-30')
    expect(page.find('.loss-mode.on').text()).toBe('这个月')

    await page.findAll('.loss-mode')[1].trigger('click')
    await flush()
    const hint = page.find('.loss-scope-hint').text()
    expect(hint).toContain('统计按 2026-09')
    expect(hint).toContain('09-18 ~ 10-17')

    await page.findAll('.loss-mode')[0].trigger('click')
    await flush()
    expect(page.find('.loss-scope-hint').exists()).toBe(false)
  })

  it('A10 非本月出现「回到本月」，点了月份与明细范围一起回来', async () => {
    const page = await mountPage()
    expect(page.find('.loss-back-current').exists()).toBe(false)
    await page.find('[aria-label="上一个月"]').trigger('click')
    await flush()
    const button = page.find('.loss-back-current')
    expect(button.exists()).toBe(true)
    expect(button.text()).toBe('回到本月')
    await button.trigger('click')
    await flush()
    expect(lastStatsParams()).toEqual({ month: '2026-10' })
    expect(lastRecordParams().start_date).toBe('2026-10-01')
    expect(page.find('.loss-back-current').exists()).toBe(false)
  })

  it('A10/A7 管理端登记入口带归属提示，关键词占位说明搜什么', async () => {
    const page = await mountPage()
    const link = page.find('.loss-head .loss-register')
    expect(link.attributes('href')).toBe('/workbench/staff/loss')
    expect(link.attributes('title')).toContain('管理端')
    expect(page.find('.loss-register-hint').text()).toContain('管理端')
    expect(page.find('input[type="search"]').attributes('placeholder')).toBe('搜物料 / 备注 / 人')
  })

  it('F1 店长（员工会话 + wastage 能力）看到的是「记自己名字」而不是管理端口径', async () => {
    const page = await mountPage('staff', '彭灼洪')
    const hint = page.find('.loss-register-hint').text()
    expect(hint).toContain('彭灼洪')
    expect(hint).toContain('全店统计')
    // 店长不用再「去员工端」：他自己就是员工，点过去记的就是自己。
    expect(hint).not.toContain('员工请用员工端')
    expect(page.find('.loss-head .loss-register').attributes('title')).toContain('彭灼洪')
  })

  it('A14 翻页后滚回明细第一行', async () => {
    const many = Array.from({ length: 20 }, (_, index) => ({
      ...RECORDS.records[0],
      id: 100 + index,
      edit_count: 0,
      images: [],
    }))
    stubApi({ records: { total: 45, records: many } })
    const page = await mountPage()
    Element.prototype.scrollIntoView.mockClear()
    await page.findAll('.loss-pager button')[1].trigger('click')
    await flush()
    expect(lastRecordParams().offset).toBe(20)
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  })

  it('A14 缩略图开页内灯箱：Esc / 点遮罩关闭，点图不关，且不再新开窗口', async () => {
    const page = await mountPage()
    expect(page.find('.loss-thumbs a').exists()).toBe(false)
    const thumbs = page.findAll('.loss-thumb')
    expect(thumbs.length).toBe(1)
    await thumbs[0].trigger('click')
    await flush()
    let overlay = document.body.querySelector('.loss-lightbox')
    expect(overlay).toBeTruthy()
    expect(document.body.querySelector('.loss-lightbox img').getAttribute('src')).toBe('/api/wastage/images/img-2')
    expect(document.body.style.overflow).toBe('hidden')

    overlay.querySelector('img').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(document.body.querySelector('.loss-lightbox')).toBeTruthy()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flush()
    expect(document.body.querySelector('.loss-lightbox')).toBeNull()
    expect(document.body.style.overflow).toBe('')

    await thumbs[0].trigger('click')
    await flush()
    overlay = document.body.querySelector('.loss-lightbox')
    overlay.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flush()
    expect(document.body.querySelector('.loss-lightbox')).toBeNull()
  })
})
