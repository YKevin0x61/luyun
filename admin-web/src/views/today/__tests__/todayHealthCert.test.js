// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 员工端「今天」页的健康证提示（2026-10 花名册改版）。
 *
 * 两件事：
 *  1. 页面最上面一条一行提示，**只在临期 / 过期时**出现，且不带日期（日期在「我的」那两行里）；
 *  2. 「我的」那张卡补两行档案（入职日期 / 健康证到期日），空值写「未设置」。
 * 数据全部来自 `/api/hygiene/staff/me` —— 底薪与身份证号**不下发也不显示**。
 */

const ME = {
  employee: { id: 7, name: '余威威' },
  days: [{
    business_date: '2026-10-05',
    is_today: true,
    scheduled: true,
    shift_id: 1,
    shift_name: '白班',
    zone_id: 3,
    zone_name: '案板',
    leave: false,
  }],
}

const HYGIENE_ME = {
  employee: {
    id: 7,
    name: '余威威',
    phone: '13800000000',
    job_title: '案板',
    permission: '普通员工',
    shift: '白班',
    zone_id: 3,
    zone_name: '案板',
  },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountToday(employee = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    if (path === '/api/scheduling/me') return jsonResponse(ME)
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') {
      return jsonResponse({ ...HYGIENE_ME, employee: { ...HYGIENE_ME.employee, ...employee } })
    }
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire: { required: false } })
    if (path === '/api/hygiene/staff/me/stats') {
      return jsonResponse({ days: 7, pass_rate: null, first_pass: 0, rejected: 0, reasons: [] })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/today', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  const wrapper = mount(TodayView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('员工端 · 健康证提示', () => {
  it('临期：页面最上面一行琥珀提示，不带日期', async () => {
    const wrapper = await mountToday({
      health_cert_state: 'soon',
      health_cert_expires_on: '2026-10-25',
      hire_date: '2026-01-01',
    })

    const alert = wrapper.get('.health-cert-alert')
    expect(alert.text()).toBe('健康证快到期，请尽快办理')
    expect(alert.attributes('role')).toBe('status')
    expect(alert.classes()).toContain('is-soon')
    // 日期不在这句里（在「我的」那两行里）。
    expect(alert.text()).not.toContain('2026-10-25')
    // 只有一处：不重复出现第二遍。
    expect(wrapper.findAll('.health-cert-alert')).toHaveLength(1)
  })

  it('过期：朱砂那档（不带 is-soon 修饰类）', async () => {
    const wrapper = await mountToday({
      health_cert_state: 'expired',
      health_cert_expires_on: '2026-09-01',
    })

    const alert = wrapper.get('.health-cert-alert')
    expect(alert.text()).toBe('健康证已过期，请尽快办理')
    expect(alert.classes()).not.toContain('is-soon')
  })

  it('正常 / 没办：整条提示不出现', async () => {
    for (const state of ['ok', 'none']) {
      const wrapper = await mountToday({ health_cert_state: state })
      expect(wrapper.find('.health-cert-alert').exists(), state).toBe(false)
      wrapper.unmount()
    }
  })

  it('「我的」补两行档案：入职日期与健康证到期日，空值写「未设置」', async () => {
    const wrapper = await mountToday({
      hire_date: '2026-01-01',
      health_cert_expires_on: '2026-10-25',
      health_cert_state: 'soon',
    })

    const meta = wrapper.get('.me-meta').text()
    expect(meta).toContain('入职日期')
    expect(meta).toContain('2026-01-01')
    expect(meta).toContain('健康证到期日')
    expect(meta).toContain('2026-10-25')

    wrapper.unmount()

    const empty = await mountToday({ hire_date: null, health_cert_expires_on: null })
    expect(empty.get('.me-meta').text()).toContain('未设置')
  })

  it('敏感字段边界：底薪与身份证号不下发也不显示', async () => {
    const wrapper = await mountToday({ hire_date: '2026-01-01', health_cert_state: 'soon' })

    for (const forbidden of ['底薪', '身份证号']) {
      expect(wrapper.text()).not.toContain(forbidden)
    }
  })
})
