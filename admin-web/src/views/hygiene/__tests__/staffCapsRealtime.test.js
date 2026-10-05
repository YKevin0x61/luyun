// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/** 捕获 `useNudgePull` 收到的 options，手动投递一条 nudge（真连 WS 在 jsdom 里做不到）。 */
let captured = null
vi.mock('../../../composables/useNudgePull', () => ({
  useNudgePull: (options) => {
    captured = options
    return { pull: vi.fn() }
  },
}))

/**
 * 放权的最后一公里（2026-10-06 真实浏览器实测发现的缺陷）。
 *
 * 实测现象：超管在花名册取消某人的「整改单」→ 服务端确实广播了 `roster`（scope 带本人的
 * `employee_id`，只推给他）→ 但员工卫生页的 `resources` 白名单里**没有** `roster`，这条
 * nudge 被 `match` 丢掉 → 页面开着 **30 秒零变化**，必须手动刷新才生效。
 *
 * 这条用例钉住修复的两半：`roster` 进白名单（match 放行），且收到它时重拉 `me` —— 页面上
 * 的判据（`canDailyReview` / `canDeepReview` / `canFix`）全部由 `employee.admin_caps` 派生，
 * 重拉一次它们就自己跟着变。
 */

const EMPLOYEE = {
  id: 7,
  name: '余威威',
  phone: '13800000000',
  job_title: '案板',
  permission: '管理员',
  admin_caps: ['daily_review'],
  shift: '白班',
  zone_id: 3,
  zone_name: '案板',
  zone_shifts: ['白班', '夜班'],
}

/** 服务端当前的权限（投放 nudge 之前改它，模拟"超管刚保存完"）。 */
let caps = ['daily_review']

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountHome() {
  const requested = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    requested.push(path)
    if (path === '/api/hygiene/staff/me') {
      return jsonResponse({
        employee: { ...EMPLOYEE, admin_caps: caps },
        daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
        deep_clock: { hhmm: '20:00' },
      })
    }
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/deep-clean') return jsonResponse({ items: [], status: '待办' })
    if (path === '/api/hygiene/staff/fix') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/daily-catalog') return jsonResponse({ zones: [] })
    if (path === '/api/hygiene/staff/boards') return jsonResponse({ week_start: '', people: [], zones: [] })
    if (path === '/api/hygiene/staff/teaching') return jsonResponse({ items: [] })
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: HygieneHomeView } = await import('../HygieneHomeView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/clean', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/clean')
  await router.isReady()

  const wrapper = mount(HygieneHomeView, {
    global: { plugins: [router, pinia], stubs: { StandardPhotoCachePanel: true } },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, requested }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  captured = null
  caps = ['daily_review']
})

describe('员工卫生页 · 放权后自动生效', () => {
  it('resources 白名单里有 roster，match 放行这条 nudge', async () => {
    const { wrapper } = await mountHome()
    expect(captured).toBeTruthy()
    expect(captured.match({ type: 'nudge', topic: 'hygiene', scope: { resource: 'roster' } })).toBe(true)
    // 白名单还是白名单：没订的资源照样被挡（别为了修这一条把 match 改成恒真）。
    expect(captured.match({ type: 'nudge', topic: 'hygiene', scope: { resource: 'orders' } })).toBe(false)
    wrapper.unmount()
  })

  it('收到 roster nudge 会重拉 me，新的 admin_caps 立刻生效（不必手动刷新）', async () => {
    const { wrapper, requested } = await mountHome()
    const meCalls = () => requested.filter((path) => path === '/api/hygiene/staff/me').length
    const before = meCalls()

    // 超管刚在花名册上加了一项「整改单」。
    caps = ['daily_review', 'fix']
    await captured.pull({ scope: { resource: 'roster' } })
    await flushPromises()
    await flushPromises()

    expect(meCalls()).toBe(before + 1)
    // 判据是 computed，拉完自己就变了 —— 「开整改单」跟着出现。
    expect(wrapper.text()).toContain('开整改单')
    wrapper.unmount()
  })

  it('与放权无关的资源不会顺带重拉 me（nudge 没有被放大）', async () => {
    const { wrapper, requested } = await mountHome()
    const meCalls = () => requested.filter((path) => path === '/api/hygiene/staff/me').length
    const before = meCalls()

    await captured.pull({ scope: { resource: 'daily' } })
    await flushPromises()

    expect(meCalls()).toBe(before)
    wrapper.unmount()
  })
})
