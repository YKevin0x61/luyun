// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import NavBar from '../NavBar.vue'

// 每个用例结束后自动卸载：导航条的首屏补滚带 rAF / 定时器，上一个用例的组件留在树里
// 会让那些回调跑到下一个用例的断言里（实测把「数据管理」那次滚动记进了 U17 的记录）。
enableAutoUnmount(afterEach)

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

  // U17：834 下「企微推送」被导航条右边缘裁掉。根因不是"没调 scrollIntoView"——首屏那次
  // 确实调了，但那一刻 `.nav-right` 里的时钟还是空的、连接状态也没到，中间那列比最终宽，
  // 导航条**还没溢出**，于是它什么都不做；等这些文字落进 DOM，列被压窄、导航条才真的溢出，
  // 而这时已经没人再滚它。修法是首屏跟在布局后面补滚（幂等）。
  it('首屏补滚：挂载后还会再滚一次，参数带 inline:center（U17）', async () => {
    const calls = []
    Element.prototype.scrollIntoView = function (options) {
      calls.push({ text: String(this.textContent || ''), options })
    }
    await mountNavBar('/wecom-push')
    // 只看这一格：文件里别的用例也会滚自己的那一格
    const mine = () => calls.filter((call) => call.text.includes('企微推送'))
    expect(mine().length, '挂载那一刻就该先滚一次').toBeGreaterThanOrEqual(1)

    // 补滚排在双 rAF 之后（jsdom 的 rAF ≈ 16ms 一帧）。
    await new Promise((resolve) => setTimeout(resolve, 80))

    expect(mine().length, '首屏没有补滚，列宽变化后当前项仍会被裁').toBeGreaterThanOrEqual(2)
    for (const call of mine()) {
      expect(call.options).toMatchObject({ inline: 'center' })
    }
  })
})

// 手机档第二行 tab 条跨满整宽那条规则，必须写在**栅格子项**身上。
// 2026-10-08「导航栏右边有遮挡」就是这条契约断了：A5 给 tab 条套了
// `.global-nav-tabs-wrap`（溢出渐隐的壳），`grid-column: 1 / -1` 却还留在里层的
// `.global-nav-tabs` 上 —— 里层不是 grid item，规则等于没写，自动排布把壳塞进第一列
// `minmax(0,1fr)`（它的宽度先被 `.nav-right` 吃掉 161px），390 下只剩 205px：tab 被
// `overflow-x` 硬裁在半个字上，右边一整片空着。jsdom 不做布局、量不出宽度，所以这里
// 守的是那个**会断的接缝**：规则写在谁身上，以及谁才是 `.global-nav` 的直接孩子。
describe('导航第二行的栅格契约（手机档）', () => {
  const THEME_CSS = readFileSync('src/styles/theme.css', 'utf8')

  /** 手机档那段 `@media (max-width: 720px)` 的正文（大括号配对，找不到就抛）。 */
  function mobileNavCss() {
    const start = THEME_CSS.indexOf('@media (max-width: 720px) {')
    if (start < 0) throw new Error('theme.css 里找不到手机档的 @media (max-width: 720px)')
    let depth = 0
    for (let i = THEME_CSS.indexOf('{', start); i < THEME_CSS.length; i += 1) {
      if (THEME_CSS[i] === '{') depth += 1
      else if (THEME_CSS[i] === '}' && --depth === 0) return THEME_CSS.slice(start, i + 1)
    }
    throw new Error('@media (max-width: 720px) 没有闭合')
  }

  /** 取某个选择器在给定 CSS 片段里的声明正文；选择器不存在返回空串。 */
  function declarations(css, selector) {
    const bare = css.replace(/\/\*[\s\S]*?\*\//g, '')
    const chunk = bare.split('}').find((part) => part.split('{')[0].trim() === selector)
    return chunk ? chunk.split('{')[1] : ''
  }

  it('跨满整宽写在栅格子项 .global-nav-tabs-wrap 上，里层滚动容器不带栅格定位', () => {
    const block = mobileNavCss()
    expect(declarations(block, '.global-nav-tabs-wrap')).toMatch(/grid-column:\s*1\s*\/\s*-1/)
    expect(declarations(block, '.global-nav-tabs-wrap')).toMatch(/grid-row:\s*2/)
    expect(
      declarations(block, '.global-nav-tabs'),
      '栅格定位写回里层了：它不是 grid item，规则会失效',
    ).not.toMatch(/grid-(column|row)/)
  })

  it('那个栅格子项确实是 .global-nav 的直接孩子', async () => {
    const { wrapper } = await mountNavBar('/admin')
    expect(wrapper.find('.global-nav > .global-nav-tabs-wrap').exists()).toBe(true)
    expect(
      wrapper.find('.global-nav > .global-nav-tabs').exists(),
      '嵌套一变，写在 .global-nav-tabs 上的栅格规则就失效了',
    ).toBe(false)
  })
})
