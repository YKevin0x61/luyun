// @vitest-environment jsdom
// ADR 0142 决策 10：版式类断言留在源码正则测试里，这一条只做「真挂载」冒烟——
// 首屏落在月历、切周表、切名单各一次，确认页面与 board 的接线在浏览器渲染路径上也成立
// （页面不再直接读接口，切视角的请求由 board 发出）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('../../../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../../../api/client'
import SchedulingCalendarView from '../SchedulingCalendarView.vue'

const MONTH = '2026-10'
const TODAY = '2026-10-14'

const CALENDAR = {
  month: MONTH,
  today: TODAY,
  window_end: '2026-10-31',
  shifts: [{ id: 1, name: '白班', is_active: true }],
  days: [
    {
      business_date: '2026-10-12',
      day: 12,
      weekday: 0,
      is_today: false,
      is_weekend: false,
      in_window: true,
      counts: { 1: 2 },
      total: 2,
      overridden: 0,
      half_rest: 0,
      row_count: 2,
    },
    {
      business_date: TODAY,
      day: 14,
      weekday: 2,
      is_today: true,
      is_weekend: false,
      in_window: true,
      counts: { 1: 3 },
      total: 3,
      overridden: 0,
      half_rest: 0,
      row_count: 3,
    },
  ],
}

const DAY = {
  business_date: TODAY,
  today: TODAY,
  groups: [{ shift: { id: 1, name: '白班' }, people: [] }],
  total: 3,
  off_count: 0,
  off_people: [],
  undoable: true,
}

const ROSTER = {
  employees: [{ id: 7, name: '张三', resigned: false, approved: true, rule: null, zone_defaults: [], job_title: '厨师' }],
  zones: [],
  shifts: [{ id: 1, name: '白班', is_active: true }],
  max_cycle_days: 30,
}

const WEEK = {
  start: '2026-10-12',
  today: TODAY,
  window_end: '2026-10-31',
  days: ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18']
    .map((business_date, weekday) => ({ business_date, weekday, in_window: true })),
  employees: [{ id: 7, name: '张三', cells: {}, has_rule: false, zone_defaults: [] }],
  shifts: [],
  leads: [],
  shard_order: [],
}

const INBOX = { requests: [] }
const SHEET = { employees: [] }

function routeGetMock() {
  api.get.mockImplementation((path) => {
    const table = {
      '/api/scheduling/calendar': CALENDAR,
      '/api/scheduling/day': DAY,
      '/api/scheduling/roster': ROSTER,
      '/api/scheduling/week': WEEK,
      '/api/scheduling/inbox': INBOX,
      '/api/attendance/sheet': SHEET,
    }
    return table[path] ? Promise.resolve(table[path]) : Promise.reject(new Error(`unexpected GET ${path}`))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  routeGetMock()
})

afterEach(() => {
  document.body.innerHTML = ''
})

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/workbench/hr/scheduling?month=2026-10')
  await router.isReady()
  const wrapper = mount(SchedulingCalendarView, {
    global: {
      plugins: [router],
      // 日期选择器与确认框是别的票的组件，这里只验本页接线。
      stubs: { LuyunDatePicker: true, ConfirmDialog: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

function clickedPaths() {
  return api.get.mock.calls.map(([path]) => path)
}

function viewButton(wrapper, label) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

describe('排班页真挂载（ADR 0142 决策 10 的三条冒烟）', () => {
  it('首屏落在月历，并读回当月 + 当天 + 待批', async () => {
    const { wrapper, router } = await mountPage()

    expect(wrapper.text()).toContain('排班')
    // 月历投影真的渲染出来了（标题来自 calendar payload），不是只挂了个空壳。
    expect(wrapper.text()).toContain('2026 年 10 月')
    // URL 归页面：首屏把 month 回写到 query（ADR 0142 决策 4）。
    expect(router.currentRoute.value.query.month).toBe('2026-10')
    expect(clickedPaths()).toContain('/api/scheduling/calendar')
    expect(api.get).toHaveBeenCalledWith('/api/scheduling/calendar', { month: MONTH })
    expect(clickedPaths()).toContain('/api/scheduling/day')
    expect(clickedPaths()).not.toContain('/api/scheduling/week')
  })

  it('切周表：页面经 board 拉那 7 天，不刷新整页', async () => {
    const { wrapper, router } = await mountPage()
    api.get.mockClear()

    await viewButton(wrapper, '周表').trigger('click')
    await flushPromises()

    // 周表投影渲染出人，页面把 view/start 回写进 URL。
    expect(wrapper.text()).toContain('张三')
    expect(router.currentRoute.value.query).toMatchObject({ view: 'week', start: '2026-10-12' })
    expect(clickedPaths()).toContain('/api/scheduling/week')
    expect(api.get).toHaveBeenCalledWith('/api/scheduling/week', { start: '2026-10-12' })
    // 切到周表不该把页面送回月历读一遍。
    expect(clickedPaths()).not.toContain('/api/scheduling/calendar')
  })

  it('切名单：读 /roster 并把视角留在名单', async () => {
    const { wrapper, router } = await mountPage()
    api.get.mockClear()

    await viewButton(wrapper, '名单').trigger('click')
    await flushPromises()

    // 名单投影渲染出人，view 回写进 URL。
    expect(wrapper.text()).toContain('张三')
    expect(router.currentRoute.value.query.view).toBe('roster')
    expect(clickedPaths()).toContain('/api/scheduling/roster')
    expect(wrapper.findAll('button').some((button) => button.classes().includes('on') && button.text() === '名单')).toBe(true)
  })
})
