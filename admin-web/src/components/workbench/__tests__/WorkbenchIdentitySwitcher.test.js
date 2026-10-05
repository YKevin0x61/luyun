// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 票 04：顶栏身份切换器。**2026-10-05 起它只显示当前身份**（用户裁定）：
// 原来两档恒显，而"一人一设备"的现实下总有一档是死的 —— 员工手机上「超级管理员 共享账号」
// 恒为禁用态却占 132px（390 宽屏的 34%，比当前身份那 107px 还宽），点它只换来一句解释。
// 现在只有**这台设备真有两个会话**时才多出一个可点开的菜单；一个会话时它就是一行纯显示。
//
// 四类会话组合（两套都在 / 只有管理端 / 只有员工 / 都没有）× 记忆值，断言**渲染与切换
// 结果**：显示的是哪一档、点不点得开菜单、点下去变成谁、有没有那句降级提示。会话靠真实的
// 两个探针（`/api/auth/status`、`/api/hygiene/staff/me`）读出来，与守卫同一套口径；
// 身份判定本身在 `utils/__tests__/workbenchIdentity.test.js` 里逐行压过。
//
// 旧用例里"点禁用那一档 → 说清缺哪种会话"三条已随结构一起消失：那一档现在根本不渲染，
// 也就没有"点了没反应"这回事（D11 当初要解决的就是那个）。

const IDENTITY_KEY = 'luyun.login.workbench.identity'

/** 内存版 localStorage（照 `loginPrefs.test.js` 的写法），用例之间互不串味。 */
function memoryStorage() {
  const map = new Map()
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    clear: () => map.clear(),
    key: (i) => [...map.keys()][i] ?? null,
    get length() { return map.size },
    dump: () => Object.fromEntries(map),
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

const SESSIONS = {
  /** 店里那台共用电脑：两套会话都在 —— 只有这一种组合下菜单才会出现。 */
  both: { admin: true, staff: true },
  /** 只有管理端会话。 */
  admin: { admin: true, staff: false },
  /** 员工自己的手机：只有员工会话。 */
  staff: { admin: false, staff: true },
  /** 两套都没有（会话就在这一页上过期了）。 */
  none: { admin: false, staff: false },
}

/** 会话探针的假后端：与 `forbidden.test.js` 同一套 URL 口径。 */
function fetchFor(session, { name = '张三' } = {}) {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) {
      return jsonResponse({ logged_in: session.admin, initialized: true })
    }
    if (path.includes('/api/hygiene/staff/me')) {
      return session.staff
        ? jsonResponse({ employee: { name } })
        : jsonResponse({ detail: '需要员工登录' }, 401)
    }
    return jsonResponse({}, 404)
  })
}

const ROUTES = [
  '/workbench',
  '/workbench/me/today',
  '/workbench/floor/daily',
]

/**
 * 挂一次切换器。每个用例都换一份崭新的模块图：`authStatus` 有 10 秒状态缓存，
 * 不清模块的话上一个用例的会话结论会漏到下一个。
 *
 * `saved` 就是「这台设备记住了哪一档」—— 探针之前先写进 localStorage，与真实的
 * 「上次选过、这次重新打开」同一个顺序。
 */
async function mountSwitcher({ session, saved = null, staffName = '张三' } = {}) {
  vi.resetModules()
  vi.stubGlobal('fetch', fetchFor(session, { name: staffName }))
  if (saved) localStorage.setItem(IDENTITY_KEY, saved)
  const [{ default: Switcher }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchIdentitySwitcher.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  // 每个用例一份崭新的 pinia：store 是单例，跨用例复用会把上一轮的会话结论带进来。
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()

  const router = createRouter({
    history: createMemoryHistory(),
    routes: ROUTES.map((path) => ({ path, component: { template: '<div />' } })),
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  // 探针由组件自己开场（`onMounted` 里那次 refresh），测试不替它调 —— 挂上去之后
  // 显示成什么样，就是真实用法下的样子。
  const wrapper = mount(Switcher, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, store, router }
}

/** 那一颗「我是谁」。它现在只有一颗 —— 两个档位并排的结构已经取消。 */
function currentEl(wrapper) {
  const node = wrapper.find('.wb-id-current')
  if (!node.exists()) throw new Error('切换器没有渲染当前身份')
  return node
}

/** 这份会话下它该不该给菜单（判据在组件里：可用档位 ≥ 2）。 */
function canSwitch(wrapper) {
  return currentEl(wrapper).classes().includes('is-switchable')
}

/** 点开菜单并返回里面那一项。 */
async function openMenu(wrapper) {
  await currentEl(wrapper).trigger('click')
  await flushPromises()
  const item = wrapper.find('.wb-id-menu-item')
  if (!item.exists()) throw new Error('这台设备只有一个会话，不该有菜单')
  return item
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台身份切换器', () => {
  it('两套会话都在 + 没选过：自动定超级管理员，且给得出切档菜单', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.both })

    expect(currentEl(wrapper).text()).toContain('超级管理员')
    expect(currentEl(wrapper).text()).toContain('共享账号')
    // 两个可用档位 —— 这一档点得开（而且是**只有这一种组合**点得开）。
    expect(canSwitch(wrapper)).toBe(true)
    expect(wrapper.find('.wb-id-menu').exists()).toBe(false)
  })

  it('两套会话都在：菜单里那一项能切到员工', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.both })
    expect(store.identity).toBe('super')

    const item = await openMenu(wrapper)
    expect(item.text()).toContain('员工')
    await item.trigger('click')
    await flushPromises()

    expect(store.identity).toBe('staff')
    expect(currentEl(wrapper).text()).toContain('员工')
    // 切完菜单收起。
    expect(wrapper.find('.wb-id-menu').exists()).toBe(false)
  })

  it('只有管理端会话：只显示超级管理员，且**点不开**（没有菜单也没有箭头）', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.admin, saved: 'staff' })

    expect(currentEl(wrapper).text()).toContain('超级管理员')
    expect(canSwitch(wrapper)).toBe(false)
    expect(wrapper.find('.wb-id-caret').exists()).toBe(false)
    // 另一档根本不渲染 —— 于是也就没有"点了没反应"这回事。
    expect(wrapper.text()).not.toContain('员工')
  })

  it('只有员工会话：显示「员工（张三）」，点不开', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff })

    expect(currentEl(wrapper).text()).toContain('员工')
    expect(currentEl(wrapper).text()).toContain('张三')
    expect(canSwitch(wrapper)).toBe(false)
    expect(wrapper.text()).not.toContain('超级管理员')
  })

  it('两套都没有：什么都不渲染（不冒充身份）', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.none, saved: 'staff' })

    expect(store.identity).toBe(null)
    expect(wrapper.find('.wb-id').exists()).toBe(false)
  })

  it('只降不升：记着超级管理员而它失效、员工会话还在 → 降为员工并提示一次', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.staff, saved: 'super' })

    expect(store.identity).toBe('staff')
    expect(currentEl(wrapper).text()).toContain('员工')
    const notice = wrapper.find('.wb-id-notice')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toMatch(/管理端|店长|共享账号/)
  })

  it('降级提示不常驻：关掉就没了，重新打开也不再提', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, saved: 'super' })

    await wrapper.get('.wb-id-dismiss').trigger('click')
    await flushPromises()
    expect(wrapper.find('.wb-id-notice').exists()).toBe(false)
  })

  it('只降不升：记着员工而店长会话也在 → 仍显示员工，绝不自动升', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.both, saved: 'staff' })

    expect(store.identity).toBe('staff')
    expect(currentEl(wrapper).text()).toContain('员工')
    // 两档都可用 → 店长想切回去，菜单在。
    expect(canSwitch(wrapper)).toBe(true)
    expect((await openMenu(wrapper)).text()).toContain('超级管理员')
  })

  it('点菜单里那一项：当场换过去、记在本机', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.both, saved: 'staff' })

    const item = await openMenu(wrapper)
    await item.trigger('click')
    await flushPromises()

    expect(store.identity).toBe('super')
    expect(localStorage.getItem(IDENTITY_KEY)).toBe('super')
  })

  it('记在本机：换一次之后重新打开（同一台设备、同一个 localStorage）还是那一档', async () => {
    const first = await mountSwitcher({ session: SESSIONS.both })
    const item = await openMenu(first.wrapper)
    await item.trigger('click')
    await flushPromises()
    expect(first.store.identity).toBe('staff')

    // 同一份 storage、重新挂一次（等价于关掉再打开这台设备上的工作台）。
    const again = await mountSwitcher({ session: SESSIONS.both })
    expect(again.store.identity).toBe('staff')
    expect(currentEl(again.wrapper).text()).toContain('员工')
  })

  it('拿不到姓名时不显示空括号，只写「员工」', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, staffName: '' })

    const text = currentEl(wrapper).text()
    expect(text).toContain('员工')
    expect(text).not.toContain('（）')
    expect(text).not.toContain('()')
  })
})
