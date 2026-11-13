// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 首页两处员工餐入口（2026-11-13 走查 S2 / S7，ADR 0135）：
// - 员工档一行「今天这张单的状态」，点进单日页并带上**自然日**（不是首页那个 06:00
//   切的营业日）；
// - 管理端「下月菜单还没导入」一句提醒，点进员工餐页；
// - 两处都必须是「读不到就不显示」，不能把首页打回错误态。
//
// 断言全是渲染出来的东西与发出去的请求 —— 不看组件内部状态。日期用 `toFake: ['Date']`
// 只冻 Date（`staffRequest` 的内部超时用的定时器照常走真时钟）。

const ROUTES = [
  { path: '/workbench', component: { template: '<div />' } },
  { path: '/workbench/hr/calendar', component: { template: '<div />' } },
  { path: '/workbench/hr/inbox', component: { template: '<div />' } },
  { path: '/workbench/hr/roster', component: { template: '<div />' } },
  { path: '/workbench/hr/shifts', component: { template: '<div />' } },
  { path: '/workbench/hr/reminders', component: { template: '<div />' } },
  { path: '/workbench/floor/daily', component: { template: '<div />' } },
  { path: '/workbench/floor/fix', component: { template: '<div />' } },
  { path: '/workbench/staff/today', component: { template: '<div />' } },
  { path: '/workbench/staff/month', component: { template: '<div />' } },
  { path: '/workbench/staff/overtime', component: { template: '<div />' } },
  { path: '/workbench/staff/loss', component: { template: '<div />' } },
  { path: '/workbench/staff/clean', component: { template: '<div />' } },
  { path: '/workbench/staff/tasks', component: { template: '<div />' } },
  { path: '/workbench/tasks', component: { template: '<div />' } },
  { path: '/workbench/kitchen/prep-plan', component: { template: '<div />' } },
  { path: '/workbench/kitchen/recipe/manage', component: { template: '<div />' } },
  { path: '/workbench/kitchen/loss', component: { template: '<div />' } },
  { path: '/workbench/kitchen/staff-meal', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
]

// 北京 2026-11-13 00:30：自然日已经是 13 号；mock 的营业日仍是 2026-10-05。
// 这两个日期必须对不上，才能证明首页没有拿 `businessDate` 去问员工餐。
const NOW = new Date('2026-11-12T16:30:00.000Z')
const NATURAL_DAY = '2026-11-13'
const NEXT_MONTH = '2026-12'

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
  ],
}

function managerData(overrides = {}) {
  return {
    '/api/scheduling/day': { business_date: '2026-10-05', total: 0, groups: [] },
    '/api/scheduling/inbox': { today: '2026-10-05', requests: [], without_rule: [] },
    '/api/hygiene/admin/daily-queue': {
      date: '2026-10-05',
      is_today: true,
      items: [],
      health_cert_due: { count: 0, items: [] },
    },
    '/api/hygiene/admin/fix': { items: [] },
    '/api/tasks/board': { counts: { pending_review: 0 } },
    '/api/hygiene/admin/hr-reminders': {
      seniority: { count: 0, items: [] },
      birthdays: { count: 0, items: [] },
      certs: { count: 0, items: [] },
      incomplete: { count: 0, items: [] },
    },
    ...overrides,
  }
}

function staffData(overrides = {}) {
  return {
    '/api/scheduling/me': ME,
    '/api/scheduling/me/requests': { requests: [], incoming: [] },
    '/api/hygiene/staff/daily-work': { items: [] },
    '/api/hygiene/staff/deep-clean': { items: [] },
    '/api/hygiene/staff/fix': { items: [] },
    '/api/hygiene/staff/me': {
      employee: { id: 1, name: '张三', shift: '白班', zone_id: 3, zone_name: '后厨' },
      daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
      deep_clock: { hhmm: '20:00' },
    },
    '/api/hygiene/staff/attire': {
      attire: { business_date: '2026-10-05', required: true, status: 'todo', has_standard: true },
    },
    '/api/tasks/mine': { counts: { todo: 0, review: 0 } },
    ...overrides,
  }
}

/** 员工餐单日响应：`order` 是单据，`summary` 非空表示草稿已经填过内容。 */
function mealDay({ order = null, menuDay = { menu_date: NATURAL_DAY }, summary = [] } = {}) {
  return { menu_day: menuDay, order, dishes: [], other_items: [], summary }
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
  return vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    if (Object.prototype.hasOwnProperty.call(table, path)) {
      const value = table[path]
      if (value === 'fail') return jsonResponse({ detail: '读不出来' }, 500)
      if (value === 'missing') return jsonResponse({ detail: '还没有员工餐菜单' }, 404)
      return jsonResponse(value)
    }
    // 别的接口一律 404：首页不该顺手打别的端点（断言里查得到）。
    return jsonResponse({ detail: 'not found' }, 404)
  })
}

async function mountHome({ identity = null, table = {}, staffCaps = [] } = {}) {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
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
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []
  store.staffCaps = staffCaps

  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push('/workbench')
  await router.isReady()

  const wrapper = mount(WorkbenchHomeView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, fetchMock }
}

function requestedPaths(fetchMock) {
  return fetchMock.mock.calls.map(([url]) => String(url).split('?')[0])
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台首页 · 员工档的员工餐状态行', () => {
  it('草稿：显示「草稿」，点进单日页并带**自然日**（不是营业日）', async () => {
    const { wrapper, fetchMock } = await mountHome({
      identity: 'staff',
      table: staffData({
        [`/api/staff-meal/days/${NATURAL_DAY}`]: mealDay({
          order: { id: 7, order_date: NATURAL_DAY, status: 'draft', push_status: null },
          summary: [{ name: '鸡蛋', unit: '个', quantity: 30 }],
        }),
      }),
    })

    const row = wrapper.get('.wbh-meal')
    expect(row.text()).toContain('员工餐')
    expect(row.text()).toContain('草稿')
    // 00:30 北京时：自然日已经翻到 13 号，营业日还是 5 号 —— query 只能取前者。
    expect(row.attributes('href')).toBe(`/workbench/kitchen/staff-meal?date=${NATURAL_DAY}`)
    expect(requestedPaths(fetchMock)).toContain(`/api/staff-meal/days/${NATURAL_DAY}`)
    // 员工档不该去读月历/下月（那是管理端那一条）。
    expect(requestedPaths(fetchMock)).not.toContain(`/api/staff-meal/days/2026-10-05`)
  })

  it('已提交但推送失败：显示「推送失败」，失败原因就摊在行上', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({
        [`/api/staff-meal/days/${NATURAL_DAY}`]: mealDay({
          order: {
            id: 7,
            order_date: NATURAL_DAY,
            status: 'submitted',
            push_status: 'failed',
            push_error: '群机器人 webhook 已被移除',
          },
          summary: [{ name: '鸡蛋', unit: '个', quantity: 30 }],
        }),
      }),
    })

    expect(wrapper.get('.wbh-meal-state').text()).toBe('推送失败')
    expect(wrapper.get('.wbh-meal-detail').text()).toContain('webhook 已被移除')
  })

  it('已提交且推送成功：显示「已提交」', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({
        [`/api/staff-meal/days/${NATURAL_DAY}`]: mealDay({
          order: { id: 7, order_date: NATURAL_DAY, status: 'submitted', push_status: 'sent' },
          summary: [{ name: '鸡蛋', unit: '个', quantity: 30 }],
        }),
      }),
    })

    expect(wrapper.get('.wbh-meal-state').text()).toBe('已提交')
  })

  it('定时推送（scheduled）：说清哪天几点推，不写成已经发出去了', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({
        [`/api/staff-meal/days/${NATURAL_DAY}`]: mealDay({
          order: {
            id: 7,
            order_date: NATURAL_DAY,
            status: 'submitted',
            push_status: 'scheduled',
            planned_push_at: '2026-11-16T10:00:00+08:00',
          },
          summary: [{ name: '鸡蛋', unit: '个', quantity: 30 }],
        }),
      }),
    })

    const row = wrapper.get('.wbh-meal')
    expect(row.get('.wbh-meal-state').text()).toBe('已提交 · 11-16 10:00 推')
    // 还没到推送时间，不能有「推送失败」那句小字。
    expect(row.find('.wbh-meal-detail').exists()).toBe(false)
    expect(row.attributes('href')).toBe(`/workbench/kitchen/staff-meal?date=${NATURAL_DAY}`)
  })

  it('404（今天没有菜单）：这一行仍然在，说清楚是没有菜单，也照样能点进页面', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({ [`/api/staff-meal/days/${NATURAL_DAY}`]: 'missing' }),
    })

    const row = wrapper.get('.wbh-meal')
    expect(row.text()).toContain('今天没有员工餐菜单')
    expect(row.attributes('href')).toBe(`/workbench/kitchen/staff-meal?date=${NATURAL_DAY}`)
  })

  it('读失败（500）：整行不显示，但首页其余部分照常渲染', async () => {
    const { wrapper } = await mountHome({
      identity: 'staff',
      table: staffData({ [`/api/staff-meal/days/${NATURAL_DAY}`]: 'fail' }),
    })

    expect(wrapper.find('.wbh-meal').exists()).toBe(false)
    // 不能因为这一块读不出来就把「我的班」也一起吞掉。
    expect(wrapper.find('.wbh-me').exists()).toBe(true)
    expect(wrapper.text()).toContain('我的待办')
  })
})

describe('工作台首页 · 管理端的下月员工餐提醒', () => {
  const staffMeal = (value) => ({ [`/api/staff-meal/months/${NEXT_MONTH}`]: value })

  it('下月还没导入：提醒一句，点进员工餐页；常用区也有入口', async () => {
    const { wrapper, fetchMock } = await mountHome({
      identity: 'super',
      table: managerData(staffMeal({ month: NEXT_MONTH, days: [], summary: { days: 0, orders: 0 } })),
    })

    const row = wrapper.get('.wbh-meal-next')
    expect(row.text()).toContain(`${NEXT_MONTH} 员工餐菜单还没导入`)
    expect(row.text()).toContain('提前报 1 号的单')
    expect(row.attributes('href')).toBe('/workbench/kitchen/staff-meal')
    expect(requestedPaths(fetchMock)).toContain(`/api/staff-meal/months/${NEXT_MONTH}`)
    // 管理端不该去读单日（那是员工档那一条）。
    expect(requestedPaths(fetchMock).some((path) => path.startsWith('/api/staff-meal/days/'))).toBe(false)
    // 「常用」里的入口（R9）：从首页直接进员工餐页。
    expect(wrapper.get('.wbh-quick').text()).toContain('员工餐采购')
  })

  it('下月已经导入：不提醒', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData(staffMeal({
        month: NEXT_MONTH,
        days: [{ menu_day: { menu_date: `${NEXT_MONTH}-01` }, order: null }],
        summary: { days: 1, orders: 0 },
      })),
    })

    expect(wrapper.find('.wbh-meal-next').exists()).toBe(false)
  })

  it('下月菜单读失败：不提醒，也不影响管理端四格', async () => {
    const { wrapper } = await mountHome({
      identity: 'super',
      table: managerData(staffMeal('fail')),
    })

    expect(wrapper.find('.wbh-meal-next').exists()).toBe(false)
    expect(wrapper.findAll('.wbh-kpi')).toHaveLength(4)
  })
})
