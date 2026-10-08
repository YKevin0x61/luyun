// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useRecipeAdmin } from '../useRecipeAdmin.js'
import { clearAuthStatusCache } from '../../utils/authStatus.js'

// 票 07 的缝：**「谁能编辑配方」由身份模型决定**（不再由一个前端登录开关决定）。
// 断言的是渲染出来的那一个词（`isAdmin` 是「管理」还是「只读」）跟着两套会话走 ——
// 不看组件内部状态、不碰私有函数。
//
// 四种组合：只有管理端会话（店长）、只有员工会话（扫码的厨师）、谁都没登录、
// 以及「探针还没回来」那一瞬间（fail-closed：先不显示管理入口，晚半拍比点错便宜）。
// 外加「只降不升」那一条：共用电脑上两套会话都在、本机记着员工档 → 不给管理入口；
// 而员工会话已经不在（登出 / 过期）时记忆值失去载体，按唯一可用的那一档走。

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 只有管理端会话（店长自己的机器）。 */
function adminOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    return jsonResponse({ detail: '需要员工登录' }, 401)
  })
}

/** 只有员工会话（厨师的手机）。 */
function staffOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    return jsonResponse({}, 404)
  })
}

/** 谁都没登录（扫码进来、还没登录）。 */
function anonymousFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    return jsonResponse({ detail: '需要员工登录' }, 401)
  })
}

/** 两套会话都有效（店里那台共用电脑）。 */
function bothSessionsFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    return jsonResponse({}, 404)
  })
}

/** 一个只用这个组合式函数的探针组件 —— 断言的标的就是它渲染出来的那两个字。 */
const Probe = {
  template: '<div>{{ isAdmin ? "管理" : "只读" }}</div>',
  setup() {
    return useRecipeAdmin()
  },
}

function mountProbe() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(Probe, { global: { plugins: [pinia] } })
}

beforeEach(() => {
  // `isLoggedIn()` 有 10 秒缓存，是跨用例唯一会串的模块级状态（会话本身每个用例自己 stub）。
  clearAuthStatusCache()
  window.localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('配方的管理入口：由工作台身份决定', () => {
  it('管理端会话在：超级管理员那一档，管理入口显示', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())

    const wrapper = mountProbe()
    // 探针还没回来之前一律按「不是管理端」算（fail-closed）。
    expect(wrapper.text()).toBe('只读')

    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('管理')
  })

  it('只有员工会话：厨师看到的是只读面，没有管理入口', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())

    const wrapper = mountProbe()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('只读')
  })

  it('谁都没登录（扫码进来还没登录）：也只读，不会先闪一下管理入口', async () => {
    vi.stubGlobal('fetch', anonymousFetch())

    const wrapper = mountProbe()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('只读')
  })

  it('只降不升：两套会话都在而本机记着员工档 → 不给管理入口', async () => {
    // 共用电脑上的底线（spec 故事 6）：员工那一档的会话还在（人就在这台设备上），
    // 即便店长的会话也挂着，也不自动升回超级管理员。
    window.localStorage.setItem('luyun.login.workbench.identity', 'staff')
    vi.stubGlobal('fetch', bothSessionsFetch())

    const wrapper = mountProbe()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('只读')
  })

  it('员工登出后只剩管理端会话（记忆员工仍在）：按超级管理员给管理入口', async () => {
    // 员工会话已经没了 —— 员工那一档此刻一格都打不开（进去就被守卫弹回登录页），
    // 记忆值失去载体，身份按唯一可用的管理端会话定，与顶栏切换器显示的那一档一致。
    window.localStorage.setItem('luyun.login.workbench.identity', 'staff')
    vi.stubGlobal('fetch', adminOnlyFetch())

    const wrapper = mountProbe()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('管理')
  })

  it('本机记着超级管理员、两套会话都在：管理入口显示', async () => {
    window.localStorage.setItem('luyun.login.workbench.identity', 'super')
    vi.stubGlobal('fetch', bothSessionsFetch())

    const wrapper = mountProbe()
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toBe('管理')
  })
})
