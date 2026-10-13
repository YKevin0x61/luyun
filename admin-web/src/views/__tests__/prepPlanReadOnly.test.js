// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrepPlanView from '../PrepPlanView.vue'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

// 票 08 的验收（页面那一半）改到新形态：**备货数量不登记、每次计算都按 0 库存**之后，
// 这一页对谁都没有写控件 —— 员工那一档没有，管理端那一档也没有。
//
// 断言的是渲染出来的东西（有哪些按钮 / 输入框），不是组件内部状态。管理端那一档从前
// 断言「有登记表单」，现在反过来了：**它也不该有**，所以这条改成了防回归的「不存在」断言。
//
// 只读那一档还剩什么：**读路径**照旧 —— 刷新建议（GET /forecast）、复制清单（纯文本）、
// 时间窗与档口筛选。它们不写库，收掉反而让这一页在员工手里没用（看不了"今天要备什么"
// 就白搬进来了）。

/** 一条备货品，字段按新契约：没有 available_* / produced_qty / can_record / risk_level /
 *  batches / undo_batch_id，也没有 `slots[].available_qty`。 */
const FORECAST = {
  target_window: { start: '2026-10-05T07:30:00+08:00', end: '2026-10-06T07:30:00+08:00' },
  summary: { item_count: 1, todo_count: 1 },
  items: [
    {
      prep_item_id: 7,
      item_name: '虾饺馅',
      unit: '份',
      station: 'shulong',
      position: '1',
      forecast_qty: 20,
      safety_qty: 3,
      safety_stock_ratio: 0.1,
      effective_safety_ratio: 0.15,
      recommended_qty: 12,
      min_batch_qty: 1,
      min_batch_applied: false,
      confidence: 'high',
      reason: '',
      slots: [
        {
          slot_name: '午市',
          slot_start: '2026-10-05T11:00:00+08:00',
          slot_end: '2026-10-05T14:00:00+08:00',
          forecast_qty: 20,
          recommended_qty: 12,
        },
      ],
    },
  ],
  missing_rules: [],
  low_confidence: [],
}

/** 只在「列表项」里出现，不会出现在只读提示那种说明文字里的写法。 */
const FORBIDDEN_IN_ROW = [
  '非临期可用',
  '临期可用',
  '已做',
  '本品批次',
  '剩余',
  '过期时间',
  '多做一笔',
  '这次做了',
  '不用做',
  '缺货高',
]

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

/** 两套身份各挂一次：身份由工作台记忆值定，所以这里把记忆钉死。
 *
 *  点一次「刷新建议」把列表拉出来（这一页进来不会自己拉数据，与原样一致）—— 要断言的东西是
 *  **每条备货品**上的，没有列表就无从断言。 */
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

/** 写路径的入口与库存读数：两档身份都不许出现。 */
function expectNoWriteChrome(wrapper, who) {
  for (const selector of [
    '.prep-record',
    '.prep-extra',
    '.prep-undo',
    '.prep-discard',
    '.prep-row-actions',
    '.luyun-number',
  ]) {
    expect(wrapper.find(selector).exists(), `${selector} 还在${who}面上`).toBe(false)
  }
  expect(wrapper.find('input[type="number"]').exists()).toBe(false)
  expect(wrapper.find('.btn-danger').exists()).toBe(false)
  expect(wrapper.find('.prep-expiring').exists()).toBe(false)

  for (const word of FORBIDDEN_IN_ROW) {
    expect(wrapper.text(), `${word} 还在${who}面上`).not.toContain(word)
  }
}

beforeEach(() => {
  clearAuthStatusCache()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('备货计划页：登记那一套没了', () => {
  it('员工这一档没有可写控件，也没有可用量读数', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: true }))
    const wrapper = await mountView({ identity: 'staff' })

    // 页面确实渲染到了（否则"什么都没渲染"会假装通过）。
    expect(wrapper.text()).toContain('备货计划')
    expect(wrapper.text()).toContain('虾饺馅')

    expectNoWriteChrome(wrapper, '员工')
  })

  it('管理端这一档同样没有可写控件 —— 登记表单不许回来', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: false }))
    const wrapper = await mountView({ identity: 'super' })

    expect(wrapper.text()).toContain('备货计划')
    expect(wrapper.text()).toContain('虾饺馅')

    expectNoWriteChrome(wrapper, '管理端')
  })

  it('展开「怎么算的」只留预测 / 安全库存（含裕度）/ 建议制作量 / 信心', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: false }))
    const wrapper = await mountView({ identity: 'super' })

    const details = wrapper.get('.prep-row-more')
    const text = details.text()
    expect(text).toContain('预测')
    expect(text).toContain('安全库存')
    expect(text).toContain('裕度 15%')
    expect(text).toContain('建议制作量')
    expect(text).toContain('把握高')
    // ADR 0110 那句裕度说明留在面板里。
    expect(text).toContain('裕度按每个品最近的消耗波动定')
    // 库存那两行不再占位。
    expect(text).not.toContain('非临期可用')
    expect(text).not.toContain('临期可用')
  })
})

describe('员工这一档的备货计划页：读路径照旧', () => {
  it('刷新建议与复制清单还在，读接口只发 GET', async () => {
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

  it('明说这一页只读（不是静默少了东西）', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: true }))
    const wrapper = await mountView({ identity: 'staff' })

    expect(wrapper.text()).toContain('只读')
    // 只读那句不能提已经不存在的功能。
    expect(wrapper.get('.prep-readonly').text()).not.toContain('登记')
    expect(wrapper.get('.prep-readonly').text()).not.toContain('报废')
  })

  it('管理端不显示那句只读提示', async () => {
    vi.stubGlobal('fetch', probeFetch({ staff: false }))
    const wrapper = await mountView({ identity: 'super' })

    expect(wrapper.find('.prep-readonly').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('只读')
  })
})
