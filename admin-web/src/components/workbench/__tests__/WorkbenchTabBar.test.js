// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 手机底栏（`WorkbenchTabBar.vue`）2026-11-02 的便捷改动：有待批时「人事」直接落待办页、
// 图标上带条数角标；没有待批、或这个人**批不动**（没开「人事管理」），仍旧回月历、不标角标。
//
// 这一层要钉的正是这三处分叉：它们都靠「读没读到 / 读到几条 / 有没有能力键」决定，
// 而这三件事在真机上肉眼分不清（角标不出现，可能是有待批没读出来、也可能是没权限）。
const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' } },
  { path: '/workbench/hr/calendar', component: { template: '<div>月历</div>' } },
  { path: '/workbench/hr/inbox', component: { template: '<div>待办</div>' } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常</div>' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' } },
  { path: '/workbench/staff/today', component: { template: '<div>我的</div>' } },
  { path: '/workbench/tasks', component: { template: '<div>任务</div>' } },
]

const LEAVE_ROWS = [
  { id: 1, employee_id: 3, employee_name: '张织纷', kind: 'leave', start_date: '2026-11-06', end_date: '2026-11-06' },
  { id: 2, employee_id: 3, employee_name: '张织纷', kind: 'leave', start_date: '2026-11-09', end_date: '2026-11-09' },
]

/** jsdom 没有 `matchMedia`，而底栏只在手机档拉这份数（桌面档待批入口在月历页头）。 */
function stubPhone(matches = true) {
  vi.stubGlobal('matchMedia', () => ({
    matches,
    media: '(max-width: 720px)',
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }))
}

function stubInbox(requests) {
  vi.stubGlobal('fetch', vi.fn(async (url) => ({
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => (String(url).includes('/api/scheduling/inbox')
      ? { requests, today: '2026-11-02' }
      : {}),
  })))
}

async function mountBar({ identity = 'staff', staffCaps = ['schedule'], path = '/workbench' } = {}) {
  vi.resetModules()
  localStorage.clear()
  const [{ default: WorkbenchTabBar }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchTabBar.vue'),
    import('../../../stores/workbenchIdentity'),
  ])
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  store.identity = identity
  store.staffCaps = staffCaps
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(WorkbenchTabBar, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return wrapper
}

/** 「人事」那一格的 href（底栏一格一组，按组名找）。 */
function hrLink(wrapper) {
  return wrapper.findAll('a').find((a) => a.text().includes('人事'))
}

beforeEach(() => {
  stubPhone(true)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('手机底栏的「人事」：有待批就直接是待办入口', () => {
  it('有待批：落在待办页，角标写条数', async () => {
    stubInbox(LEAVE_ROWS)
    const wrapper = await mountBar()
    const link = hrLink(wrapper)
    expect(link.attributes('href')).toBe('/workbench/hr/inbox')
    expect(link.find('.wb-tab-badge').text()).toBe('2')
  })

  it('没有待批：仍是月历，不出角标', async () => {
    stubInbox([])
    const wrapper = await mountBar()
    const link = hrLink(wrapper)
    expect(link.attributes('href')).toBe('/workbench/hr/calendar')
    expect(link.find('.wb-tab-badge').exists()).toBe(false)
  })

  it('没开「人事管理」的员工：底栏里根本没有「人事」这一格，也不会去问那份队列', async () => {
    // 这类人读 `/api/scheduling/inbox` 会被 403，导航表也把这一格过滤掉了 —— 连请求都不该发。
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({ requests: LEAVE_ROWS }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountBar({ staffCaps: [] })
    expect(hrLink(wrapper)).toBeUndefined()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('读到几条都数得清：角标跟着队列走，不是固定写死的 1', async () => {
    stubInbox([LEAVE_ROWS[0]])
    const wrapper = await mountBar()
    expect(hrLink(wrapper).find('.wb-tab-badge').text()).toBe('1')
  })

  it('桌面档不拉这份数（待批入口在月历页头）', async () => {
    stubPhone(false)
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => ({ requests: LEAVE_ROWS }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountBar()
    expect(hrLink(wrapper).attributes('href')).toBe('/workbench/hr/calendar')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

/** 「任务」那一格的 href（管理端底栏第五格）。 */
function taskLink(wrapper) {
  return wrapper.findAll('a').find((a) => a.text().includes('任务'))
}

/** 底栏会读的那两份数：人事待批队列 + 任务待验收数。 */
function stubBadges({ tasks = 0, inbox = [] } = {}) {
  const fetchMock = vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    let data = {}
    if (path === '/api/scheduling/inbox') data = { requests: inbox, today: '2026-11-02' }
    if (path === '/api/tasks/board') data = { counts: { pending_review: tasks } }
    return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => data }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('手机底栏的「任务」：管理端待验收角标（2026-11-11 票 07）', () => {
  it('有待验收：任务格标条数，落点仍是任务页', async () => {
    stubBadges({ tasks: 2 })
    const wrapper = await mountBar({ identity: 'super', staffCaps: [] })
    const link = taskLink(wrapper)
    expect(link.attributes('href')).toBe('/workbench/tasks')
    expect(link.find('.wb-tab-badge').text()).toBe('2')
    expect(link.find('.wb-tab-badge').attributes('aria-label')).toContain('条待验收')
  })

  it('没有待验收：任务格不出角标', async () => {
    stubBadges({ tasks: 0 })
    const wrapper = await mountBar({ identity: 'super', staffCaps: [] })
    expect(taskLink(wrapper).find('.wb-tab-badge').exists()).toBe(false)
  })

  it('员工会话不读任务板（那个接口对员工是 403），底栏也没有任务格', async () => {
    const fetchMock = stubBadges({ tasks: 2 })
    const wrapper = await mountBar({ staffCaps: ['schedule', 'task'] })
    expect(taskLink(wrapper)).toBeUndefined()
    expect(fetchMock.mock.calls.map(([url]) => String(url)).some((url) => url.includes('/api/tasks/board'))).toBe(false)
  })

  it('桌面档不拉待验收数', async () => {
    stubPhone(false)
    const fetchMock = stubBadges({ tasks: 2 })
    await mountBar({ identity: 'super', staffCaps: [] })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
