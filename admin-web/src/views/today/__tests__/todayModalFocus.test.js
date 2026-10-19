// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

const here = dirname(fileURLToPath(import.meta.url))

// 挂到真 document 上：焦点陷阱与 inert 判的是**真 DOM**（`document.activeElement`），
// 游离的 wrapper 里没有 `document.querySelector('.modal-overlay')` 可查。
afterEach(() => { document.body.innerHTML = '' })

/**
 * 员工端最后三条判据的可执行版本（t72 / ③-3k 收尾）。
 * 「怎么在测试里开出一个弹层」用组件在 t72 加的测试专用暴露面 `openModal(key)`
 * —— 入口按钮依赖数据（无申请/无班次时不渲染），靠点按钮去开弹层会让判据时好时坏。
 */

const ME = {
  employee: { id: 7, name: '余威威' },
  days: [],
}

const HYGIENE_ME = {
  employee: { id: 7, name: '余威威', phone: '13800000000', shift: '白班', zone_id: 3, zone_name: '案板', job_title: '领班', birthday: '1990-05-06', hire_date: '2020-03-01' },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

const STATS = {
  days: 7,
  since: '2026-09-29',
  business_date: '2026-10-05',
  first_pass: 12,
  rejected: 2,
  captured: 14,
  missed: 0,
  pass_rate: 0.857,
  reasons: [
    { reason: '台面还有油渍', count: 2 },
    { reason: '角落没擦到', count: 1 },
  ],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountToday({ stats = STATS, statsStatus = 200 } = {}) {
  const statsUrls = []
  const fetchMock = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    if (method !== 'GET') return jsonResponse({ ok: true })
    if (path === '/api/scheduling/me') return jsonResponse(ME)
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') return jsonResponse(HYGIENE_ME)
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire: { required: false } })
    if (path === '/api/hygiene/staff/me/stats') {
      statsUrls.push(raw)
      if (statsStatus !== 200) return jsonResponse({ detail: '统计读不出来' }, statsStatus)
      return jsonResponse(stats)
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/staff/today', component: { template: '<div />' } }],
  })
  await router.push('/workbench/staff/today')
  await router.isReady()

  const wrapper = mount(TodayView, { global: { plugins: [router, pinia] } }, { attachTo: document.body })
  await flushPromises()
  await flushPromises()
  return { wrapper, statsUrls }
}

const MAIN_SIX = ['手机号', '当天区域', '当天班次', '入职日期', '健康证到期日', '工龄奖']
const MOVED_FIVE = ['姓名', '本班日常截止', '专项截止', '职位', '生日']

function metaTexts(wrapper) {
  return wrapper.findAll('.me-meta').map((dl) => dl.findAll('dt').map((dt) => dt.text()))
}

/** 打开弹层（走组件在 t72 加的测试专用暴露面，不依赖入口按钮是否渲染）。 */
/** 页面是多根片段：手动把根挂进 body，焦点与 inert 才有真 DOM 可判。 */
function attachToBody(wrapper) {
  if (!document.body.contains(wrapper.element)) document.body.appendChild(wrapper.element)
}

async function openModal(wrapper, key) {
  // 页面是**多根片段**（弹层是 .today-page 的兄弟），attachTo 不会把片段塞进 document，
  // 所以这里手动把根节点挂进 body —— 焦点陷阱与 inert 判的就是真 DOM。
  attachToBody(wrapper)
  wrapper.vm.openModal(key)
  await flushPromises()
}

describe('资料表折叠：主表恰 6 行、移出 5 项可达、路由未动（t72 第一组）', () => {
  it('主表恰 6 行，逐项点名；多一行少一行都红', async () => {
    const { wrapper } = await mountToday()
    const rows = metaTexts(wrapper)[0]
    expect(rows).toEqual(MAIN_SIX)
  })

  it('移出的 5 项：入口在、主表没有、点开「完整资料」后全部可达', async () => {
    // 入口本身也要判：没了入口，「可达」就只是测试钩子里的可达（t72 负向④踩到的）

    const { wrapper } = await mountToday()
    expect(wrapper.find('.me-more button').exists(), '主表里必须有「完整资料」入口').toBe(true)
    expect(wrapper.get('.me-more button').text(), '入口文案就是「完整资料」').toContain('完整资料')
    const main = wrapper.get('.me-meta').text()
    for (const label of MOVED_FIVE) {
      expect(main, `主表不该再有「${label}」`).not.toContain(label)
    }
    await openModal(wrapper, 'more')
    const all = metaTexts(wrapper)
    const more = all[all.length - 1]
    expect(more).toEqual(MOVED_FIVE)
  })

  it('路由未被改动：二级视图是页内弹层，没有新增任何路由', () => {
    const routes = JSON.parse(readFileSync(join(here, '../../../router/pageRoutes.json'), 'utf8'))
    const flat = JSON.stringify(routes)
    expect(flat).not.toContain('me/more')
    expect(flat).not.toContain('完整资料')
    // 「我的」组的路由仍是那几条（今天 / 整月 / 加班与补钟 / 卫生 / 损耗登记）：本票
    // 的二级视图是页内弹层，多一条路由就是越界 —— 那几条各自是别的票的功能页。
    const me = JSON.stringify(routes).match(/"\/workbench\/staff\/[a-z]+"/g) || []
    expect(new Set(me).size).toBe(5)
  })
})

describe('弹层的焦点陷阱与背景隔离：真跑一遍（t72 第二组）', () => {
  it('开着时背景 inert（底层不可达）、焦点在弹层里；关掉后 inert 解除、焦点回到触发器', async () => {
    const { wrapper } = await mountToday()
    const trigger = wrapper.get('.me-more button')
    attachToBody(wrapper)
    trigger.element.focus()
    expect(wrapper.get('.tA-body').attributes('inert'), '关着时不该是 inert').toBeUndefined()

    await openModal(wrapper, 'more')
    expect(wrapper.get('.tA-body').attributes('inert')).toBeDefined()
    // 临时诊断
    console.log('DEBUG overlay in document:', document.querySelectorAll('.modal-overlay').length,
      '| in wrapper:', wrapper.findAll('.modal-overlay').length,
      '| body html len:', document.body.innerHTML.length,
      '| has modal-box:', wrapper.html().includes('modal-box'),
      '| tA-body contains overlay:', !!wrapper.get('.tA-body').element.querySelector('.modal-overlay'))
    const box = document.querySelector('.modal-overlay .modal-box')
    expect(box.contains(document.activeElement), '焦点没送进弹层').toBe(true)

    const close = [...box.querySelectorAll('button')].find((b) => b.textContent.includes('关闭'))
    close.click()
    await flushPromises()
    expect(wrapper.get('.tA-body').attributes('inert')).toBeUndefined()
    expect(document.activeElement, '关闭后焦点没回到触发器').toBe(trigger.element)
  })

  it('Tab 在弹层内循环，不落到底层；Shift+Tab 反向也循环', async () => {
    const { wrapper } = await mountToday()
    await openModal(wrapper, 'profile')   // 这个弹层里有多个可聚焦元素，循环才有意义
    const box = document.querySelector('.modal-overlay .modal-box')
    const list = [...box.querySelectorAll('a[href], button:not([disabled]), input:not([disabled])')]
    expect(list.length).toBeGreaterThan(1)
    const first = list[0]
    const last = list[list.length - 1]

    last.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(first)
    expect(box.contains(document.activeElement)).toBe(true)

    first.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement).toBe(last)
  })

  it('焦点被挪到弹层外时，Tab 会把它拉回弹层里', async () => {
    const { wrapper } = await mountToday()
    await openModal(wrapper, 'more')
    const box = document.querySelector('.modal-overlay .modal-box')
    document.body.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(box.contains(document.activeElement)).toBe(true)
  })

  it('换一个弹层（修改个人信息）同样成立：机制是共用的，不是只给二级视图写的', async () => {
    const { wrapper } = await mountToday()
    await openModal(wrapper, 'profile')
    expect(wrapper.get('.tA-body').attributes('inert')).toBeDefined()
    const box = document.querySelector('.modal-overlay .modal-box')
    expect(box.contains(document.activeElement)).toBe(true)
    document.querySelector('.modal-overlay button').click()
    await flushPromises()
    expect(wrapper.get('.tA-body').attributes('inert')).toBeUndefined()
  })
})
