// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const CARD = readFileSync(join(here, '..', 'StaffHygieneCard.vue'), 'utf8')

/**
 * 员工「今天」的卫生块（2026-10-14 用户裁定：从「我的今天」页整块搬到工作台首页）。
 *
 * 这一份测试是**跟着块搬过来的**：原先压在 `views/today/__tests__/todayView.test.js`
 * 里的那组仪容仪表断言（票 12）整组迁到这里，判据一个字没松 —— 尤其 ADR 0050 那条
 * 「标准图那一屏不许出现取景框」：它是用户裁定回退过一版的硬规则，跟着块走，不许因为
 * 搬家而少一条。另一半是**真挂一遍**这个组件（jsdom + 假 fetch），压渲染与两步拍照的
 * 外部行为。
 */

const ME = {
  employee: { id: 7, name: '余威威', shift: '白班', zone_id: 3, zone_name: '案板' },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

const DAILY_ITEMS = [
  { id: 1, zone_id: 3, name: '案板台面', status: '已通过' },
  { id: 2, zone_id: 3, name: '刀具消毒', status: '已通过' },
  { id: 3, zone_id: 3, name: '冰箱温度', status: '待拍' },
  { id: 4, zone_id: 3, name: '地漏', status: '待拍' },
  { id: 5, zone_id: 3, name: '垃圾桶', status: '待验收' },
]

const ATTIRE_REQUIRED = {
  business_date: '2026-10-14',
  required: true,
  status: 'todo',
  has_standard: true,
  note: '',
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountCard({ me = ME, items = [], attire = ATTIRE_REQUIRED } = {}) {
  const requested = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    requested.push(path)
    if (path === '/api/hygiene/staff/me') return jsonResponse(me)
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire })
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: StaffHygieneCard } = await import('../StaffHygieneCard.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workbench', component: { template: '<div />' } },
      { path: '/workbench/me/clean', component: { template: '<div />' } },
    ],
  })
  await router.push('/workbench')
  await router.isReady()

  const wrapper = mount(StaffHygieneCard, {
    global: {
      plugins: [router, pinia],
      // 相机与标准图那两个组件各自要设备/图片资源，jsdom 里给不出：换成能识别的壳，
      // 「哪一屏渲染了什么」照样压得住（ADR 0050 那条要的正是"这一屏里没有取景器"）。
      stubs: {
        HygieneLiveCamera: { template: '<div class="stub-camera" />' },
        HygieneStandardOverlay: { template: '<div class="stub-standard" />' },
      },
    },
  })
  await flushPromises()
  return { wrapper, router, requested }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('今天要交什么 · 源码契约（跟着块从「我的今天」页搬来）', () => {
  it('读员工自己那三条门，不碰管理端，也没有 employee_id', () => {
    expect(CARD).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me'\)/)
    expect(CARD).toMatch(/staffRequest\('\/api\/hygiene\/staff\/daily-work'\)/)
    expect(CARD).toMatch(/staffRequest\('\/api\/hygiene\/staff\/attire'\)/)
    expect(CARD).toMatch(/\/api\/hygiene\/staff\/attire\/submit/)
    expect(CARD).not.toMatch(/\/api\/hygiene\/admin/)
    // 读谁由 cookie 定：请求路径里不许出现员工号（注释里说明这件事不算 —— 所以断的是路径形状）。
    expect(CARD).not.toMatch(/\/api\/hygiene\/staff\/[^'"\s]*employee_id/)
  })

  it('仪容仪表按人拍：只在排到班次时出现', () => {
    // 判据是服务端给的 `required`（排班那天有班次）：休假的与没排到的整行不显示。
    expect(CARD).toMatch(/v-if="attire\.required"/)
    expect(CARD).toContain('超级管理员还没传标准图')
    expect(CARD).toContain('已交，等店长验收')
  })

  it('上传带 live=true，并进上传队列（断网不丢）', () => {
    expect(CARD).toMatch(/form\.append\('live', 'true'\)/)
    expect(CARD).toMatch(/imageUploads\.enqueue\(/)
    // 「这张正在传」从队列派生，刷新页面之后仍然成立。
    expect(CARD).toMatch(/pendingKey: attireKey\.value/)
  })

  it('ADR 0050：标准图那一屏没有取景框', () => {
    const branch = CARD.slice(
      CARD.indexOf("attireSheet === 'standard'"),
      CARD.indexOf("attireSheet === 'camera'"),
    )
    expect(branch).toMatch(/HygieneStandardOverlay/)
    expect(branch).toMatch(/打开相机/)
    // 标准图与取景器同屏是 ADR 0050 明说不做的：这里一旦出现就是又并排了。
    expect(branch).not.toMatch(/HygieneLiveCamera/)
  })

  it('已经通过的不给重拍入口', () => {
    // 服务端会拒 `already_accepted`：前端不摆一个按了必然失败的按钮。
    expect(CARD).toMatch(/attire\.status !== 'passed'/)
  })

  it('仪容仪表那一行不借英雄数字的字号，且按状态着色', () => {
    expect(CARD).toMatch(/<p class="shc-attire" :class="attireTone">/)
    expect(CARD).toMatch(/const attireTone = computed/)
    expect(CARD).toMatch(/\.shc-attire \{/)
    expect(CARD).toMatch(/\.shc-attire\.rejected \{/)
    expect(CARD).toMatch(/\.shc-attire\.nostandard \{/)
  })

  it('实时：这块自己订阅排班与卫生 nudge（宿主页不再代劳）', () => {
    expect(CARD).toMatch(/useNudgePull\(\{/)
    expect(CARD).toMatch(/id: 'staff-hygiene-card'/)
    expect(CARD).toMatch(/topics: \['hygiene', 'scheduling'\]/)
  })
})

describe('今天要交什么 · 真挂一遍', () => {
  it('有班次但没有日常检查：说清「今天没有要交的」，入口照旧在', async () => {
    const { wrapper } = await mountCard({ items: [] })
    expect(wrapper.text()).toContain('今天没有要交的日常检查。')
    expect(wrapper.text()).toContain('仪容仪表：今天还没拍')
    expect(wrapper.text()).toContain('工作卫生 ›')
    // 没有日常就一个数字都不摊（不给「日常 0/0」这种读不出来是 0 还是没读到的东西）。
    expect(wrapper.text()).not.toContain('日常 0/0')
  })

  it('排班还没给班次：说清该找谁，别让人在这儿等', async () => {
    const { wrapper } = await mountCard({
      me: { ...ME, employee: { ...ME.employee, shift: null, zone_name: '' } },
      attire: { ...ATTIRE_REQUIRED, required: false },
    })
    expect(wrapper.text()).toContain('排班还没给你班次')
    expect(wrapper.text()).toContain('找超级管理员配一下规则')
    // 没排到班次的人连仪容仪表那一行都看不到（按人拍由排班给）。
    expect(wrapper.text()).not.toContain('仪容仪表：')
  })

  it('有日常就摊进度：交了几项、还差几项、本班几点前交', async () => {
    const { wrapper } = await mountCard({ items: DAILY_ITEMS })
    expect(wrapper.text()).toContain('日常 2/5')
    expect(wrapper.text()).toContain('还差 3 项')
    expect(wrapper.text()).toContain('18:00 前交')
  })

  it('两步拍照：先看标准图（这一屏没有取景器），点了才开相机', async () => {
    const { wrapper } = await mountCard()
    await wrapper.get('.shc .acts .btn').trigger('click')
    await flushPromises()

    // 第一步：标准图那一屏 —— 取景器**不在**（ADR 0050）。
    expect(wrapper.find('.stub-standard').exists()).toBe(true)
    expect(wrapper.find('.stub-camera').exists()).toBe(false)

    const open = wrapper.findAll('button').find((node) => node.text() === '打开相机')
    expect(open).toBeTruthy()
    await open.trigger('click')
    await flushPromises()

    // 第二步：相机的屏 —— 标准图那一屏同时退场（是换屏，不是叠上去）。
    expect(wrapper.find('.stub-camera').exists()).toBe(true)
    expect(wrapper.find('.stub-standard').exists()).toBe(false)
  })

  it('已经通过：不给重拍入口，仍留「工作卫生 ›」', async () => {
    const { wrapper } = await mountCard({
      attire: { ...ATTIRE_REQUIRED, status: 'passed' },
    })
    expect(wrapper.text()).toContain('仪容仪表：已通过')
    expect(wrapper.text()).not.toContain('重拍一张 ›')
    expect(wrapper.text()).toContain('工作卫生 ›')
  })

  it('没传标准图：说清是谁没传，也不给一颗点了必然失败的按钮', async () => {
    const { wrapper } = await mountCard({
      attire: { ...ATTIRE_REQUIRED, has_standard: false },
    })
    expect(wrapper.text()).toContain('超级管理员还没传标准图')
    const labels = wrapper.findAll('button').map((node) => node.text())
    expect(labels).not.toContain('仪容仪表 ›')
    expect(labels).toContain('工作卫生 ›')
  })

  it('读不出来：整块变成「读不出来 + 重试」，不静默留空', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'boom' }, 500)))
    const { default: StaffHygieneCard } = await import('../StaffHygieneCard.vue')
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/workbench', component: { template: '<div />' } }],
    })
    await router.push('/workbench')
    await router.isReady()
    const wrapper = mount(StaffHygieneCard, { global: { plugins: [router, pinia] } })
    await flushPromises()

    expect(wrapper.findAll('button').map((node) => node.text())).toContain('重试')
  })

  it('「工作卫生 ›」把人送到员工卫生待办那一页', async () => {
    const { wrapper, router } = await mountCard()
    const go = wrapper.findAll('button').find((node) => node.text() === '工作卫生 ›')
    await go.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/workbench/me/clean')
  })
})
