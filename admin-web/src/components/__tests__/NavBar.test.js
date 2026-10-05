// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import NavBar from '../NavBar.vue'

// 后台导航条上的「退出登录」（票 10）：以前是 `window.location.href = '/login'` ——
// 整页重载、丢掉原目标（navigation-audit 条目 8）。现在走与其余四处同一个实现：
// 客户端路由 + `?next=<原页>`。这里只断言外部行为：发哪个请求、落到哪个路由。

const ROUTES = [
  { path: '/', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
  { path: '/admin', component: { template: '<div />' } },
  { path: '/settings', component: { template: '<div />' } },
  { path: '/sales-report', component: { template: '<div />' } },
  { path: '/wecom-push', component: { template: '<div />' } },
  { path: '/logs', component: { template: '<div />' } },
  { path: '/workbench', component: { template: '<div />' } },
]

async function mountNavBar(path = '/admin') {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push('/')
  await router.push(path)
  await router.isReady()
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
  vi.stubGlobal('fetch', fetchMock)
  const wrapper = mount(NavBar, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router, fetchMock }
}

function logoutButton(wrapper) {
  return wrapper.findAll('button').find((button) => button.text() === '退出登录')
}

beforeEach(() => {
  vi.stubGlobal('confirm', () => true)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('后台导航的退出登录', () => {
  it('确认之后发登出请求，并客户端回登录页（带上当前页，整页不重载）', async () => {
    const { wrapper, router, fetchMock } = await mountNavBar('/workbench')

    await logoutButton(wrapper).trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/logout')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench')
  })

  it('点「取消」什么都不做（误点不该把会话退掉）', async () => {
    const { wrapper, router, fetchMock } = await mountNavBar('/admin')
    vi.stubGlobal('confirm', () => false)

    await logoutButton(wrapper).trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/admin')
  })
})

// A5：390 下 tab 条内容 424px、可视 374px，「日志」那一格整个在视口外；而容器又
// `scrollbar-width: none`，于是既看不出右边还有内容，切到 `/logs` 时也不会自动滚过去
// ——在日志页自己也看不出自己在哪。这两条断言的是"导航会把自己带到观众面前"。
describe('全局导航的溢出与当前项', () => {
  it('390 下确实溢出时给出两端渐隐提示（未溢出则不出现）', async () => {
    const { wrapper } = await mountNavBar('/admin')
    const wrap = wrapper.find('.global-nav-tabs-wrap')
    const scroller = wrap.find('.global-nav-tabs').element

    // jsdom 不做布局，scrollWidth/clientWidth 恒为 0；显式造出「溢出且停在最左」的
    // 读数，验证的是判定与 class 的接线，不是浏览器的排版结果。
    Object.defineProperty(scroller, 'scrollWidth', { value: 424, configurable: true })
    Object.defineProperty(scroller, 'clientWidth', { value: 374, configurable: true })
    scroller.dispatchEvent(new Event('scroll'))
    await wrapper.vm.$nextTick()

    // 停在最左：右边还有内容 → 只有右端遮罩；左边已经到底 → 左端不遮。
    expect(wrap.classes()).not.toContain('is-scroll-end')
    expect(wrap.classes()).toContain('is-scroll-start')

    // 滚到最右后反过来：左端提示出现，右端消失。
    scroller.scrollLeft = 50
    scroller.dispatchEvent(new Event('scroll'))
    await wrapper.vm.$nextTick()
    expect(wrap.classes()).toContain('is-scroll-end')
    expect(wrap.classes()).not.toContain('is-scroll-start')
  })

  it('路由切到 /logs 时把当前那一格滚进视野，而不是永远停在第一格', async () => {
    // jsdom 不实现 scrollIntoView：必须在挂载前装替身，否则首次同步就直接返回，
    // 记录不到任何调用。
    const scrolled = []
    Element.prototype.scrollIntoView = function () { scrolled.push(this.textContent) }
    const { router } = await mountNavBar('/admin')

    await router.push('/logs')
    await flushPromises()

    expect(scrolled.some((text) => String(text).includes('日志'))).toBe(true)
    // 被滚动的那一格必须真的是当前页——曾经把 :ref 写死在 `<router-link>` 上，
    // 函数 ref 会把"当前项"覆盖成最后渲染的一格（永远是「日志」），在别的页面上
    // 就会把日志格滚进视野、当前页反而看不见。
    await router.push('/sales-report')
    await flushPromises()
    expect(scrolled[scrolled.length - 1]).toContain('销售报表')
  })
})
