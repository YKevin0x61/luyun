// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 员工卫生页（`/workbench/me/clean`）**渲染出来的结构**（2026-10-05 用户裁定）。
 *
 * 同目录其它用例都是"读源码断字符串"的契约式断言。这一条不一样：五格合并成"单页依次
 * 渲染三组 + 页尾一行信息入口"之后，"三组按顺序排、汇总在最上面、榜默认收起"这件事
 * 只有真渲染一遍才看得准 —— 模板里一个 `v-if` 挂错地方，字符串断言照样绿。
 *
 * 只挂这一页（外壳、底栏各自有单测）：这一条压的是页内的排布与那三行入口的行为。
 */

const EMPLOYEE = {
  id: 7,
  name: '余威威',
  phone: '13800000000',
  job_title: '案板',
  permission: '普通员工',
  shift: '白班',
  zone_id: 3,
  zone_name: '案板',
}

// 三组各一项：汇总那一行因此是「今天要做 3 项 · 日常 1 · 专项 1 · 整改 1」。
const TABLE = {
  '/api/hygiene/staff/me': {
    employee: EMPLOYEE,
    daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
    deep_clock: { hhmm: '20:00' },
  },
  '/api/hygiene/staff/daily-work': {
    items: [{ item_id: 1, item_name: '台面', zone_id: 3, zone_name: '案板', shift: '白班', status: '待拍' }],
  },
  '/api/hygiene/staff/deep-clean': {
    items: [{ item_id: 2, item_name: '冰箱里面', status: '待拍' }],
    status: '待办',
  },
  '/api/hygiene/staff/fix': {
    items: [{
      id: 3, zone_name: '案板', ticket_type: '卫生', status: '待回拍', deadline: '2026-10-05T22:00:00+08:00',
    }],
  },
  '/api/hygiene/staff/daily-catalog': { zones: [{ id: 3, name: '案板' }] },
  '/api/hygiene/staff/boards': {
    week_start: '2026-09-28',
    people: [{ employee_id: 7, name: '余威威', 实拍: 2, 驳回: 0, 一次通过: 2, 逾期: 0 }],
    zones: [{ zone_id: 3, zone_name: '案板', 逾期: 0 }],
  },
  '/api/hygiene/staff/teaching': { items: [{ id: 9, title: '刀架怎么摆', left_label: '对', right_label: '错' }] },
}

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
  const fetchMock = vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    requested.push(path)
    if (Object.prototype.hasOwnProperty.call(TABLE, path)) return jsonResponse(TABLE[path])
    // 这一页不该顺手打别的端点（断言里查得到）。
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const [{ default: HygieneHomeView }] = await Promise.all([
    import('../HygieneHomeView.vue'),
  ])
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/clean', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/clean')
  await router.isReady()

  const wrapper = mount(HygieneHomeView, {
    global: {
      plugins: [router, pinia],
      // 标准图缓存面板自己有单测，它挂载时会去初始化本机缓存（jsdom 里没有那些 API）：
      // 这一条压的是页内排布，把它换成桩。
      stubs: { StandardPhotoCachePanel: true },
    },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, requested }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('员工卫生页 · 单页三组（2026-10-05 用户裁定）', () => {
  it('页头只有页名与那颗只读班次胶囊：页内那条「‹ 今天」与五格 tab 条都不在', async () => {
    const { wrapper } = await mountHome()

    expect(wrapper.find('.hy-tabbar').exists()).toBe(false)
    expect(wrapper.find('.hy-work-today').exists()).toBe(false)
    expect(wrapper.get('.hy-work-header').text()).toContain('卫生')
    expect(wrapper.get('.hy-work-shift').text()).toContain('余威威')
    expect(wrapper.get('.hy-work-shift').text()).toContain('案板')
  })

  it('顶部一行汇总三组待办之和，然后按 日常 → 专项 → 整改 → 信息入口 依次排下去', async () => {
    const { wrapper } = await mountHome()
    const text = wrapper.text()

    expect(text).toContain('今天要做 3 项')
    expect(text).toContain('日常 1 · 专项 1 · 整改 1')
    // 组标题各自带条数。
    const heads = wrapper.findAll('.hy-queue-group > h2').map((node) => node.text())
    expect(heads[0]).toBe('日常 1')
    expect(heads[1]).toContain('专项 1')
    expect(heads[2]).toBe('整改 1')
    expect(heads[3]).toBe('信息')
    // 三组 + 信息入口的顺序（按渲染出来的位置比，不按源码行号）。
    const order = ['今天要做 3 项', '日常 1', '专项 1', '整改 1', '红黑榜 · 卫生教材']
    const positions = order.map((needle) => text.indexOf(needle))
    expect(positions.every((at) => at > -1)).toBe(true)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it('「榜」默认收起（不是平级的一格）：点开才在本页展开，并去拉榜与教材', async () => {
    const { wrapper, requested } = await mountHome()
    expect(wrapper.find('#hygiene-boards-panel').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('人的红黑榜')
    // 收起时不拉：别人拍照广播的 boards，不在看就没必要跟着刷。
    expect(requested).not.toContain('/api/hygiene/staff/boards')

    const entry = wrapper.get('[aria-controls="hygiene-boards-panel"]')
    expect(entry.text()).toContain('红黑榜 · 卫生教材')
    expect(entry.attributes('aria-expanded')).toBe('false')
    await entry.trigger('click')
    await flushPromises()

    expect(wrapper.find('#hygiene-boards-panel').exists()).toBe(true)
    expect(wrapper.text()).toContain('人的红黑榜')
    expect(wrapper.text()).toContain('卫生教材')
    expect(requested).toContain('/api/hygiene/staff/boards')
    expect(requested).toContain('/api/hygiene/staff/teaching')
    expect(wrapper.get('[aria-controls="hygiene-boards-panel"]').attributes('aria-expanded')).toBe('true')
  })

  it('三组的数据一进页面就都拉上（不再等"切到哪一格"）', async () => {
    const { requested } = await mountHome()
    for (const path of [
      '/api/hygiene/staff/daily-work',
      '/api/hygiene/staff/deep-clean',
      '/api/hygiene/staff/fix',
      // 整改那一组要的工作区名单：原来靠"切到整改那一格"才拉，现在随首屏一起来。
      '/api/hygiene/staff/daily-catalog',
    ]) {
      expect(requested).toContain(path)
    }
  })
})
