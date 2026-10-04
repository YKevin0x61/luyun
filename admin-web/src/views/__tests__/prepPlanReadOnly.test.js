// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrepPlanView from '../PrepPlanView.vue'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

// 票 08 的验收（页面那一半）：**员工身份下这一页是只读的 —— 没有任何可写控件**。
//
// 断言的是渲染出来的东西（有哪些按钮 / 输入框），不是组件内部状态：员工这一档看不到
// 「登记 / 多做一笔 / 撤销 / 报废」这几条写路径的入口，管理端那一档照旧看得到。
// 身份仍是工作台身份（`stores/workbenchIdentity`，判据 `identity === 'super'`）。
//
// 只读那一档还剩什么：**读路径**照旧 —— 刷新建议（GET /forecast）、复制清单（纯文本）、
// 时间窗与档口筛选。它们不写库，收掉反而让这一页在员工手里没用（看不了"今天要备什么"
// 就白搬进来了）。

/** 一条有主数据的备货品：管理端这一档它带着可写的登记表单。 */
const FORECAST = {
  target_window: { start: '2026-10-05T07:30:00+08:00', end: '2026-10-06T07:30:00+08:00' },
  summary: { item_count: 1, todo_count: 1 },
  items: [
    {
      item_name: '虾饺馅',
      unit: '份',
      station: 'shulong',
      recommended_qty: 12,
      forecast_qty: 20,
      safety_qty: 3,
      available_fresh_qty: 8,
      available_near_expiry_qty: 0,
      available_qty: 8,
      produced_qty: 0,
      confidence: 'normal',
      risk_level: 'normal',
      can_record: true,
      reason: '',
      batches: [],
    },
  ],
  missing_rules: [],
  low_confidence: [],
  expiring: [],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 记录每一次请求，供「员工这一档不写」的断言用。 */
function probeFetch({ staff }) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    calls.push({ path, method: options.method || 'GET' })
    if (path.includes('/api/auth/status')) {
      return jsonResponse({ logged_in: !staff, initialized: true })
    }
    if (path.includes('/api/hygiene/staff/me')) {
      return staff
        ? jsonResponse({ employee: { name: '张三' } })
        : jsonResponse({ detail: '需要员工登录' }, 401)
    }
    if (path.includes('/api/stations')) {
      return jsonResponse([{ id: 'shulong', name: '熟笼', color: '#111' }])
    }
    if (path.includes('/api/prep-plan/forecast')) return jsonResponse(FORECAST)
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

/** 员工那一档：两套 cookie 同域时身份由记忆值定，所以这里把记忆钉成员工。
 *
 *  点一次「刷新建议」把列表拉出来（这一页进来不会自己拉数据，与原样一致）—— 写控件是
 *  **每条备货品**上的东西，没有列表就无从断言。 */
async function mountView({ identity }) {
  vi.resetModules()
  window.localStorage.clear()
  window.localStorage.setItem('luyun.login.workbench.identity', identity)
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PrepPlanView, { global: { plugins: [pinia] } })
  await flushPromises()
  await flushPromises()
  await wrapper.get('.prep-refresh').trigger('click')
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  clearAuthStatusCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('员工这一档的备货计划页：只读', () => {
  it('没有可写控件：登记 / 多做一笔 / 撤销 / 报废一颗都不在，也没有数量输入框', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: true }))
    const wrapper = await mountView({ identity: 'staff' })

    // 页面确实渲染到了（否则"什么都没渲染"会假装通过）。
    expect(wrapper.text()).toContain('备货计划')
    expect(wrapper.text()).toContain('虾饺馅')

    // 写路径的四颗按钮按**类名**查（不按文字：只读提示那句话里也带着「登记」「报废」
    // 这两个词，按文字查会把提示本身当成写控件）。
    for (const selector of ['.prep-record', '.prep-extra', '.prep-undo', '.prep-discard']) {
      expect(wrapper.find(selector).exists(), `${selector} 还在员工面上`).toBe(false)
    }
    expect(wrapper.find('.luyun-number').exists()).toBe(false)
    expect(wrapper.find('input[type="number"]').exists()).toBe(false)
    // 一整条写路径的容器也不该剩下（`.prep-row-actions` 是那几颗按钮的住处）。
    expect(wrapper.find('.prep-row-actions').exists()).toBe(false)
    // 报废入口（临期那一条）挂的是 `btn-danger`。
    expect(wrapper.find('.btn-danger').exists()).toBe(false)
  })

  it('读路径照旧：刷新建议与复制清单还在，读接口只发 GET', async () => {
    const fetchMock = probeFetch({ staff: true })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = await mountView({ identity: 'staff' })

    expect(wrapper.find('.prep-refresh').exists()).toBe(true)
    expect(wrapper.find('.prep-export').exists()).toBe(true)
    // 员工的页面上没有一条非 GET 的请求。
    expect(fetchMock.calls.filter((call) => call.method !== 'GET')).toEqual([])
    // 已经拉过一次读接口（页面不是靠"什么都没请求"装出来的）。
    expect(fetchMock.calls.some((call) => call.path.includes('/api/prep-plan/forecast'))).toBe(true)
  })

  it('明说这一页只读（不是静默少了几颗按钮）', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: true }))
    const wrapper = await mountView({ identity: 'staff' })

    expect(wrapper.text()).toContain('只读')
  })
})

describe('管理端这一档的备货计划页：写入口照旧', () => {
  it('有主数据的备货品带着登记表单（数量输入框 + 登记）', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: false }))
    const wrapper = await mountView({ identity: 'super' })

    expect(wrapper.text()).toContain('备货计划')
    expect(wrapper.find('.luyun-number').exists()).toBe(true)
    expect(wrapper.text()).toContain('登记')
    // 管理端不显示那句只读提示。
    expect(wrapper.text()).not.toContain('只读')
  })
})
