// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'

/**
 * 花名册 · 从别的页面带 `?q=` 过来（2026-11-09 走查）。
 *
 * 人事提醒里健康证 / 档案待补的那一行点过来，落点必须已经筛到这个人：
 * 超管不该再在 19 个人里手动找一遍。这里压的是「进页面就预填 + 只剩匹配行」，
 * 以及这一页终于有自己的浏览器标签标题。
 */

function person(overrides = {}) {
  return {
    id: 1,
    name: '张三',
    phone: '13800000000',
    job_title: '案板',
    permission: '普通员工',
    admin_caps: [],
    approved: true,
    resigned: false,
    resigned_at: null,
    created_at: '2026-10-01T09:00:00+08:00',
    id_card_no: '110101199003077213',
    health_cert_date: '2025-12-01',
    health_cert_expires_on: '2026-12-01',
    health_cert_state: 'ok',
    health_cert_days_left: 60,
    base_salary: 6000,
    hire_date: '2026-01-01',
    profile_incomplete: false,
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

const 王强 = person({ id: 1, name: '王强', phone: '13800000001' })
const 李娜 = person({ id: 2, name: '李娜', phone: '13800000002' })

let originalUrl

beforeEach(() => {
  originalUrl = window.location.href
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => jsonResponse({ employees: [王强, 李娜], zones: [] })),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', originalUrl)
})

describe('花名册 · ?q= 预填搜索', () => {
  it('带着 ?q=王强 进来：搜索框预填、列表只剩王强', async () => {
    window.history.replaceState({}, '', '/workbench/hr/roster?q=王强')

    const wrapper = mount(HygieneRosterView)
    await flushPromises()

    expect(wrapper.get('.roster-search input').element.value).toBe('王强')
    const names = wrapper
      .findAll('.roster-row .roster-person-copy strong')
      .map((node) => node.text())
    expect(names).toEqual(['王强'])
    expect(wrapper.text()).not.toContain('李娜')
    wrapper.unmount()
  })

  it('没有 ?q= 时不预填，整份名单都在', async () => {
    window.history.replaceState({}, '', '/workbench/hr/roster')

    const wrapper = mount(HygieneRosterView)
    await flushPromises()

    expect(wrapper.get('.roster-search input').element.value).toBe('')
    const names = wrapper
      .findAll('.roster-row .roster-person-copy strong')
      .map((node) => node.text())
    expect(names).toHaveLength(2)
    wrapper.unmount()
  })

  it('浏览器标签标题是「花名册 · 工作台」，不再只写后台名', async () => {
    const wrapper = mount(HygieneRosterView)
    await flushPromises()

    expect(document.title).toBe('花名册 · 工作台')
    wrapper.unmount()
  })
})
