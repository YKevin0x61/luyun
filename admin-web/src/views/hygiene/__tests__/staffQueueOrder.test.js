// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 员工卫生页：专项 / 整改两组的**渲染顺序**（③-3b / t51-F2，t57 落地）。
 *
 * 为什么这条要真挂载：排序是**行为**，字符串断言只能证明"代码里有那几行"，证明不了
 * 排对了。载荷故意打乱，且混进一条「待验收」——它必须沉到最后（「等验收不算逾期」，
 * 页面自己也是这么写的一句话），只靠读源码看不出这个。
 *
 * 夹具的时限都按 `Date.now()` 相对算，避免写死日期后过一阵子就变成另一种桶。
 */

const EMPLOYEE = {
  id: 7, name: '余威威', phone: '13800000000', job_title: '案板',
  permission: '普通员工', shift: '白班', zone_id: 3, zone_name: '案板',
}

const MIN = 60 * 1000
const past = () => new Date(Date.now() - 5 * MIN).toISOString()
const soon = () => new Date(Date.now() + 10 * MIN).toISOString()
const later = () => new Date(Date.now() + 3 * 60 * MIN).toISOString()

// 专项：载荷顺序打乱（还早 / 已交 / 超时 / 快到期）→ 期望渲染成 超时 → 快到期 → 还早 → 已交
const DEEP = [
  { item_id: 11, item_name: '专项-还早', status: '待拍', deadline: later() },
  { item_id: 12, item_name: '专项-已交', status: '待验收', deadline: past() },
  { item_id: 13, item_name: '专项-超时', status: '待拍', deadline: past() },
  { item_id: 14, item_name: '专项-快到期', status: '待拍', deadline: soon() },
]
// 整改：载荷顺序打乱（还早 / 超时 / 快到期）→ 期望 超时 → 快到期 → 还早
const FIX = [
  { id: 21, zone_name: '整改-还早', ticket_type: '卫生', status: '待整改', deadline: later() },
  { id: 22, zone_name: '整改-超时', ticket_type: '卫生', status: '待整改', deadline: past() },
  { id: 23, zone_name: '整改-快到期', ticket_type: '卫生', status: '待整改', deadline: soon() },
]

const TABLE = {
  '/api/hygiene/staff/me': {
    employee: EMPLOYEE,
    daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
    deep_clock: { hhmm: '20:00' },
  },
  '/api/hygiene/staff/daily-work': {
    items: [{ item_id: 1, item_name: '台面', zone_id: 3, zone_name: '案板', shift: '白班', status: '待拍' }],
  },
  '/api/hygiene/staff/deep-clean': { items: DEEP, status: '待办' },
  '/api/hygiene/staff/fix': { items: FIX },
  '/api/hygiene/staff/daily-catalog': { zones: [{ id: 3, name: '案板' }] },
  '/api/hygiene/staff/boards': { week_start: '2026-09-28', people: [], zones: [] },
  '/api/hygiene/staff/teaching': { items: [] },
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
  const fetchMock = vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    if (Object.prototype.hasOwnProperty.call(TABLE, path)) return jsonResponse(TABLE[path])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const [{ default: HygieneHomeView }] = await Promise.all([import('../HygieneHomeView.vue')])
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
  return wrapper
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** 页面上所有任务行的标题（`<strong>`），按渲染顺序。 */
function renderedTitles(wrapper) {
  return wrapper.findAll('.hy-task strong').map((node) => node.text())
}

describe('员工卫生页 · 专项与整改两组按紧迫度排（t57 / F2）', () => {
  it('专项组：逾期 → 将到期 → 待拍 → 等验收（载荷顺序被打乱也一样）', async () => {
    const wrapper = await mountHome()
    const deep = renderedTitles(wrapper).filter((t) => t.startsWith('专项-'))
    expect(deep).toEqual(['专项-超时', '专项-快到期', '专项-还早', '专项-已交'])
  })

  it('整改组：逾期 → 将到期 → 待拍', async () => {
    const wrapper = await mountHome()
    const fix = renderedTitles(wrapper).filter((t) => t.startsWith('整改-'))
    // 整改行的标题是「区域 · 类型」两段（模板里就是这么渲染的）
    expect(fix).toEqual(['整改-超时 · 卫生', '整改-快到期 · 卫生', '整改-还早 · 卫生'])
  })

  it('日常那一组不受影响：它照常渲染（顺序由共享 util 的 buildWorkQueue 负责）', async () => {
    const wrapper = await mountHome()
    // 日常那一组的第一件走「下一件」那张卡（不是 .hy-task），所以这里只钉它在屏上，
    // 组内顺序仍由 staffWork / staffHomeRender 那两条守。
    expect(wrapper.text()).toContain('台面')
    expect(renderedTitles(wrapper)).toContain('专项-超时')
  })
})
