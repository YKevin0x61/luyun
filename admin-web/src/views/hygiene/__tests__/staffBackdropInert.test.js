// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 弹层打开时背景要 `inert`，**关闭时属性必须真的从 DOM 上消失**（t70）。
 *
 * 为什么这条值得单开一个文件：`inert` 是「**存在即生效**」的布尔属性 —— 写
 * `:inert="Boolean(sheet)"` 时，关闭状态下 Vue 照样把 `inert="false"` 渲染出来，
 * 于是**背景永久不可达**（不是漏做，是反向生效）。t68 拿这两行当唯一照搬对象、
 * 照抄后把同一个 bug 搬进了 `TodayView.vue`，是它自己的测试把两边一起咬出来的。
 * 所以这里断的是 **DOM 上有没有这个属性**（`hasAttribute`），不是"值是不是 false"。
 *
 * 对照的断言写法见 §3：`Boolean(sheet) || null` —— `|| null` 才是让属性消失的那一半。
 */

const here = dirname(fileURLToPath(import.meta.url))
const HOME = join(here, '../HygieneHomeView.vue')

const EMPLOYEE = {
  id: 7, name: '余威威', phone: '13800000000', job_title: '案板',
  permission: '普通员工', shift: '白班', zone_id: 3, zone_name: '案板',
}

// 一个「待验收」的专项项：点它走 review 分支（jsdom 里安全 —— 不碰摄像头）
const DEEP_ITEM = { item_id: 12, item_name: '冷柜一号', status: '待验收' }

const TABLE = {
  '/api/hygiene/staff/me': {
    employee: EMPLOYEE,
    daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
    deep_clock: { hhmm: '20:00' },
  },
  '/api/hygiene/staff/daily-work': { items: [] },
  '/api/hygiene/staff/deep-clean': { items: [DEEP_ITEM], status: '待办' },
  '/api/hygiene/staff/deep-clean/12/review': {
    item_name: '冷柜一号', status: '待验收',
    watermark: { time: '2026-10-09T20:00:00+08:00', zone: '案板', photographer: '彭灼洪' },
    standard: null, shot: null, before: null, after: null,
  },
  '/api/hygiene/staff/fix': { items: [] },
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
    attachTo: document.body, // 焦点要用真实 document.activeElement
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('卫生页弹层的背景隔离：inert 关掉时必须真的消失（t70）', () => {
  it('弹层关闭：背景元素**没有** `inert` 属性（不是「有但为 false」）', async () => {
    const wrapper = await mountHome()
    const header = wrapper.find('.hy-work-header')
    const main = wrapper.find('#hygiene-work-main')
    expect(header.exists() && main.exists()).toBe(true)
    expect(header.attributes('inert'), '`inert="false"` 也算"存在即生效"，背景会永久不可达')
      .toBeUndefined()
    expect(main.attributes('inert')).toBeUndefined()
    expect(header.element.hasAttribute('inert')).toBe(false)
    expect(main.element.hasAttribute('inert')).toBe(false)
  })

  it('弹层打开：背景带上 `inert`，且 body 滚动锁上', async () => {
    const wrapper = await mountHome()
    const trigger = wrapper.find('.hy-task-main')
    expect(trigger.exists(), '夹具里那个专项项该渲染出来').toBe(true)
    await trigger.trigger('click')
    await flushPromises()
    await flushPromises()
    expect(wrapper.find('.hy-work-header').element.hasAttribute('inert')).toBe(true)
    expect(wrapper.find('#hygiene-work-main').element.hasAttribute('inert')).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('关掉弹层：属性重新消失，焦点回到触发它的那颗按钮', async () => {
    const wrapper = await mountHome()
    const trigger = wrapper.find('.hy-task-main')
    // 先真聚焦：`trigger('click')` 是合成事件，不会像真人那样把焦点带上去，
    // 而这一页存的 `lastFocusedElement` 正是 `document.activeElement`。
    trigger.element.focus()
    expect(document.activeElement).toBe(trigger.element)
    await trigger.trigger('click')
    await flushPromises()
    await flushPromises()

    const close = wrapper.find('.staff-preview-close')
    expect(close.exists(), '弹层该有一颗关闭键').toBe(true)
    await close.trigger('click')
    await flushPromises()

    expect(wrapper.find('.hy-work-header').element.hasAttribute('inert')).toBe(false)
    expect(wrapper.find('#hygiene-work-main').element.hasAttribute('inert')).toBe(false)
    expect(document.body.style.overflow).toBe('')
    // 关闭后焦点回到触发器（`watch(sheet)` 里存的 lastFocusedElement）
    expect(document.activeElement).toBe(trigger.element)
  })
})

describe('写法守卫：必须是 `Boolean(sheet) || null`（防止照抄回旧的坏写法）', () => {
  const src = readFileSync(HOME, 'utf8')

  it('两处绑定都带 `|| null`，且不再出现裸的 `Boolean(sheet)` 绑定', () => {
    const good = src.match(/:inert="Boolean\(sheet\) \|\| null"/g) || []
    expect(good.length).toBe(2)
    expect(src).not.toMatch(/:inert="Boolean\(sheet\)"/)
  })

  it('注释里写明「为什么必须是 `|| null`」，并点名参照物的教训', () => {
    expect(src).toMatch(/存在即生效/)
    expect(src).toMatch(/参照物本身可能比没有参照物更危险/)
  })
})
