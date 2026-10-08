// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 工作台首页（票 06）：给定身份渲染哪些区块、数字为 0 / 读不出来时什么样、
// 每个数字点去哪。断言全是渲染出来的东西与发出去的请求 —— 不看组件内部状态。
//
// 身份直接放进 store（探针本身在 `workbenchShell.test.js` / `WorkbenchIdentitySwitcher.test.js`
// 里压过）：这里要压的是「首页拿到某一档之后渲染什么、不发什么」。

const ROUTES = [
  { path: '/workbench', component: { template: '<div />' } },
  { path: '/workbench/hr/calendar', component: { template: '<div />' } },
  { path: '/workbench/hr/inbox', component: { template: '<div />' } },
  { path: '/workbench/hr/roster', component: { template: '<div />' } },
  { path: '/workbench/floor/daily', component: { template: '<div />' } },
  { path: '/workbench/floor/fix', component: { template: '<div />' } },
  { path: '/workbench/me/today', component: { template: '<div />' } },
  { path: '/workbench/me/clean', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
]

const DAY = {
  business_date: '2026-10-05',
  total: 3,
  groups: [
    { shift: { id: 1, name: '白班' }, count: 2, people: [{ id: 1, name: '张三' }, { id: 2, name: '李四' }] },
    { shift: { id: 2, name: '夜班' }, count: 1, people: [{ id: 3, name: '王五' }] },
  ],
}

const ME = {
  employee: { id: 1, name: '张三' },
  today: '2026-10-05',
  days: [
    {
      business_date: '2026-10-05',
      is_today: true,
      scheduled: true,
      shift_id: 1,
      shift_name: '白班',
      zone_id: 3,
      zone_name: '后厨',
      leave: false,
    },
    { business_date: '2026-10-06', is_today: false, scheduled: true, shift_id: 2, shift_name: '夜班' },
  ],
}

/** 店长那一档的接口响应（默认全空 —— 「数字为空」那一条用它）。 */
function managerData(overrides = {}) {
  return {
    '/api/scheduling/day': { business_date: '2026-10-05', total: 0, groups: [] },
    '/api/scheduling/inbox': { today: '2026-10-05', requests: [], without_rule: [] },
    '/api/hygiene/admin/daily-queue': {
      date: '2026-10-05',
      is_today: true,
      items: [],
      // 健康证到期（2026-10 花名册改版）：第四格的数字来自这里，与花名册行标签同源。
      health_cert_due: { count: 0, items: [] },
    },
    '/api/hygiene/admin/fix': { items: [] },
    // 人事提醒：本月该调工龄奖 + 本月生日 + 健康证临期/过期的人数，三块都从这一条来
    // （健康证到期 2026-10-08 从首页单独一格并进来）。
    '/api/hygiene/admin/hr-reminders': {
      seniority: { count: 0, items: [] },
      birthdays: { count: 0, items: [] },
      certs: { count: 0, items: [] },
      incomplete: { count: 0, items: [] },
    },
    ...overrides,
  }
}

/** 员工那一档的接口响应。 */
function staffData(overrides = {}) {
  return {
    '/api/scheduling/me': ME,
    '/api/scheduling/me/requests': { requests: [], incoming: [] },
    '/api/hygiene/staff/daily-work': { items: [] },
    '/api/hygiene/staff/deep-clean': { items: [] },
    '/api/hygiene/staff/fix': { items: [] },
    ...overrides,
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function fetchStub(table) {
  return vi.fn(async (url, init = {}) => {
    const path = String(url).split('?')[0]
    if (Object.prototype.hasOwnProperty.call(table, path)) {
      const value = table[path]
      if (value === 'fail') return jsonResponse({ detail: '读不出来' }, 500)
      return jsonResponse(value)
    }
    // 别的接口一律 404：首页不该顺手打别的端点（断言里查得到）。
    return jsonResponse({ detail: 'not found' }, 404)
  })
}

async function mountHome({ identity = null, table = {}, path = '/workbench' } = {}) {
  vi.resetModules()
  const fetchMock = fetchStub(table)
  vi.stubGlobal('fetch', fetchMock)

  const [{ default: WorkbenchHomeView }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchHomeView.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  // 探针的结论直接放进 store：`sessions` 也都标成探过了，免得首页去等一个不会来的探针。
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []

  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(WorkbenchHomeView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router, fetchMock, store }
}

function requestedPaths(fetchMock) {
  return fetchMock.mock.calls.map(([url]) => String(url).split('?')[0])
}

let originalLocation

beforeEach(() => {
  originalLocation = window.location
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  if (window.location !== originalLocation) {
    Object.defineProperty(window, 'location', { value: originalLocation, writable: true })
  }
})

describe('工作台首页 · 身份还没探出来', () => {
  it('不渲染任何数字、也不发任何一个取数请求（别先闪一个错的视角）', async () => {
    const { wrapper, fetchMock } = await mountHome({ identity: null, table: {} })

    expect(wrapper.text()).toContain('正在确认身份')
    expect(wrapper.find('.wbh-num').exists()).toBe(false)
    expect(wrapper.find('.wbh-cell').exists()).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('工作台首页 · 店长视角', () => {
  it('今天谁上班：各班人数与名字，点进排班月历', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({ '/api/scheduling/day': DAY }),
    })

    const duty = wrapper.get('.wbh-duty')
    expect(duty.text()).toContain('今天谁上班')
    expect(duty.text()).toContain('白班')
    expect(duty.text()).toContain('张三')
    expect(duty.text()).toContain('夜班')
    expect(wrapper.get('.wbh-duty').attributes('href')).toBe('/workbench/hr/calendar')
    // 员工那一档的东西一个都不在。
    expect(wrapper.find('.wbh-me').exists()).toBe(false)
  })

  it('四个数字各自渲染，各点进对应专页', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({
        '/api/scheduling/inbox': {
          requests: [{ id: 1, kind: 'leave' }, { id: 2, kind: 'swap' }],
        },
        '/api/hygiene/admin/daily-queue': {
          items: [{ status: '待验收' }, { status: '待验收' }],
          health_cert_due: { count: 3, items: [] },
        },
        '/api/hygiene/admin/fix': {
          items: [
            { id: 1, status: '待回拍', deadline: '2020-01-01T00:00:00+08:00' },
            { id: 2, status: '待回拍', deadline: '2999-01-01T00:00:00+08:00' },
          ],
        },
      }),
    })

    const cells = wrapper.findAll('.wbh-cell')
    expect(cells).toHaveLength(4)
    expect(cells.map((cell) => cell.attributes('href'))).toEqual([
      '/workbench/hr/inbox', '/workbench/floor/daily', '/workbench/floor/fix',
      '/workbench/hr/reminders',
    ])
    // 待批请假只数请假（换班那条还在等对方点头，店长这儿看不到）。
    expect(wrapper.get('.wbh-cell.leaves .wbh-num').text()).toBe('1')
    expect(wrapper.get('.wbh-cell.reviews .wbh-num').text()).toBe('2')
    // 逾期整改只数过了截止时间、还等着回拍的那张。
    expect(wrapper.get('.wbh-cell.fixes .wbh-num').text()).toBe('1')
  })

  it('健康证到期不再占首页一格：它的数字并进人事提醒（2026-10-08 用户裁定）', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({
        '/api/hygiene/admin/daily-queue': {
          date: '2026-10-05',
          is_today: true,
          items: [],
          // 这条接口仍然带健康证那一块，但首页已经不看它了 —— 数字走 hr-reminders。
          health_cert_due: { count: 2, items: [{ id: 5, name: '孙平', expires_on: '2026-09-01', state: 'expired' }] },
        },
      }),
    })

    expect(wrapper.find('.wbh-cell.certs').exists()).toBe(false)
    expect(wrapper.get('.wbh-cell.reminders').attributes('href')).toBe('/workbench/hr/reminders')
    // 首页只摊数字：敏感字段（底薪 / 身份证号）哪里都不出现。
    expect(wrapper.text()).not.toContain('底薪')
    expect(wrapper.text()).not.toContain('身份证号')
  })

  it('hr-reminders 读不出来时那一格给「—」，不是 0（少一块也不给半截数）', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({ '/api/hygiene/admin/hr-reminders': 'fail' }),
    })
    expect(wrapper.get('.wbh-cell.reminders .wbh-num').text()).toBe('—')

    // 只回一半（缺 birthdays）：相加会得到一个偏小的数，比画「—」更误导。
    const partial = await mountHome({
      identity: 'super',
      table: managerData({
        '/api/hygiene/admin/hr-reminders': { seniority: { count: 2, items: [] } },
      }),
    })
    expect(partial.wrapper.get('.wbh-cell.reminders .wbh-num').text()).toBe('—')
  })

  it('人事提醒那一格：数字是三块相加，点进人事提醒页', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({
        '/api/hygiene/admin/hr-reminders': {
          seniority: { count: 2, items: [] },
          birthdays: { count: 1, items: [] },
          // 健康证到期（2026-10-08 从首页单独一格并进来）：它算进这个数字。
          certs: { count: 1, items: [] },
          // 档案待补的人数**不进这一格** —— 那不是一件要办的事，是档案缺项。
          incomplete: { count: 7, items: [] },
        },
      }),
    })

    const cell = wrapper.get('.wbh-cell.reminders')
    expect(cell.get('.wbh-num').text()).toBe('4')
    expect(cell.attributes('href')).toBe('/workbench/hr/reminders')
  })

  it('数字为 0 时是稳稳的 0（不是空白、也不是整块消失）', async () => {
    const { wrapper } = await mountHome({ identity: 'super', table: managerData() })

    for (const key of ['leaves', 'reviews', 'fixes', 'reminders']) {
      const num = wrapper.get(`.wbh-cell.${key} .wbh-num`)
      expect(num.text()).toBe('0')
    }
    expect(wrapper.get('.wbh-duty').text()).toContain('今天还没排班')
    // 0 也照旧点得进去（门口的意义就是「去看那一页」）。
    expect(wrapper.get('.wbh-cell.leaves').attributes('href')).toBe('/workbench/hr/inbox')
  })

  it('某一块读不出来时那一格给「—」，别的格子照常显示', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData({
        '/api/hygiene/admin/daily-queue': 'fail',
        '/api/scheduling/inbox': { requests: [{ id: 1, kind: 'leave' }] },
      }),
    })

    expect(wrapper.get('.wbh-cell.leaves .wbh-num').text()).toBe('1')
    expect(wrapper.get('.wbh-cell.reviews .wbh-num').text()).toBe('—')
    expect(wrapper.get('.wbh-cell.fixes .wbh-num').text()).toBe('0')
  })

  it('首页不发任何写请求、也不打员工端那些接口', async () => {
    const { fetchMock } = await mountHome({
      identity: 'super',
      table: managerData({ '/api/scheduling/day': DAY }),
    })

    for (const [, init] of fetchMock.mock.calls) {
      const method = (init && init.method) || 'GET'
      expect(method).toBe('GET')
    }
    const paths = requestedPaths(fetchMock)
    expect(paths).toContain('/api/scheduling/day')
    expect(paths).toContain('/api/scheduling/inbox')
    expect(paths).toContain('/api/hygiene/admin/daily-queue')
    expect(paths).toContain('/api/hygiene/admin/fix')
    expect(paths).toContain('/api/hygiene/admin/hr-reminders')
    for (const path of paths) {
      expect(path.startsWith('/api/hygiene/staff/')).toBe(false)
      expect(path.startsWith('/api/scheduling/me')).toBe(false)
      // 首页上不能出现验收 / 批准这类动作端点。
      expect(path).not.toMatch(/\/accept|\/reject|\/approve|\/submit/)
    }
    // 今天谁上班问的是**服务端那个营业日**（不是手机上的今天）：营业日从
    // `daily-queue` 那条响应拿（它不带日期时回的就是营业日），再拿它去问排班。
    const dayCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/scheduling/day'))
    expect(String(dayCall[0])).toContain('date=2026-10-05')
    const queueCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/hygiene/admin/daily-queue'))
    expect(String(queueCall[0])).not.toContain('date=')
  })
})

describe('工作台首页 · 员工视角', () => {
  it('我的班次与工作区用自己的接口，点进「今天」页那张卡', async () => {
    const { wrapper } = await mountHome({ identity: 'staff', table: staffData() })

    const me = wrapper.get('.wbh-me')
    expect(me.text()).toContain('白班')
    expect(me.text()).toContain('后厨')
    expect(me.attributes('href')).toBe('/workbench/me/today')
    // 店长那三个数字与今天谁上班都不在。
    expect(wrapper.find('.wbh-duty').exists()).toBe(false)
    expect(wrapper.findAll('.wbh-cell.leaves')).toHaveLength(0)
  })

  it('我的待办条数是自己那三块的和，点进「卫生待办」', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({
        '/api/hygiene/staff/daily-work': { items: [{ status: '待拍' }, { status: '已通过' }] },
        '/api/hygiene/staff/deep-clean': { items: [{ status: '待回拍' }] },
        '/api/hygiene/staff/fix': { items: [{ id: 9, status: '待回拍' }] },
      }),
    })

    expect(wrapper.get('.wbh-cell.myItems .wbh-num').text()).toBe('3')
    expect(wrapper.get('.wbh-cell.myItems').attributes('href')).toBe('/workbench/me/clean')
  })

  it('等我回应的换班算一条待办，点进「今天」页处理', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({ '/api/scheduling/me/requests': { requests: [], incoming: [{ id: 7 }] } }),
    })

    const cell = wrapper.get('.wbh-cell.myRequests')
    expect(cell.get('.wbh-num').text()).toBe('1')
    expect(cell.attributes('href')).toBe('/workbench/me/today')
  })

  it('没班 / 数据为空时给一句人话，不是空白', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({
        '/api/scheduling/me': {
          employee: { id: 1, name: '张三' },
          today: '2026-10-05',
          days: [{ business_date: '2026-10-05', is_today: true, scheduled: true, shift_id: null }],
        },
      }),
    })

    expect(wrapper.get('.wbh-me').text()).toContain('休')
    expect(wrapper.get('.wbh-cell.myItems .wbh-num').text()).toBe('0')
  })
})
